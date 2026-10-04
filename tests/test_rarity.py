import unittest

from config import (
    DROP_100_RARITY,
    DROP_20_RARITY,
    DROP_300_RARITY,
    DROP_400_RARITY,
    DROP_500_PRIMARY_RARITY,
    DROP_500_SECONDARY_RARITY,
    RARITY_ORDER,
)
from utils.rarity import (
    get_rarity_button_emoji,
    get_rarity_custom_emoji_id,
    get_rarity_emoji,
    get_scheduled_drop_rarity,
)


class RarityTests(unittest.TestCase):
    def test_all_rarities_have_resolvable_emoji_helpers(self):
        self.assertEqual(len(RARITY_ORDER), 10)
        for rarity in RARITY_ORDER:
            self.assertIsInstance(get_rarity_emoji(rarity), str)
            self.assertIsInstance(get_rarity_button_emoji(rarity), str)
            self.assertIsInstance(get_rarity_custom_emoji_id(rarity), str)

    def test_custom_emoji_markup_is_well_formed_when_configured(self):
        import utils.rarity as rarity_module

        target = RARITY_ORDER[-1]
        old_id = rarity_module._RARITY_CUSTOM_EMOJI_IDS.get(target, "")
        old_fallback = rarity_module._RARITY_FALLBACK_EMOJIS.get(target, "🎴")
        try:
            rarity_module._RARITY_CUSTOM_EMOJI_IDS[target] = "1234567890123456789"
            rarity_module._RARITY_FALLBACK_EMOJIS[target] = "🔵"

            self.assertEqual(
                get_rarity_emoji(target),
                '<tg-emoji emoji-id="1234567890123456789">🔵</tg-emoji>',
            )
            self.assertEqual(get_rarity_custom_emoji_id(target), "1234567890123456789")
            self.assertEqual(get_rarity_button_emoji(target), "🔵")
        finally:
            rarity_module._RARITY_CUSTOM_EMOJI_IDS[target] = old_id
            rarity_module._RARITY_FALLBACK_EMOJIS[target] = old_fallback

    def test_configured_milestone_schedule(self):
        self.assertEqual(get_scheduled_drop_rarity(20), DROP_20_RARITY)
        self.assertEqual(get_scheduled_drop_rarity(100), DROP_100_RARITY)
        self.assertEqual(get_scheduled_drop_rarity(300), DROP_300_RARITY)
        self.assertEqual(get_scheduled_drop_rarity(400), DROP_400_RARITY)

    def test_five_hundred_has_highest_priority(self):
        value = get_scheduled_drop_rarity(500)
        self.assertIn(value, {DROP_500_PRIMARY_RARITY, DROP_500_SECONDARY_RARITY})

    def test_non_milestone_uses_base_rarities(self):
        for drop_number in (1, 19, 21, 99, 101, 299, 301, 399, 401, 499):
            self.assertIn(get_scheduled_drop_rarity(drop_number), {
                "Common",
                "Uncommon",
                "Rare",
            })


if __name__ == "__main__":
    unittest.main()
