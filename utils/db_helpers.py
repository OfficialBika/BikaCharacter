from __future__ import annotations

import random
from datetime import timedelta
from typing import Optional

from pymongo import ReturnDocument
from telegram import Chat, User

from config import DEFAULT_CHANGETIME, RARITY_ORDER, LIMITED_CARDS_COLLECTION, RARITY_COMMON_NAME, DROP_BASE_RARITIES
from database.mongodb import get_db
from utils.parser import normalized_search_name
from utils.rarity import get_rarity_exp
from utils.text import safe_chat_title, utcnow


async def ensure_user(tg_user: User | None) -> Optional[dict]:
    if not tg_user or not tg_user.id:
        return None
    db = get_db()
    now = utcnow()
    return await db.users.find_one_and_update(
        {"userId": int(tg_user.id)},
        {
            "$set": {
                "username": tg_user.username or "",
                "firstName": tg_user.first_name or "",
                "lastName": tg_user.last_name or "",
                "updatedAt": now,
            },
            "$setOnInsert": {
                "exp": 0,
                "favoriteCardId": "",
                "haremView": "default",
                "cards": [],
                "createdAt": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def ensure_user_by_id(user_id: int, username: str = "", first_name: str = "", last_name: str = "") -> dict:
    db = get_db()
    now = utcnow()
    return await db.users.find_one_and_update(
        {"userId": int(user_id)},
        {
            "$set": {
                "username": username or "",
                "firstName": first_name or "",
                "lastName": last_name or "",
                "updatedAt": now,
            },
            "$setOnInsert": {
                "exp": 0,
                "favoriteCardId": "",
                "haremView": "default",
                "cards": [],
                "createdAt": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def ensure_group(chat: Chat | None) -> Optional[dict]:
    if not chat or not chat.id:
        return None
    db = get_db()
    now = utcnow()
    return await db.groups.find_one_and_update(
        {"groupId": int(chat.id)},
        {
            "$set": {
                "title": safe_chat_title(chat),
                "username": getattr(chat, "username", "") or "",
                "updatedAt": now,
            },
            "$setOnInsert": {
                # Approve system removed: every group can use the bot immediately.
                # These legacy fields are kept only for old database compatibility.
                "isApproved": True,
                "approvedBy": 0,
                "approvedAt": None,
                "messageCount": 0,
                "totalDrops": 0,
                "changeTime": DEFAULT_CHANGETIME,
                "activeDrop": None,
                "lastSpeakerId": 0,
                "lastSpeakerCount": 0,
                "createdAt": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def is_approved_group(chat_id: int) -> bool:
    """Legacy compatibility helper. Approve system is removed; all groups are allowed."""
    return True


async def get_user_doc(user_id: int) -> Optional[dict]:
    return await get_db().users.find_one({"userId": int(user_id)})


async def get_photo_by_card_id(card_id: str) -> Optional[dict]:
    """Find a card by ID from normal photos first, then owner-only limited_cards.

    Limited cards are intentionally not used by random drop helpers below because
    all random drop queries read only from db.photos.
    """
    db = get_db()
    card_id = str(card_id)
    doc = await db.photos.find_one({"cardId": card_id})
    if doc:
        doc = dict(doc)
        doc["_sourceCollection"] = "photos"
        doc["ownerOnly"] = False
        return doc

    doc = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": card_id})
    if doc:
        doc = dict(doc)
        doc["_sourceCollection"] = LIMITED_CARDS_COLLECTION
        doc["ownerOnly"] = True
        return doc
    return None


async def get_random_photo(query: Optional[dict] = None) -> Optional[dict]:
    """Return one random photo matching query. Falls back to skip if $sample fails."""
    db = get_db()
    query = query or {}
    docs = await db.photos.aggregate([{"$match": query}, {"$sample": {"size": 1}}]).to_list(1)
    if docs:
        return docs[0]

    total = await db.photos.count_documents(query)
    if total <= 0:
        return None
    skip = random.randint(0, total - 1)
    docs = await db.photos.find(query).skip(skip).limit(1).to_list(1)
    return docs[0] if docs else None


async def get_random_photo_by_rarity(rarity: str) -> Optional[dict]:
    return await get_random_photo({"rarity": str(rarity)})


async def get_drop_photo_for_rarity(rarity: str) -> Optional[dict]:
    """Choose a card for the scheduled rarity.

    If the database has no cards for that rarity yet, fall back safely so the
    group does not lose a spawn. Normal drops fall back within DROP_BASE_RARITIES
    first, then any card. Milestone drops fall back to any card only if the target
    rarity is empty.
    """
    rarity = str(rarity or RARITY_COMMON_NAME)
    photo = await get_random_photo_by_rarity(rarity)
    if photo:
        return photo

    if rarity in DROP_BASE_RARITIES:
        # Keep normal drops within configured base rarities when possible.
        photo = await get_random_photo({"rarity": {"$in": list(DROP_BASE_RARITIES)}})
        if photo:
            return photo

    return await get_random_photo()


def public_card_snapshot(photo_doc: dict, qty: int = 1) -> dict:
    media_type = str(photo_doc.get("mediaType") or "photo").strip().lower()
    mime_type = str(photo_doc.get("mimeType") or "").strip()

    # Backward compatibility for old database records that did not store mediaType.
    if not media_type or media_type == "photo":
        if mime_type.startswith("video/"):
            media_type = "video"
        elif mime_type == "image/gif":
            media_type = "animation"
        else:
            media_type = media_type or "photo"

    return {
        "cardId": str(photo_doc.get("cardId", "")),
        "name": str(photo_doc.get("name", "")),
        "normalizedName": str(photo_doc.get("normalizedName") or normalized_search_name(photo_doc.get("name", ""))),
        "rarity": str(photo_doc.get("rarity", RARITY_COMMON_NAME)),
        "anime": str(photo_doc.get("anime", "Unknown")),
        "fileId": str(photo_doc.get("fileId", "")),
        "fileUniqueId": str(photo_doc.get("fileUniqueId", "")),
        "mediaType": media_type,
        "mimeType": mime_type,
        "fileName": str(photo_doc.get("fileName") or ""),
        "count": int(qty),
    }


async def add_card_to_user_id(user_id: int, card_doc: dict, qty: int = 1) -> dict:
    db = get_db()
    qty = max(1, int(qty or 1))
    card_id = str(card_doc.get("cardId", ""))
    card = public_card_snapshot(card_doc, qty)
    now = utcnow()

    user = await db.users.find_one({"userId": int(user_id), "cards.cardId": card_id})
    exp_inc = get_rarity_exp(card.get("rarity")) * qty
    if user:
        await db.users.update_one(
            {"userId": int(user_id), "cards.cardId": card_id},
            {"$inc": {"cards.$.count": qty, "exp": exp_inc}, "$set": {"updatedAt": now}},
        )
    else:
        await db.users.update_one(
            {"userId": int(user_id)},
            {"$push": {"cards": card}, "$inc": {"exp": exp_inc}, "$set": {"updatedAt": now}},
            upsert=False,
        )
    return await db.users.find_one({"userId": int(user_id)})


async def add_card_to_user(tg_user: User, card_doc: dict, qty: int = 1) -> dict:
    await ensure_user(tg_user)
    return await add_card_to_user_id(tg_user.id, card_doc, qty)


async def remove_card_from_user(user_id: int, card_id: str, qty: int = 1) -> dict:
    db = get_db()
    qty = max(1, int(qty or 1))
    user = await db.users.find_one({"userId": int(user_id)})
    if not user:
        return {"ok": False, "reason": "User not found."}

    cards = list(user.get("cards", []))
    idx = next((i for i, c in enumerate(cards) if str(c.get("cardId")) == str(card_id)), -1)
    if idx < 0:
        return {"ok": False, "reason": "Card not found in inventory."}
    if int(cards[idx].get("count", 0)) < qty:
        return {"ok": False, "reason": "Not enough quantity."}

    removed_snapshot = dict(cards[idx])
    cards[idx]["count"] = int(cards[idx].get("count", 0)) - qty
    if cards[idx]["count"] <= 0:
        cards.pop(idx)
        if str(user.get("favoriteCardId", "")) == str(card_id):
            user["favoriteCardId"] = ""

    await db.users.update_one(
        {"userId": int(user_id)},
        {
            "$set": {
                "cards": cards,
                "favoriteCardId": user.get("favoriteCardId", ""),
                "updatedAt": utcnow(),
            }
        },
    )
    return {"ok": True, "removedCardSnapshot": removed_snapshot, "userDoc": await db.users.find_one({"userId": int(user_id)})}


async def global_card_stats(card_id: str) -> dict:
    db = get_db()
    users = await db.users.find(
        {"cards.cardId": str(card_id)},
        {"userId": 1, "username": 1, "firstName": 1, "lastName": 1, "cards": 1},
    ).to_list(500)

    def _time_sort_value(value) -> float:
        try:
            return float(value.timestamp())
        except Exception:
            # No claim history: keep these behind users with known claim time.
            return 4102444800.0

    total_owned = 0
    catchers = []
    user_ids: list[int] = []

    for user in users:
        card = next((c for c in user.get("cards", []) if str(c.get("cardId")) == str(card_id)), None)
        if not card:
            continue

        count = int(card.get("count", 0) or 0)
        total_owned += count

        user_id = int(user.get("userId", 0) or 0)
        user_ids.append(user_id)

        catchers.append({
            "userId": user_id,
            "username": user.get("username", ""),
            "firstName": user.get("firstName", ""),
            "lastName": user.get("lastName", ""),
            "count": count,
            "reachedCountAt": None,
        })

    if user_ids:
        log_rows = await db.claim_logs.aggregate(
            [
                {
                    "$match": {
                        "cardId": str(card_id),
                        "userId": {"$in": user_ids},
                    }
                },
                {"$sort": {"createdAt": 1}},
                {
                    "$group": {
                        "_id": "$userId",
                        "claimTimes": {"$push": "$createdAt"},
                    }
                },
            ]
        ).to_list(None)

        claim_times_by_user = {
            int(row.get("_id", 0) or 0): list(row.get("claimTimes", []))
            for row in log_rows
        }

        for catcher in catchers:
            user_id = int(catcher.get("userId", 0) or 0)
            count = int(catcher.get("count", 0) or 0)
            times = claim_times_by_user.get(user_id, [])

            # Sort by the time the user reached this card count.
            # Example: for x3, use the 3rd claim time for this card when available.
            if times and len(times) >= count:
                catcher["reachedCountAt"] = times[count - 1]
            elif times:
                catcher["reachedCountAt"] = times[-1]
            else:
                catcher["reachedCountAt"] = None

    catchers.sort(
        key=lambda x: (
            -int(x.get("count", 0) or 0),
            _time_sort_value(x.get("reachedCountAt")),
            str(x.get("firstName", "")).lower(),
        )
    )

    return {"totalOwned": total_owned, "topCatchers": catchers[:10]}


def rarity_counts(cards: list[dict]) -> dict:
    counts = {r: {"unique": 0, "total": 0} for r in RARITY_ORDER}
    for card in cards:
        rarity = str(card.get("rarity", RARITY_COMMON_NAME))
        if rarity not in counts:
            counts[rarity] = {"unique": 0, "total": 0}
        counts[rarity]["unique"] += 1
        counts[rarity]["total"] += int(card.get("count", 1))
    return counts
