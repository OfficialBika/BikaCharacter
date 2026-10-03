import unittest

from utils.card_adding import rarity_aliases, normalize_add_rarity, add_anime_to_catalog
from utils.parser import parse_add_caption
from handlers.add_help import ADD_HELP_TEXT


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

    def test_add_help_covers_core_commands(self):
        self.assertIn("/addmode", ADD_HELP_TEXT)
        self.assertIn("/addanime", ADD_HELP_TEXT)
        self.assertIn("/addhelp", ADD_HELP_TEXT)
        self.assertIn("Update Existing", ADD_HELP_TEXT)
        self.assertIn("Create New", ADD_HELP_TEXT)

    def test_anime_catalog_helper_is_available(self):
        self.assertTrue(callable(add_anime_to_catalog))

    def test_explicit_numeric_id_is_preserved(self):
        parsed = parse_add_caption("/add 123 | Yelan | Lg | Genshin Impact")
        self.assertEqual(parsed["cardId"], "123")
        self.assertTrue(parsed["_cardIdProvided"])
        self.assertEqual(parsed["name"], "Yelan")


if __name__ == "__main__":
    unittest.main()
