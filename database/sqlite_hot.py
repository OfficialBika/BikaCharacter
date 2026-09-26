"""SQLite hot-state cache for the single Render worker.

This is deliberately a CACHE, not the source of truth. MongoDB remains the
authoritative persistent store. The cache removes high-frequency group-state
round trips from the Telegram message hot path.

Render Free has an ephemeral filesystem, so every restart/redeploy rebuilds this
cache from MongoDB. That is intentional.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SQLITE_HOT_PATH = Path(
    os.getenv("SQLITE_HOT_PATH", "data/hot_cache.db").strip()
    or "data/hot_cache.db"
)
SQLITE_FLUSH_SECONDS = max(
    1.0,
    float(os.getenv("SQLITE_FLUSH_SECONDS", "3") or 3),
)
SQLITE_REFRESH_SECONDS = max(
    5.0,
    float(os.getenv("SQLITE_REFRESH_SECONDS", "10") or 10),
)
SQLITE_BUSY_TIMEOUT_MS = max(
    1000,
    int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000") or 5000),
)

_LOCK = asyncio.Lock()
_INITIALIZED = False
_FLUSH_TASK: asyncio.Task | None = None


def _connect() -> sqlite3.Connection:
    SQLITE_HOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(SQLITE_HOT_PATH),
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _json_default(value: Any):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_load(value: str | None, fallback=None):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _row_to_group(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None

    active = _json_load(row["active_drop_json"], {}) or {}
    paused_until = row["drop_paused_until"]
    lock_until = row["drop_spawn_lock_until"]

    return {
        "groupId": int(row["group_id"]),
        "changeTime": int(row["change_time"] or 100),
        "messageCount": int(row["message_count"] or 0),
        "totalDrops": int(row["total_drops"] or 0),
        "activeDrop": active,
        "dropPaused": bool(row["drop_paused"]),
        "dropPausedUntil": paused_until,
        "dropPausedReason": str(row["drop_paused_reason"] or ""),
        "dropSpawnLockUntil": lock_until,
        "dropSpawnLockAt": row["drop_spawn_lock_at"],
        "dropSpawnLockReason": str(row["drop_spawn_lock_reason"] or ""),
        "_sqliteLastRefresh": float(row["last_mongo_refresh"] or 0),
        "_sqliteDirtyCount": bool(row["dirty_count"]),
    }


async def init() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    async with _LOCK:
        if _INITIALIZED:
            return

        def _init():
            conn = _connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS groups_hot (
                        group_id INTEGER PRIMARY KEY,
                        change_time INTEGER NOT NULL DEFAULT 100,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        total_drops INTEGER NOT NULL DEFAULT 0,
                        active_drop_json TEXT NOT NULL DEFAULT '{}',
                        drop_paused INTEGER NOT NULL DEFAULT 0,
                        drop_paused_until TEXT,
                        drop_paused_reason TEXT NOT NULL DEFAULT '',
                        drop_spawn_lock_until TEXT,
                        drop_spawn_lock_at TEXT,
                        drop_spawn_lock_reason TEXT NOT NULL DEFAULT '',
                        last_mongo_refresh REAL NOT NULL DEFAULT 0,
                        last_mongo_flush REAL NOT NULL DEFAULT 0,
                        dirty_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_groups_hot_dirty "
                    "ON groups_hot(dirty_count, last_mongo_flush)"
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_init)
        _INITIALIZED = True


async def clear_groups() -> None:
    """Drop local hot state so startup always rebuilds from MongoDB."""
    await init()
    async with _LOCK:
        def _clear():
            conn = _connect()
            try:
                conn.execute("DELETE FROM groups_hot")
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_clear)


async def close() -> None:
    global _FLUSH_TASK, _INITIALIZED
    if _FLUSH_TASK is not None:
        _FLUSH_TASK.cancel()
        try:
            await _FLUSH_TASK
        except asyncio.CancelledError:
            pass
        _FLUSH_TASK = None
    _INITIALIZED = False


def _write_group_sync(group: dict, *, refresh: bool = True) -> None:
    conn = _connect()
    try:
        now = time.time()
        conn.execute(
            """
            INSERT INTO groups_hot (
                group_id, change_time, message_count, total_drops,
                active_drop_json, drop_paused, drop_paused_until,
                drop_paused_reason, drop_spawn_lock_until, drop_spawn_lock_at,
                drop_spawn_lock_reason, last_mongo_refresh, dirty_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                change_time=excluded.change_time,
                message_count=excluded.message_count,
                total_drops=excluded.total_drops,
                active_drop_json=excluded.active_drop_json,
                drop_paused=excluded.drop_paused,
                drop_paused_until=excluded.drop_paused_until,
                drop_paused_reason=excluded.drop_paused_reason,
                drop_spawn_lock_until=excluded.drop_spawn_lock_until,
                drop_spawn_lock_at=excluded.drop_spawn_lock_at,
                drop_spawn_lock_reason=excluded.drop_spawn_lock_reason,
                last_mongo_refresh=excluded.last_mongo_refresh,
                dirty_count=0
            """,
            (
                int(group["groupId"]),
                int(group.get("changeTime", 100) or 100),
                int(group.get("messageCount", 0) or 0),
                int(group.get("totalDrops", 0) or 0),
                json.dumps(group.get("activeDrop") or {}, default=_json_default, separators=(",", ":")),
                1 if group.get("dropPaused") else 0,
                _json_default(group.get("dropPausedUntil")) if group.get("dropPausedUntil") else None,
                str(group.get("dropPausedReason", "") or ""),
                _json_default(group.get("dropSpawnLockUntil")) if group.get("dropSpawnLockUntil") else None,
                _json_default(group.get("dropSpawnLockAt")) if group.get("dropSpawnLockAt") else None,
                str(group.get("dropSpawnLockReason", "") or ""),
                now if refresh else float(group.get("_sqliteLastRefresh", 0) or 0),
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def seed_group(group: dict, *, force: bool = False) -> dict:
    await init()

    def _seed():
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM groups_hot WHERE group_id=?",
                (int(group["groupId"]),),
            ).fetchone()
            now = time.time()
            if row is not None and not force:
                cached = _row_to_group(row)
                if cached and now - float(cached.get("_sqliteLastRefresh", 0) or 0) < SQLITE_REFRESH_SECONDS:
                    return cached

            conn.close()
            _write_group_sync(group, refresh=True)
            conn = _connect()
            row = conn.execute(
                "SELECT * FROM groups_hot WHERE group_id=?",
                (int(group["groupId"]),),
            ).fetchone()
            return _row_to_group(row)
        finally:
            conn.close()

    # _write_group_sync opens a separate connection, so serialize seed/update calls.
    async with _LOCK:
        return await asyncio.to_thread(_seed)


async def get_group(group_id: int) -> dict | None:
    await init()

    def _get():
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM groups_hot WHERE group_id=?",
                (int(group_id),),
            ).fetchone()
            return _row_to_group(row)
        finally:
            conn.close()

    return await asyncio.to_thread(_get)


async def increment_message_count(group_id: int) -> dict | None:
    """Atomically increment local group count and return the new hot state."""
    await init()

    async with _LOCK:
        def _inc():
            conn = _connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    UPDATE groups_hot
                    SET message_count = message_count + 1,
                        dirty_count = 1
                    WHERE group_id = ?
                    """,
                    (int(group_id),),
                )
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                conn.commit()
                return _row_to_group(row)
            finally:
                conn.close()

        return await asyncio.to_thread(_inc)


