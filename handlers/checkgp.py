from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from telegram import Chat, ChatMemberUpdated, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, ChatMemberHandler, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.db_helpers import ensure_group
from utils.text import utcnow


MIN_GROUP_MEMBERS = 40
LEAVE_MESSAGE = "This group can't afford me. I'm leaving now...."

ACTIVE_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}

INACTIVE_STATUSES = {
    ChatMemberStatus.LEFT,
    ChatMemberStatus.BANNED,
}


def _is_group_chat(chat: Chat | None) -> bool:
    return bool(chat and chat.type in ("group", "supergroup"))


async def _get_member_count(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    try:
        return int(await context.bot.get_chat_member_count(chat_id))
    except Exception as exc:
        print("CHECKGP MEMBER COUNT ERROR:", repr(exc), flush=True)
        return 0


async def _save_check_result(
    chat: Chat,
    member_count: int,
    passed: bool,
    trigger: str,
    reason: str = "",
) -> None:
    await get_db().groups.update_one(
        {"groupId": int(chat.id)},
        {
            "$set": {
                "title": chat.title or getattr(chat, "full_name", "") or str(chat.id),
                "username": getattr(chat, "username", "") or "",
                "checkgpPassed": bool(passed),
                "checkgpMemberCount": int(member_count),
                "checkgpMinMembers": int(MIN_GROUP_MEMBERS),
                "checkgpTrigger": str(trigger),
                "checkgpCheckedAt": utcnow(),
                "checkgpLeaveReason": reason,
                "updatedAt": utcnow(),
            }
        },
        upsert=True,
    )


async def _check_and_leave_if_needed(
    context: ContextTypes.DEFAULT_TYPE,
    chat: Chat,
    trigger: str,
    delay_seconds: float = 0.0,
) -> bool:
    """Return True if the bot left or tried to leave the group."""
    if not _is_group_chat(chat):
        return False

    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    await ensure_group(chat)
    member_count = await _get_member_count(context, int(chat.id))

    # If Telegram API failed and returned 0, still leave because the group cannot be verified.
    if member_count >= MIN_GROUP_MEMBERS:
        await _save_check_result(chat, member_count, True, trigger)
        return False

    reason = f"members={member_count}/{MIN_GROUP_MEMBERS}"
    await _save_check_result(chat, member_count, False, trigger, reason)

    try:
        await context.bot.send_message(chat_id=int(chat.id), text=LEAVE_MESSAGE)
    except Exception as exc:
        print("CHECKGP SEND LEAVE MESSAGE ERROR:", repr(exc), flush=True)

    try:
        await context.bot.leave_chat(int(chat.id))
        print(f"CHECKGP LEFT CHAT {chat.id}: {reason}", flush=True)
    except Exception as exc:
        print("CHECKGP LEAVE CHAT ERROR:", repr(exc), flush=True)

    return True


async def check_group_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check immediately when the bot is added/promoted into a group."""
    member_update: ChatMemberUpdated | None = update.my_chat_member
    if not member_update:
        return

    chat = member_update.chat
    if not _is_group_chat(chat):
        return

    old_status = member_update.old_chat_member.status
    new_status = member_update.new_chat_member.status

    # Only check when bot becomes active in a group.
    # This covers left -> member, left -> administrator, kicked -> member/admin.
    if old_status in ACTIVE_STATUSES and new_status in ACTIVE_STATUSES:
        return
    if new_status not in ACTIVE_STATUSES:
        return

    await _check_and_leave_if_needed(context, chat, trigger="my_chat_member", delay_seconds=1.0)


async def check_group_requirements_on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback check.

    my_chat_member only fires when the bot is added/promoted. If the bot was already
    in a group before this feature was deployed, this fallback checks on the next
    normal group message and leaves if the group is still below the minimum.
    """
    chat = update.effective_chat
    if not _is_group_chat(chat):
        return

    group = await ensure_group(chat)
    min_saved = int((group or {}).get("checkgpMinMembers", 0) or 0)
    already_passed = bool((group or {}).get("checkgpPassed") is True)

    # Already checked with the current 40-member rule.
    if already_passed and min_saved >= MIN_GROUP_MEMBERS:
        return

    await _check_and_leave_if_needed(context, chat, trigger="group_message")


def register_checkgp_handlers(app: Application) -> None:
    app.add_handler(ChatMemberHandler(check_group_requirements, ChatMemberHandler.MY_CHAT_MEMBER), group=-100)
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, check_group_requirements_on_message),
        group=-100,
    )
