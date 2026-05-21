from __future__ import annotations

from datetime import timedelta

from pymongo import ReturnDocument
from telegram import Update

from config import ANTI_SPAM_STREAK, BOT_MUTE_SECONDS
from database.mongodb import get_db
from utils.text import utcnow


async def is_bot_muted(group_id: int, user_id: int) -> bool:
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
    """
    if not update.effective_chat or not update.effective_user:
        return False
    db = get_db()
    group_id = int(update.effective_chat.id)
    user_id = int(update.effective_user.id)

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
    return await is_bot_muted(update.effective_chat.id, update.effective_user.id)
