"""Async MongoDB connection and index setup."""
from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from config import DB_NAME, MONGODB_URI, LIMITED_CARDS_COLLECTION

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB is not initialized. Call init_db() first.")
    return _db


async def init_db() -> None:
    global _client, _db
    if not MONGODB_URI:
        raise RuntimeError("Missing MONGODB_URI in .env")

    _client = AsyncIOMotorClient(MONGODB_URI)
    _db = _client[DB_NAME]
    await _db.command("ping")
    await ensure_indexes()
    print(f"MongoDB connected: {DB_NAME}")


async def ensure_indexes() -> None:
    db = get_db()
    await db.photos.create_index([("cardId", ASCENDING)], unique=True)
    await db.photos.create_index([("normalizedName", ASCENDING)])
    await db.photos.create_index([("rarity", ASCENDING)])
    await db.photos.create_index([("anime", ASCENDING)])

    limited = db[LIMITED_CARDS_COLLECTION]
    await limited.create_index([("cardId", ASCENDING)], unique=True)
    await limited.create_index([("normalizedName", ASCENDING)])
    await limited.create_index([("rarity", ASCENDING)])
    await limited.create_index([("anime", ASCENDING)])

    await db.users.create_index([("userId", ASCENDING)], unique=True)
    await db.users.create_index([("updatedAt", DESCENDING)])
    await db.users.create_index([("cards.cardId", ASCENDING)])

    await db.groups.create_index([("groupId", ASCENDING)], unique=True)
    await db.groups.create_index([("isApproved", ASCENDING)])
    await db.groups.create_index([("updatedAt", DESCENDING)])

    await db.transfers.create_index([("fromUserId", ASCENDING), ("createdAt", DESCENDING)])
    await db.transfers.create_index([("toUserId", ASCENDING), ("createdAt", DESCENDING)])

    await db.bot_mutes.create_index([("groupId", ASCENDING), ("userId", ASCENDING)], unique=True)
    await db.bot_mutes.create_index([("mutedUntil", ASCENDING)], expireAfterSeconds=0)

    await db.bot_settings.create_index([("updatedAt", DESCENDING)])
    await db.counters.create_index([("updatedAt", DESCENDING)])
    await db.harem_transfers.create_index([("fromUserId", ASCENDING), ("createdAt", DESCENDING)])
    await db.harem_transfers.create_index([("toUserId", ASCENDING), ("createdAt", DESCENDING)])

    await db.claim_logs.create_index([("userId", ASCENDING), ("createdAt", DESCENDING)])
    await db.claim_logs.create_index([("groupId", ASCENDING), ("createdAt", DESCENDING)])
    await db.claim_logs.create_index([("yangonDate", ASCENDING), ("userId", ASCENDING)])
    await db.claim_logs.create_index([("yangonDate", ASCENDING), ("groupId", ASCENDING)])

    await db.daily_claim_limits.create_index([("userId", ASCENDING), ("date", ASCENDING)], unique=True)
    await db.daily_claim_limits.create_index([("date", ASCENDING), ("count", DESCENDING)])


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
