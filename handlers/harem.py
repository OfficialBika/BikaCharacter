from __future__ import annotations

import math
import random
from collections import defaultdict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import HAREM_PAGE_SIZE
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_user_doc
from utils.permissions import is_global_admin
from utils.rarity import get_rarity_emoji
from utils.text import escape_html
from utils.i18n import t

# Telegram photo captions are limited to 1024 characters.
# Keep the generated harem caption safely below that limit.
MAX_HAREM_CAPTION_CHARS = 900
MAX_CARD_ROWS_PER_PAGE = 12
MAX_ANIME_GROUPS_PER_PAGE = max(1, int(HAREM_PAGE_SIZE or 5))

MEDIA_FIELDS = ("fileId", "fileUniqueId", "mediaType", "mimeType", "fileName")


def _media_type(card: dict) -> str:
    media_type = str(card.get("mediaType") or "photo").strip().lower()
    mime_type = str(card.get("mimeType") or "").strip().lower()
    if not media_type or media_type == "photo":
        if mime_type.startswith("video/"):
            return "video"
        if mime_type == "image/gif":
            return "animation"
    return media_type or "photo"


async def hydrate_user_card_media(user_doc: dict) -> dict:
    """Merge latest media fields from photos collection into user's card snapshots.

    This keeps old claimed video cards working even if their user.cards snapshot
    was saved before mediaType support existed. It does not modify the database.
    """
    cards = [dict(card) for card in user_doc.get("cards", [])]
    card_ids = [str(card.get("cardId", "")).strip() for card in cards if str(card.get("cardId", "")).strip()]
    if not card_ids:
        updated = dict(user_doc)
        updated["cards"] = cards
        return updated

    docs = await get_db().photos.find(
        {"cardId": {"$in": card_ids}},
        {"cardId": 1, "fileId": 1, "fileUniqueId": 1, "mediaType": 1, "mimeType": 1, "fileName": 1},
    ).to_list(None)
    by_card_id = {str(doc.get("cardId", "")): doc for doc in docs}

    for card in cards:
        doc = by_card_id.get(str(card.get("cardId", "")))
        if not doc:
            card.setdefault("mediaType", "photo")
            continue
        for field in MEDIA_FIELDS:
            value = doc.get(field)
            if value not in (None, ""):
                card[field] = value
        card.setdefault("mediaType", "photo")

    updated = dict(user_doc)
    updated["cards"] = cards
    return updated


def _input_media_for_card(card: dict, caption: str):
    media_type = _media_type(card)
    file_id = card["fileId"]
    if media_type == "video":
        return InputMediaVideo(media=file_id, caption=caption, parse_mode="HTML")
    if media_type == "animation":
        return InputMediaAnimation(media=file_id, caption=caption, parse_mode="HTML")
    if media_type == "document":
        return InputMediaDocument(media=file_id, caption=caption, parse_mode="HTML")
    return InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML")


