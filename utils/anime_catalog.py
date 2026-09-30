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
    """One-time, update-only Anime cleanup.

    IMPORTANT: This migration never deletes MongoDB documents. It only:
      - rewrites known Anime aliases in card documents to the canonical name;
      - updates one catalog document to be the canonical row;
      - marks extra catalog rows as aliases instead of deleting them.
    Card IDs, media, storage references, and user data are never removed.
    """
    db = get_db()
    settings = await db.bot_settings.find_one(
        {"_id": "config"},
        {"animeCatalogCanonicalizedV3": 1},
    )
    if (settings or {}).get("animeCatalogCanonicalizedV3"):
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

    # Read existing catalog rows once. Legacy duplicate rows are retained,
    # because normalizedName is unique and changing multiple rows to the same
    # normalized key would otherwise require deleting data.
    catalog_docs = await db[ANIME_COLLECTION].find(
        {},
        {"name": 1, "normalizedName": 1, "isAlias": 1},
    ).to_list(None)
    catalog_by_key: dict[str, list[dict]] = {}
    for doc in catalog_docs:
        key = normalized_anime_key(doc.get("name", ""))
        if key:
            catalog_by_key.setdefault(key, []).append(doc)

    now = utcnow()

    for key, group in groups.items():
        canonical = str(group["canonical"])
        aliases = sorted(group["aliases"])

        # Card data is preserved; only the Anime string is corrected.
        if aliases:
            for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
                await db[collection_name].update_many(
                    {"anime": {"$in": aliases}},
                    {"$set": {"anime": canonical, "updatedAt": now}},
                )

        docs = catalog_by_key.get(key, [])
        if not docs:
            # The seed step normally creates this row already. Avoid creating
            # anything here unless the catalog is genuinely missing it.
            continue

        keep = next(
            (
                doc
                for doc in docs
                if str(doc.get("name", "")).strip() == canonical
                and not doc.get("isAlias", False)
            ),
            None,
        )

        if keep is None:
            # Pick one existing row as the canonical row. If its old
            # normalizedName is a legacy key, changing it is safe because no
            # canonical normalizedName exists in this group.
            keep = docs[0]
            await db[ANIME_COLLECTION].update_one(
                {"_id": keep["_id"]},
                {
                    "$set": {
                        "name": canonical,
                        "normalizedName": key,
                        "isAlias": False,
                        "updatedAt": now,
                    }
                },
            )
        else:
            await db[ANIME_COLLECTION].update_one(
                {"_id": keep["_id"]},
                {
                    "$set": {
                        "name": canonical,
                        "normalizedName": key,
                        "isAlias": False,
                        "updatedAt": now,
                    },
                    "$unset": {"canonicalName": ""},
                },
            )

        keep_id = keep["_id"]
        for doc in docs:
            if doc["_id"] == keep_id:
                continue
            # Never delete the duplicate row. Keep it for data safety/audit,
            # but make its user-facing name canonical and hide it from the
            # selectable Anime catalog.
            await db[ANIME_COLLECTION].update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "name": canonical,
                        "canonicalName": canonical,
                        "isAlias": True,
                        "updatedAt": now,
                    }
                },
            )

    await db.bot_settings.update_one(
        {"_id": "config"},
        {
            "$set": {
                "animeCatalogCanonicalizedV3": True,
                "animeCatalogCanonicalizedAt": now,
            },
            "$setOnInsert": {"createdAt": now},
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
                "$set": {
                    "name": clean,
                    "normalizedName": key,
                    "isAlias": False,
                    "updatedAt": now,
                },
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

    catalog_filter = {"isAlias": {"$ne": True}}
    total = int(await db[ANIME_COLLECTION].count_documents(catalog_filter))
    if total <= 0:
        return [], 0

    docs = await (
        db[ANIME_COLLECTION]
        .find(catalog_filter, {"_id": 0, "name": 1})
        .sort("normalizedName", 1)
        .skip(page * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return [normalize_anime_name(d.get("name", "")) for d in docs if normalize_anime_name(d.get("name", ""))], total
