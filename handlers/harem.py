from __future__ import annotations

import asyncio
import html
import json
import math
import os
import random
import urllib.error
import urllib.request
from collections import defaultdict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import HAREM_PAGE_SIZE, LIMITED_CARDS_COLLECTION
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_user_doc
from utils.permissions import is_global_admin
from utils.rarity import get_rarity_emoji
from utils.buttons import action_button
from utils.text import escape_html
from utils.i18n import t

# Telegram photo captions are limited to 1024 characters.
# Keep the generated harem caption safely below that limit.
MAX_HAREM_CAPTION_CHARS = 900
MAX_CARD_ROWS_PER_PAGE = 12
MAX_ANIME_GROUPS_PER_PAGE = max(1, int(HAREM_PAGE_SIZE or 5))

# TABLE=true  -> Message 1: cover media, Message 2: rich table + buttons.
# TABLE=false -> original single media + caption + buttons behavior.
TABLE_ENABLED = str(os.getenv("TABLE", "false") or "false").strip().lower() in {
    "1", "true", "yes", "on"
}
RICH_API_TIMEOUT_SECONDS = 20

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

    projection = {"cardId": 1, "fileId": 1, "fileUniqueId": 1, "mediaType": 1, "mimeType": 1, "fileName": 1}
    normal_docs = await get_db().photos.find({"cardId": {"$in": card_ids}}, projection).to_list(None)
    limited_docs = await get_db()[LIMITED_CARDS_COLLECTION].find({"cardId": {"$in": card_ids}}, projection).to_list(None)
    by_card_id = {str(doc.get("cardId", "")): doc for doc in normal_docs + limited_docs}

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


async def _reply_cover_media_only(message, card: dict):
    """Send only the harem cover media; no caption and no buttons."""
    media_type = _media_type(card)
    file_id = card["fileId"]
    if media_type == "video":
        return await message.reply_video(file_id)
    if media_type == "animation":
        return await message.reply_animation(file_id)
    if media_type == "document":
        return await message.reply_document(file_id)
    return await message.reply_photo(file_id)


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

    totals: dict[str, int] = {}
    pipeline = [
        {"$match": {"anime": {"$in": names}}},
        {"$group": {"_id": "$anime", "total": {"$sum": 1}}},
    ]
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        rows = await get_db()[collection_name].aggregate(pipeline).to_list(None)
        for row in rows:
            key = str(row.get("_id", "")).strip()
            totals[key] = totals.get(key, 0) + int(row.get("total", 0) or 0)
    return totals


def get_harem_cards_for_view(user_doc: dict) -> tuple[list[dict], str, str]:
    cards = list(user_doc.get("cards", []))
    sort_mode = str(user_doc.get("haremSort") or user_doc.get("haremView") or "anime").lower()
    rarity = str(user_doc.get("haremRarity") or "").strip()

    if sort_mode == "rarity" and rarity:
        filtered = [card for card in cards if str(card.get("rarity", "")).lower() == rarity.lower()]
        return filtered, "rarity", rarity

    return cards, "anime", ""


def choose_cover(user_doc: dict, view_cards: list[dict] | None = None) -> dict | None:
    all_cards = list(user_doc.get("cards", []))
    fav_id = str(user_doc.get("favoriteCardId", ""))
    if fav_id:
        # Favourite cover must always win, even when hmode filters the list by rarity/anime.
        fav = next((c for c in all_cards if str(c.get("cardId")) == fav_id), None)
        if fav:
            return fav

    cards = list(view_cards if view_cards is not None else all_cards)
    if not cards:
        cards = all_cards
    if not cards:
        return None
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



def _rich_escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


