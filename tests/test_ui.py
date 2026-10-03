import unittest

from handlers.harem import get_harem_cards_for_view
from handlers.hmode import build_hmode_menu
from handlers.rankings import _leaderboard_keyboard


class UIHelpersTest(unittest.TestCase):
    def test_harem_compact_mode(self):
        user = {
            "haremSort": "compact",
            "haremView": "compact",
            "haremRarity": "",
            "cards": [
                {"cardId": "10", "name": "B", "rarity": "Rare", "count": 1},
                {"cardId": "2", "name": "A", "rarity": "Common", "count": 2},
            ],
        }
        cards, mode, rarity = get_harem_cards_for_view(user)
        self.assertEqual(mode, "compact")
        self.assertEqual(rarity, "")
        self.assertEqual(len(cards), 2)

    def test_hmode_has_three_view_choices(self):
        markup = build_hmode_menu(12345)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("hmode:12345:anime", callbacks)
        self.assertIn("hmode:12345:rarity_menu", callbacks)
        self.assertIn("hmode:12345:compact", callbacks)
        self.assertIn("hmode:12345:close", callbacks)

    def test_leaderboard_keyboard_is_user_scoped(self):
        markup = _leaderboard_keyboard(12345, "global")
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertTrue(callbacks)
        self.assertTrue(all(value.startswith("rank:12345:") for value in callbacks))
        self.assertIn("rank:12345:global", callbacks)
        self.assertIn("rank:12345:today", callbacks)
        self.assertIn("rank:12345:week", callbacks)
        self.assertIn("rank:12345:month", callbacks)
        self.assertIn("rank:12345:group", callbacks)


if __name__ == "__main__":
    unittest.main()
