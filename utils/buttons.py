from __future__ import annotations

import re

from telegram import InlineKeyboardButton

from config import (
    ENABLE_BUTTON_CUSTOM_EMOJI,
    ENABLE_BUTTON_STYLE,
    LIMITED_CUSTOM_EMOJI_ID,
    LIMITED_FALLBACK_EMOJI,
    LIMITED_RARITY_NAME,
)
from utils.rarity import get_rarity_button_emoji

BUTTON_STYLE_PREFIX = {
    "primary": "🔵",
    "success": "✅",
    "danger": "❌",
}

_LEADING_SYMBOL_RE = re.compile(
    r"^\s*(?:✅|❌|🔵|🟢|🔴|🟡|🟠|🟣|🔮|🎟️|🪞|✨|⚡|⚜️|💮|📘|🚮|⬅️|⛩|💠|«|»)\s*"
)


def _strip_leading_symbol(text: str) -> str:
    cleaned = _LEADING_SYMBOL_RE.sub("", str(text or "").strip()).strip()
    return cleaned or str(text or "").strip()


def _button_api_kwargs(style: str | None = None, custom_emoji_id: str | None = None) -> dict:
    api_kwargs: dict = {}
    if ENABLE_BUTTON_STYLE and style in BUTTON_STYLE_PREFIX:
        api_kwargs["style"] = style
    if ENABLE_BUTTON_CUSTOM_EMOJI and custom_emoji_id:
        api_kwargs["icon_custom_emoji_id"] = str(custom_emoji_id)
    return api_kwargs


def make_button(
    text: str,
    *,
    style: str | None = None,
    fallback_emoji: str = "",
    custom_emoji_id: str = "",
    strip_existing_emoji: bool = True,
    **kwargs,
) -> InlineKeyboardButton:
    """Create an InlineKeyboardButton with Bot API button style/custom emoji.

    Fallback behavior:
    - If custom emoji icon is disabled or missing, fallback_emoji is prefixed in text.
    - If python-telegram-bot is too old to accept api_kwargs, the button is created
      without api_kwargs and the fallback emoji stays in the text.
    """
    label = _strip_leading_symbol(text) if strip_existing_emoji else str(text or "").strip()
    api_kwargs = _button_api_kwargs(style=style, custom_emoji_id=custom_emoji_id)

    # Avoid duplicated emoji when Telegram can display the custom icon before text.
    needs_text_fallback = bool(fallback_emoji) and not api_kwargs.get("icon_custom_emoji_id")
    display_text = f"{fallback_emoji} {label}".strip() if needs_text_fallback else label

    try:
        if api_kwargs:
            return InlineKeyboardButton(display_text, api_kwargs=api_kwargs, **kwargs)
        return InlineKeyboardButton(display_text, **kwargs)
    except TypeError:
        # Older PTB versions may not accept api_kwargs. Fall back to plain text.
        fallback_text = f"{fallback_emoji} {label}".strip() if fallback_emoji else label
        return InlineKeyboardButton(fallback_text, **kwargs)


def action_button(text: str, style: str = "primary", **kwargs) -> InlineKeyboardButton:
    return make_button(
        text,
        style=style,
        fallback_emoji=BUTTON_STYLE_PREFIX.get(style, ""),
        strip_existing_emoji=True,
        **kwargs,
    )


def rarity_button(text: str, rarity: str, style: str = "primary", **kwargs) -> InlineKeyboardButton:
    rarity_text = str(rarity or "")
    custom_emoji_id = ""
    fallback = get_rarity_button_emoji(rarity_text)
    if rarity_text == str(LIMITED_RARITY_NAME):
        custom_emoji_id = str(LIMITED_CUSTOM_EMOJI_ID or "")
        fallback = str(LIMITED_FALLBACK_EMOJI or fallback or "🔮")

    return make_button(
        text,
        style=style,
        fallback_emoji=fallback,
        custom_emoji_id=custom_emoji_id,
        strip_existing_emoji=True,
        **kwargs,
    )
