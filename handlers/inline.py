from __future__ import annotations

import re
from hashlib import md5

from telegram import (
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedMpeg4Gif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    Update,
)
from telegram.ext import Application, ContextTypes, InlineQueryHandler

from database.mongodb import get_db
from utils.parser import normalized_search_name
from utils.permissions import is_global_admin
from utils.rarity import get_rarity_emoji
from utils.text import escape_html
from utils.i18n import t

# Telegram Bot API allows up to 50 inline results per answer.
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
    raw = ":".join(
        [
            str(photo.get("cardId", "")),
            str(photo.get("mediaType", "")),
            str(photo.get("fileUniqueId", "")),
            str(photo.get("fileId", "")),
            str(photo.get("count", "")),
        ]
    )
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
        "mediaType": 1,
        "mimeType": 1,
        "fileName": 1,
    }


def _card_sort_key(card: dict):
    card_id = str(card.get("cardId", ""))
    if card_id.isdigit():
        return (0, int(card_id))
    return (1, card_id)


def _media_type_from_doc(card: dict) -> str:
    media_type = str(card.get("mediaType") or "").strip().lower()
    mime_type = str(card.get("mimeType") or "").strip().lower()
    file_name = str(card.get("fileName") or "").strip().lower()

    if media_type in {"photo", "video", "animation", "gif", "document"}:
        return media_type
    if mime_type.startswith("video/"):
        return "video"
    if mime_type == "image/gif":
        return "animation"
    if file_name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")):
        return "video"
    if file_name.endswith(".gif"):
        return "animation"
    if file_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "photo"
    return "photo"


def _has_known_media_type(card: dict) -> bool:
    return bool(str(card.get("mediaType") or card.get("mimeType") or card.get("fileName") or "").strip())


async def _hydrate_user_cards(cards: list[dict]) -> list[dict]:
    """Backfill media fields for old user card snapshots from photos collection."""
    card_ids = [str(c.get("cardId", "")) for c in cards if c.get("cardId")]
    if not card_ids:
        return cards

    db_cards = await get_db().photos.find(
        {"cardId": {"$in": card_ids}},
        _base_projection(),
    ).to_list(None)
    by_id = {str(c.get("cardId")): c for c in db_cards}

    hydrated: list[dict] = []
    for card in cards:
        merged = dict(by_id.get(str(card.get("cardId")), {}))
        merged.update(card)
        for key in ("mediaType", "mimeType", "fileName", "fileUniqueId"):
            if not merged.get(key) and by_id.get(str(card.get("cardId")), {}).get(key):
                merged[key] = by_id[str(card.get("cardId"))].get(key)
        hydrated.append(merged)
    return hydrated


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
                    "$or": [
                        {"normalizedName": {"$regex": contains_regex}},
                        {"cardId": str(raw_q).strip()},
                    ],
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
    if int(user_id) != int(requester_id) and not is_global_admin(int(requester_id)):
        return [], False

    user_doc = await get_db().users.find_one({"userId": int(user_id)}, {"cards": 1})
    if not user_doc:
        return [], False

    cards = [dict(c) for c in user_doc.get("cards", []) if c.get("fileId")]
    cards = await _hydrate_user_cards(cards)

    search = normalized_search_name(search_q)
    if search:
        cards = [
            c for c in cards
            if search in str(c.get("normalizedName") or normalized_search_name(c.get("name", "")))
            or search == str(c.get("cardId", "")).strip().lower()
        ]

    cards.sort(key=_card_sort_key)
    chunk = cards[offset: offset + INLINE_PAGE_SIZE + 1]
    has_more = len(chunk) > INLINE_PAGE_SIZE
    return chunk[:INLINE_PAGE_SIZE], has_more


def _build_inline_result(photo: dict, is_harem_query: bool, strict_known_media: bool = False):
    card_id = str(photo.get("cardId", ""))
    file_id = str(photo.get("fileId", ""))
    if not card_id or not file_id:
        return None

    if strict_known_media and not _has_known_media_type(photo):
        return None

    qty = int(photo.get("count", 1) or 1)
    title = f"{photo.get('name', 'Unknown')} [{card_id}]"
    if is_harem_query:
        title += f" ×{qty}"

    description = t(
        "inline_description",
        anime=photo.get("anime", "Unknown"),
        rarity=photo.get("rarity", "Common"),
        card_id=card_id,
    )
    caption = _inline_caption(photo)
    result_id = _result_id(photo)
    media_type = _media_type_from_doc(photo)
    mime_type = str(photo.get("mimeType") or "").lower()
    file_name = str(photo.get("fileName") or "").lower()

    if media_type == "video":
        return InlineQueryResultCachedVideo(
            id=result_id,
            video_file_id=file_id,
            title=title,
            description=description,
            caption=caption,
            parse_mode="HTML",
        )

    if media_type in {"animation", "gif"}:
        if mime_type == "image/gif" or file_name.endswith(".gif"):
            return InlineQueryResultCachedGif(
                id=result_id,
                gif_file_id=file_id,
                title=title,
                caption=caption,
                parse_mode="HTML",
            )
        return InlineQueryResultCachedMpeg4Gif(
            id=result_id,
            mpeg4_file_id=file_id,
            title=title,
            caption=caption,
            parse_mode="HTML",
        )

    if media_type == "document":
        return InlineQueryResultCachedDocument(
            id=result_id,
            document_file_id=file_id,
            title=title,
            description=description,
            caption=caption,
            parse_mode="HTML",
        )

    return InlineQueryResultCachedPhoto(
        id=result_id,
        photo_file_id=file_id,
        title=title,
        description=description,
        caption=caption,
        parse_mode="HTML",
    )


def _build_results(photos: list[dict], is_harem_query: bool, strict_known_media: bool = False) -> list:
    results = []
    seen_ids: set[str] = set()
    for photo in photos:
        card_id = str(photo.get("cardId", ""))
        if not card_id or card_id in seen_ids:
            continue
        seen_ids.add(card_id)
        try:
            result = _build_inline_result(photo, is_harem_query, strict_known_media=strict_known_media)
        except Exception as exc:
            print("INLINE RESULT BUILD ERROR:", repr(exc), photo)
            result = None
        if result:
            results.append(result)
    return results


async def inline_character_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query:
        return

    raw_q = (query.query or "").strip()

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

    results = _build_results(photos, is_harem_query, strict_known_media=False)
    next_offset = str(offset + INLINE_PAGE_SIZE) if has_more else ""

    try:
        await query.answer(results, cache_time=5, is_personal=True, next_offset=next_offset)
    except Exception as exc:
        # If one legacy item has an unknown/incorrect media type, Telegram rejects the whole answer.
        # Retry with only items that have explicit media metadata.
        print("INLINE ANSWER ERROR, RETRYING STRICT:", repr(exc))
        safe_results = _build_results(photos, is_harem_query, strict_known_media=True)
        try:
            await query.answer(safe_results, cache_time=1, is_personal=True, next_offset="")
        except Exception as exc2:
            print("INLINE STRICT ANSWER ERROR:", repr(exc2))
            await query.answer([], cache_time=1, is_personal=True, next_offset="")


def register_inline_handlers(app: Application) -> None:
    app.add_handler(InlineQueryHandler(inline_character_search))
