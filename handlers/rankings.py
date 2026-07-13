from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def _user_doc_from_row(row: dict) -> dict:
    return {
        "userId": int(row.get("_id", 0) or 0),
        "username": row.get("username", ""),
        "firstName": row.get("firstName", ""),
        "lastName": row.get("lastName", ""),
    }


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
        lines.append(
            t(
                "rank_group_row",
                rank=_rank_emoji(i),
                group=_group_link_from_doc(row),
                count=int(row.get("count", 0)),
            )
        )
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def gtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return

    rows = await get_db().users.aggregate(
        [
            {
                "$project": {
                    "_id": "$userId",
                    "unique": {"$size": {"$ifNull": ["$cards", []]}},
                    "total": {
                        "$sum": {
                            "$map": {
                                "input": {"$ifNull": ["$cards", []]},
                                "as": "c",
                                "in": {"$ifNull": ["$$c.count", 0]},
                            }
                        }
                    },
                    "username": 1,
                    "firstName": 1,
                    "lastName": 1,
                }
            },
            {"$sort": {"unique": -1, "total": -1, "firstName": 1}},
            {"$limit": 10},
        ]
    ).to_list(10)

    if not rows:
        await update.effective_message.reply_text(t("rank_no_global"))
        return

    lines = [t("rank_global_header"), "", t("rank_global_subtitle"), ""]
    for i, row in enumerate(rows, start=1):
        lines.append(
            t(
                "rank_global_row",
                rank=_rank_emoji(i),
                user=mention_user_doc(_user_doc_from_row(row)),
                total=int(row.get("total", 0) or 0),
                unique=int(row.get("unique", 0) or 0),
            )
        )
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


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
        lines.append(
            t(
                "rank_today_row",
                rank=_rank_emoji(i),
                user=mention_user_doc(_user_doc_from_row(row)),
                count=int(row.get("count", 0) or 0),
            )
        )
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


def _period_bounds(period: str) -> tuple[datetime, datetime, str]:
    tz = ZoneInfo(CLAIM_TIMEZONE)
    now_local = datetime.now(tz)

    if period == "month":
        start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_local.month == 12:
            end_local = start_local.replace(year=start_local.year + 1, month=1)
        else:
            end_local = start_local.replace(month=start_local.month + 1)
        label = start_local.strftime("%B %Y")
    else:
        start_local = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_local = start_local + timedelta(days=7)
        label = f"{start_local:%d %b} - {(end_local - timedelta(days=1)):%d %b %Y}"

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        label,
    )


async def _period_top_rows(period: str) -> tuple[list[dict], str]:
    start_utc, end_utc, label = _period_bounds(period)

    # Monthly/weekly top should count every successful claim log.
    # Re-claiming the same card again must add another point.
    pipeline = [
        {"$match": {"createdAt": {"$gte": start_utc, "$lt": end_utc}}},
        {"$sort": {"createdAt": 1}},
        {
            "$group": {
                "_id": "$userId",
                "count": {"$sum": 1},
                "lastClaimAt": {"$last": "$createdAt"},
                "username": {"$last": "$username"},
                "firstName": {"$last": "$firstName"},
                "lastName": {"$last": "$lastName"},
            }
        },
        {"$sort": {"count": -1, "lastClaimAt": 1, "firstName": 1}},
        {"$limit": 10},
    ]
    rows = await get_db().claim_logs.aggregate(pipeline).to_list(10)
    return rows, label


async def _period_top_cmd(update: Update, period: str) -> None:
    if await should_ignore_update(update):
        return

    rows, label = await _period_top_rows(period)
    title = "📅 𝐌𝐎𝐍𝐓𝐇𝐋𝐘 𝐓𝐎𝐏 𝟏𝟎" if period == "month" else "🗓 𝐖𝐄𝐄𝐊𝐋𝐘 𝐓𝐎𝐏 𝟏𝟎"

    if not rows:
        await update.effective_message.reply_text(
            f"{title}\n\nNo claim data for {label}."
        )
        return

    lines = [
        f"<b>{title}</b>",
        f"<i>{escape_html(label)} • Total claimed cards</i>",
        "",
    ]
    for i, row in enumerate(rows, start=1):
        lines.append(
            f"{_rank_emoji(i)} {mention_user_doc(_user_doc_from_row(row))} — "
            f"<b>{int(row.get('count', 0) or 0)}</b> catches"
        )

    await update.effective_message.reply_html(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


async def mtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _period_top_cmd(update, "month")


async def wtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _period_top_cmd(update, "week")


async def mylimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    if await should_ignore_update(update):
        return
    today = yangon_date_key()
    used = await get_daily_claim_count(update.effective_user.id, today)
    remaining = max(0, CLAIM_DAILY_LIMIT - used)
    await update.effective_message.reply_text(
        t(
            "mylimit",
            date=today,
            timezone=CLAIM_TIMEZONE,
            used=used,
            limit=CLAIM_DAILY_LIMIT,
            remaining=remaining,
        )
    )


def register_ranking_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("topgroup", topgroup_cmd))
    app.add_handler(CommandHandler("gtop", gtop_cmd))
    app.add_handler(CommandHandler("todaygtop", todaygtop_cmd))
    app.add_handler(CommandHandler("mtop", mtop_cmd))
    app.add_handler(CommandHandler("wtop", wtop_cmd))
    app.add_handler(CommandHandler("mylimit", mylimit_cmd))
