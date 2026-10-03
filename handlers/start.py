from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import ADD_TO_GROUP_URL, BOT_USERNAME, SUPPORT_GROUP_URL, UPDATE_CHANNEL_URL
from utils.db_helpers import ensure_user
from utils.text import escape_html
from utils.i18n import t
from utils.buttons import action_button


def _add_to_group_url() -> str:
    if ADD_TO_GROUP_URL:
        return ADD_TO_GROUP_URL
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?startgroup=true"
    return "https://t.me/"


def _bot_deep_link(action: str) -> str:
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start={action}"
    return "https://t.me/"


def _start_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t("start_button_harem"), url=_bot_deep_link("harem")),
                InlineKeyboardButton(t("start_button_profile"), url=_bot_deep_link("profile")),
            ],
            [
                InlineKeyboardButton(t("start_button_search"), url=_bot_deep_link("search")),
                InlineKeyboardButton(t("start_button_favourite"), url=_bot_deep_link("fav")),
            ],
            [
                action_button(t("start_button_rankings"), "primary", callback_data=f"start:{user_id}:rank"),
                action_button(t("start_button_settings"), "primary", callback_data=f"start:{user_id}:settings"),
            ],
            [InlineKeyboardButton(t("start_button_add_group"), url=_add_to_group_url())],
            [
                InlineKeyboardButton(t("start_button_support"), url=SUPPORT_GROUP_URL),
                InlineKeyboardButton(t("start_button_update"), url=UPDATE_CHANNEL_URL),
            ],
        ]
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        await ensure_user(update.effective_user)

    user = update.effective_user
    if user:
        mention = f'<a href="tg://user?id={user.id}">{escape_html(user.full_name or user.username or "User")}</a>'
    else:
        mention = "User"

    action = str(context.args[0]).strip().lower() if context.args else ""
    deep_link_handlers = {
        "harem": ("handlers.harem", "harem_cmd"),
        "profile": ("handlers.profile", "profile_cmd"),
        "search": ("handlers.search", "search_cmd"),
        "fav": ("handlers.fav", "fav_cmd"),
    }
    target = deep_link_handlers.get(action)
    if target and update.effective_message and update.effective_user:
        module = __import__(target[0], fromlist=[target[1]])
        await getattr(module, target[1])(update, context)
        return

    text = t("start_message", mention=mention)

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_start_keyboard(int(user.id) if user else 0),
        disable_web_page_preview=True,
    )


async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer(t("invalid_mode"), show_alert=True)
        return
    _, user_raw, action = parts
    try:
        user_id = int(user_raw)
    except ValueError:
        await query.answer(t("invalid_mode"), show_alert=True)
        return
    if query.from_user.id != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return

    if action == "home":
        await query.edit_message_text(
            t("start_message", mention=f'<a href="tg://user?id={user_id}">{escape_html(query.from_user.full_name or query.from_user.username or "User")}</a>'),
            parse_mode=ParseMode.HTML,
            reply_markup=_start_keyboard(user_id),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action == "settings":
        from handlers.hmode import build_hmode_menu
        await query.edit_message_text(
            t("settings_message"),
            parse_mode=ParseMode.HTML,
            reply_markup=build_hmode_menu(user_id, include_home=True),
        )
        await query.answer()
        return

    if action == "rank":
        from handlers.rankings import build_leaderboard_view
        text, markup = await build_leaderboard_view(user_id, "global")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
        await query.answer()
        return

    handlers = {
        "harem": ("handlers.harem", "harem_cmd"),
        "profile": ("handlers.profile", "profile_cmd"),
        "search": ("handlers.search", "search_cmd"),
        "fav": ("handlers.fav", "fav_cmd"),
    }
    target = handlers.get(action)
    if target:
        module_name, func_name = target
        module = __import__(module_name, fromlist=[func_name])
        func = getattr(module, func_name)
        await func(update, context)
        await query.answer()
        return

    await query.answer(t("invalid_mode"), show_alert=True)


def register_start_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(start_callback, pattern=r"^start:\d+:.+$"))
