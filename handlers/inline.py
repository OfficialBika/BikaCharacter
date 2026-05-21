from __future__ import annotations

import re
from hashlib import md5

from telegram import InlineQueryResultCachedPhoto, Update
from telegram.ext import Application, ContextTypes, InlineQueryHandler

from database.mongodb import get_db
from utils.parser import normalized_search_name
from utils.rarity import get_rarity_emoji
from utils.text import escape_html

# Telegram Bot API allows up to 50 inline results per answer.
# We do not impose a database/result total limit; all matching cards are shown
# by Telegram inline pagination using next_offset as the user scrolls.
INLINE_PAGE_SIZE = 50


def _inline_caption(photo: dict) -> str:
    return "\n".join(
        [
            "<b>OwO! Check out this character!</b>",
            "",
            f"<b>{escape_html(photo.get('anime', 'Unknown'))}</b>",
            f"<b>{escape_html(photo.get('cardId', ''))}:</b> {escape_html(photo.get('name', 'Unknown'))}",
            f"({get_rarity_emoji(photo.get('rarity'))} <b>RARITY:</b> {escape_html(photo.get('rarity', 'Common'))})",
        ]
    )


def _result_id(photo: dict) -> str:
    # Telegram inline result IDs are short strings. Keep them stable per card/update.
    raw = f"{photo.get('cardId','')}:{photo.get('fileUniqueId','')}:{photo.get('fileId','')}"
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


async def inline_character_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query:
        return

    raw_q = (query.query or "").strip()

    # Empty query: @BotUsername -> show all database cards from ID 1 upward.
    # Non-empty query: @BotUsername Yelan -> show all matching Yelan cards.
    # Telegram delivers results page-by-page with next_offset, so the bot can
    # expose the whole database without a fixed total result limit.
    try:
        offset = max(0, int(query.offset or "0"))
    except ValueError:
        offset = 0

    photos, has_more = await _fetch_inline_photos(raw_q, offset)

    results = []
    seen_ids: set[str] = set()
    for photo in photos:
        card_id = str(photo.get("cardId", ""))
        file_id = str(photo.get("fileId", ""))
        if not card_id or not file_id or card_id in seen_ids:
            continue
        seen_ids.add(card_id)

        title = f"{photo.get('name', 'Unknown')} [{card_id}]"
        description = f"{photo.get('anime', 'Unknown')} | {photo.get('rarity', 'Common')} | ID: {card_id}"
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
