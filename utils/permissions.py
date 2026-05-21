from __future__ import annotations

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from config import OWNER_ID


def is_owner(user_id: int | None) -> bool:
    return bool(user_id) and int(user_id) == int(OWNER_ID)


def is_global_admin(user_id: int | None) -> bool:
    # Global bot-management permissions are owner-only.
    # Group admins are checked dynamically per group with is_group_admin_or_owner().
    return is_owner(user_id)


async def is_group_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if is_owner(user_id):
        return True
    if update.effective_chat is None:
        return False
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False
