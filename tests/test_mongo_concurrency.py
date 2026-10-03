from __future__ import annotations

import asyncio
import os
import unittest
import uuid

from pymongo import AsyncMongoClient


class MongoConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Optional real-Mongo race tests.

    They run only when MONGODB_TEST_URI is explicitly configured, so normal CI
    never touches production data.
    """

    async def asyncSetUp(self):
        uri = os.getenv("MONGODB_TEST_URI", "").strip()
        if not uri:
            self.skipTest("MONGODB_TEST_URI not configured")
        self.client = AsyncMongoClient(uri, serverSelectionTimeoutMS=3000)
        await self.client.admin.command("ping")
        self.db = self.client[f"bika_character_concurrency_test_{uuid.uuid4().hex}"]

    async def asyncTearDown(self):
        if getattr(self, "client", None):
            await self.client.drop_database(self.db.name)
            await self.client.close()

    async def test_claim_first_writer_wins(self):
        group_id = -9000000001
        await self.db.groups.insert_one({
            "groupId": group_id,
            "activeDrop": {"cardId": "race-1", "isClaimed": False},
        })

        async def contender(user_id: int):
            return await self.db.groups.find_one_and_update(
                {
                    "groupId": group_id,
                    "activeDrop.cardId": "race-1",
                    "activeDrop.isClaimed": False,
                },
                {"$set": {"activeDrop.isClaimed": True, "activeDrop.claimedByUserId": user_id}},
            )

        results = await asyncio.gather(*(contender(i) for i in range(100)))
        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_gift_quantity_never_goes_negative(self):
        sender, receiver = 1001, 1002
        await self.db.users.insert_many([
            {"userId": sender, "cards": [{"cardId": "gift-1", "count": 10}]},
            {"userId": receiver, "cards": []},
        ])

        async def gift_once():
            result = await self.db.users.update_one(
                {
                    "userId": sender,
                    "cards": {"$elemMatch": {"cardId": "gift-1", "count": {"$gte": 1}}},
                },
                {"$inc": {"cards.$[card].count": -1}},
                array_filters=[{"card.cardId": "gift-1"}],
            )
            if result.modified_count != 1:
                return False
            await self.db.users.update_one(
                {"userId": receiver, "cards.cardId": {"$ne": "gift-1"}},
                {"$push": {"cards": {"cardId": "gift-1", "count": 1}}},
            )
            return True

        results = await asyncio.gather(*(gift_once() for _ in range(100)))
        self.assertEqual(sum(results), 10)
        sender_doc = await self.db.users.find_one({"userId": sender})
        self.assertEqual(sender_doc["cards"][0]["count"], 0)

    async def test_transfer_quantity_never_goes_negative(self):
        source, target = 2001, 2002
        await self.db.users.insert_many([
            {"userId": source, "cards": [{"cardId": "transfer-1", "count": 7}]},
            {"userId": target, "cards": []},
        ])

        async def transfer_once():
            result = await self.db.users.update_one(
                {
                    "userId": source,
                    "cards": {"$elemMatch": {"cardId": "transfer-1", "count": {"$gte": 1}}},
                },
                {"$inc": {"cards.$[card].count": -1}},
                array_filters=[{"card.cardId": "transfer-1"}],
            )
            if result.modified_count != 1:
                return False
            await self.db.users.update_one(
                {"userId": target, "cards.cardId": {"$ne": "transfer-1"}},
                {"$push": {"cards": {"cardId": "transfer-1", "count": 1}}},
            )
            return True

        results = await asyncio.gather(*(transfer_once() for _ in range(100)))
        self.assertEqual(sum(results), 7)
        source_doc = await self.db.users.find_one({"userId": source})
        self.assertEqual(source_doc["cards"][0]["count"], 0)


if __name__ == "__main__":
    unittest.main()
