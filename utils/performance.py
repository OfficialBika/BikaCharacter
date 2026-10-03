from __future__ import annotations

import asyncio
import math
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class MetricStore:
    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def observe(self, elapsed_ms: float, error: bool = False) -> None:
        async with self._lock:
            self.calls += 1
            self.total_ms += float(elapsed_ms)
            self.max_ms = max(self.max_ms, float(elapsed_ms))
            if error:
                self.errors += 1

    async def snapshot(self) -> dict[str, float | int]:
        async with self._lock:
            avg = self.total_ms / self.calls if self.calls else 0.0
            return {
                "calls": self.calls,
                "errors": self.errors,
                "avg_ms": round(avg, 3),
                "max_ms": round(self.max_ms, 3),
                "total_ms": round(self.total_ms, 3),
            }


MONGO_METRICS: dict[str, MetricStore] = {}
TELEGRAM_METRICS: dict[str, MetricStore] = {}
EVENT_LOOP_LAG = MetricStore()


def _metric(registry: dict[str, MetricStore], name: str) -> MetricStore:
    return registry.setdefault(str(name), MetricStore())


async def observe_awaitable(
    registry: dict[str, MetricStore],
    name: str,
    operation: Awaitable[T],
) -> T:
    started = time.perf_counter()
    try:
        result = await operation
    except Exception:
        await _metric(registry, name).observe((time.perf_counter() - started) * 1000, True)
        raise
    await _metric(registry, name).observe((time.perf_counter() - started) * 1000)
    return result


class BoundedTTLCache:
    """Positive-only LRU/TTL cache with item and approximate byte ceilings."""

    def __init__(self, max_items: int, max_bytes: int, ttl_seconds: float):
        self.max_items = max(1, int(max_items))
        self.max_bytes = max(1024, int(max_bytes))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._items: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _estimate(value: Any) -> int:
        try:
            return max(64, int(sys.getsizeof(value)))
        except Exception:
            return 256

    def _purge_expired(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        expired = [key for key, (_, expires, _) in self._items.items() if expires <= now]
        for key in expired:
            _, _, size = self._items.pop(key)
            self._bytes -= size

    def get(self, key: str) -> Any | None:
        self._purge_expired()
        item = self._items.get(str(key))
        if item is None:
            return None
        value, expires, size = item
        if expires <= time.monotonic():
            self._items.pop(str(key), None)
            self._bytes -= size
            return None
        self._items.move_to_end(str(key))
        return value

    def set(self, key: str, value: Any, *, size_hint: int | None = None) -> None:
        key = str(key)
        now = time.monotonic()
        self._purge_expired(now)
        old = self._items.pop(key, None)
        if old:
            self._bytes -= old[2]
        size = max(64, int(size_hint or self._estimate(value)))
        if size > self.max_bytes:
            return
        self._items[key] = (value, now + self.ttl_seconds, size)
        self._bytes += size
        self._items.move_to_end(key)
        while self._items and (len(self._items) > self.max_items or self._bytes > self.max_bytes):
            _, _, removed_size = self._items.popitem(last=False)
            self._bytes -= removed_size

    def delete(self, key: str) -> None:
        item = self._items.pop(str(key), None)
        if item:
            self._bytes -= item[2]

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def stats(self) -> dict[str, int]:
        self._purge_expired()
        return {
            "items": len(self._items),
            "bytes": max(0, self._bytes),
            "max_items": self.max_items,
            "max_bytes": self.max_bytes,
        }


async def monitor_event_loop_lag(interval: float = 1.0, stop_event: asyncio.Event | None = None) -> None:
    interval = max(0.1, float(interval))
    while stop_event is None or not stop_event.is_set():
        expected = time.monotonic() + interval
        await asyncio.sleep(interval)
        lag_ms = max(0.0, (time.monotonic() - expected) * 1000.0)
        await EVENT_LOOP_LAG.observe(lag_ms)


async def metrics_snapshot() -> dict[str, Any]:
    return {
        "mongo": {name: await metric.snapshot() for name, metric in MONGO_METRICS.items()},
        "telegram": {name: await metric.snapshot() for name, metric in TELEGRAM_METRICS.items()},
        "event_loop_lag": await EVENT_LOOP_LAG.snapshot(),
    }
