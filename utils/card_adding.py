from __future__ import annotations

import asyncio
import re
import time

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from database.mongodb import get_db
from config import ANIMES_COLLECTION, LIMITED_CARDS_COLLECTION, RARITY_ORDER
from utils.parser import normalized_search_name
from utils.text import utcnow

CARD_COUNTER_ID = "photo_card_id"
ADD_MODE_PREFIX = "adder:"
ADD_MODE_TTL = 86400
ADD_MODE_MAX = 10000

# Historical short codes: Su Cv Ca Dv My Lg Ra Un Co.
_SHORT_CODES = ("su", "cv", "ca", "dv", "my", "lg", "ra", "un", "co")

_MODE_CACHE: dict[int, tuple[float, str, str]] = {}
_MODE_LOCK = asyncio.Lock()
_COUNTER_READY = False
_ANIME_CACHE: dict[str, tuple[float, str]] = {}
_ANIME_CACHE_TTL = 600
_ANIME_CACHE_MAX = 5000


def rarity_aliases() -> dict[str, str]:
    non_limited = [r for r in RARITY_ORDER if str(r).lower() != "limited"]
    aliases: dict[str, str] = {}
    for code, rarity in zip(_SHORT_CODES, reversed(non_limited)):
        aliases[code] = rarity
    aliases.update({str(r).lower(): r for r in RARITY_ORDER})
    return aliases


def normalize_add_rarity(raw: str) -> str | None:
    return rarity_aliases().get(str(raw or "").strip().lower())


async def _max_numeric_card_id() -> int:
    db = get_db()
    max_id = 0
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await (await db[collection_name].aggregate([
            {"$match": {"cardId": {"$regex": r"^[0-9]+$"}}},
            {"$project": {"cardIdNum": {"$toInt": "$cardId"}}},
            {"$sort": {"cardIdNum": -1}},
            {"$limit": 1},
        ])).to_list(1)
        if docs:
            max_id = max(max_id, int(docs[0].get("cardIdNum", 0) or 0))
    return max_id


async def ensure_card_counter() -> None:
    global _COUNTER_READY
    if _COUNTER_READY:
        return
    db = get_db()
    max_id = await _max_numeric_card_id()
    now = utcnow()
    await db.counters.update_one(
        {"_id": CARD_COUNTER_ID},
        {"$setOnInsert": {"seq": max_id, "createdAt": now}, "$set": {"updatedAt": now}},
        upsert=True,
    )
    await db.counters.update_one(
        {"_id": CARD_COUNTER_ID, "seq": {"$lt": max_id}},
        {"$set": {"seq": max_id, "updatedAt": now}},
    )
    _COUNTER_READY = True


async def next_card_id() -> str:
    # The counter update itself is atomic; the local lock only avoids duplicate
    # initialization work inside this process.
    async with _MODE_LOCK:
        await ensure_card_counter()
    doc = await get_db().counters.find_one_and_update(
        {"_id": CARD_COUNTER_ID},
        {"$inc": {"seq": 1}, "$set": {"updatedAt": utcnow()}},
        projection={"seq": 1},
        return_document=ReturnDocument.AFTER,
    )
    return str(int(doc["seq"]))


async def sync_counter_at_least(card_id: str) -> None:
    if not str(card_id).isdigit():
        return
    value = int(card_id)
    await get_db().counters.update_one(
        {"_id": CARD_COUNTER_ID, "seq": {"$lt": value}},
        {"$set": {"seq": value, "updatedAt": utcnow()}},
    )


async def add_anime_to_catalog(anime: str, user_id: int = 0) -> str:
    """Create/reuse an Anime catalog entry without touching card documents.

    Existing catalog rows are reused by normalizedName. New rows use the
    normalized name as their MongoDB _id, making concurrent creation atomic
    without requiring a destructive migration or a unique secondary index.
    """
    value = " ".join(str(anime or "").strip().split())
    normalized = normalized_search_name(value)
    if not normalized:
        raise ValueError("Anime name cannot be empty.")
    now = utcnow()
    db = get_db()

    existing = await db[ANIMES_COLLECTION].find_one(
        {"normalizedName": normalized},
        {"name": 1},
    )
    if existing and existing.get("name"):
        canonical = str(existing["name"]).strip()
        await db[ANIMES_COLLECTION].update_one(
            {"_id": existing["_id"]},
            {"$set": {"updatedAt": now, "updatedBy": int(user_id or 0)}},
        )
        return canonical

    try:
        await db[ANIMES_COLLECTION].update_one(
            {"_id": normalized},
            {
                "$set": {"updatedAt": now, "updatedBy": int(user_id or 0)},
                "$setOnInsert": {
                    "name": value,
                    "normalizedName": normalized,
                    "createdAt": now,
                    "createdBy": int(user_id or 0),
                },
            },
            upsert=True,
        )
    except DuplicateKeyError:
        pass

    doc = await db[ANIMES_COLLECTION].find_one(
        {"normalizedName": normalized},
        {"name": 1},
    )
    return str((doc or {}).get("name") or value).strip()

async def anime_catalog_exists(anime: str) -> bool:
    normalized = normalized_search_name(anime)
    if not normalized:
        return False
    return bool(await get_db()[ANIMES_COLLECTION].find_one(
        {"normalizedName": normalized},
        {"_id": 1},
    ))


