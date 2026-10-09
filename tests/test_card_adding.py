import re
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    _database_caption,
    _handle_media_update,
    is_allowed_add_chat,
)


class _FakeAnimeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.last_update = None

    async def update_one(self, query, update, upsert=False):
        self.last_update = {
            "query": dict(query),
            "update": update,
            "upsert": upsert,
        }
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def find_one(self, query, projection=None):
        def matches(doc):
            for key, value in query.items():
                if key == "normalizedName":
                    actual = str(doc.get(key, "")).lower()
                    if isinstance(value, str):
                        expected = value.lower()
                        if actual != expected:
                            return False
                    else:
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

    async def test_no_duplicate_when_only_target_matches(self):
        db = _FakeAnimeDB({
            "photos": _FakeAnimeCollection([
                {"cardId": "25", "fileUniqueId": "same-file", "name": "Target",
                 "normalizedName": "acheron", "anime": "Honkai Star Rail [🎮]"},
            ]),
            "limited_cards": _FakeAnimeCollection(),
        })
        with patch("utils.card_adding.get_db", return_value=db):
            media = await card_adding.find_duplicate_media("same-file", "25")
            name = await card_adding.find_possible_duplicate(
                "Acheron", "Honkai Star Rail [🎮]", "25"
            )
        self.assertIsNone(media)
        self.assertIsNone(name)

class ExplicitUpdateHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_target_comes_from_session_and_is_not_allocated(self):
        import handlers.photo_add as photo_add_module

        original_pending = dict(photo_add_module._PENDING_UPDATES)
        photo_add_module._PENDING_UPDATES.clear()
        key = (500, -100123)
        photo_add_module._PENDING_UPDATES[key] = {
            "created": time.time(),
            "user_id": 500,
            "chat_id": -100123,
            "card_id": "25",
            "collection_name": "photos",
        }
        db = _FakeAnimeDB({
            "photos": _FakeAnimeCollection([{"cardId": "25", "name": "Old Name"}]),
            "limited_cards": _FakeAnimeCollection(),
        })
        user = SimpleNamespace(id=500)
        message = SimpleNamespace(chat_id=-100123, reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_message=message)
        save = AsyncMock(return_value=(True, "Updated"))

        try:
            with (
                patch("handlers.photo_add.get_db", return_value=db),
                patch("handlers.photo_add.canonical_anime", new=AsyncMock(return_value="New Anime")),
                patch("handlers.photo_add.anime_catalog_exists", new=AsyncMock(return_value=False)),
                patch("handlers.photo_add.add_anime_to_catalog", new=AsyncMock(return_value="New Anime")) as add_anime,
                patch("handlers.photo_add.send_card_action_log", new=AsyncMock(return_value=True)) as action_log,
                patch("handlers.photo_add._save_card", new=save),
            ):
                await _handle_media_update(
                    update,
                    SimpleNamespace(),
                    "/update Acheron | Lg | New Anime",
                    {"mediaType": "photo", "fileId": "new-file", "fileUniqueId": "new-unique"},
                )

            self.assertEqual(save.await_count, 1)
            args, kwargs = save.await_args
            parsed = args[2]
            self.assertEqual(parsed["cardId"], "25")
            self.assertTrue(parsed["_cardIdProvided"])
            self.assertTrue(kwargs["update_only"])
            self.assertEqual(kwargs["expected_collection"], "photos")
            self.assertNotIn(key, photo_add_module._PENDING_UPDATES)
            message.reply_text.assert_awaited_once()
            add_anime.assert_awaited_once_with("New Anime", 500)
            action_log.assert_awaited_once()
            log_args, log_kwargs = action_log.await_args
            self.assertEqual(log_args[1], "Card Updated")
            self.assertEqual(log_args[3]["Old Anime"], "")
            self.assertEqual(log_args[3]["New Anime"], "New Anime")
        finally:
            photo_add_module._PENDING_UPDATES.clear()
            photo_add_module._PENDING_UPDATES.update(original_pending)


class SaveCardUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_update_preserves_original_adder_and_targets_same_document(self):
        import handlers.photo_add as photo_add_module

        original = {
            "_id": "mongo-target",
            "cardId": "25",
            "name": "Old Name",
            "normalizedName": "old name",
            "rarity": "Legendary",
            "anime": "Genshin Impact",
            "fileId": "old-file",
            "fileUniqueId": "old-unique",
            "mediaType": "photo",
            "storageChatId": -100555,
            "storageMessageId": 77,
            "storageCaption": "old archive caption",
            "addedBy": 111,
        }
        photos = _FakeAnimeCollection([original])
        db = _FakeAnimeDB({
            "photos": photos,
            "limited_cards": _FakeAnimeCollection(),
        })
        user = SimpleNamespace(id=500)
        parsed = {
            "cardId": "25",
            "name": "Acheron",
            "normalizedName": "acheron",
            "rarity": "Legendary",
            "anime": "Genshin Impact",
            "_cardIdProvided": True,
            "_animeProvided": True,
            "_rarityProvided": True,
        }
        media = {
            "mediaType": "photo",
            "fileId": "new-file",
            "fileUniqueId": "new-unique",
            "mimeType": "",
            "fileName": "",
        }
        storage = {
            "storageChatId": -100555,
            "storageMessageId": 77,
            "fileId": "new-archive-file",
            "fileUniqueId": "new-archive-unique",
            "mediaType": "photo",
        }

        with (
            patch("handlers.photo_add.get_db", return_value=db),
            patch("handlers.photo_add.find_duplicate_media", new=AsyncMock(return_value=None)),
            patch("handlers.photo_add.find_possible_duplicate", new=AsyncMock(return_value=None)),
            patch("handlers.photo_add._edit_card_database_message", new=AsyncMock(return_value=storage)),
            patch("handlers.photo_add.upsert_card", new=AsyncMock()),
            patch("handlers.photo_add.sync_counter_at_least", new=AsyncMock()),
            patch("handlers.photo_add.mention_user", return_value="@adder"),
        ):
            ok, result = await photo_add_module._save_card(
                SimpleNamespace(), user, parsed, media,
                update_only=True, expected_collection="photos",
            )

        self.assertTrue(ok, result)
        self.assertIn("Card Update", result)
        self.assertIsNotNone(photos.last_update)
        self.assertEqual(photos.last_update["query"]["cardId"], "25")
        self.assertEqual(photos.last_update["query"]["_id"], "mongo-target")
        self.assertFalse(photos.last_update["upsert"])
        saved = photos.last_update["update"]["$set"]
        self.assertEqual(saved["addedBy"], 111)
        self.assertEqual(saved["updatedBy"], 500)
        self.assertIn("Updated By", saved["storageCaption"])


class UpdateTargetBindingTest(unittest.TestCase):
    def test_explicit_update_archive_caption_identifies_updater(self):
        adder = SimpleNamespace(id=500)
        parsed = {
            "name": "Acheron",
            "cardId": "25",
            "rarity": "Legendary",
            "anime": "Honkai Star Rail",
        }
        with patch("handlers.photo_add.mention_user", return_value="@adder"):
            explicit_update = _database_caption("Update", parsed, adder, updated_by=True)
            normal_add = _database_caption("Saved", parsed, adder)
        self.assertIn("Updated By", explicit_update)
        self.assertIn("Updater ID", explicit_update)
        self.assertNotIn("Added By", explicit_update)
        self.assertIn("Added By", normal_add)
        self.assertIn("Adder ID", normal_add)

    def test_update_only_write_filter_binds_document_id(self):
        import inspect
        import handlers.photo_add as photo_add_module

        source = inspect.getsource(photo_add_module._save_card)
        self.assertIn('if update_only:', source)
        self.assertIn('document_id = existing.get("_id")', source)
        self.assertIn('update_filter["_id"] = document_id', source)
        self.assertIn('upsert=not update_only', source)

    def test_telegram_command_menu_exposes_update(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('BotCommand("update", "Update an existing card by ID")', source)


class CanonicalAnimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_canonical_name_wins_over_card_variant(self):
        original_cache = dict(card_adding._ANIME_CACHE)
        try:
            card_adding._ANIME_CACHE.clear()
            db = _FakeAnimeDB({
                "animes": _FakeAnimeCollection([
                    {"normalizedName": "genshin impact", "name": "Genshin Impact"}
                ]),
                "photos": _FakeAnimeCollection([
                    {"anime": "Genshin Impact [🎮]"}
                ]),
            })
            with patch("utils.card_adding.get_db", return_value=db):
                self.assertEqual(
                    await canonical_anime("Genshin Impact"),
                    "Genshin Impact",
                )
        finally:
            card_adding._ANIME_CACHE.clear()
            card_adding._ANIME_CACHE.update(original_cache)

    async def test_catalog_canonical_name_is_used_when_card_has_no_value(self):
        original_cache = dict(card_adding._ANIME_CACHE)
        try:
            card_adding._ANIME_CACHE.clear()
            db = _FakeAnimeDB({
                "animes": _FakeAnimeCollection([
                    {"normalizedName": "honkai star rail", "name": "Honkai Star Rail"}
                ]),
                "photos": _FakeAnimeCollection(),
            })
            with patch("utils.card_adding.get_db", return_value=db):
                self.assertEqual(
                    await canonical_anime("Honkai Star Rail"),
                    "Honkai Star Rail",
                )
        finally:
            card_adding._ANIME_CACHE.clear()
            card_adding._ANIME_CACHE.update(original_cache)

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
