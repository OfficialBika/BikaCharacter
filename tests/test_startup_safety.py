from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram.error import RetryAfter, TelegramError

import bot


class StartupSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_rate_limit_does_not_abort_startup(self):
        set_my_commands = AsyncMock(side_effect=RetryAfter(60))
        app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands))

        await bot.register_commands(app)

        set_my_commands.assert_awaited_once()

    async def test_command_menu_telegram_error_does_not_abort_startup(self):
        set_my_commands = AsyncMock(side_effect=TelegramError("temporary Telegram API error"))
        app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands))

        await bot.register_commands(app)

        set_my_commands.assert_awaited_once()

    async def test_polling_discards_updates_queued_during_downtime(self):
        delete_webhook = AsyncMock()
        start_polling = AsyncMock()
        app = SimpleNamespace(
            bot=SimpleNamespace(delete_webhook=delete_webhook),
            updater=SimpleNamespace(start_polling=start_polling),
        )

        with patch.object(bot, "ENABLE_HEALTH_SERVER", False), patch.object(
            bot, "POLLING_DROP_PENDING_UPDATES", True
        ):
            await bot.run_polling(app)

        delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
        start_polling.assert_awaited_once()
        self.assertTrue(start_polling.await_args.kwargs["drop_pending_updates"])

    async def test_collect_targets_deduplicates_and_uses_expected_projection(self):
        import handlers.broadcast as broadcast

        class FakeCursor:
            def __init__(self, docs):
                self.docs = docs
            def __aiter__(self):
                self._index = 0
                return self
            async def __anext__(self):
                if self._index >= len(self.docs):
                    raise StopAsyncIteration
                value = self.docs[self._index]
                self._index += 1
                return value

        class FakeCollection:
            def __init__(self, docs):
                self.docs = docs
                self.calls = []
            def find(self, query, projection):
                self.calls.append((query, projection))
                return FakeCursor(self.docs)

        groups = FakeCollection([{"groupId": "-1001"}, {"groupId": -1002}, {"groupId": "bad"}])
        users = FakeCollection([{"userId": 2001}, {"userId": "-1001"}, {"userId": "bad"}])
        fake_db = SimpleNamespace(groups=groups, users=users)

        with patch.object(broadcast, "get_db", return_value=fake_db):
            targets, group_ids, user_ids = await broadcast._collect_targets(
                include_groups=True,
                include_users=True,
                harem_only=False,
            )

        self.assertEqual(group_ids, {-1001, -1002})
        self.assertEqual(user_ids, {2001, -1001})
        self.assertEqual(targets, [-1002, -1001, 2001])
        self.assertEqual(groups.calls, [({}, {"groupId": 1})])
        self.assertEqual(users.calls, [({}, {"userId": 1})])

    async def test_bounded_queue_producer_feeds_more_targets_than_capacity(self):
        import asyncio
        import handlers.broadcast as broadcast

        queue = asyncio.Queue(maxsize=4)
        targets = list(range(25))
        seen = []

        async def consumer():
            while True:
                target = await queue.get()
                try:
                    if target is None:
                        return
                    seen.append(target)
                finally:
                    queue.task_done()

        consumer_tasks = [asyncio.create_task(consumer()) for _ in range(3)]
        producer = asyncio.create_task(
            broadcast._enqueue_broadcast_targets(queue, targets, len(consumer_tasks))
        )
        await asyncio.wait_for(producer, timeout=2)
        await asyncio.wait_for(queue.join(), timeout=2)
        await asyncio.gather(*consumer_tasks)
        self.assertEqual(sorted(seen), targets)


    async def test_broadcast_rate_limiter_smoothly_spaces_concurrent_requests(self):
        import asyncio
        import time
        import handlers.broadcast as broadcast

        limiter = broadcast._BroadcastRateLimiter(10)

        async def acquire_and_record():
            await limiter.acquire()
            return time.monotonic()

        started_at = time.monotonic()
        timestamps = sorted(await asyncio.gather(
            acquire_and_record(),
            acquire_and_record(),
            acquire_and_record(),
        ))

        self.assertGreaterEqual(timestamps[1] - timestamps[0], 0.07)
        self.assertGreaterEqual(timestamps[2] - timestamps[1], 0.07)
        self.assertGreaterEqual(timestamps[-1] - started_at, 0.15)

    async def test_broadcast_rate_limiter_adapts_and_honors_global_cooldown(self):
        import time
        import handlers.broadcast as broadcast

        limiter = broadcast._BroadcastRateLimiter(20)
        started_at = time.monotonic()

        throttled, reduced_rate = await limiter.report_retry_after(0.06)
        self.assertTrue(throttled)
        self.assertEqual(reduced_rate, 14)
        self.assertEqual(limiter.current_rate, 14)

        # Other workers receiving the same RetryAfter must not repeatedly
        # multiply the rate down during the already-active cooldown window.
        throttled_again, repeated_rate = await limiter.report_retry_after(0.06)
        self.assertFalse(throttled_again)
        self.assertEqual(repeated_rate, 14)

        await limiter.report_success()
        self.assertEqual(limiter.current_rate, 14)

        await limiter.acquire()
        self.assertGreaterEqual(time.monotonic() - started_at, 0.05)


    async def test_free_broadcast_rate_has_a_hard_safety_ceiling(self):
        import handlers.broadcast as broadcast

        self.assertLessEqual(broadcast._FREE_RATE_LIMITER.rate, 28)


if __name__ == "__main__":
    unittest.main()
