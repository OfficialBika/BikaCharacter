import unittest
from types import SimpleNamespace

from utils.card_adding import (
    add_anime_to_catalog,
    list_anime_catalog_page,
    normalize_add_rarity,
    rarity_aliases,
    search_anime_catalog,
)
from utils.parser import parse_add_caption
from handlers.add_help import ADD_HELP_TEXT
from handlers.photo_add import (
    _addmode_keyboard,
    _addmode_text,
    is_allowed_add_chat,
)


class CardAddingParserTest(unittest.TestCase):
    def test_short_rarity_codes(self):
        aliases = rarity_aliases()
        self.assertEqual(normalize_add_rarity("Lg"), aliases["lg"])
        self.assertEqual(normalize_add_rarity("Co"), aliases["co"])
        self.assertEqual(normalize_add_rarity("Su"), aliases["su"])

    def test_full_rarity_names_remain_supported(self):
        parsed = parse_add_caption("/add Yelan | Legendary | Genshin Impact")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "Yelan")
        self.assertTrue(parsed["_rarityProvided"])
        self.assertTrue(parsed["_animeProvided"])

    def test_add_mode_single_name(self):
        parsed = parse_add_caption("/add Yelan")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "Yelan")
        self.assertIsNone(parsed["rarity"])
        self.assertFalse(parsed["_animeProvided"])
        self.assertFalse(parsed["_rarityProvided"])

    def test_invalid_rarity_is_not_treated_as_missing_field(self):
        parsed = parse_add_caption("/add Yelan | NotARarity | Genshin Impact")
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["rarity"])
        self.assertTrue(parsed["_rarityProvided"])

    def test_add_help_covers_core_commands(self):
        self.assertIn("/addmode", ADD_HELP_TEXT)
        self.assertIn("/addanime", ADD_HELP_TEXT)
        self.assertIn("/addhelp", ADD_HELP_TEXT)
        self.assertIn("Update Existing", ADD_HELP_TEXT)
        self.assertIn("Create New", ADD_HELP_TEXT)
        self.assertIn("Anime Search", ADD_HELP_TEXT)
        self.assertIn("Add New", ADD_HELP_TEXT)
        self.assertIn("Back", ADD_HELP_TEXT)
        self.assertIn("Next", ADD_HELP_TEXT)
        self.assertIn("DM / Private Chat", ADD_HELP_TEXT)

    def test_anime_catalog_helper_is_available(self):
        self.assertTrue(callable(add_anime_to_catalog))

    def test_explicit_numeric_id_is_preserved(self):
        parsed = parse_add_caption("/add 123 | Yelan | Lg | Genshin Impact")
        self.assertEqual(parsed["cardId"], "123")
        self.assertTrue(parsed["_cardIdProvided"])
        self.assertEqual(parsed["name"], "Yelan")

    def test_private_chat_is_not_valid_add_source(self):
        update = SimpleNamespace(effective_chat=SimpleNamespace(type="private", id=1))
        self.assertFalse(is_allowed_add_chat(update))

    def test_configured_group_is_valid_add_source(self):
        import handlers.photo_add as photo_add_module

        original = list(photo_add_module.ADDER_GROUP_IDS)
        try:
            photo_add_module.ADDER_GROUP_IDS = [-123456789]
            update = SimpleNamespace(effective_chat=SimpleNamespace(type="supergroup", id=-123456789))
            self.assertTrue(is_allowed_add_chat(update))
        finally:
            photo_add_module.ADDER_GROUP_IDS = original

    def test_unconfigured_group_is_not_valid_add_source(self):
        update = SimpleNamespace(effective_chat=SimpleNamespace(type="supergroup", id=-999999999))
        self.assertFalse(is_allowed_add_chat(update))

    def test_addmode_panel_text_shape(self):
        text = _addmode_text("Genshin Impact [🎮]", "Legendary")
        self.assertIn("⚙️ <b>CARD ADD MODE</b>", text)
        self.assertIn("Anime: <b>Genshin Impact [🎮]</b>", text)
        self.assertIn("Rarity: <b>Legendary</b>", text)
        self.assertIn("You can also use: <code>/addmode Anime | Lg</code>", text)

    def test_addmode_panel_has_three_actions(self):
        markup = _addmode_keyboard(12345, "deadbeef")
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertEqual(len(markup.inline_keyboard[0]), 3)

        rarity, anime, close = markup.inline_keyboard[0]
        self.assertEqual(rarity.callback_data, "addmode:12345:rarity")
        self.assertEqual(
            anime.switch_inline_query_current_chat,
            "animepick:deadbeef ",
        )
        self.assertEqual(close.callback_data, "addmode:12345:close")
        self.assertEqual(rarity.api_kwargs.get("style"), "primary")
        self.assertEqual(anime.api_kwargs.get("style"), "success")
        self.assertEqual(close.api_kwargs.get("style"), "danger")

    def test_anime_selector_helpers_are_available(self):
        self.assertTrue(callable(list_anime_catalog_page))
        self.assertTrue(callable(search_anime_catalog))


if __name__ == "__main__":
    unittest.main()