async def build_rich_harem_html(user_doc: dict, page: int = 1) -> tuple[str, int, int]:
    view_cards, sort_mode, selected_rarity = get_harem_cards_for_view(user_doc)
    grouped = group_cards_by_anime(view_cards)
    anime_names = [anime for anime, _cards in grouped]
    totals = await get_database_anime_totals(anime_names)

    sections: list[tuple[str, list[dict], int]] = []
    for anime, cards in grouped:
        sorted_cards = sorted(cards, key=_card_id_sort_value)
        total = int(totals.get(str(anime).strip(), 0) or 0) or len(sorted_cards)
        sections.append((anime, sorted_cards, total))

    pages: list[list[tuple[str, list[dict], int]]] = []
    current: list[tuple[str, list[dict], int]] = []
    row_count = 0

    for anime, cards, total in sections:
        index = 0
        while index < len(cards):
            if current and (
                len(current) >= MAX_ANIME_GROUPS_PER_PAGE
                or row_count >= MAX_CARD_ROWS_PER_PAGE
            ):
                pages.append(current)
                current = []
                row_count = 0

            remaining = max(1, MAX_CARD_ROWS_PER_PAGE - row_count)
            chunk = cards[index:index + remaining]
            current.append((anime, chunk, total))
            row_count += len(chunk)
            index += len(chunk)

            if index < len(cards):
                pages.append(current)
                current = []
                row_count = 0

    if current:
        pages.append(current)
    if not pages:
        pages = [[]]

    total_pages = len(pages)
    safe_page = max(1, min(int(page or 1), total_pages))
    page_sections = pages[safe_page - 1]

    all_cards = list(user_doc.get("cards", []))
    display_name = (
        " ".join([
            str(user_doc.get("firstName", "") or ""),
            str(user_doc.get("lastName", "") or ""),
        ]).strip()
        or str(user_doc.get("username", "") or "")
        or f"User {user_doc.get('userId')}"
    )
    total_cards = sum(int(c.get("count", 0) or 0) for c in all_cards)
    fav = next(
        (
            c for c in all_cards
            if str(c.get("cardId")) == str(user_doc.get("favoriteCardId", ""))
        ),
        None,
    )

    parts = [
        f"<h2>📘 {_rich_escape(display_name)}'s Characters</h2>",
        (
            f"<p><b>Page:</b> {safe_page}/{total_pages}<br/>"
            f"<b>Total Cards:</b> {total_cards}<br/>"
            f"<b>Total Series:</b> {len(grouped)}</p>"
        ),
    ]

    if sort_mode == "rarity" and selected_rarity:
        parts.append(
            f"<p><b>Mode:</b> {_rich_escape(get_rarity_emoji(selected_rarity))} "
            f"{_rich_escape(selected_rarity)}</p>"
        )
    else:
        parts.append("<p><b>Mode:</b> Anime</p>")

    if fav:
        parts.append(
            f"<p><b>💖 Favourite:</b> {_rich_escape(fav.get('name'))} "
            f"[{_rich_escape(fav.get('cardId'))}]</p>"
        )

    if not grouped:
        parts.append("<p>No cards found.</p>")
    else:
        grouped_lookup = {anime: cards for anime, cards in grouped}
        for anime, cards, database_total in page_sections:
            owned_unique = len(grouped_lookup.get(anime, cards))
            rows = [
                "<tr>"
                "<th align=\"center\">ID</th>"
                "<th align=\"center\">Rarity</th>"
                "<th align=\"left\">Character</th>"
                "</tr>"
            ]
            for card in cards:
                count = int(card.get("count", 1) or 1)
                rows.append(
                    "<tr>"
                    f"<td align=\"center\">🍀 {_rich_escape(card.get('cardId'))}</td>"
                    f"<td align=\"center\">{_rich_escape(get_rarity_emoji(card.get('rarity')))}</td>"
                    f"<td align=\"left\">{_rich_escape(card.get('name'))} (x{count})</td>"
                    "</tr>"
                )
            parts.append(
                "<table bordered striped>"
                f"<caption>⚜️ {_rich_escape(anime)} ({owned_unique}/{database_total})</caption>"
                + "".join(rows)
                + "</table>"
            )

    return "\n".join(parts), safe_page, total_pages


