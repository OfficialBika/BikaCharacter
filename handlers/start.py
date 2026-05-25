from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from config import ADD_TO_GROUP_URL, BOT_USERNAME, SUPPORT_GROUP_URL, UPDATE_CHANNEL_URL
from utils.db_helpers import ensure_user
from utils.text import escape_html
from utils.i18n import t


def _add_to_group_url() -> str:
    if ADD_TO_GROUP_URL:
        return ADD_TO_GROUP_URL
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?startgroup=true"
    return "https://t.me/"


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
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

    text = t("start_message", mention=mention)

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_start_keyboard(),
        disable_web_page_preview=True,
    )


def register_start_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_cmd))
