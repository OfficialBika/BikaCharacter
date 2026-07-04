from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from config import OWNER_ID, OWNER_USERNAME


def _normalize_username(value: str | None) -> str:
    """Normalize a Telegram username for case-insensitive comparison."""
    return str(value or "").strip().lstrip("@").casefold()


_OWNER_USERNAME = _normalize_username(OWNER_USERNAME)


def is_owner(user_or_id: Any = None, username: str | None = None) -> bool:
    """Return True when a Telegram user matches OWNER_ID or OWNER_USERNAME.

    Preferred usage:
        is_owner(update.effective_user)

    Backward-compatible usage:
        is_owner(user_id)
        is_owner(user_id, username)

    Username matching is case-insensitive and accepts OWNER_USERNAME with or
    without a leading ``@``. Numeric OWNER_ID matching remains fully supported.
    """
    if user_or_id is None:
        return False

    user_id = None
    user_username = username

    # telegram.User (or another object exposing id/username)
    if hasattr(user_or_id, "id"):
        user_id = getattr(user_or_id, "id", None)
        if user_username is None:
            user_username = getattr(user_or_id, "username", None)
    else:
        user_id = user_or_id

    # Keep the original stable numeric owner check fully compatible.
    try:
        configured_owner_id = int(OWNER_ID or 0)
        if configured_owner_id > 0 and user_id is not None and int(user_id) == configured_owner_id:
            return True
    except (TypeError, ValueError):
        pass

    # Username-based owner control from .env.
    if _OWNER_USERNAME and _normalize_username(user_username) == _OWNER_USERNAME:
        return True

    return False


def is_global_admin(user_or_id: Any = None, username: str | None = None) -> bool:
    # Global bot-management permissions are owner-only.
    # Group admins are checked dynamically per group with is_group_admin_or_owner().
    return is_owner(user_or_id, username)


async def is_group_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if is_owner(user):
        return True

    if update.effective_chat is None or user is None:
        return False

    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False