async def acquire_spawn(group_id: int, change_time: int, reason: str = "auto_drop") -> dict | None:
    """Single-instance atomic spawn gate.

    Render Free allows one instance, so this local lock removes a second Mongo
    findOneAndUpdate round trip while preserving the concurrency guard.
    """
    await init()

    async with _LOCK:
        def _acquire():
            conn = _connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                current = _row_to_group(row)
                if not current:
                    conn.rollback()
                    return None

                if int(current.get("messageCount", 0)) < int(change_time):
                    conn.rollback()
                    return None

                lock_until = current.get("dropSpawnLockUntil")
                now = time.time()
                if lock_until:
                    try:
                        if datetime.fromisoformat(str(lock_until).replace("Z", "+00:00")).timestamp() > now:
                            conn.rollback()
                            return None
                    except Exception:
                        pass

                until = datetime.fromtimestamp(
                    now + max(10, int(os.getenv("DROP_SPAWN_LOCK_SECONDS", "45") or 45)),
                    tz=timezone.utc,
                ).isoformat()

                conn.execute(
                    """
                    UPDATE groups_hot
                    SET message_count=0,
                        drop_spawn_lock_until=?,
                        drop_spawn_lock_at=?,
                        drop_spawn_lock_reason=?,
                        dirty_count=1
                    WHERE group_id=?
                    """,
                    (
                        until,
                        datetime.now(timezone.utc).isoformat(),
                        str(reason),
                        int(group_id),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                conn.commit()
                return _row_to_group(row)
            finally:
                conn.close()

        return await asyncio.to_thread(_acquire)


async def release_spawn(group_id: int) -> dict | None:
    """Release the local spawn gate if the Mongo-side gate did not succeed."""
    await init()

    async with _LOCK:
        def _release():
            conn = _connect()
            try:
                conn.execute(
                    """
                    UPDATE groups_hot
                    SET message_count=0,
                        drop_spawn_lock_until=NULL,
                        drop_spawn_lock_at=NULL,
                        drop_spawn_lock_reason='',
                        dirty_count=1
                    WHERE group_id=?
                    """,
                    (int(group_id),),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                return _row_to_group(row)
            finally:
                conn.close()

        return await asyncio.to_thread(_release)


async def set_group_from_mongo(group: dict) -> dict:
    """Sync Mongo state without clobbering a newer dirty local message counter."""
    await init()
    async with _LOCK:
        def _sync():
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group["groupId"]),),
                ).fetchone()
                current = _row_to_group(row)
                merged = dict(group)
                if current and current.get("_sqliteDirtyCount"):
                    merged["messageCount"] = current.get("messageCount", 0)
                _write_group_sync(merged, refresh=True)
            finally:
                conn.close()
        await asyncio.to_thread(_sync)
    return group