async def canonical_anime(raw: str) -> str:
    value = " ".join(str(raw or "").strip().split())
    if not value:
        return ""
    key = normalized_search_name(value)
    now = time.monotonic()
    cached = _ANIME_CACHE.get(key)
    if cached and now - cached[0] < _ANIME_CACHE_TTL:
        return cached[1]

    db = get_db()
    catalog_doc = await db[ANIMES_COLLECTION].find_one(
        {"normalizedName": key},
        {"name": 1},
    )
    if catalog_doc and catalog_doc.get("name"):
        result = str(catalog_doc["name"]).strip()
        _ANIME_CACHE[key] = (now, result)
        return result

    escaped = re.escape(value)
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        doc = await db[collection_name].find_one(
            {"anime": {"$regex": f"^{escaped}$", "$options": "i"}},
            {"anime": 1},
        )
        if doc and doc.get("anime"):
            result = str(doc["anime"]).strip()
            _ANIME_CACHE[key] = (now, result)
            return result

    _ANIME_CACHE[key] = (now, value)
    if len(_ANIME_CACHE) > _ANIME_CACHE_MAX:
        oldest = sorted(_ANIME_CACHE.items(), key=lambda x: x[1][0])[: max(1, len(_ANIME_CACHE) - _ANIME_CACHE_MAX)]
        for old_key, _ in oldest:
            _ANIME_CACHE.pop(old_key, None)
    return value


async def find_duplicate_media(file_unique_id: str, exclude_card_id: str = "") -> dict | None:
    uid = str(file_unique_id or "").strip()
    if not uid:
        return None
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        doc = await get_db()[collection_name].find_one(
            {"fileUniqueId": uid},
            {"cardId": 1, "name": 1, "anime": 1, "rarity": 1},
        )
        if doc and str(doc.get("cardId", "")) != str(exclude_card_id):
            return {**doc, "collection": collection_name}
    return None


async def find_possible_duplicate(name: str, anime: str, exclude_card_id: str = "") -> dict | None:
    query = {"normalizedName": normalized_search_name(name), "anime": anime}
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        doc = await get_db()[collection_name].find_one(
            query,
            {"cardId": 1, "name": 1, "anime": 1, "rarity": 1},
        )
        if doc and str(doc.get("cardId", "")) != str(exclude_card_id):
            return {**doc, "collection": collection_name}
    return None


async def set_add_mode(user_id: int, anime: str, rarity: str) -> None:
    anime = await canonical_anime(anime)
    rarity = normalize_add_rarity(rarity) or str(rarity).strip()
    now = time.time()
    _MODE_CACHE[int(user_id)] = (now, anime, rarity)
    if len(_MODE_CACHE) > ADD_MODE_MAX:
        oldest = sorted(_MODE_CACHE.items(), key=lambda x: x[1][0])[: max(1, len(_MODE_CACHE) - ADD_MODE_MAX)]
        for key, _ in oldest:
            _MODE_CACHE.pop(key, None)
    await get_db().bot_settings.update_one(
        {"_id": f"{ADD_MODE_PREFIX}{int(user_id)}"},
        {"$set": {"anime": anime, "rarity": rarity, "updatedAt": utcnow()}},
        upsert=True,
    )


async def get_add_mode(user_id: int) -> tuple[str, str]:
    now = time.time()
    cached = _MODE_CACHE.get(int(user_id))
    if cached and now - cached[0] < ADD_MODE_TTL:
        return cached[1], cached[2]
    doc = await get_db().bot_settings.find_one(
        {"_id": f"{ADD_MODE_PREFIX}{int(user_id)}"},
        {"anime": 1, "rarity": 1},
    )
    if not doc:
        return "", ""
    anime = str(doc.get("anime", "")).strip()
    rarity = normalize_add_rarity(doc.get("rarity", "")) or str(doc.get("rarity", "")).strip()
    if anime and rarity:
        _MODE_CACHE[int(user_id)] = (now, anime, rarity)
    return anime, rarity


async def clear_add_mode(user_id: int) -> None:
    _MODE_CACHE.pop(int(user_id), None)
    await get_db().bot_settings.delete_one({"_id": f"{ADD_MODE_PREFIX}{int(user_id)}"})


async def list_common_anime(limit: int = 12) -> list[str]:
    db = get_db()
    rows: dict[str, int] = {}
    catalog_limit = max(100, int(limit) * 10)
    catalog_docs = await db[ANIMES_COLLECTION].find(
        {"name": {"$type": "string", "$ne": ""}},
        {"name": 1},
    ).sort("updatedAt", -1).limit(catalog_limit).to_list(catalog_limit)
    for row in catalog_docs:
        anime = str(row.get("name", "")).strip()
        if anime:
            rows.setdefault(anime, 0)
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        pipeline = [
            {"$match": {"anime": {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": "$anime", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
            {"$limit": int(limit)},
        ]
        docs = await (await (await db[collection_name].aggregate(pipeline)).to_list(limit)
        for row in docs:
            anime = str(row.get("_id", "")).strip()
            if anime:
                rows[anime] = rows.get(anime, 0) + int(row.get("count", 0) or 0)
    return [x[0] for x in sorted(rows.items(), key=lambda x: (-x[1], x[0].lower()))[:limit]]
