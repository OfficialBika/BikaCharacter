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


def group_cards_by_anime(cards: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped = defaultdict(list)
    for card in cards:
        grouped[card.get("anime") or "Unknown"].append(card)
    return sorted(grouped.items(), key=lambda item: item[0].lower())


def choose_cover(user_doc: dict) -> dict | None:
    cards = list(user_doc.get("cards", []))
    if not cards:
        return None
    fav_id = str(user_doc.get("favoriteCardId", ""))
    if fav_id:
        fav = next((c for c in cards if str(c.get("cardId")) == fav_id), None)
        if fav:
            return fav
    return random.choice(cards)


def build_harem_caption(user_doc: dict, page: int = 1) -> tuple[str, int, int]:
    cards = list(user_doc.get("cards", []))
    grouped = group_cards_by_anime(cards)
    total_pages = max(1, math.ceil(len(grouped) / HAREM_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    current = grouped[(page - 1) * HAREM_PAGE_SIZE : page * HAREM_PAGE_SIZE]
    view = user_doc.get("haremView", "default")

    header_name = " ".join([user_doc.get("firstName", ""), user_doc.get("lastName", "")]).strip() or user_doc.get("username") or f"User {user_doc.get('userId')}"
    total_cards = sum(int(c.get("count", 0)) for c in cards)
    fav = next((c for c in cards if str(c.get("cardId")) == str(user_doc.get("favoriteCardId", ""))), None)

    lines = [
        t("harem_header", name=escape_html(header_name), page=page, total_pages=total_pages),
        t("harem_summary", total_cards=total_cards, total_series=len(grouped), mode=escape_html(view.upper())),
    ]
    if fav:
        lines.append(t("harem_favourite", name=escape_html(fav.get("name")), card_id=escape_html(fav.get("cardId"))))
    lines.append("")

    if not current:
        lines.append(t("harem_no_cards"))
    else:
        for anime, anime_cards in current:
            unique_count = len(anime_cards)
            total_count = sum(int(c.get("count", 0)) for c in anime_cards)
            lines.append(f"⚜️ <b>{escape_html(anime)}</b> ({unique_count}/{total_count})")
            lines.append("────────────")
            for card in sorted(anime_cards, key=lambda c: str(c.get("cardId", ""))):
                emoji = get_rarity_emoji(card.get("rarity"))
                suffix = f" × {int(card.get('count', 1))}" if int(card.get("count", 1)) > 1 else ""
                if view == "detailed":
                    lines.append(
                        f"🍀 {escape_html(card.get('cardId'))} | {emoji} {escape_html(card.get('rarity'))} | "
                        f"{escape_html(card.get('name'))}{suffix}"
                    )
                else:
                    lines.append(f"🍀 {escape_html(card.get('cardId'))} | {emoji} | {escape_html(card.get('name'))}{suffix}")
            lines.append("")
    return "\n".join(lines).strip(), page, total_pages


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

    cover = choose_cover(user_doc)
    caption, safe_page, total_pages = build_harem_caption(user_doc, page)
    keyboard = harem_keyboard(user_id, safe_page, total_pages)

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
