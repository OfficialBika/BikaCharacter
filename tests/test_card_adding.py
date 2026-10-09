import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import utils.card_adding as card_adding
from utils.card_adding import (
    add_anime_to_catalog,
    canonical_anime,
    list_anime_catalog_page,
    normalize_add_rarity,
    rarity_aliases,
    search_anime_catalog,
)
from utils.parser import parse_add_caption, parse_update_caption
from handlers.add_help import ADD_HELP_TEXT
from handlers.photo_add import (
    _addmode_keyboard,
    _addmode_text,
    _anime_article_result,
    _anime_display,
    is_allowed_add_chat,
)


class _FakeAnimeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    async def find_one(self, query, projection=None):
        def matches(doc):
            for key, value in query.items():
                if key == "normalizedName":
                    if doc.get(key) != value:
                        return False
                elif key == "fileUniqueId":
                    if doc.get(key) != value:
                        return False
                elif key == "cardId":
                    if isinstance(value, dict) and "$ne" in value:
                        if str(doc.get(key, "")) == str(value["$ne"]):
                            return False
                    elif doc.get(key) != value:
                        return False
                elif key == "normalizedName":
                    if doc.get(key) != value:
                        return False
                elif key == "anime":
                    spec = value
                    pattern = spec.get("$regex", "")
                    flags = re.I if spec.get("$options") == "i" else 0
                    if not re.search(pattern, str(doc.get("anime", "")), flags=flags):
                        return False
                else:
                    return False
            return True

        return next((doc for doc in self.documents if matches(doc)), None)


class _FakeAnimeDB:
    def __init__(self, collections=None):
        self.collections = dict(collections or {})

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeAnimeCollection())


class DuplicateLookupTest(unittest.IsolatedAsyncioTestCase):
    async def test_media_duplicate_skips_target_but_finds_another_card(self):
        db = _FakeAnimeDB({
            "photos": _FakeAnimeCollection([
                {"cardId": "25", "fileUniqueId": "same-file", "name": "Target"},
                {"cardId": "26", "fileUniqueId": "same-file", "name": "Other"},
            ]),
            "limited_cards": _FakeAnimeCollection(),
        })
        with patch("utils.card_adding.get_db", return_value=db):
            duplicate = await card_adding.find_duplicate_media("same-file", "25")
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["cardId"], "26")
        self.assertEqual(duplicate["collection"], "photos")

    async def test_name_duplicate_skips_target_but_finds_another_card(self):
        db = _FakeAnimeDB({
            "photos": _FakeAnimeCollection([
                {
                    "cardId": "25", "normalizedName": "acheron",
                    "anime": "Honkai Star Rail [🎮]", "name": "Acheron",
                },
                {
                    "cardId": "26", "normalizedName": "acheron",
                    "anime": "Honkai Star Rail", "name": "Acheron",
                },
            ]),
            "limited_cards": _FakeAnimeCollection(),
        })
        with patch("utils.card_adding.get_db", return_value=db):
            duplicate = await card_adding.find_possible_duplicate(
                "Acheron", "Honkai Star Rail [🎮]", "25"
            )
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["cardId"], "26")

class CanonicalAnimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_anime_preserves_only_stored_game_marker(self):
        original_cache = dict(card_adding._ANIME_CACHE)
        try:
            card_adding._ANIME_CACHE.clear()
            marked_db = _FakeAnimeDB({
                "photos": _FakeAnimeCollection([{"anime": "Genshin Impact [🎮]"}])
            })
            with patch("utils.card_adding.get_db", return_value=marked_db):
                self.assertEqual(
                    await canonical_anime("Genshin Impact"),
                    "Genshin Impact [🎮]",
                )

            card_adding._ANIME_CACHE.clear()
            plain_db = _FakeAnimeDB({
                "photos": _FakeAnimeCollection([{"anime": "Honkai Star Rail"}])
            })
            with patch("utils.card_adding.get_db", return_value=plain_db):
                self.assertEqual(
                    await canonical_anime("Honkai Star Rail [🎮]"),
                    "Honkai Star Rail",
                )

            card_adding._ANIME_CACHE.clear()
            empty_db = _FakeAnimeDB()
            with patch("utils.card_adding.get_db", return_value=empty_db):
                self.assertEqual(
                    await canonical_anime("Unknown Anime [🎮]"),
                    "Unknown Anime",
                )
        finally:
            card_adding._ANIME_CACHE.clear()
            card_adding._ANIME_CACHE.update(original_cache)


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

    def test_update_caption_requires_explicit_name_rarity_and_anime(self):
        parsed = parse_update_caption("/update Acheron | Lg | Honkai Star Rail")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "Acheron")
        self.assertEqual(parsed["rarity"], normalize_add_rarity("Lg"))
        self.assertEqual(parsed["anime"], "Honkai Star Rail")
        self.assertFalse(parsed["_cardIdProvided"])
        self.assertIsNone(parse_update_caption("/update 25"))
        self.assertIsNone(parse_update_caption("/update Acheron | Lg"))
        self.assertIsNone(parse_update_caption("/add Acheron | Lg | Honkai Star Rail"))

    def test_update_caption_rejects_unknown_rarity_for_handler_validation(self):
        parsed = parse_update_caption("/update Acheron | NotARarity | Honkai Star Rail")
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed["_rarityProvided"])
        self.assertIsNone(parsed["rarity"])

    def test_anime_display_preserves_the_database_marker_exactly(self):
        self.assertEqual(_anime_display("Honkai Star Rail"), "Honkai Star Rail")
        self.assertEqual(_anime_display("Genshin Impact [🎮]"), "Genshin Impact [🎮]")

    def test_anime_inline_result_does_not_invent_game_marker(self):
        result = _anime_article_result("token", "Honkai Star Rail", "test", "noop")
        self.assertEqual(result.title, "Honkai Star Rail")
        marked = _anime_article_result("token", "Genshin Impact [🎮]", "test", "noop")
        self.assertEqual(marked.title, "Genshin Impact [🎮]")

    def test_invalid_rarity_is_not_treated_as_missing_field(self):
        parsed = parse_add_caption("/add Yelan | NotARarity | Genshin Impact")
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["rarity"])
        self.assertTrue(parsed["_rarityProvided"])

    def test_add_help_covers_core_commands(self):
        self.assertIn("/addmode", ADD_HELP_TEXT)
        self.assertIn("/addanime", ADD_HELP_TEXT)
        self.assertIn("/addhelp", ADD_HELP_TEXT)
        self.assertIn("/update ID", ADD_HELP_TEXT)
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
