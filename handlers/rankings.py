from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CLAIM_DAILY_LIMIT, CLAIM_TIMEZONE
from database.mongodb import get_db
from utils.claim_stats import get_daily_claim_count, yangon_date_key
from utils.cooldown import should_ignore_update
from utils.text import escape_html, mention_user_doc


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
        await update.effective_message.reply_text("No group catch ranking yet.")
        return

    lines = ["🏆 <b>TOP GROUP RANKING</b>", "", "<b>/bika catches ranking</b>", ""]
    for i, row in enumerate(rows, start=1):
        lines.append(f"{_rank_emoji(i)} {_group_link_from_doc(row)} — <b>{int(row.get('count', 0))}</b> catches")
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def gtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    rows = await get_db().users.aggregate(
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
            {"$sort": {"total": -1, "unique": -1, "firstName": 1}},
            {"$limit": 10},
        ]
    ).to_list(10)

    if not rows:
        await update.effective_message.reply_text("No global harem ranking yet.")
        return

    lines = ["🌍 <b>GLOBAL TOP 10 USERS</b>", "", "<b>By total harem characters</b>", ""]
    for i, row in enumerate(rows, start=1):
        user_doc = {
            "userId": int(row.get("_id", 0) or 0),
            "username": row.get("username", ""),
            "firstName": row.get("firstName", ""),
            "lastName": row.get("lastName", ""),
        }
        lines.append(
            f"{_rank_emoji(i)} {mention_user_doc(user_doc)} — "
            f"<b>{int(row.get('total', 0) or 0)}</b> total | "
            f"{int(row.get('unique', 0) or 0)} unique"
        )
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def todaygtop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    today = yangon_date_key()
    rows = await get_db().claim_logs.aggregate(
        [
            {"$match": {"yangonDate": today}},
            {
                "$group": {
                    "_id": "$userId",
                    "count": {"$sum": 1},
                    "username": {"$last": "$username"},
                    "firstName": {"$last": "$firstName"},
                    "lastName": {"$last": "$lastName"},
                }
            },
            {"$sort": {"count": -1, "firstName": 1}},
            {"$limit": 10},
        ]
    ).to_list(10)

    if not rows:
        await update.effective_message.reply_text(f"No catches yet today.\nDate: {today} ({CLAIM_TIMEZONE})")
        return

    lines = ["📅 <b>TODAY GLOBAL TOP 10</b>", f"Date: <b>{escape_html(today)}</b> ({escape_html(CLAIM_TIMEZONE)})", "", "<b>By /bika catches today</b>", ""]
    for i, row in enumerate(rows, start=1):
        user_doc = {
            "userId": int(row.get("_id", 0) or 0),
            "username": row.get("username", ""),
            "firstName": row.get("firstName", ""),
            "lastName": row.get("lastName", ""),
        }
        lines.append(f"{_rank_emoji(i)} {mention_user_doc(user_doc)} — <b>{int(row.get('count', 0) or 0)}</b> catches")
    await update.effective_message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def mylimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    if await should_ignore_update(update):
        return
    today = yangon_date_key()
    used = await get_daily_claim_count(update.effective_user.id, today)
    remaining = max(0, CLAIM_DAILY_LIMIT - used)
    await update.effective_message.reply_text(
        "🎯 Daily Catch Limit\n\n"
        f"Date: {today} ({CLAIM_TIMEZONE})\n"
        f"Used: {used}/{CLAIM_DAILY_LIMIT}\n"
        f"Remaining: {remaining}"
    )


def register_ranking_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("topgroup", topgroup_cmd))
    app.add_handler(CommandHandler("gtop", gtop_cmd))
    app.add_handler(CommandHandler("todaygtop", todaygtop_cmd))
    app.add_handler(CommandHandler("mylimit", mylimit_cmd))
