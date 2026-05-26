from __future__ import annotations

import re
from hashlib import md5

from telegram import InlineQueryResultCachedPhoto, Update
from telegram.ext import Application, ContextTypes, InlineQueryHandler

from database.mongodb import get_db
from utils.parser import normalized_search_name
from utils.permissions import is_global_admin
from utils.rarity import get_rarity_emoji
from utils.text import escape_html
from utils.i18n import t

# Telegram Bot API allows up to 50 inline results per answer.
# We do not impose a database/result total limit; all matching cards are shown
# by Telegram inline pagination using next_offset as the user scrolls.
INLINE_PAGE_SIZE = 50


def _inline_caption(photo: dict) -> str:
    return "\n".join(
        [
            t("card_check_header"),
            "",
            f"<b>{escape_html(photo.get('anime', 'Unknown'))}</b>",
            f"<b>{escape_html(photo.get('cardId', ''))}:</b> {escape_html(photo.get('name', 'Unknown'))}",
            t("rarity_line", emoji=get_rarity_emoji(photo.get("rarity")), rarity=escape_html(photo.get("rarity", "Common"))),
        ]
    )


def _result_id(photo: dict) -> str:
    # Telegram inline result IDs are short strings. Keep them stable per card/update.
    raw = f"{photo.get('cardId','')}:{photo.get('fileUniqueId','')}:{photo.get('fileId','')}:{photo.get('count','')}"
    return md5(raw.encode("utf-8", errors="ignore")).hexdigest()


def _base_projection() -> dict:
    return {
        "cardId": 1,
        "name": 1,
        "normalizedName": 1,
        "rarity": 1,
        "anime": 1,
        "fileId": 1,
        "fileUniqueId": 1,
    }


def _card_sort_key(card: dict):
    card_id = str(card.get("cardId", ""))
    if card_id.isdigit():
        return (0, int(card_id))
    return (1, card_id)


async def _fetch_inline_photos(raw_q: str, offset: int) -> tuple[list[dict], bool]:
    db = get_db()

    common_add_fields = {
        "cardIdNum": {
            "$convert": {
                "input": "$cardId",
                "to": "int",
                "onError": 999999999,
                "onNull": 999999999,
            }
        }
    }

    if not raw_q:
        pipeline = [
            {"$match": {"fileId": {"$exists": True, "$ne": ""}}},
            {"$addFields": common_add_fields},
            {"$sort": {"cardIdNum": 1, "createdAt": 1, "cardId": 1}},
            {"$skip": offset},
            {"$limit": INLINE_PAGE_SIZE + 1},
            {"$project": _base_projection()},
        ]
    else:
        search = normalized_search_name(raw_q)
        if not search:
            return [], False

        contains_regex = re.compile(re.escape(search), re.IGNORECASE)
        prefix_regex = f"^{re.escape(search)}"
        pipeline = [
            {
                "$match": {
                    "normalizedName": {"$regex": contains_regex},
                    "fileId": {"$exists": True, "$ne": ""},
                }
            },
            {
                "$addFields": {
                    **common_add_fields,
                    "exactRank": {"$cond": [{"$eq": ["$normalizedName", search]}, 0, 1]},
                    "prefixRank": {
                        "$cond": [
                            {"$regexMatch": {"input": "$normalizedName", "regex": prefix_regex, "options": "i"}},
                            0,
                            1,
                        ]
                    },
                }
            },
            {"$sort": {"exactRank": 1, "prefixRank": 1, "cardIdNum": 1, "createdAt": 1, "cardId": 1}},
            {"$skip": offset},
            {"$limit": INLINE_PAGE_SIZE + 1},
            {"$project": _base_projection()},
        ]

    docs = await db.photos.aggregate(pipeline).to_list(INLINE_PAGE_SIZE + 1)
    has_more = len(docs) > INLINE_PAGE_SIZE
    return docs[:INLINE_PAGE_SIZE], has_more


async def _fetch_user_harem_photos(user_id: int, requester_id: int, search_q: str, offset: int) -> tuple[list[dict], bool]:
    # Normal users can open only their own harem inline list.
    # Owner/global admins can inspect another user's harem.
    if int(user_id) != int(requester_id) and not is_global_admin(int(requester_id)):
        return [], False

    user_doc = await get_db().users.find_one({"userId": int(user_id)}, {"cards": 1})
    if not user_doc:
        return [], False

    cards = [dict(c) for c in user_doc.get("cards", []) if c.get("fileId")]

    search = normalized_search_name(search_q)
    if search:
        cards = [
            c for c in cards
            if search in str(c.get("normalizedName") or normalized_search_name(c.get("name", "")))
        ]

    cards.sort(key=_card_sort_key)
    chunk = cards[offset: offset + INLINE_PAGE_SIZE + 1]
    has_more = len(chunk) > INLINE_PAGE_SIZE
    return chunk[:INLINE_PAGE_SIZE], has_more


async def inline_character_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query:
        return

    raw_q = (query.query or "").strip()

    # Empty query: @BotUsername -> show all database cards from ID 1 upward.
    # Non-empty query: @BotUsername Yelan -> show all matching Yelan cards.
    # Harem query: harem:<user_id> [search text] -> show only that user's owned cards.
    try:
        offset = max(0, int(query.offset or "0"))
    except ValueError:
        offset = 0

    harem_match = re.fullmatch(r"harem:(\d+)(?:\s+(.*))?", raw_q)
    is_harem_query = bool(harem_match)

    if is_harem_query:
        photos, has_more = await _fetch_user_harem_photos(
            user_id=int(harem_match.group(1)),
            requester_id=int(query.from_user.id),
            search_q=(harem_match.group(2) or ""),
            offset=offset,
        )
    else:
        photos, has_more = await _fetch_inline_photos(raw_q, offset)

    results = []
    seen_ids: set[str] = set()
    for photo in photos:
        card_id = str(photo.get("cardId", ""))
        file_id = str(photo.get("fileId", ""))
        if not card_id or not file_id or card_id in seen_ids:
            continue
        seen_ids.add(card_id)

        qty = int(photo.get("count", 1) or 1)
        title = f"{photo.get('name', 'Unknown')} [{card_id}]"
        if is_harem_query:
            title += f" ×{qty}"

        description = t("inline_description", anime=photo.get("anime", "Unknown"), rarity=photo.get("rarity", "Common"), card_id=card_id)
        results.append(
            InlineQueryResultCachedPhoto(
                id=_result_id(photo),
                photo_file_id=file_id,
                title=title,
                description=description,
                caption=_inline_caption(photo),
                parse_mode="HTML",
            )
        )

    next_offset = str(offset + INLINE_PAGE_SIZE) if has_more else ""
    await query.answer(results, cache_time=5, is_personal=True, next_offset=next_offset)


def register_inline_handlers(app: Application) -> None:
    app.add_handler(InlineQueryHandler(inline_character_search))
