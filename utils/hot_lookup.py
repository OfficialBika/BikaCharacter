from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from database.mongodb import get_db
from utils.performance import BoundedTTLCache, observe_awaitable, MONGO_METRICS
from utils.parser import normalized_search_name

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = Path(os.getenv("HOT_LOOKUP_SQLITE_PATH", str(BASE_DIR / "data" / "hot_lookup.sqlite3")))
SQLITE_SYNC_SECONDS = max(30, int(os.getenv("HOT_LOOKUP_SYNC_SECONDS", "300") or 300))

SEARCH_CACHE = BoundedTTLCache(
    max_items=int(os.getenv("LOOKUP_CACHE_MAX_ITEMS", "20000") or 20000),
    max_bytes=int(os.getenv("LOOKUP_CACHE_MAX_BYTES", str(64 * 1024 * 1024)) or 64 * 1024 * 1024),
    ttl_seconds=float(os.getenv("LOOKUP_CACHE_TTL_SECONDS", "120") or 120),
)
CATALOG_CACHE = BoundedTTLCache(
    max_items=128,
    max_bytes=4 * 1024 * 1024,
    ttl_seconds=float(os.getenv("CATALOG_CACHE_TTL_SECONDS", "300") or 300),
)
RANK_CACHE = BoundedTTLCache(
    max_items=int(os.getenv("RANK_CACHE_MAX_ITEMS", "10000") or 10000),
    max_bytes=8 * 1024 * 1024,
    ttl_seconds=float(os.getenv("RANK_CACHE_TTL_SECONDS", "60") or 60),
)

_lock = asyncio.Lock()
_initialized = False


def _conn() -> sqlite3.Connection:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_PATH), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


async def init_hot_lookup() -> None:
    global _initialized
    async with _lock:
        if _initialized:
            return
        await asyncio.to_thread(_init_sqlite)
        _initialized = True


