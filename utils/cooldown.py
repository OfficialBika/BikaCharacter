from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta, timezone
import time

from telegram import Update

from config import ANTI_SPAM_STREAK, BOT_MUTE_SECONDS
from database.mongodb import get_db
from utils.text import utcnow


# Bounded in-memory caches for the hot group-message path.
_FREE_CACHE_TTL = 60.0
_MUTE_CACHE_TTL = 15.0
_HOT_CACHE_MAX = 8192

_free_cache: OrderedDict[tuple[int, int], tuple[float, bool]] = OrderedDict()
_mute_cache: OrderedDict[tuple[int, int], tuple[float, float]] = OrderedDict()
_streak_cache: OrderedDict[int, tuple[int, int]] = OrderedDict()


def _cache_put(cache, key, value, ttl: float) -> None:
    cache[key] = (time.monotonic() + float(ttl), value)
    cache.move_to_end(key)
    while len(cache) > _HOT_CACHE_MAX:
        cache.popitem(last=False)


def _streak_put(group_id: int, user_id: int, count: int) -> None:
    _streak_cache[int(group_id)] = (int(user_id), int(count))
    _streak_cache.move_to_end(int(group_id))
    while len(_streak_cache) > _HOT_CACHE_MAX:
        _streak_cache.popitem(last=False)


def _streak_clear(group_id: int) -> None:
    _streak_cache.pop(int(group_id), None)



async def is_free_user(group_id: int, user_id: int) -> bool:
    key = (int(group_id), int(user_id))
    now_mono = time.monotonic()
    cached = _free_cache.get(key)
    if cached:
        expires_mono, value = cached
        if expires_mono > now_mono:
            _free_cache.move_to_end(key)
            return bool(value)
        _free_cache.pop(key, None)

    free = await get_db().bot_free_users.find_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {"_id": 1},
    )
    value = bool(free)
    _cache_put(_free_cache, key, value, _FREE_CACHE_TTL)
    return value


async def add_free_user(group_id: int, user_id: int, by_owner_id: int) -> None:
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

    await db.bot_mutes.delete_one(
        {"groupId": int(group_id), "userId": int(user_id)}
    )

    _cache_put(_free_cache, (int(group_id), int(user_id)), True, _FREE_CACHE_TTL)
    _mute_cache.pop((int(group_id), int(user_id)), None)
    _streak_clear(int(group_id))


async def remove_free_user(group_id: int, user_id: int) -> bool:
    result = await get_db().bot_free_users.delete_one(
        {"groupId": int(group_id), "userId": int(user_id)}
    )
    key = (int(group_id), int(user_id))
    _cache_put(_free_cache, key, False, _FREE_CACHE_TTL)
    _mute_cache.pop(key, None)
    _streak_clear(int(group_id))
    return result.deleted_count > 0


async def is_bot_muted(group_id: int, user_id: int) -> bool:
    if await is_free_user(group_id, user_id):
        return False

    key = (int(group_id), int(user_id))
    now_mono = time.monotonic()
    now_unix = time.time()

    cached = _mute_cache.get(key)
    if cached:
        expires_mono, muted_until_unix = cached
        if expires_mono > now_mono:
            _mute_cache.move_to_end(key)
            return float(muted_until_unix) > now_unix
        _mute_cache.pop(key, None)

    mute = await get_db().bot_mutes.find_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {"_id": 1, "mutedUntil": 1},
    )
    muted_until = (mute or {}).get("mutedUntil")
    if muted_until is None:
        _cache_put(_mute_cache, key, 0.0, _MUTE_CACHE_TTL)
        return False

    try:
        if muted_until.tzinfo is None:
            muted_until_unix = float(muted_until.replace(tzinfo=timezone.utc).timestamp())
        else:
            muted_until_unix = float(muted_until.astimezone(timezone.utc).timestamp())
    except Exception:
        muted_until_unix = 0.0

    _cache_put(_mute_cache, key, muted_until_unix, _MUTE_CACHE_TTL)
    return muted_until_unix > now_unix


async def mute_user_for_bot(group_id: int, user_id: int, reason: str = "anti_spam") -> None:
    if await is_free_user(group_id, user_id):
        return

    now = utcnow()
    muted_until = now + timedelta(seconds=BOT_MUTE_SECONDS)

    await get_db().bot_mutes.update_one(
        {"groupId": int(group_id), "userId": int(user_id)},
        {
            "$set": {
                "mutedUntil": muted_until,
                "reason": reason,
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )

    _cache_put(
        _mute_cache,
        (int(group_id), int(user_id)),
        float(muted_until.replace(tzinfo=timezone.utc).timestamp()),
        _MUTE_CACHE_TTL,
    )


async def record_message_and_maybe_mute(update: Update) -> bool:
    """Track consecutive group messages locally; persist only actual mute events."""
    if not update.effective_chat or not update.effective_user:
        return False

    group_id = int(update.effective_chat.id)
    user_id = int(update.effective_user.id)

    if await is_free_user(group_id, user_id):
        _streak_put(group_id, user_id, 0)
        return False

    last_id, last_count = _streak_cache.get(group_id, (0, 0))
    new_count = int(last_count) + 1 if int(last_id) == user_id else 1
    _streak_put(group_id, user_id, new_count)

    if new_count >= ANTI_SPAM_STREAK:
        await mute_user_for_bot(group_id, user_id, "sent_6_messages_in_a_row")
        _streak_clear(group_id)
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
