from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CLAIM_DAILY_LIMIT, CLAIM_TIMEZONE
from database.mongodb import get_db
from utils.claim_stats import get_daily_claim_count, yangon_date_key
from utils.cooldown import should_ignore_update
from utils.text import escape_html, mention_user_doc
from utils.i18n import t


def _group_link_from_doc(doc: dict) -> str:
    title = str(doc.get("groupTitle") or doc.get("_id") or "Unknown Group")
    username = str(doc.get("groupUsername") or "").strip().lstrip("@")
    if username:
        return f'<a href="https://t.me/{escape_html(username)}">{escape_html(title)}</a>'
    return escape_html(title)


def _rank_emoji(index: int) -> str:
    if index == 1:
        return "🥇"
    if index == 2:
        return "🥈"
    if index == 3:
        return "🥉"
    return f"{index}."


def _time_sort_value(value) -> float:
    try:
        return float(value.timestamp())
    except Exception:
        return 4102444800.0


def _name_sort_value(row: dict) -> str:
    return str(row.get("firstName") or row.get("username") or "").lower()


async def topgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    rows = await get_db().claim_logs.aggregate(
        [
            {
                "$group": {
                    "_id": "$groupId",
                    "count": {"$sum": 1},
                    "groupTitle": {"$last": "$groupTitle"},
                    "groupUsername": {"$last": "$groupUsername"},
                }
            },
            {"$sort": {"count": -1, "groupTitle": 1}},
            {"$limit": 10},
        ]
    ).to_list(10)

    if not rows:
        await update.effective_message.reply_text(t("rank_no_group"))
        return

    lines = [t("rank_group_header"), "", t("rank_group_subtitle"), ""]
    for i, row in enumerate(rows, start=1):
        lines.append(t("rank_group_row", rank=_rank_emoji(i), group=_group_link_from_doc(row), count=int(row.get("count", 0))))
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def gtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return

    db = get_db()

    # Fetch more than 10 first, then sort with time-based tie breaker.
    rows = await db.users.aggregate(
        [
            {"$unwind": "$cards"},
            {
                "$group": {
                    "_id": "$userId",
                    "total": {"$sum": "$cards.count"},
                    "unique": {"$sum": 1},
                    "username": {"$last": "$username"},
                    "firstName": {"$last": "$firstName"},
                    "lastName": {"$last": "$lastName"},
                }
            },
            {"$sort": {"total": -1, "unique": -1}},
            {"$limit": 100},
        ]
    ).to_list(100)

    if not rows:
        await update.effective_message.reply_text(t("rank_no_global"))
        return

    user_ids = [int(row.get("_id", 0) or 0) for row in rows]

    log_rows = await db.claim_logs.aggregate(
        [
            {"$match": {"userId": {"$in": user_ids}}},
            {"$sort": {"createdAt": 1}},
            {
                "$group": {
                    "_id": "$userId",
                    "claimTimes": {"$push": "$createdAt"},
                }
            },
        ]
    ).to_list(None)

    claim_times_by_user = {
        int(row.get("_id", 0) or 0): list(row.get("claimTimes", []))
        for row in log_rows
    }

    for row in rows:
        user_id = int(row.get("_id", 0) or 0)
        total = int(row.get("total", 0) or 0)
        times = claim_times_by_user.get(user_id, [])

        if times and len(times) >= total:
            row["reachedTotalAt"] = times[total - 1]
        elif times:
            row["reachedTotalAt"] = times[-1]
        else:
            row["reachedTotalAt"] = None

    rows.sort(
        key=lambda row: (
            -int(row.get("total", 0) or 0),
            -int(row.get("unique", 0) or 0),
            _time_sort_value(row.get("reachedTotalAt")),
            _name_sort_value(row),
        )
    )

    rows = rows[:10]

    lines = [t("rank_global_header"), "", t("rank_global_subtitle"), ""]
    for i, row in enumerate(rows, start=1):
        user_doc = {
            "userId": int(row.get("_id", 0) or 0),
            "username": row.get("username", ""),
            "firstName": row.get("firstName", ""),
            "lastName": row.get("lastName", ""),
        }
        lines.append(
            t(
                "rank_global_row",
                rank=_rank_emoji(i),
                user=mention_user_doc(user_doc),
                total=int(row.get("total", 0) or 0),
                unique=int(row.get("unique", 0) or 0),
            )
        )

    await update.effective_message.reply_html(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


async def todaygtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return

    today = yangon_date_key()
    daily_limit = int(CLAIM_DAILY_LIMIT)

    rows = await get_db().claim_logs.aggregate(
        [
            {"$match": {"yangonDate": today}},

            
            {"$sort": {"createdAt": 1}},

            {
                "$group": {
                    "_id": "$userId",
                    "count": {"$sum": 1},
                    "claimTimes": {"$push": "$createdAt"},
                    "username": {"$last": "$username"},
                    "firstName": {"$last": "$firstName"},
                    "lastName": {"$last": "$lastName"},
                }
            },

        
            {
                "$addFields": {
                    "limitReached": {"$gte": ["$count", daily_limit]},
                    "limitReachedAt": {
                        "$cond": [
                            {"$gte": ["$count", daily_limit]},
                            {"$arrayElemAt": ["$claimTimes", daily_limit - 1]},
                            None,
                        ]
                    },
                    "lastClaimAt": {"$arrayElemAt": ["$claimTimes", -1]},
                }
            },

            {
                "$sort": {
                    "limitReached": -1,
                    "limitReachedAt": 1,
                    "count": -1,
                    "lastClaimAt": 1,
                    "firstName": 1,
                }
            },

            {"$limit": 10},
        ]
    ).to_list(10)

    if not rows:
        await update.effective_message.reply_text(
            t("rank_no_today", date=today, timezone=CLAIM_TIMEZONE)
        )
        return

    lines = [
        t("rank_today_header"),
        t("rank_today_date", date=escape_html(today), timezone=escape_html(CLAIM_TIMEZONE)),
        "",
        t("rank_today_subtitle"),
        "",
    ]

    for i, row in enumerate(rows, start=1):
        user_doc = {
            "userId": int(row.get("_id", 0) or 0),
            "username": row.get("username", ""),
            "firstName": row.get("firstName", ""),
            "lastName": row.get("lastName", ""),
        }

        lines.append(
            t(
                "rank_today_row",
                rank=_rank_emoji(i),
                user=mention_user_doc(user_doc),
                count=int(row.get("count", 0) or 0),
            )
        )

    await update.effective_message.reply_html(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


async def mylimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    if await should_ignore_update(update):
        return
    today = yangon_date_key()
    used = await get_daily_claim_count(update.effective_user.id, today)
    remaining = max(0, CLAIM_DAILY_LIMIT - used)
    await update.effective_message.reply_text(
        t("mylimit", date=today, timezone=CLAIM_TIMEZONE, used=used, limit=CLAIM_DAILY_LIMIT, remaining=remaining)
    )


def register_ranking_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("topgroup", topgroup_cmd))
    app.add_handler(CommandHandler("gtop", gtop_cmd))
    app.add_handler(CommandHandler("todaygtop", todaygtop_cmd))
    app.add_handler(CommandHandler("mylimit", mylimit_cmd))
