from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import LIMITED_CUSTOM_EMOJI_ID, LIMITED_FALLBACK_EMOJI
from utils.buttons import make_button
from utils.cooldown import should_ignore_update

SEARCH_TEXT = "ᴛᴏ sᴇᴀʀᴄʜ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴄʟɪᴄᴋ  ʙᴜᴛᴛᴏɴ"
SEARCH_BUTTON_TEXT = "sᴇᴀʀᴄʜ ᴄʜᴀʀᴀᴄᴛᴇʀs"


def _search_keyboard() -> InlineKeyboardMarkup:
    """Open this bot's inline mode with an empty query.

    Empty inline query is handled by handlers.inline._fetch_inline_photos(), which
    returns all normal photos + owner-only limited_cards with pagination.
    """
    button = make_button(
        SEARCH_BUTTON_TEXT,
        style="success",
        fallback_emoji=str(LIMITED_FALLBACK_EMOJI or "🔮"),
        custom_emoji_id=str(LIMITED_CUSTOM_EMOJI_ID or ""),
        switch_inline_query_current_chat="",
    )
    return InlineKeyboardMarkup([[button]])


def _fallback_search_keyboard() -> InlineKeyboardMarkup:
    """Plain Telegram-compatible fallback if custom button fields are rejected."""
    fallback = str(LIMITED_FALLBACK_EMOJI or "🔮")
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{fallback} {SEARCH_BUTTON_TEXT}", switch_inline_query_current_chat="")]]
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    msg = update.effective_message
    if not msg:
        return

    try:
        await msg.reply_text(SEARCH_TEXT, reply_markup=_search_keyboard())
    except Exception as exc:
        # Runtime fallback for older Bot API servers/clients that reject button
        # custom emoji/style fields even though the local PTB object accepted them.
        print("SEARCH BUTTON STYLE/CUSTOM_EMOJI ERROR, FALLBACK:", repr(exc))
        await msg.reply_text(SEARCH_TEXT, reply_markup=_fallback_search_keyboard())


def register_search_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("search", search_cmd))
