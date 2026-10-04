from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import CLAIM_DAILY_LIMIT, CLAIM_TIMEZONE
from database.mongodb import get_db
from utils.claim_stats import get_daily_claim_count, yangon_date_key
from utils.cooldown import should_ignore_update
from utils.text import escape_html, mention_user_doc
from utils.i18n import t
from utils.buttons import action_button


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


async def _global_rows() -> list[dict]:
    return await (await get_db().users.aggregate(
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
    )).to_list(10)


async def _today_rows() -> tuple[list[dict], str]:
    today = yangon_date_key()
    daily_limit = int(CLAIM_DAILY_LIMIT)
    rows = await (await get_db().claim_logs.aggregate(
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
    )).to_list(10)
    return rows, today


async def _group_rows() -> list[dict]:
    return await (await get_db().claim_logs.aggregate(
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
    )).to_list(10)


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

    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), label


async def _period_top_rows(period: str) -> tuple[list[dict], str]:
    start_utc, end_utc, label = _period_bounds(period)
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
    rows = await (await get_db().claim_logs.aggregate(pipeline).to_list(10)
    return rows, label


def _leaderboard_keyboard(user_id: int, active: str) -> InlineKeyboardMarkup:
    options = [
        ("global", t("rank_button_global"), "primary"),
        ("today", t("rank_button_today"), "primary"),
        ("week", t("rank_button_week"), "primary"),
        ("month", t("rank_button_month"), "primary"),
        ("group", t("rank_button_group"), "primary"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for mode, label, style in options:
        prefix = "✓ " if mode == active else ""
        current.append(action_button(prefix + label, style, callback_data=f"rank:{user_id}:{mode}"))
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([
        action_button(t("rank_button_close"), "danger", callback_data=f"rank:{user_id}:close"),
    ])
    return InlineKeyboardMarkup(rows)


async def build_leaderboard_view(user_id: int, mode: str = "global") -> tuple[str, InlineKeyboardMarkup]:
    mode = str(mode or "global").lower()
    if mode == "global":
        rows = await _global_rows()
        if not rows:
            text = t("rank_no_global")
        else:
            lines = [t("rank_global_header"), "", t("rank_global_subtitle"), ""]
            for i, row in enumerate(rows, start=1):
                lines.append(
                    f"{_rank_emoji(i)} {mention_user_doc(_user_doc_from_row(row))}\n"
                    f"   ├ <b>{int(row.get('unique', 0) or 0):,}</b> unique\n"
                    f"   └ <b>{int(row.get('total', 0) or 0):,}</b> total"
                )
            text = "\n".join(lines)
    elif mode == "today":
        rows, today = await _today_rows()
        if not rows:
            text = t("rank_no_today", date=today, timezone=CLAIM_TIMEZONE)
        else:
            lines = [
                t("rank_today_header"),
                t("rank_today_date", date=escape_html(today), timezone=escape_html(CLAIM_TIMEZONE)),
                "",
            ]
            for i, row in enumerate(rows, start=1):
                lines.append(
                    f"{_rank_emoji(i)} {mention_user_doc(_user_doc_from_row(row))} — "
                    f"<b>{int(row.get('count', 0) or 0):,}</b> catches"
                )
            text = "\n".join(lines)
    elif mode in {"week", "month"}:
        rows, label = await _period_top_rows(mode)
        title = t("rank_month_header") if mode == "month" else t("rank_week_header")
        if not rows:
            text = f"<b>{title}</b>\n\nNo claim data for {escape_html(label)}."
        else:
            lines = [f"<b>{title}</b>", f"<i>{escape_html(label)}</i>", ""]
            for i, row in enumerate(rows, start=1):
                lines.append(
                    f"{_rank_emoji(i)} {mention_user_doc(_user_doc_from_row(row))} — "
                    f"<b>{int(row.get('count', 0) or 0):,}</b> catches"
                )
            text = "\n".join(lines)
    elif mode == "group":
        rows = await _group_rows()
        if not rows:
            text = t("rank_no_group")
        else:
            lines = [t("rank_group_header"), "", t("rank_group_subtitle"), ""]
            for i, row in enumerate(rows, start=1):
                lines.append(
                    f"{_rank_emoji(i)} {_group_link_from_doc(row)} — "
                    f"<b>{int(row.get('count', 0) or 0):,}</b> catches"
                )
            text = "\n".join(lines)
    else:
        mode = "global"
        return await build_leaderboard_view(user_id, mode)
    return text, _leaderboard_keyboard(user_id, mode)


async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer(t("invalid_mode"), show_alert=True)
        return
    _, raw_user_id, mode = parts
    try:
        user_id = int(raw_user_id)
    except ValueError:
        await query.answer(t("invalid_mode"), show_alert=True)
        return
    if query.from_user.id != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return
    if mode == "close":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(t("cancelled"))
        await query.answer()
        return
    text, markup = await build_leaderboard_view(user_id, mode)
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    await query.answer()


async def _leaderboard_command(update: Update) -> None:
    if await should_ignore_update(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    text, markup = await build_leaderboard_view(int(update.effective_user.id), "global")
    await update.effective_message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def topgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    text, markup = await build_leaderboard_view(int(update.effective_user.id), "group")
    await update.effective_message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def gtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _leaderboard_command(update)


async def todaygtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    text, markup = await build_leaderboard_view(int(update.effective_user.id), "today")
    await update.effective_message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def mtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    if await should_ignore_update(update):
        return
    text, markup = await build_leaderboard_view(int(update.effective_user.id), "month")
    await update.effective_message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def wtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_message:
        return
    if await should_ignore_update(update):
        return
    text, markup = await build_leaderboard_view(int(update.effective_user.id), "week")
    await update.effective_message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


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
    app.add_handler(CallbackQueryHandler(leaderboard_callback, pattern=r"^rank:\d+:.+$"))
