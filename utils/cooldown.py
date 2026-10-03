from __future__ import annotations

from datetime import timedelta
import time

from telegram import Update

from config import ANTI_SPAM_STREAK, BOT_MUTE_SECONDS
from database.mongodb import get_db
from utils.text import utcnow

_LOCAL_STREAKS: dict[int, tuple[int, int, float]] = {}
_LOCAL_STREAK_TTL = 900.0
_LOCAL_STREAK_MAX = 100_000


def _cleanup_local_streaks(now: float) -> None:
    if len(_LOCAL_STREAKS) <= _LOCAL_STREAK_MAX:
        expired = [k for k, (_, _, seen) in _LOCAL_STREAKS.items() if now - seen > _LOCAL_STREAK_TTL]
    else:
        expired = [k for k, (_, _, seen) in _LOCAL_STREAKS.items() if now - seen > _LOCAL_STREAK_TTL]
    for key in expired:
        _LOCAL_STREAKS.pop(key, None)
    if len(_LOCAL_STREAKS) > _LOCAL_STREAK_MAX:
        for key, _ in sorted(_LOCAL_STREAKS.items(), key=lambda item: item[1][2])[: len(_LOCAL_STREAKS) - _LOCAL_STREAK_MAX]:
            _LOCAL_STREAKS.pop(key, None)


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
    """Track consecutive group messages in RAM and write Mongo only on mute."""
    if not update.effective_chat or not update.effective_user:
        return False

    group_id = int(update.effective_chat.id)
    user_id = int(update.effective_user.id)
    now_mono = time.monotonic()
    _cleanup_local_streaks(now_mono)
    key = group_id

    if await is_free_user(group_id, user_id):
        _LOCAL_STREAKS[key] = (user_id, 0, now_mono)
        return False

    last_id, last_count, _ = _LOCAL_STREAKS.get(key, (0, 0, now_mono))
    new_count = last_count + 1 if last_id == user_id else 1
    _LOCAL_STREAKS[key] = (user_id, new_count, now_mono)

    if new_count >= ANTI_SPAM_STREAK:
        await mute_user_for_bot(group_id, user_id, "sent_6_messages_in_a_row")
        _LOCAL_STREAKS[key] = (0, 0, now_mono)
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
