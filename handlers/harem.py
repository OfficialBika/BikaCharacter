from __future__ import annotations

import math
import random
from collections import defaultdict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import HAREM_PAGE_SIZE
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_user_doc
from utils.permissions import is_global_admin
from utils.rarity import get_rarity_emoji
from utils.text import escape_html
from utils.i18n import t


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


def build_harem_caption(user_doc: dict, page: int = 1) -> tuple[str, int, int, list[dict]]:
    all_cards = list(user_doc.get("cards", []))
    view_cards, sort_mode, selected_rarity = get_harem_cards_for_view(user_doc)
    grouped = group_cards_by_anime(view_cards)
    total_pages = max(1, math.ceil(len(grouped) / HAREM_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    current = grouped[(page - 1) * HAREM_PAGE_SIZE : page * HAREM_PAGE_SIZE]

    header_name = " ".join([user_doc.get("firstName", ""), user_doc.get("lastName", "")]).strip() or user_doc.get("username") or f"User {user_doc.get('userId')}"
    total_cards = sum(int(c.get("count", 0)) for c in all_cards)
    shown_total = sum(int(c.get("count", 0)) for c in view_cards)
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

    if not current:
        if sort_mode == "rarity" and selected_rarity:
            lines.append(t("harem_no_rarity_cards", emoji=get_rarity_emoji(selected_rarity), rarity=escape_html(selected_rarity)))
        else:
            lines.append(t("harem_no_cards"))
    else:
        for anime, anime_cards in current:
            unique_count = len(anime_cards)
            total_count = sum(int(c.get("count", 0)) for c in anime_cards)
            lines.append(f"⚜️ <b>{escape_html(anime)}</b> ({unique_count}/{total_count})")
            lines.append("────────────────────")
            for card in sorted(anime_cards, key=_card_id_sort_value):
                emoji = get_rarity_emoji(card.get("rarity"))
                suffix = f" (x{int(card.get('count', 1))})"
                lines.append(f"🍀 <b>{escape_html(card.get('cardId'))}</b> | {emoji} | {escape_html(card.get('name'))}{suffix}")
            lines.append("")
    return "\n".join(lines).strip(), page, total_pages, view_cards


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

    caption, safe_page, total_pages, view_cards = build_harem_caption(user_doc, page)
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
                InputMediaPhoto(media=cover["fileId"], caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await query.edit_message_caption(caption=caption, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                pass
        await query.answer()
        return

    await update.effective_message.reply_photo(cover["fileId"], caption=caption, parse_mode="HTML", reply_markup=keyboard)


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
