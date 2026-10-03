from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from utils.performance import BoundedTTLCache


class PerformanceTests(unittest.TestCase):
    def test_cache_is_bounded(self):
        cache = BoundedTTLCache(max_items=3, max_bytes=1024, ttl_seconds=60)
        for i in range(10):
            cache.set(str(i), "x" * 100)
        self.assertLessEqual(cache.stats()["items"], 3)
        self.assertLessEqual(cache.stats()["bytes"], 1024)

    def test_cache_eviction_is_lru(self):
        cache = BoundedTTLCache(max_items=2, max_bytes=1024, ttl_seconds=60)
        cache.set("a", "a")
        cache.set("b", "b")
        self.assertEqual(cache.get("a"), "a")
        cache.set("c", "c")
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), "a")

    def test_claim_like_concurrency(self):
        async def worker(lock, state):
            async with lock:
                if state["claimed"]:
                    return False
                state["claimed"] = True
                return True

        async def run():
            lock = asyncio.Lock()
            state = {"claimed": False}
            results = await asyncio.gather(*(worker(lock, state) for _ in range(100)))
            self.assertEqual(sum(results), 1)

        asyncio.run(run())

    def test_gift_like_concurrency(self):
        async def worker(lock, state):
            async with lock:
                if state["balance"] <= 0:
                    return False
                state["balance"] -= 1
                state["receiver"] += 1
                return True

        async def run():
            lock = asyncio.Lock()
            state = {"balance": 10, "receiver": 0}
            results = await asyncio.gather(*(worker(lock, state) for _ in range(100)))
            self.assertEqual(sum(results), 10)
            self.assertEqual(state["balance"], 0)
            self.assertEqual(state["receiver"], 10)

        asyncio.run(run())

    def test_sqlite_wal_smoke(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hot.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("CREATE TABLE cards(card_id TEXT PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO cards VALUES('1','Test')")
            conn.commit()
            row = conn.execute("SELECT name FROM cards WHERE card_id='1'").fetchone()
            self.assertEqual(row[0], "Test")
            conn.close()


if __name__ == "__main__":
    unittest.main()
