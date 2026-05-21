from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from pymongo import ReturnDocument
from telegram import Chat, User

from config import CLAIM_DAILY_LIMIT, CLAIM_TIMEZONE
from database.mongodb import get_db
from utils.text import safe_chat_title, utcnow


def yangon_date_key(dt: Optional[datetime] = None) -> str:
    """Return YYYY-MM-DD in Myanmar/Yangon time for daily limits and rankings."""
    current = dt or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("UTC"))
    return current.astimezone(ZoneInfo(CLAIM_TIMEZONE)).strftime("%Y-%m-%d")


async def get_daily_claim_count(user_id: int, date_key: Optional[str] = None) -> int:
    date_key = date_key or yangon_date_key()
    doc = await get_db().daily_claim_limits.find_one({"userId": int(user_id), "date": date_key})
    return int((doc or {}).get("count", 0) or 0)


async def reserve_daily_claim(user_id: int, date_key: Optional[str] = None, limit: int = CLAIM_DAILY_LIMIT) -> dict:
    """Atomically reserve one daily claim slot.

    Returns {ok, used, remaining, limit, date}. If ok=False, the claim should not proceed.
    If the caller later loses the active-drop race, call release_daily_claim().
    """
    db = get_db()
    now = utcnow()
    date_key = date_key or yangon_date_key(now)
    await db.daily_claim_limits.update_one(
        {"userId": int(user_id), "date": date_key},
        {"$setOnInsert": {"userId": int(user_id), "date": date_key, "count": 0, "createdAt": now}, "$set": {"updatedAt": now}},
        upsert=True,
    )
    updated = await db.daily_claim_limits.find_one_and_update(
        {"userId": int(user_id), "date": date_key, "count": {"$lt": int(limit)}},
        {"$inc": {"count": 1}, "$set": {"updatedAt": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        used = await get_daily_claim_count(user_id, date_key)
        return {"ok": False, "used": used, "remaining": max(0, int(limit) - used), "limit": int(limit), "date": date_key}
    used = int(updated.get("count", 0) or 0)
    return {"ok": True, "used": used, "remaining": max(0, int(limit) - used), "limit": int(limit), "date": date_key}


async def release_daily_claim(user_id: int, date_key: Optional[str] = None) -> None:
    date_key = date_key or yangon_date_key()
    await get_db().daily_claim_limits.update_one(
        {"userId": int(user_id), "date": date_key, "count": {"$gt": 0}},
        {"$inc": {"count": -1}, "$set": {"updatedAt": utcnow()}},
    )


async def log_claim_event(user: User, chat: Chat, card_doc: dict, date_key: Optional[str] = None) -> None:
    now = utcnow()
    date_key = date_key or yangon_date_key(now)
    await get_db().claim_logs.insert_one(
        {
            "userId": int(user.id),
            "username": user.username or "",
            "firstName": user.first_name or "",
            "lastName": user.last_name or "",
            "groupId": int(chat.id),
            "groupTitle": safe_chat_title(chat),
            "groupUsername": getattr(chat, "username", "") or "",
            "cardId": str(card_doc.get("cardId", "")),
            "name": str(card_doc.get("name", "")),
            "rarity": str(card_doc.get("rarity", "")),
            "anime": str(card_doc.get("anime", "")),
            "yangonDate": date_key,
            "createdAt": now,
        }
    )
