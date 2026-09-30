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


# Explicit aliases for values already known to be duplicates in the catalog.
# Keeping aliases explicit avoids risky fuzzy merging of unrelated Anime names.
_ANIME_CANONICAL_ALIASES = {
    "genshin impact": "Genshin Impact [🎮]",
    "genshin impacts": "Genshin Impact [🎮]",
    "genshin imapact": "Genshin Impact [🎮]",
    "genshin imapacts": "Genshin Impact [🎮]",
    "genshin impact[🎮]": "Genshin Impact [🎮]",
    "genshin impacts[🎮]": "Genshin Impact [🎮]",
    "genshin imapact[🎮]": "Genshin Impact [🎮]",
    "genshin imapacts[🎮]": "Genshin Impact [🎮]",
}


def _anime_base_key(value: str = "") -> str:
    text = str(value or "").replace("\u00a0", " ").strip().casefold()
    text = re.sub(r"\s*\[([^\]]+)\]\s*$", r"[\1]", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_anime_name(value: str = "") -> str:
    text = str(value or "").replace("\u00a0", " ").strip()
    text = re.sub(r"\s*\[([^\]]+)\]\s*$", r" [\1]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _ANIME_CANONICAL_ALIASES.get(_anime_base_key(text), text)


def normalized_anime_key(value: str = "") -> str:
    return _anime_base_key(normalize_anime_name(value))


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
                try:
                    await db[ANIME_COLLECTION].bulk_write(operations, ordered=False)
                except DuplicateKeyError:
                    # Another bot instance may have seeded the same Anime at
                    # the same time. Re-apply one normalized upsert at a time.
                    for name in sorted(names, key=normalized_anime_key):
                        key = normalized_anime_key(name)
                        await db[ANIME_COLLECTION].update_one(
                            {"normalizedName": key},
                            {
                                "$setOnInsert": {
                                    "name": name,
                                    "normalizedName": key,
                                }
                            },
                            upsert=True,
                        )

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


async def canonicalize_anime_catalog() -> None:
    """One-time cleanup of duplicate Anime aliases across catalog and cards.

    Only Anime strings/catalog rows are changed. Card IDs, media, and user data
    are preserved.
    """
    db = get_db()
    settings = await db.bot_settings.find_one(
        {"_id": "config"},
        {"animeCatalogCanonicalizedV2": 1},
    )
    if (settings or {}).get("animeCatalogCanonicalizedV2"):
        return

    raw_values: set[str] = set()
    for raw in await db[ANIME_COLLECTION].distinct("name"):
        if str(raw or "").strip():
            raw_values.add(str(raw).strip())
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        for raw in await db[collection_name].distinct("anime"):
            if str(raw or "").strip():
                raw_values.add(str(raw).strip())

    groups: dict[str, dict[str, object]] = {}
    for raw in sorted(raw_values, key=lambda item: (item.casefold(), item)):
        canonical = normalize_anime_name(raw)
        key = normalized_anime_key(canonical)
        if not canonical or not key:
            continue
        group = groups.setdefault(key, {"canonical": canonical, "aliases": set()})
        aliases = group["aliases"]
        if isinstance(aliases, set):
            aliases.add(raw)

    for key, group in groups.items():
        canonical = str(group["canonical"])
        aliases = sorted(group["aliases"])
        if aliases:
            for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
                await db[collection_name].update_many(
                    {"anime": {"$in": aliases}},
                    {"$set": {"anime": canonical}},
                )

        candidate_keys = sorted({normalized_anime_key(alias) for alias in aliases})
        docs = await db[ANIME_COLLECTION].find(
            {"normalizedName": {"$in": candidate_keys}},
            {"name": 1, "normalizedName": 1},
        ).to_list(None)

        if docs:
            keep = next(
                (doc for doc in docs if str(doc.get("name", "")).strip() == canonical),
                docs[0],
            )
            duplicate_ids = [doc["_id"] for doc in docs if doc["_id"] != keep["_id"]]
            if duplicate_ids:
                await db[ANIME_COLLECTION].delete_many({"_id": {"$in": duplicate_ids}})
            await db[ANIME_COLLECTION].update_one(
                {"_id": keep["_id"]},
                {"$set": {"name": canonical, "normalizedName": key, "updatedAt": utcnow()}},
            )
        else:
            await db[ANIME_COLLECTION].insert_one(
                {"name": canonical, "normalizedName": key, "createdAt": utcnow(), "updatedAt": utcnow()},
            )

    await db.bot_settings.update_one(
        {"_id": "config"},
        {"$set": {"animeCatalogCanonicalizedV2": True, "animeCatalogCanonicalizedAt": utcnow()}},
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