def _init_sqlite() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                normalized_name TEXT NOT NULL,
                name TEXT NOT NULL,
                rarity TEXT NOT NULL,
                anime TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                source_collection TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_normalized_name ON cards(normalized_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_rarity ON cards(rarity)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_anime ON cards(anime)")
        conn.commit()


def _rows_from_docs(docs: list[dict[str, Any]], source: str):
    for doc in docs:
        card_id = str(doc.get("cardId", "") or "").strip()
        if not card_id:
            continue
        yield (
            card_id,
            str(doc.get("normalizedName") or normalized_search_name(doc.get("name", ""))),
            str(doc.get("name", "")),
            str(doc.get("rarity", "")),
            str(doc.get("anime", "")),
            str(doc.get("fileId", "")),
            str(doc.get("fileUniqueId", "")),
            str(doc.get("mediaType", "") or ""),
            str(doc.get("mimeType", "") or ""),
            str(doc.get("fileName", "") or ""),
            source,
            str(doc.get("updatedAt", "") or ""),
        )


def _upsert_rows(rows: list[tuple]) -> None:
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            """
            INSERT INTO cards(
                card_id, normalized_name, name, rarity, anime, file_id,
                file_unique_id, media_type, mime_type, file_name,
                source_collection, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(card_id) DO UPDATE SET
                normalized_name=excluded.normalized_name,
                name=excluded.name,
                rarity=excluded.rarity,
                anime=excluded.anime,
                file_id=excluded.file_id,
                file_unique_id=excluded.file_unique_id,
                media_type=excluded.media_type,
                mime_type=excluded.mime_type,
                file_name=excluded.file_name,
                source_collection=excluded.source_collection,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()


async def upsert_card(doc: dict[str, Any], source: str = "photos") -> None:
    await init_hot_lookup()
    await asyncio.to_thread(_upsert_rows, list(_rows_from_docs([doc], source)))
    invalidate_card(str(doc.get("cardId", "")))


def invalidate_card(card_id: str) -> None:
    key = str(card_id)
    SEARCH_CACHE.delete(key)
    SEARCH_CACHE.delete(f"id:{key}")
    CATALOG_CACHE.clear()


async def delete_card(card_id: str, source: str | None = None) -> int:
    """Remove a deleted MongoDB card from SQLite and invalidate dependent caches."""
    await init_hot_lookup()
    card_id = str(card_id)

    def remove_row() -> int:
        with _conn() as conn:
            if source:
                cursor = conn.execute(
                    "DELETE FROM cards WHERE card_id=? AND source_collection=?",
                    (card_id, str(source)),
                )
            else:
                cursor = conn.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
            conn.commit()
            return int(cursor.rowcount or 0)

    removed = await asyncio.to_thread(remove_row)
    # Search result caches can contain the deleted card even when the query
    # key isn't the card ID. Flush them to avoid serving a stale preview.
    invalidate_card(card_id)
    SEARCH_CACHE.clear()
    CATALOG_CACHE.clear()
    RANK_CACHE.clear()
    return removed


def invalidate_user_rank(user_id: int) -> None:
    # Any unique-card change can change every user global rank.
    RANK_CACHE.clear()


def _sqlite_count() -> int:
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM cards").fetchone()
    return int(row[0] if row else 0)


def hot_lookup_count() -> int:
    return _sqlite_count()


async def anime_totals(anime_names: list[str]) -> dict[str, int]:
    names = [str(x or "").strip() for x in anime_names if str(x or "").strip()]
    if not names:
        return {}
    key = "anime:" + "|".join(sorted({x.lower() for x in names}))
    cached = CATALOG_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    def query():
        placeholders = ",".join("?" for _ in names)
        with _conn() as conn:
            rows = conn.execute(
                f"SELECT anime, COUNT(*) FROM cards WHERE anime IN ({placeholders}) GROUP BY anime",
                names,
            ).fetchall()
        return {str(anime): int(total) for anime, total in rows}
    result = await asyncio.to_thread(query)
    if not result:
        db = get_db()
        result = {}
        for collection_name in ("photos", os.getenv("LIMITED_CARDS_COLLECTION", "limited_cards")):
            rows = await observe_awaitable(
                MONGO_METRICS,
                "harem_anime_totals",
                await (await db[collection_name].aggregate([{"$match":{"anime":{"$in":names}}},{"$group":{"_id":"$anime","total":{"$sum":1}}}])).to_list(None),
            )
            for row in rows:
                key_name = str(row.get("_id",""))
                result[key_name] = result.get(key_name, 0) + int(row.get("total",0) or 0)
    CATALOG_CACHE.set(key, result, size_hint=max(256, len(result) * 80))
    return result


async def rebuild_hot_lookup() -> int:
    await init_hot_lookup()
    db = get_db()
    docs: list[dict] = []
    for collection_name in ("photos", os.getenv("LIMITED_CARDS_COLLECTION", "limited_cards")):
        cursor = db[collection_name].find(
            {},
            {
                "cardId": 1, "name": 1, "normalizedName": 1, "rarity": 1,
                "anime": 1, "fileId": 1, "fileUniqueId": 1, "mediaType": 1,
                "mimeType": 1, "fileName": 1, "updatedAt": 1,
            },
        )
        while True:
            batch = await cursor.to_list(1000)
            if not batch:
                break
            docs.extend(dict(row) for row in batch)
            if len(docs) >= 5000:
                source = collection_name
                rows = list(_rows_from_docs(docs, source))
                await asyncio.to_thread(_upsert_rows, rows)
                docs.clear()
    if docs:
        await asyncio.to_thread(_upsert_rows, list(_rows_from_docs(docs, collection_name)))
    return _sqlite_count()


async def _sqlite_search(search: str, offset: int, limit: int) -> tuple[list[dict], bool]:
    def query():
        normalized = normalized_search_name(search)
        with _conn() as conn:
            if normalized:
                rows = conn.execute(
                    """
                    SELECT card_id, name, normalized_name, rarity, anime, file_id,
                           file_unique_id, media_type, mime_type, file_name
                    FROM cards
                    WHERE normalized_name LIKE ? OR lower(card_id)=?
                    ORDER BY
                        CASE WHEN normalized_name=? OR lower(card_id)=? THEN 0 ELSE 1 END,
                        CASE WHEN normalized_name LIKE ? THEN 0 ELSE 1 END,
                        CASE WHEN card_id GLOB '[0-9]*' THEN 0 ELSE 1 END,
                        CASE WHEN card_id GLOB '[0-9]*' THEN CAST(card_id AS INTEGER) ELSE 0 END,
                        card_id
                    LIMIT ? OFFSET ?
                    """,
                    (f"%{normalized}%", normalized, normalized, normalized, f"{normalized}%", limit + 1, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT card_id, name, normalized_name, rarity, anime, file_id,
                           file_unique_id, media_type, mime_type, file_name
                    FROM cards
                    ORDER BY
                        CASE WHEN card_id GLOB '[0-9]*' THEN 0 ELSE 1 END,
                        CASE WHEN card_id GLOB '[0-9]*' THEN CAST(card_id AS INTEGER) ELSE 0 END,
                        card_id
                    LIMIT ? OFFSET ?
                    """,
                    (limit + 1, offset),
                ).fetchall()
        keys = ["cardId","name","normalizedName","rarity","anime","fileId","fileUniqueId","mediaType","mimeType","fileName"]
        docs = [dict(zip(keys, row)) for row in rows]
        return docs[:limit], len(docs) > limit
    return await asyncio.to_thread(query)


