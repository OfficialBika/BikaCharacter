"""Async MongoDB connection and index setup."""
from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from utils.text import utcnow

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

    _client = AsyncIOMotorClient(
        MONGODB_URI,
        compressors="zstd,zlib",
    )
    _db = _client[DB_NAME]
    await _db.command("ping")
    await ensure_indexes()
    await recover_pending_add_operations()
    print(f"MongoDB connected: {DB_NAME}")


async def ensure_indexes() -> None:
    db = get_db()
    await db.photos.create_index([("cardId", ASCENDING)], unique=True)
    await db.photos.create_index([("normalizedName", ASCENDING), ("cardId", ASCENDING)])
    await db.photos.create_index([("rarity", ASCENDING)])
    await db.photos.create_index([("anime", ASCENDING)])
    await db.photos.create_index([("fileUniqueId", ASCENDING)])

    limited = db[LIMITED_CARDS_COLLECTION]
    await limited.create_index([("cardId", ASCENDING)], unique=True)
    await limited.create_index([("normalizedName", ASCENDING), ("cardId", ASCENDING)])
    await limited.create_index([("rarity", ASCENDING)])
    await limited.create_index([("anime", ASCENDING)])
    await limited.create_index([("fileUniqueId", ASCENDING)])

    await db.users.create_index([("userId", ASCENDING)], unique=True)
    await db.users.create_index([("updatedAt", DESCENDING)])
    await db.users.create_index([("cards.cardId", ASCENDING)])

    await db.groups.create_index([("groupId", ASCENDING)], unique=True)
    await db.groups.create_index([("isApproved", ASCENDING)])
    await db.groups.create_index([("updatedAt", DESCENDING)])

    await db.transfers.create_index([("fromUserId", ASCENDING), ("createdAt", DESCENDING)])
    await db.transfers.create_index([("toUserId", ASCENDING), ("createdAt", DESCENDING)])
    await db.gift_requests.create_index([("senderId", ASCENDING), ("createdAt", DESCENDING)])
    await db.gift_requests.create_index([("status", ASCENDING), ("updatedAt", DESCENDING)])

    await db.bot_mutes.create_index([("groupId", ASCENDING), ("userId", ASCENDING)], unique=True)
    await db.bot_mutes.create_index([("mutedUntil", ASCENDING)], expireAfterSeconds=0)

    await db.bot_settings.create_index([("updatedAt", DESCENDING)])
    await db.counters.create_index([("updatedAt", DESCENDING)])
    await db.harem_transfers.create_index([("fromUserId", ASCENDING), ("createdAt", DESCENDING)])
    await db.harem_transfers.create_index([("toUserId", ASCENDING), ("createdAt", DESCENDING)])

    await db.claim_logs.create_index([("userId", ASCENDING), ("createdAt", DESCENDING)])
    await db.claim_logs.create_index([("groupId", ASCENDING), ("createdAt", DESCENDING)])
    await db.claim_logs.create_index([("createdAt", ASCENDING)])
    await db.claim_logs.create_index([("yangonDate", ASCENDING), ("userId", ASCENDING)])
    await db.claim_logs.create_index([("yangonDate", ASCENDING), ("groupId", ASCENDING)])

    await db.daily_claim_limits.create_index([("userId", ASCENDING), ("date", ASCENDING)], unique=True)
    await db.daily_claim_limits.create_index([("date", ASCENDING), ("count", DESCENDING)])

    # Adding wizard / catalog indexes. These are additive and do not change
    # existing card/user data.
    await db.animes.create_index([("normalizedName", ASCENDING)], unique=True)
    await db.animes.create_index([("normalizedName", ASCENDING), ("name", ASCENDING)])
    await db.add_sessions.create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    await db.add_sessions.create_index([("userId", ASCENDING), ("chatId", ASCENDING), ("status", ASCENDING)])
    await db.add_operations.create_index([("status", ASCENDING), ("createdAt", ASCENDING)])
    await db.add_operations.create_index([("cardId", ASCENDING)])
    await db.add_operations.create_index([("completedAt", ASCENDING)], expireAfterSeconds=604800)
    # Legacy reservation records are no longer used by new allocation code.
    # Keep a TTL index so abandoned records from older versions cannot grow forever.
    await db.card_id_reservations.create_index([("reservedAt", ASCENDING)], expireAfterSeconds=1800)


async def recover_pending_add_operations() -> None:
    """Replay archived Add operations that were interrupted before final DB save.

    Only operations that already reached the archived state are recovered.
    If a newer card update exists, it wins and the stale operation is marked
    complete instead of overwriting current data.
    """
    db = get_db()
    rows = await db.add_operations.find(
        {"status": "archived"},
        {
            "_id": 1,
            "collectionName": 1,
            "cardId": 1,
            "document": 1,
            "createdAt": 1,
        },
    ).sort("createdAt", 1).limit(100).to_list(100)

    for operation in rows:
        try:
            collection_name = str(operation.get("collectionName") or "photos")
            if collection_name not in {"photos", LIMITED_CARDS_COLLECTION}:
                await db.add_operations.update_one(
                    {"_id": operation["_id"], "status": "archived"},
                    {
                        "$set": {
                            "status": "failed",
                            "lastError": "Invalid Add operation collection.",
                        }
                    },
                )
                continue

            document = dict(operation.get("document") or {})
            card_id = str(operation.get("cardId") or document.get("cardId") or "").strip()
            if not card_id:
                continue

            existing = await db[collection_name].find_one(
                {"cardId": card_id},
                {"updatedAt": 1},
            )
            op_created = operation.get("createdAt")
            existing_updated = (existing or {}).get("updatedAt")

            # A later successful edit must never be overwritten by an older
            # interrupted operation.
            if existing and existing_updated and op_created and existing_updated > op_created:
                await db.add_operations.update_one(
                    {"_id": operation["_id"], "status": "archived"},
                    {
                        "$set": {
                            "status": "completed",
                            "recoveredAt": utcnow(),
                            "recoverySkipped": True,
                            "updatedAt": utcnow(),
                        }
                    },
                )
                continue

            await db[collection_name].update_one(
                {"cardId": card_id},
                {
                    "$set": document,
                    "$setOnInsert": {
                        "createdAt": document.get("createdAt") or op_created or utcnow(),
                    },
                },
                upsert=True,
            )
            await db.add_operations.update_one(
                {"_id": operation["_id"], "status": "archived"},
                {
                    "$set": {
                        "status": "completed",
                        "recoveredAt": utcnow(),
                        "updatedAt": utcnow(),
                    }
                },
            )
        except Exception as exc:
            print(
                "ADD OPERATION RECOVERY FAILED:",
                operation.get("_id"),
                repr(exc),
                flush=True,
            )


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
