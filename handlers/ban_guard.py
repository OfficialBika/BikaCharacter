from __future__ import annotations

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from utils.permissions import is_owner
from utils.user_bans import build_ban_notice, get_active_ban


BAN_GUARD_GROUP = -10000


def _message_is_bot_usage(update: Update) -> bool:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return False

    # Any direct message to the bot is bot usage.
    if chat.type == "private":
        return True

    # In groups, only intercept command-like usage so banned users can still chat.
    for value in (getattr(msg, "text", None), getattr(msg, "caption", None)):
        text = str(value or "").lstrip()
        if text.startswith("/") or text.startswith("."):
            return True

    return False


async def banned_message_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or is_owner(user):
        return
    if not _message_is_bot_usage(update):
        return

    ban = await get_active_ban(user.id)
    if not ban:
        return

    msg = update.effective_message
    try:
        if msg:
            await msg.reply_text(build_ban_notice(ban))
    except Exception:
        pass
    finally:
        raise ApplicationHandlerStop


async def banned_callback_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or is_owner(user):
        return

    ban = await get_active_ban(user.id)
    if not ban:
        return

    notice = build_ban_notice(ban)
    alert_text = notice if len(notice) <= 195 else notice[:192] + "..."
    try:
        await query.answer(alert_text, show_alert=True)
    finally:
        raise ApplicationHandlerStop


async def banned_inline_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    user = update.effective_user
    if not query or not user or is_owner(user):
        return

    ban = await get_active_ban(user.id)
    if not ban:
        return

    # Inline mode has no normal chat reply target. Clear results and try to send
    # the full notice in DM when Telegram permits it.
    try:
        await query.answer([], cache_time=0, is_personal=True)
    except Exception:
        pass

    try:
        await context.bot.send_message(chat_id=int(user.id), text=build_ban_notice(ban))
    except Exception:
        pass

    raise ApplicationHandlerStop


def register_ban_guard_handlers(app: Application) -> None:
    app.add_handler(MessageHandler(filters.ALL, banned_message_guard), group=BAN_GUARD_GROUP)
    app.add_handler(CallbackQueryHandler(banned_callback_guard), group=BAN_GUARD_GROUP)
    app.add_handler(InlineQueryHandler(banned_inline_guard), group=BAN_GUARD_GROUP)