def _bot_api_json_sync(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=RICH_API_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} HTTP {exc.code}: {detail}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"{method}: {result.get('description')}")
    return result


async def _bot_api_json(context: ContextTypes.DEFAULT_TYPE, method: str, payload: dict) -> dict:
    return await asyncio.to_thread(
        _bot_api_json_sync,
        str(context.bot.token),
        method,
        payload,
    )


async def _send_or_edit_rich_harem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_doc: dict,
    user_id: int,
    page: int,
    edit: bool,
) -> bool:
    rich_html, safe_page, total_pages = await build_rich_harem_html(user_doc, page)
    keyboard = harem_keyboard(user_id, safe_page, total_pages).to_dict()
    rich_message = {
        "html": rich_html,
        "skip_entity_detection": True,
    }

    try:
        if edit and update.callback_query and update.callback_query.message:
            query = update.callback_query
            await _bot_api_json(
                context,
                "editMessageText",
                {
                    "chat_id": int(query.message.chat.id),
                    "message_id": int(query.message.message_id),
                    "rich_message": rich_message,
                    "reply_markup": keyboard,
                },
            )
            await query.answer()
            return True

        message = update.effective_message
        payload = {
            "chat_id": int(update.effective_chat.id),
            "rich_message": rich_message,
            "reply_markup": keyboard,
        }
        if message and getattr(message, "message_thread_id", None):
            payload["message_thread_id"] = int(message.message_thread_id)

        await _bot_api_json(context, "sendRichMessage", payload)
        return True
    except Exception as exc:
        print("HAREM RICH MESSAGE ERROR:", repr(exc), flush=True)
        return False


def harem_keyboard(user_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    prev_page = page - 1 if page > 1 else total_pages
    next_page = page + 1 if page < total_pages else 1
    return InlineKeyboardMarkup(
        [
            [
                action_button(t("harem_button_back"), "danger", callback_data=f"harem:{user_id}:{prev_page}"),
                action_button(f"{page}/{total_pages}", "primary", callback_data="noop"),
                action_button(t("harem_button_next"), "primary", callback_data=f"harem:{user_id}:{next_page}"),
            ],
            [
                action_button(
                    t("harem_inline_button"),
                    "success",
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

    if TABLE_ENABLED:
        # Pagination callbacks come from Message 2, so only the rich message is edited.
        if edit and update.callback_query:
            sent = await _send_or_edit_rich_harem(
                update, context, user_doc, user_id, page, edit=True
            )
            if not sent:
                await update.callback_query.answer(
                    "Rich table update failed.",
                    show_alert=True,
                )
            return

        view_cards, _sort_mode, _selected_rarity = get_harem_cards_for_view(user_doc)
        cover = choose_cover(user_doc, view_cards)
        if not cover:
            await update.effective_message.reply_text(t("harem_no_cards_user"))
            return

        cover_message = None
        try:
            # Message 1: cover media only.
            cover_message = await _reply_cover_media_only(
                update.effective_message,
                cover,
            )

            # Message 2: rich message + pagination buttons.
            sent = await _send_or_edit_rich_harem(
                update, context, user_doc, user_id, page, edit=False
            )
            if sent:
                return
        except Exception as exc:
            print("HAREM TABLE MODE ERROR:", repr(exc), flush=True)

        # Rich send failed after the cover was sent. Remove that standalone cover
        # before falling back to the original one-message harem when possible.
        if cover_message:
            try:
                await cover_message.delete()
            except Exception:
                pass

    # TABLE=false, or automatic fallback after a rich-message failure:
    # preserve the original single media + caption + buttons format.
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
                await query.edit_message_caption(
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception:
                pass
        await query.answer()
        return

    try:
        await _reply_card_media(
            update.effective_message,
            cover,
            caption,
            reply_markup=keyboard,
        )
    except Exception as exc:
        print("HAREM SEND MEDIA ERROR:", repr(exc))
        await update.effective_message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


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
