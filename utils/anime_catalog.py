from __future__ import annotations

import asyncio
import re

from utils.text import utcnow

from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from config import LIMITED_CARDS_COLLECTION
from database.mongodb import get_db

ANIME_COLLECTION = "animes"
ANIME_CATALOG_SEEDED_KEY = "animeCatalogSeeded"
ANIME_PAGE_SIZE = 10

_seed_lock = asyncio.Lock()


def normalize_anime_name(value: str = "") -> str:
    text = str(value or "").replace("\u00a0", " ").strip()
    return re.sub(r"\s+", " ", text)


def normalized_anime_key(value: str = "") -> str:
    return normalize_anime_name(value).casefold()


async def ensure_anime_catalog_seeded() -> None:
    """Backfill the dedicated anime catalog once from existing card collections."""
    db = get_db()
    async with _seed_lock:
        settings = await db.bot_settings.find_one(
            {"_id": "config"},
            {ANIME_CATALOG_SEEDED_KEY: 1},
        )
        if (settings or {}).get(ANIME_CATALOG_SEEDED_KEY):
            return

        names: set[str] = set()
        for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
            values = await db[collection_name].distinct("anime")
            for value in values:
                name = normalize_anime_name(value)
                if name:
                    names.add(name)

        if names:
            operations = [
                UpdateOne(
                    {"normalizedName": normalized_anime_key(name)},
                    {
                        "$setOnInsert": {
                            "name": name,
                            "normalizedName": normalized_anime_key(name),
                        }
                    },
                    upsert=True,
                )
                for name in sorted(names, key=normalized_anime_key)
            ]
            if operations:
                await db[ANIME_COLLECTION].bulk_write(operations, ordered=False)

        await db.bot_settings.update_one(
            {"_id": "config"},
            {
                "$set": {
                    ANIME_CATALOG_SEEDED_KEY: True,
                },
                "$setOnInsert": {"createdAt": utcnow()},
            },
            upsert=True,
        )


async def add_anime(name: str, *, created_by: int) -> tuple[bool, str]:
    db = get_db()
    clean = normalize_anime_name(name)
    key = normalized_anime_key(clean)
    if not clean or not key:
        return False, ""

    now = utcnow()
    try:
        result = await db[ANIME_COLLECTION].update_one(
            {"normalizedName": key},
            {
                "$setOnInsert": {
                    "name": clean,
                    "normalizedName": key,
                    "createdBy": int(created_by),
                    "createdAt": now,
                },
                "$set": {"updatedAt": now},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        # Another adder inserted the same normalized Anime concurrently.
        return False, clean
    return bool(result.upserted_id), clean


async def list_animes(page: int = 0, page_size: int = ANIME_PAGE_SIZE) -> tuple[list[str], int]:
    await ensure_anime_catalog_seeded()
    db = get_db()
    page = max(0, int(page or 0))
    page_size = max(1, min(20, int(page_size or ANIME_PAGE_SIZE)))

    total = int(await db[ANIME_COLLECTION].count_documents({}))
    if total <= 0:
        return [], 0

    docs = await (
        db[ANIME_COLLECTION]
        .find({}, {"_id": 0, "name": 1})
        .sort("normalizedName", 1)
        .skip(page * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return [normalize_anime_name(d.get("name", "")) for d in docs if normalize_anime_name(d.get("name", ""))], total
