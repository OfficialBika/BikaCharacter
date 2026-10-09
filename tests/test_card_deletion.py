from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import admin


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self, documents=None):
        self.documents = [dict(doc) for doc in (documents or [])]
        self.delete_calls = []

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$regex" in expected:
                    flags = re.I if expected.get("$options") == "i" else 0
                    if not re.search(expected["$regex"], str(actual or ""), flags):
                        return False
                elif "$in" in expected:
                    if actual not in expected["$in"]:
                        return False
                elif "$ne" in expected:
                    if actual == expected["$ne"]:
                        return False
                else:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, projection=None):
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return _Cursor(
            dict(document)
            for document in self.documents
            if self._matches(document, query)
        )

    async def delete_one(self, query):
        self.delete_calls.append(dict(query))
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def update_many(self, query, update):
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Database:
    def __init__(self, **collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _card(card_id="25", name="Acheron", anime="Honkai Star Rail"):
    return {
        "_id": f"mongo-{card_id}",
        "cardId": card_id,
        "name": name,
        "normalizedName": name.casefold(),
        "rarity": "Legendary",
        "anime": anime,
        "mediaType": "photo",
        "fileId": "",
        "fileUniqueId": f"unique-{card_id}",
        "mimeType": "image/jpeg",
        "fileName": "",
        "storageChatId": None,
        "storageMessageId": None,
        "updatedAt": None,
    }


class ConfirmedCardDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_pending = dict(admin._PENDING_CARD_DELETIONS)
        admin._PENDING_CARD_DELETIONS.clear()
        self.owner = SimpleNamespace(id=9001, username="owner")
        self.message = SimpleNamespace(
            chat_id=-100123,
            reply_text=AsyncMock(),
        )
        self.update = SimpleNamespace(
            effective_user=self.owner,
            effective_message=self.message,
        )

    def tearDown(self):
        admin._PENDING_CARD_DELETIONS.clear()
        admin._PENDING_CARD_DELETIONS.update(self.original_pending)

    @staticmethod
    def _buttons(markup):
        return [button for row in markup.inline_keyboard for button in row]

    async def test_delete_card_only_previews_until_confirmed(self):
        target = _card()
        photos = _Collection([target])
        db = _Database(photos=photos, limited_cards=_Collection())
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_card_cmd(
                self.update,
                SimpleNamespace(args=["25"]),
            )

        self.assertEqual(len(photos.documents), 1)
        self.assertEqual(photos.delete_calls, [])
        self.message.reply_text.assert_awaited_once()
        text, kwargs = self.message.reply_text.await_args.args[0], self.message.reply_text.await_args.kwargs
        self.assertIn("DELETE CARD CONFIRMATION", text)
        buttons = self._buttons(kwargs["reply_markup"])
        callbacks = {button.text: button.callback_data for button in buttons}
        self.assertTrue(any(value.startswith("carddel:confirm:") for value in callbacks.values()))
        self.assertTrue(any(value.startswith("carddel:cancel:") for value in callbacks.values()))
        self.assertEqual(len(admin._PENDING_CARD_DELETIONS), 1)

    async def test_cancel_card_deletion_never_calls_delete_one(self):
        target = _card()
        photos = _Collection([target])
        db = _Database(photos=photos, limited_cards=_Collection())
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_card_cmd(
                self.update,
                SimpleNamespace(args=["25"]),
            )

        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        cancel = next(
            button.callback_data
            for button in self._buttons(markup)
            if button.callback_data.startswith("carddel:cancel:")
        )
        query = SimpleNamespace(
            data=cancel,
            from_user=self.owner,
            message=SimpleNamespace(chat_id=self.message.chat_id),
            answer=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_confirmation_callback(
                SimpleNamespace(callback_query=query),
                SimpleNamespace(),
            )

        self.assertEqual(photos.delete_calls, [])
        self.assertEqual(len(photos.documents), 1)
        self.assertEqual(admin._PENDING_CARD_DELETIONS, {})

    async def test_confirm_refuses_card_changed_after_preview(self):
        target = _card()
        photos = _Collection([target])
        db = _Database(photos=photos, limited_cards=_Collection())
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_card_cmd(
                self.update,
                SimpleNamespace(args=["25"]),
            )

        photos.documents[0]["name"] = "Changed after preview"
        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        confirm = next(
            button.callback_data
            for button in self._buttons(markup)
            if button.callback_data.startswith("carddel:confirm:")
        )
        query = SimpleNamespace(
            data=confirm,
            from_user=self.owner,
            message=SimpleNamespace(chat_id=self.message.chat_id),
            answer=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_confirmation_callback(
                SimpleNamespace(callback_query=query),
                SimpleNamespace(),
            )

        self.assertEqual(photos.delete_calls, [])
        self.assertEqual(len(photos.documents), 1)
        self.assertEqual(photos.documents[0]["name"], "Changed after preview")

    async def test_confirm_deletes_only_after_owner_clicks_confirm(self):
        target = _card()
        photos = _Collection([target])
        db = _Database(photos=photos, limited_cards=_Collection(), users=_Collection(), groups=_Collection())
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_card_cmd(
                self.update,
                SimpleNamespace(args=["25"]),
            )

        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        confirm = next(
            button.callback_data
            for button in self._buttons(markup)
            if button.callback_data.startswith("carddel:confirm:")
        )
        query = SimpleNamespace(
            data=confirm,
            from_user=self.owner,
            message=SimpleNamespace(chat_id=self.message.chat_id),
            answer=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
            patch("handlers.admin.delete_hot_lookup_card", new=AsyncMock(return_value=1)),
            patch("handlers.admin.send_card_action_log", new=AsyncMock(return_value=True)) as action_log,
        ):
            await admin.delete_confirmation_callback(
                SimpleNamespace(callback_query=query),
                SimpleNamespace(),
            )

        self.assertEqual(len(photos.documents), 0)
        self.assertEqual(len(photos.delete_calls), 1)
        action_log.assert_awaited_once()
        self.assertEqual(action_log.await_args.args[1], "Card Deleted")


class ConfirmedAnimeDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_pending = dict(admin._PENDING_CARD_DELETIONS)
        admin._PENDING_CARD_DELETIONS.clear()
        self.owner = SimpleNamespace(id=9001, username="owner")
        self.message = SimpleNamespace(
            chat_id=-100123,
            reply_text=AsyncMock(),
        )
        self.update = SimpleNamespace(
            effective_user=self.owner,
            effective_message=self.message,
        )

    def tearDown(self):
        admin._PENDING_CARD_DELETIONS.clear()
        admin._PENDING_CARD_DELETIONS.update(self.original_pending)

    async def test_delete_anime_previews_related_cards_and_requires_confirmation(self):
        cards = [_card(str(number), f"Character {number}", "Genshin Impact") for number in range(1, 8)]
        photos = _Collection(cards)
        animes = _Collection([{
            "_id": "genshin-impact",
            "name": "Genshin Impact",
            "normalizedName": "genshin impact",
        }])
        db = _Database(
            photos=photos,
            limited_cards=_Collection(),
            animes=animes,
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_anime_cmd(
                self.update,
                SimpleNamespace(args=["Genshin", "Impact"]),
            )

        self.assertEqual(len(photos.documents), 7)
        self.assertEqual(photos.delete_calls, [])
        self.message.reply_text.assert_awaited_once()
        text, kwargs = self.message.reply_text.await_args.args[0], self.message.reply_text.await_args.kwargs
        self.assertIn("DELETE ANIME CONFIRMATION", text)
        self.assertIn("Related cards:</b> <code>7</code>", text)
        self.assertIn("Character 1", text)
        self.assertIn("Page:</b> <code>1/2</code>", text)
        buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons]
        self.assertTrue(any(value.startswith("animedel:page:") for value in callbacks))
        self.assertTrue(any(value.startswith("animedel:confirm:") for value in callbacks))
        self.assertTrue(any(value.startswith("animedel:cancel:") for value in callbacks))
        self.assertEqual(len(admin._PENDING_CARD_DELETIONS), 1)


    async def test_confirm_deletes_related_cards_and_catalog_entry(self):
        cards = [_card(str(number), f"Character {number}", "Genshin Impact") for number in range(1, 4)]
        photos = _Collection(cards)
        animes = _Collection([{
            "_id": "genshin-impact",
            "name": "Genshin Impact",
            "normalizedName": "genshin impact",
        }])
        db = _Database(
            photos=photos,
            limited_cards=_Collection(),
            animes=animes,
            users=_Collection(),
            groups=_Collection(),
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
        ):
            await admin.delete_anime_cmd(
                self.update,
                SimpleNamespace(args=["Genshin", "Impact"]),
            )

        markup = self.message.reply_text.await_args.kwargs["reply_markup"]
        confirm = next(
            button.callback_data
            for button in self._buttons(markup)
            if button.callback_data.startswith("animedel:confirm:")
        )
        query = SimpleNamespace(
            data=confirm,
            from_user=self.owner,
            message=SimpleNamespace(chat_id=self.message.chat_id),
            answer=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        with (
            patch("handlers.admin.get_db", return_value=db),
            patch("handlers.admin.is_owner", return_value=True),
            patch("handlers.admin.delete_hot_lookup_card", new=AsyncMock(return_value=1)),
            patch("handlers.admin.send_card_action_log", new=AsyncMock(return_value=True)) as action_log,
        ):
            await admin.delete_confirmation_callback(
                SimpleNamespace(callback_query=query),
                SimpleNamespace(),
            )

        self.assertEqual(len(photos.documents), 0)
        self.assertEqual(len(animes.documents), 0)
        self.assertEqual(len(photos.delete_calls), 3)
        action_log.assert_awaited_once()
        self.assertEqual(action_log.await_args.args[1], "Anime Deleted")


if __name__ == "__main__":
    unittest.main()