async def _reply_card_media(message, card: dict, caption: str, reply_markup=None):
    media_type = _media_type(card)
    file_id = card["fileId"]
    if media_type == "video":
        return await message.reply_video(file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    if media_type == "animation":
        return await message.reply_animation(file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    if media_type == "document":
        return await message.reply_document(file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    return await message.reply_photo(file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)


def _card_id_sort_value(card: dict) -> tuple[int, int | str]:
    card_id = str(card.get("cardId", ""))
    if card_id.isdigit():
        return (0, int(card_id))
    return (1, card_id)


def group_cards_by_anime(cards: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped = defaultdict(list)
    for card in cards:
        grouped[card.get("anime") or "Unknown"].append(card)
    return sorted(grouped.items(), key=lambda item: item[0].lower())


async def get_database_anime_totals(anime_names: list[str]) -> dict[str, int]:
    """Return database total card count for each anime shown in harem.

    Header format becomes owned unique cards / total cards in database.
    Example: Demon Slayer (3/20).
    """
    names = [str(name or "").strip() for name in anime_names if str(name or "").strip()]
    if not names:
        return {}

    rows = await get_db().photos.aggregate(
        [
            {"$match": {"anime": {"$in": names}}},
            {"$group": {"_id": "$anime", "total": {"$sum": 1}}},
        ]
    ).to_list(None)

    return {
        str(row.get("_id", "")).strip(): int(row.get("total", 0) or 0)
        for row in rows
    }


def get_harem_cards_for_view(user_doc: dict) -> tuple[list[dict], str, str]:
    cards = list(user_doc.get("cards", []))
    sort_mode = str(user_doc.get("haremSort") or user_doc.get("haremView") or "anime").lower()
    rarity = str(user_doc.get("haremRarity") or "").strip()

    if sort_mode == "rarity" and rarity:
        filtered = [card for card in cards if str(card.get("rarity", "")).lower() == rarity.lower()]
        return filtered, "rarity", rarity

    return cards, "anime", ""


def choose_cover(user_doc: dict, view_cards: list[dict] | None = None) -> dict | None:
    cards = list(view_cards if view_cards is not None else user_doc.get("cards", []))
    if not cards:
        cards = list(user_doc.get("cards", []))
    if not cards:
        return None

    fav_id = str(user_doc.get("favoriteCardId", ""))
    if fav_id:
        fav = next((c for c in cards if str(c.get("cardId")) == fav_id), None)
        if fav:
            return fav
    return random.choice(cards)


def _card_line(card: dict) -> str:
    emoji = get_rarity_emoji(card.get("rarity"))
    count = int(card.get("count", 1) or 1)
    return (
        f"🍀 <b>{escape_html(card.get('cardId'))}</b> | {emoji} | "
        f"{escape_html(card.get('name'))} (x{count})"
    )


def _can_add_lines(current: list[str], new_lines: list[str], card_rows: int, add_card_rows: int, group_count: int, add_group: bool) -> bool:
    if add_group and group_count >= MAX_ANIME_GROUPS_PER_PAGE:
        return False
    if card_rows + add_card_rows > MAX_CARD_ROWS_PER_PAGE:
        return False
    raw_len = len("\n".join(current + new_lines))
    return raw_len <= 650


def _paginate_grouped_cards(
    grouped: list[tuple[str, list[dict]]],
    database_anime_totals: dict[str, int] | None = None,
) -> list[list[str]]:
    database_anime_totals = database_anime_totals or {}

    pages: list[list[str]] = []
    current: list[str] = []
    current_card_rows = 0
    current_group_count = 0

    def flush() -> None:
        nonlocal current, current_card_rows, current_group_count
        while current and current[-1] == "":
            current.pop()
        if current:
            pages.append(current)
        current = []
        current_card_rows = 0
        current_group_count = 0

    for anime, anime_cards in grouped:
        sorted_cards = sorted(anime_cards, key=_card_id_sort_value)

        # User owned unique card count for this anime.
        owned_unique = len(sorted_cards)

        # Total unique card count in database for this anime.
        # If the database lookup cannot find it, fallback to owned_unique
        # so /harem never breaks because of missing/old data.
        anime_key = str(anime).strip()
        database_total = int(database_anime_totals.get(anime_key, 0) or 0)
        if database_total <= 0:
            database_total = owned_unique

        header = [f"⚜️ <b>{escape_html(anime)}</b> ({owned_unique}/{database_total})", "─────────────"]
        card_lines = [_card_line(card) for card in sorted_cards]

        index = 0
        while index < len(card_lines):
            # Start a new anime section. If the current page cannot fit the section header
            # and at least one card, move to the next page.
            first_line = card_lines[index]
            if current and not _can_add_lines(
                current,
                header + [first_line, ""],
                current_card_rows,
                1,
                current_group_count,
                True,
            ):
                flush()

            current.extend(header)
            current_group_count += 1

            while index < len(card_lines):
                line = card_lines[index]
                if current_card_rows > 0 and not _can_add_lines(
                    current,
                    [line],
                    current_card_rows,
                    1,
                    current_group_count,
                    False,
                ):
                    break
                current.append(line)
                current_card_rows += 1
                index += 1

            current.append("")
            if index < len(card_lines):
                flush()

    flush()
    return pages


def _build_harem_prefix(user_doc: dict, page: int, total_pages: int, view_cards: list[dict], grouped: list[tuple[str, list[dict]]], sort_mode: str, selected_rarity: str) -> list[str]:
    all_cards = list(user_doc.get("cards", []))
    header_name = " ".join([user_doc.get("firstName", ""), user_doc.get("lastName", "")]).strip() or user_doc.get("username") or f"User {user_doc.get('userId')}"
    total_cards = sum(int(c.get("count", 0) or 0) for c in all_cards)
    shown_total = sum(int(c.get("count", 0) or 0) for c in view_cards)
    fav = next((c for c in all_cards if str(c.get("cardId")) == str(user_doc.get("favoriteCardId", ""))), None)

    lines = [
        t("harem_header", name=escape_html(header_name), page=page, total_pages=total_pages),
    ]

    if sort_mode == "rarity" and selected_rarity:
        lines.append(
            t(
                "harem_summary_rarity",
                emoji=get_rarity_emoji(selected_rarity),
                rarity=escape_html(selected_rarity),
                shown_cards=shown_total,
                total_cards=total_cards,
                total_series=len(grouped),
            )
        )
    else:
        lines.append(
            t(
                "harem_summary_anime",
                total_cards=total_cards,
                total_series=len(grouped),
            )
        )

    if fav:
        lines.append(t("harem_favourite", name=escape_html(fav.get("name")), card_id=escape_html(fav.get("cardId"))))
    lines.append("")
    return lines


def _fit_caption(prefix: list[str], body: list[str]) -> str:
    lines = list(prefix) + list(body)
    caption = "\n".join(lines).strip()
    if len(caption) <= MAX_HAREM_CAPTION_CHARS:
        return caption

    # Last-resort safety trim: remove card rows from the end rather than failing /harem.
    trimmed_body = list(body)
    while trimmed_body and len("\n".join(prefix + trimmed_body).strip()) > MAX_HAREM_CAPTION_CHARS:
        trimmed_body.pop()
    if trimmed_body and trimmed_body[-1] != "":
        trimmed_body.append("…")
    return "\n".join(prefix + trimmed_body).strip()


async def build_harem_caption(user_doc: dict, page: int = 1) -> tuple[str, int, int, list[dict]]:
    view_cards, sort_mode, selected_rarity = get_harem_cards_for_view(user_doc)
    grouped = group_cards_by_anime(view_cards)

    # For anime section headers, show owned unique cards / total cards in database.
    # Example: ⚜️ Demon Slayer (3/20)
    anime_names = [anime for anime, _cards in grouped]
    database_anime_totals = await get_database_anime_totals(anime_names)

    if not grouped:
        if sort_mode == "rarity" and selected_rarity:
            body_pages = [[t("harem_no_rarity_cards", emoji=get_rarity_emoji(selected_rarity), rarity=escape_html(selected_rarity))]]
        else:
            body_pages = [[t("harem_no_cards")]]
    else:
        body_pages = _paginate_grouped_cards(grouped, database_anime_totals)

    total_pages = max(1, len(body_pages))
    page = max(1, min(page, total_pages))
    body = body_pages[page - 1]
    prefix = _build_harem_prefix(user_doc, page, total_pages, view_cards, grouped, sort_mode, selected_rarity)
    caption = _fit_caption(prefix, body)
    return caption, page, total_pages, view_cards


def harem_keyboard(user_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    prev_page = page - 1 if page > 1 else total_pages
    next_page = page + 1 if page < total_pages else 1
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t("harem_button_back"), callback_data=f"harem:{user_id}:{prev_page}"),
                InlineKeyboardButton(f"💠 {page}/{total_pages}", callback_data="noop"),
                InlineKeyboardButton(t("harem_button_next"), callback_data=f"harem:{user_id}:{next_page}"),
            ],
            [
                InlineKeyboardButton(
                    t("harem_inline_button"),
                    switch_inline_query_current_chat=f"harem:{user_id}",
                )
            ],
        ]
    )


async def send_harem(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int = 1, edit: bool = False) -> None:
    user_doc = await get_user_doc(user_id)
    if not user_doc or not user_doc.get("cards"):
        if edit and update.callback_query:
            await update.callback_query.answer(t("harem_no_cards_alert"), show_alert=True)
        else:
            await update.effective_message.reply_text(t("harem_no_cards_user"))
        return

    user_doc = await hydrate_user_card_media(user_doc)
    caption, safe_page, total_pages, view_cards = await build_harem_caption(user_doc, page)
    cover = choose_cover(user_doc, view_cards)
    keyboard = harem_keyboard(user_id, safe_page, total_pages)

    if not cover:
        if edit and update.callback_query:
            await update.callback_query.answer(t("harem_no_cards_alert"), show_alert=True)
        else:
            await update.effective_message.reply_text(t("harem_no_cards_user"))
        return

    if edit and update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_media(
                _input_media_for_card(cover, caption),
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await query.edit_message_caption(caption=caption, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                # If Telegram refuses the edit, at least answer the callback cleanly.
                pass
        await query.answer()
        return

    try:
        await _reply_card_media(update.effective_message, cover, caption, reply_markup=keyboard)
    except Exception as exc:
        print("HAREM SEND MEDIA ERROR:", repr(exc))
        await update.effective_message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)


async def harem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    await ensure_user(update.effective_user)
    page = 1
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    await send_harem(update, context, update.effective_user.id, page)


async def harem_dot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    await ensure_user(update.effective_user)
    page = 1
    text = update.effective_message.text or ""
    parts = text.split()
    if len(parts) > 1 and parts[1].isdigit():
        page = int(parts[1])
    await send_harem(update, context, update.effective_user.id, page)


async def harem_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    _, user_id_raw, page_raw = query.data.split(":", 2)
    user_id = int(user_id_raw)
    page = int(page_raw)
    if query.from_user.id != user_id and not is_global_admin(query.from_user.id):
        await query.answer(t("not_allowed"), show_alert=True)
        return
    await send_harem(update, context, user_id, page, edit=True)


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


def register_harem_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("harem", harem_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.harem(?:\s+\d+)?$"), harem_dot_cmd))
    app.add_handler(CallbackQueryHandler(harem_callback, pattern=r"^harem:\d+:-?\d+$"))
    app.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop$"))
