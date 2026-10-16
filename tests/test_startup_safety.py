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


if __name__ == "__main__":
    unittest.main()