async def search_cards(search: str, offset: int, limit: int) -> tuple[list[dict], bool]:
    key = f"{normalized_search_name(search)}:{int(offset)}:{int(limit)}"
    cached = SEARCH_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        result = await _sqlite_search(search, offset, limit)
        if result[0]:
            SEARCH_CACHE.set(key, result, size_hint=max(1024, len(result[0]) * 900))
            return result
    except Exception as exc:
        print("HOT LOOKUP SQLITE ERROR:", repr(exc), flush=True)
    # Mongo is the source of truth; SQLite is never authoritative.
    db = get_db()
    normalized = normalized_search_name(search)
    query: dict[str, Any] = {}
    if normalized:
        query = {"$or": [{"normalizedName": {"$regex": normalized, "$options": "i"}}, {"cardId": str(search).strip()}]}
    docs: list[dict] = []
    for collection_name in ("photos", os.getenv("LIMITED_CARDS_COLLECTION", "limited_cards")):
        part = await observe_awaitable(
            MONGO_METRICS, "lookup_fallback",
            db[collection_name].find(query, {
                "cardId":1,"name":1,"normalizedName":1,"rarity":1,"anime":1,
                "fileId":1,"fileUniqueId":1,"mediaType":1,"mimeType":1,"fileName":1,
            }).to_list(None)
        )
        docs.extend(dict(d) for d in part)
        for doc in part:
            await upsert_card(doc, collection_name)
    docs.sort(key=lambda d: (str(d.get("cardId",""))))
    result = (docs[offset:offset+limit], len(docs) > offset+limit)
    if result[0]:
        SEARCH_CACHE.set(key, result, size_hint=max(1024, len(result[0]) * 900))
    return result


async def get_card(card_id: str) -> dict | None:
    key = f"id:{str(card_id)}"
    cached = SEARCH_CACHE.get(key)
    if cached is not None:
        return cached
    await init_hot_lookup()
    def query():
        with _conn() as conn:
            row = conn.execute(
                "SELECT card_id,name,normalized_name,rarity,anime,file_id,file_unique_id,media_type,mime_type,file_name,source_collection FROM cards WHERE card_id=?",
                (str(card_id),),
            ).fetchone()
        if not row:
            return None
        keys=["cardId","name","normalizedName","rarity","anime","fileId","fileUniqueId","mediaType","mimeType","fileName","_sourceCollection"]
        return dict(zip(keys,row))
    doc = await asyncio.to_thread(query)
    if doc:
        SEARCH_CACHE.set(key, doc, size_hint=1200)
        return doc
    db=get_db()
    for collection_name in ("photos", os.getenv("LIMITED_CARDS_COLLECTION", "limited_cards")):
        doc = await observe_awaitable(MONGO_METRICS, "card_fallback", db[collection_name].find_one({"cardId":str(card_id)}))
        if doc:
            doc=dict(doc)
            doc["_sourceCollection"]=collection_name
            await upsert_card(doc, collection_name)
            SEARCH_CACHE.set(key, doc, size_hint=1200)
            return doc
    return None


async def catalog_stats() -> dict[str, Any]:
    cached = CATALOG_CACHE.get("stats")
    if cached is not None:
        return cached
    db = get_db()
    total = await observe_awaitable(MONGO_METRICS, "catalog_count", db.photos.count_documents({}))
    rarities = await observe_awaitable(
        MONGO_METRICS,
        "catalog_rarity",
        await (await db.photos.aggregate([{"$group":{"_id":"$rarity","count":{"$sum":1}}}])).to_list(None),
    )
    value = {"total": int(total), "rarities": {str(r.get("_id","")): int(r.get("count",0)) for r in rarities}}
    CATALOG_CACHE.set("stats", value, size_hint=4096)
    return value


async def get_global_rank(user_id: int, unique_cards: int) -> int:
    key = str(int(user_id))
    cached = RANK_CACHE.get(key)
    if cached is not None and int(cached.get("unique_cards", -1)) == int(unique_cards):
        return int(cached["rank"])
    db = get_db()
    higher = await observe_awaitable(
        MONGO_METRICS,
        "profile_rank",
        db.users.count_documents({
            "$expr": {"$gt": [{"$size": {"$ifNull": ["$cards", []]}}, int(unique_cards)]}
        }),
    )
    rank = int(higher) + 1
    RANK_CACHE.set(key, {"unique_cards": int(unique_cards), "rank": rank}, size_hint=128)
    return rank


async def invalidate_all() -> None:
    SEARCH_CACHE.clear()
    CATALOG_CACHE.clear()
    RANK_CACHE.clear()