async def update_group_fields(
    group_id: int,
    *,
    active_drop: dict | None = None,
    total_drops: int | None = None,
    message_count: int | None = None,
    change_time: int | None = None,
    drop_paused: bool | None = None,
    drop_paused_until: Any = None,
    drop_paused_reason: str | None = None,
    clear_spawn_lock: bool = False,
) -> dict | None:
    """Update local hot state after a Mongo authoritative write."""
    await init()

    async with _LOCK:
        def _update():
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                current = _row_to_group(row) or {
                    "groupId": int(group_id),
                    "changeTime": 100,
                    "messageCount": 0,
                    "totalDrops": 0,
                    "activeDrop": {},
                    "dropPaused": False,
                }

                if active_drop is not None:
                    current["activeDrop"] = active_drop
                if total_drops is not None:
                    current["totalDrops"] = int(total_drops)
                if message_count is not None:
                    current["messageCount"] = int(message_count)
                    current["_sqliteDirtyCount"] = True
                if change_time is not None:
                    current["changeTime"] = int(change_time)
                if drop_paused is not None:
                    current["dropPaused"] = bool(drop_paused)
                if drop_paused_until is not None:
                    current["dropPausedUntil"] = drop_paused_until
                if drop_paused_reason is not None:
                    current["dropPausedReason"] = str(drop_paused_reason)
                if clear_spawn_lock:
                    current["dropSpawnLockUntil"] = None
                    current["dropSpawnLockAt"] = None
                    current["dropSpawnLockReason"] = ""

                conn.close()
                _write_group_sync(current, refresh=False)
                conn = _connect()
                row = conn.execute(
                    "SELECT * FROM groups_hot WHERE group_id=?",
                    (int(group_id),),
                ).fetchone()
                return _row_to_group(row)
            finally:
                conn.close()

        return await asyncio.to_thread(_update)


async def flush_dirty_counts(get_db_func) -> int:
    """Flush only local message counters to Mongo; active drop remains Mongo-authoritative."""
    await init()

    def _dirty_rows():
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT group_id, message_count
                FROM groups_hot
                WHERE dirty_count=1
                """
            ).fetchall()
            return [(int(r["group_id"]), int(r["message_count"])) for r in rows]
        finally:
            conn.close()

    rows = await asyncio.to_thread(_dirty_rows)
    if not rows:
        return 0

    updated = 0
    db = get_db_func()
    for group_id, message_count in rows:
        try:
            await db.groups.update_one(
                {"groupId": int(group_id)},
                {"$set": {"messageCount": int(message_count), "updatedAt": datetime.now(timezone.utc)}},
            )

            def _mark_clean(gid=group_id):
                conn = _connect()
                try:
                    conn.execute(
                        """
                        UPDATE groups_hot
                        SET dirty_count=0, last_mongo_flush=?
                        WHERE group_id=? AND message_count=?
                        """,
                        (time.time(), int(gid), int(message_count)),
                    )
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_mark_clean)
            updated += 1
        except Exception as exc:
            print(f"SQLITE HOT FLUSH ERROR group={group_id}: {exc!r}", flush=True)

    return updated


async def flush_loop(get_db_func) -> None:
    while True:
        try:
            await asyncio.sleep(SQLITE_FLUSH_SECONDS)
            count = await flush_dirty_counts(get_db_func)
            if count:
                print(f"SQLITE HOT FLUSH: groups={count}", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"SQLITE HOT LOOP ERROR: {exc!r}", flush=True)


def start_flush_loop(get_db_func) -> asyncio.Task:
    global _FLUSH_TASK
    if _FLUSH_TASK is None or _FLUSH_TASK.done():
        _FLUSH_TASK = asyncio.create_task(flush_loop(get_db_func))
    return _FLUSH_TASK
