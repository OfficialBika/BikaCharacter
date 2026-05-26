from __future__ import annotations

from datetime import timedelta

from telegram import Update

from config import ANTI_SPAM_STREAK, BOT_MUTE_SECONDS
from database.mongodb import get_db
from utils.text import utcnow


async def is_free_user(group_id: int, user_id: int) -> bool:
    """Return True if the owner exempted this user from bot anti-spam mute in this group."""
    free = await get_db().bot_free_users.find_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {"_id": 1},
    )
    return bool(free)


async def add_free_user(group_id: int, user_id: int, by_owner_id: int) -> None:
    """Add a user to the group free list and clear any existing bot mute."""
    now = utcnow()
    db = get_db()

    await db.bot_free_users.update_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {
            "$set": {
                "groupId": int(group_id),
                "userId": int(user_id),
                "byOwnerId": int(by_owner_id),
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )

    # If the user was already bot-muted, /free immediately restores bot command access.
    await db.bot_mutes.delete_one({"groupId": int(group_id), "userId": int(user_id)})

    # Reset anti-spam streak so stale counts cannot instantly mute another user.
    await db.groups.update_one(
        {"groupId": int(group_id)},
        {"$set": {"lastSpeakerId": 0, "lastSpeakerCount": 0, "updatedAt": now}},
    )


async def remove_free_user(group_id: int, user_id: int) -> bool:
    """Remove a user from the group free list."""
    result = await get_db().bot_free_users.delete_one(
        {"groupId": int(group_id), "userId": int(user_id)}
    )
    return result.deleted_count > 0


async def is_bot_muted(group_id: int, user_id: int) -> bool:
    # Free users are never ignored by bot mute logic, even if an old mute record exists.
    if await is_free_user(group_id, user_id):
        return False

    mute = await get_db().bot_mutes.find_one({"groupId": int(group_id), "userId": int(user_id)})
    if not mute:
        return False

    muted_until = mute.get("mutedUntil")
    if muted_until is None:
        return False

    if muted_until.tzinfo is None:
        # MongoDB returns naive UTC datetimes by default.
        return muted_until > utcnow().replace(tzinfo=None)

    return muted_until > utcnow()


async def mute_user_for_bot(group_id: int, user_id: int, reason: str = "anti_spam") -> None:
    # Owner-free users must not be bot-muted.
    if await is_free_user(group_id, user_id):
        return

    now = utcnow()
    await get_db().bot_mutes.update_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {
            "$set": {
                "mutedUntil": now + timedelta(seconds=BOT_MUTE_SECONDS),
                "reason": reason,
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )


async def record_message_and_maybe_mute(update: Update) -> bool:
    """Track consecutive group messages.

    Returns True when the user has just been bot-muted.
    Free users still count as normal chat activity for drops, but their own
    messages never create a mute and they break another user's consecutive streak.
    """
    if not update.effective_chat or not update.effective_user:
        return False

    db = get_db()
    group_id = int(update.effective_chat.id)
    user_id = int(update.effective_user.id)

    if await is_free_user(group_id, user_id):
        await db.groups.update_one(
            {"groupId": group_id},
            {"$set": {"lastSpeakerId": user_id, "lastSpeakerCount": 0, "updatedAt": utcnow()}},
        )
        return False

    group = await db.groups.find_one({"groupId": group_id}, {"lastSpeakerId": 1, "lastSpeakerCount": 1})
    last_id = int(group.get("lastSpeakerId", 0) if group else 0)
    last_count = int(group.get("lastSpeakerCount", 0) if group else 0)

    new_count = last_count + 1 if last_id == user_id else 1
    await db.groups.update_one(
        {"groupId": group_id},
        {"$set": {"lastSpeakerId": user_id, "lastSpeakerCount": new_count, "updatedAt": utcnow()}},
    )

    if new_count >= ANTI_SPAM_STREAK:
        await mute_user_for_bot(group_id, user_id, "sent_6_messages_in_a_row")
        await db.groups.update_one(
            {"groupId": group_id},
            {"$set": {"lastSpeakerId": 0, "lastSpeakerCount": 0, "updatedAt": utcnow()}},
        )
        return True

    return False


async def should_ignore_update(update: Update) -> bool:
    if not update.effective_chat or not update.effective_user:
        return False

    if update.effective_chat.type not in ("group", "supergroup"):
        return False

    if await is_free_user(update.effective_chat.id, update.effective_user.id):
        return False

    return await is_bot_muted(update.effective_chat.id, update.effective_user.id)
