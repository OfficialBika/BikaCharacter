from __future__ import annotations

import re
from hashlib import md5
import secrets
import time

import config
from pymongo.errors import DuplicateKeyError
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from database.mongodb import get_db
from utils.card_adding import (
    add_anime_to_catalog,
    anime_catalog_exists,
    canonical_anime,
    find_duplicate_media,
    find_possible_duplicate,
    get_add_mode,
    list_anime_catalog_page,
    next_card_id,
    normalize_add_rarity,
    rarity_aliases,
    search_anime_catalog,
    set_add_mode,
    sync_counter_at_least,
)
from utils.hot_lookup import upsert_card
from utils.buttons import action_button, rarity_button
from utils.parser import parse_add_caption, parse_update_caption
from utils.permissions import is_owner
from utils.text import escape_html, mention_user, utcnow

CARD_DATABASE_CHANNEL_ID = config.CARD_DATABASE_CHANNEL_ID
RARITY_ORDER = config.RARITY_ORDER
LIMITED_CARDS_COLLECTION = getattr(config, "LIMITED_CARDS_COLLECTION", "limited_cards")
LIMITED_RARITY_NAME = getattr(config, "LIMITED_RARITY_NAME", "Limited")
ADDER_GROUP_IDS = getattr(config, "ADDER_GROUP_IDS", [-1003983636133])
SETTINGS_ID = "config"
SUPPORTED_DOCUMENT_MIME_PREFIXES = ("image/", "video/")

_PENDING: dict[str, dict] = {}
_PENDING_RARITY: dict[str, dict] = {}
_PENDING_UPDATES: dict[tuple[int, int], dict] = {}
_PENDING_TTL = 600
_PENDING_MAX = 2000

_ANIME_PICKERS: dict[str, dict] = {}
_ANIME_PICKER_TTL = 600
_ANIME_PICKER_MAX = 2000

_ADDMODE_PANELS: dict[str, dict] = {}


def _prune_pending() -> None:
    now = time.time()
    for mapping, ttl in (
        (_PENDING, _PENDING_TTL),
        (_PENDING_RARITY, _PENDING_TTL),
        (_PENDING_UPDATES, _PENDING_TTL),
        (_ADDMODE_PANELS, _ANIME_PICKER_TTL),
        (_ANIME_PICKERS, _ANIME_PICKER_TTL),
    ):
        for key, value in list(mapping.items()):
            if now - float(value.get("created", 0)) > ttl:
                mapping.pop(key, None)

    for mapping, maximum in (
        (_PENDING, _PENDING_MAX),
        (_PENDING_RARITY, _PENDING_MAX),
        (_PENDING_UPDATES, _PENDING_MAX),
        (_ADDMODE_PANELS, _ANIME_PICKER_MAX),
        (_ANIME_PICKERS, _ANIME_PICKER_MAX),
    ):
        if len(mapping) > maximum:
            oldest = sorted(mapping.items(), key=lambda x: x[1].get("created", 0))
            for key, _ in oldest[: len(mapping) - maximum]:
                mapping.pop(key, None)


def _picker_token(store: dict) -> str:
    _prune_pending()
    token = secrets.token_hex(4)
    while token in store:
        token = secrets.token_hex(4)
    return token


def _store_addmode_panel(token: str, user_id: int, chat_id: int, message_id: int) -> None:
    _ADDMODE_PANELS[token] = {
        "created": time.time(),
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "message_id": int(message_id),
    }


def _anime_display(anime: str) -> str:
    # The database's canonical Anime name is authoritative; do not synthesize
    # the optional [🎮] marker for display.
    value = str(anime or "").strip()
    return value or "Not set"


def _addmode_text(anime: str, rarity: str) -> str:
    return (
        "⚙️ <b>CARD ADD MODE</b>\n\n"
        "သင်ထည့်သွင်းလိုသော Anime ကိုရွေးပါ။\n\n"
        f"Anime: <b>{escape_html(_anime_display(anime))}</b>\n"
        f"Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "Set a default Anime + Rarity, then add many cards quickly.\n"
        "You can also use: <code>/addmode Anime | Lg</code>"
    )


def _addanime_panel_text(anime: str, rarity: str, page: int, total: int, page_size: int) -> str:
    total_pages = max(1, (total + page_size - 1) // page_size)
    return (
        "🌴 <b>ADD ANIME</b>\n\n"
        f"Current Anime: <b>{escape_html(anime or 'Not set')}</b>\n"
        f"Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "Database Anime List\n"
        f"Page <b>{page + 1}</b> / <b>{total_pages}</b>"
    )


def _addanime_keyboard(
    user_id: int,
    token: str,
    anime_list: list[str],
    current_anime: str,
    page: int,
    total: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for index, anime in enumerate(anime_list):
        selected = str(anime).lower() == str(current_anime or "").lower()
        rows.append([
            action_button(
                ("✅ " if selected else "") + str(anime),
                "primary",
                callback_data=f"addanime:{user_id}:{token}:pick:{index}",
            )
        ])

    total_pages = max(1, (total + page_size - 1) // page_size)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(action_button("Back", "primary", callback_data=f"addanime:{user_id}:{token}:back"))
    if page + 1 < total_pages:
        nav.append(action_button("Next", "primary", callback_data=f"addanime:{user_id}:{token}:next"))
    if nav:
        rows.append(nav)

    rows.append([
        action_button("Add New", "success", switch_inline_query_current_chat=f"addanime:{token} "),
        action_button("Close", "danger", callback_data=f"addanime:{user_id}:{token}:close"),
    ])
    return InlineKeyboardMarkup(rows)


async def _send_addmode_panel(message, user_id: int) -> None:
    anime, rarity = await get_add_mode(user_id)
    token = _picker_token(_ADDMODE_PANELS)
    sent = await message.reply_text(
        _addmode_text(anime, rarity),
        parse_mode="HTML",
        reply_markup=_addmode_keyboard(user_id, token),
    )
    _store_addmode_panel(token, user_id, int(sent.chat_id), int(sent.message_id))


async def addanime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if (
        not user
        or not message
        or not is_allowed_add_chat(update)
        or not await is_allowed_adder(user)
    ):
        return

    args = list(context.args or [])
    if args:
        raw_anime = " ".join(args).strip()
        if len(raw_anime) > 120:
            await message.reply_text(
                "❌ Anime name is too long. Please keep it within 120 characters."
            )
            return

        _, rarity = await get_add_mode(user.id)
        anime = await add_anime_to_catalog(raw_anime, user.id)
        await set_add_mode(user.id, anime, rarity)
        await _send_addmode_panel(message, user.id)
        return

    anime, rarity = await get_add_mode(user.id)
    anime_list, total = await list_anime_catalog_page(0, 8)
    token = _picker_token(_ANIME_PICKERS)
    sent = await message.reply_text(
        _addanime_panel_text(anime, rarity, 0, total, 8),
        parse_mode="HTML",
        reply_markup=_addanime_keyboard(user.id, token, anime_list, anime, 0, total, 8),
    )
    _ANIME_PICKERS[token] = {
        "created": time.time(),
        "user_id": int(user.id),
        "chat_id": int(sent.chat_id),
        "message_id": int(sent.message_id),
        "page": 0,
    }


async def addanime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user or not query.message:
        return

    match = re.match(
        r"^addanime:(\d+):([a-f0-9]{8}):(back|next|addnew|close|pick:\d+)$",
        query.data,
    )
    if not match or int(match.group(1)) != int(query.from_user.id):
        await query.answer("Not your anime selector.", show_alert=True)
        return

    token = match.group(2)
    item = _ANIME_PICKERS.get(token)
    if not item or int(item.get("user_id", 0)) != int(query.from_user.id):
        _ANIME_PICKERS.pop(token, None)
        await query.answer("Anime selector expired. Use /addanime again.", show_alert=True)
        return
    if int(item.get("chat_id", 0)) != int(query.message.chat_id):
        await query.answer("Invalid anime selector.", show_alert=True)
        return
    if time.time() - float(item.get("created", 0)) > _ANIME_PICKER_TTL:
        _ANIME_PICKERS.pop(token, None)
        await query.answer("Anime selector expired. Use /addanime again.", show_alert=True)
        return

    action = match.group(3)

    if action == "close":
        _ANIME_PICKERS.pop(token, None)
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "addnew":
        await query.answer()
        await query.edit_message_text(
            "➕ <b>ADD NEW ANIME</b>\n\n"
            "Press <b>Add New</b>, type the Anime name, then choose\n"
            "<b>➕ Add</b> from the inline results.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    action_button(
                        "Add New",
                        "success",
                        switch_inline_query_current_chat=f"addanime:{token} ",
                    ),
                    action_button(
                        "Back",
                        "primary",
                        callback_data=f"addanime:{query.from_user.id}:{token}:back",
                    ),
                ],
                [
                    action_button(
                        "Close",
                        "danger",
                        callback_data=f"addanime:{query.from_user.id}:{token}:close",
                    ),
                ],
            ]),
        )
        return

    page = int(item.get("page", 0))
    if action == "back":
        page -= 1
    elif action == "next":
        page += 1
    else:
        index = int(action.split(":", 1)[1])
        anime_list, _ = await list_anime_catalog_page(page, 8)
        if index < 0 or index >= len(anime_list):
            await query.answer("Anime list changed. Open /addanime again.", show_alert=True)
            return

        anime = await canonical_anime(anime_list[index])
        _, rarity = await get_add_mode(query.from_user.id)
        await set_add_mode(query.from_user.id, anime, rarity)

        _ANIME_PICKERS.pop(token, None)
        mode_token = _picker_token(_ADDMODE_PANELS)
        await query.answer("Anime selected.")
        await query.edit_message_text(
            _addmode_text(anime, rarity),
            parse_mode="HTML",
            reply_markup=_addmode_keyboard(query.from_user.id, mode_token),
        )
        _store_addmode_panel(
            mode_token,
            query.from_user.id,
            int(query.message.chat_id),
            int(query.message.message_id),
        )
        return

    anime, rarity = await get_add_mode(query.from_user.id)
    anime_list, total = await list_anime_catalog_page(page, 8)
    max_page = max(0, (total - 1) // 8)
    page = max(0, min(page, max_page))
    item["page"] = page
    item["created"] = time.time()
    await query.answer()
    await query.edit_message_text(
        _addanime_panel_text(anime, rarity, page, total, 8),
        parse_mode="HTML",
        reply_markup=_addanime_keyboard(query.from_user.id, token, anime_list, anime, page, total, 8),
    )



def _token() -> str:
    _prune_pending()
    token = secrets.token_hex(5)
    while token in _PENDING:
        token = secrets.token_hex(5)
    return token


def is_forwarded_message(msg) -> bool:
    return any(
        getattr(msg, attr, None)
        for attr in (
            "forward_origin",
            "forward_date",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
        )
    )


def is_limited_card(parsed: dict, card_id_provided: bool) -> bool:
    card_id = str(parsed.get("cardId", "")).strip()
    rarity = str(parsed.get("rarity", "")).strip()
    return rarity.lower() == str(LIMITED_RARITY_NAME).lower() or (
        card_id_provided and bool(card_id) and not card_id.isdigit()
    )


def is_allowed_add_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return False
    return int(chat.id) in {int(x) for x in ADDER_GROUP_IDS}


async def is_allowed_adder(user) -> bool:
    if is_owner(user):
        return True
    user_id = getattr(user, "id", 0)
    if not user_id:
        return False
    settings = await get_db().bot_settings.find_one({"_id": SETTINGS_ID}, {"adderIds": 1})
    return int(user_id) in [int(x) for x in (settings or {}).get("adderIds", [])]


def _update_session_key(user_id: int, chat_id: int) -> tuple[int, int]:
    return int(user_id), int(chat_id)


async def update_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Begin an explicit update session for one existing card ID."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    key = _update_session_key(user.id, message.chat_id)

    if not is_allowed_add_chat(update):
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text("❌ /update can only be used in the configured Adding Group.")
        return
    if not await is_allowed_adder(user):
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text("❌ You are not allowed to update cards. Ask the owner to grant Adding permission.")
        return

    # A new /update attempt always replaces or clears the previous target for
    # this user/chat, so a failed ID lookup cannot leave a stale target active.
    _PENDING_UPDATES.pop(key, None)
    _prune_pending()
    args = list(context.args or [])
    if len(args) != 1:
        await message.reply_text("Usage: <code>/update ID</code>\nExample: <code>/update 25</code>", parse_mode="HTML")
        return

    card_id = str(args[0] or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", card_id):
        await message.reply_text("❌ Invalid Card ID. Use <code>/update ID</code>.", parse_mode="HTML")
        return

    db = get_db()
    normal_doc = await db["photos"].find_one({"cardId": card_id})
    limited_doc = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": card_id})
    if normal_doc and limited_doc:
        await message.reply_text(
            f"❌ Card ID <code>{escape_html(card_id)}</code> exists in both card collections. "
            "Please ask the owner to resolve the duplicate ID before updating.",
            parse_mode="HTML",
        )
        return

    target = normal_doc or limited_doc
    if not target:
        await message.reply_text(
            f"❌ Card ID <code>{escape_html(card_id)}</code> was not found. No card was changed.",
            parse_mode="HTML",
        )
        return

    collection_name = LIMITED_CARDS_COLLECTION if limited_doc else "photos"
    if collection_name == LIMITED_CARDS_COLLECTION and not is_owner(user):
        await message.reply_text("❌ Limited cards can only be updated by the owner.")
        return

    _PENDING_UPDATES[key] = {
        "created": time.time(),
        "user_id": int(user.id),
        "chat_id": int(message.chat_id),
        "card_id": card_id,
        "collection_name": collection_name,
    }

    media_type = str(target.get("mediaType") or "unknown").strip().title()
    await message.reply_text(
        "♻️ <b>CARD UPDATE</b>\n\n"
        f"🆔 <b>ID:</b> <code>{escape_html(card_id)}</code>\n"
        f"🎴 <b>Name:</b> {escape_html(target.get('name', ''))}\n"
        f"🏷 <b>Rarity:</b> {escape_html(target.get('rarity', ''))}\n"
        f"🌴 <b>Anime:</b> {escape_html(target.get('anime', ''))}\n"
        f"🎞 <b>Media:</b> {escape_html(media_type)}\n\n"
        "ဒီ Card ကို update လုပ်ရန် Media အသစ်ကို Caption နဲ့အတူ ပို့ပါ —\n"
        "<code>/update New Name | Lg | Anime Name</code>\n\n"
        "Name, Rarity နဲ့ Anime သုံးခုလုံးကို ပေးရပါမယ်။\n"
        "ဒီ session က 10 မိနစ်အတွင်းသာ အကျုံးဝင်ပြီး Target ID ကို မပြောင်းပါ။",
        parse_mode="HTML",
    )


def _extract_message_media(msg) -> dict | None:
    if msg.photo:
        media = msg.photo[-1]
        return {"mediaType": "photo", "fileId": media.file_id, "fileUniqueId": media.file_unique_id, "mimeType": "", "fileName": ""}
    if msg.video:
        media = msg.video
        return {"mediaType": "video", "fileId": media.file_id, "fileUniqueId": media.file_unique_id, "mimeType": media.mime_type or "video/mp4", "fileName": media.file_name or ""}
    if msg.animation:
        media = msg.animation
        return {"mediaType": "animation", "fileId": media.file_id, "fileUniqueId": media.file_unique_id, "mimeType": media.mime_type or "image/gif", "fileName": media.file_name or ""}
    if msg.document:
        media = msg.document
        mime_type = media.mime_type or ""
        if not mime_type.startswith(SUPPORTED_DOCUMENT_MIME_PREFIXES):
            return None
        return {"mediaType": "document", "fileId": media.file_id, "fileUniqueId": media.file_unique_id, "mimeType": mime_type, "fileName": media.file_name or ""}
    return None


def _database_caption(action: str, parsed: dict, adder) -> str:
    icon = "✅" if action == "Saved" else "♻️"
    return (
        f"{icon} <b>{escape_html(action)}</b>\n\n"
        f"👤 <b>Name:</b> {escape_html(parsed['name'])}\n"
        f"🆔 <b>ID:</b> {escape_html(parsed['cardId'])}\n"
        f"🏷 <b>Rarity:</b> {escape_html(parsed['rarity'])}\n"
        f"🌴 <b>Anime:</b> {escape_html(parsed['anime'])}\n\n"
        f"➕ <b>Added By:</b> {mention_user(adder)}\n"
        f"🆔 <b>Adder ID:</b> {adder.id}"
    )


async def _post_to_card_database_channel(context, file_id: str, caption: str, media_type: str) -> dict:
    if not CARD_DATABASE_CHANNEL_ID:
        raise RuntimeError("CARD_DATABASE_CHANNEL_ID is missing in .env")
    if media_type == "video":
        sent = await context.bot.send_video(chat_id=CARD_DATABASE_CHANNEL_ID, video=file_id, caption=caption, parse_mode="HTML")
        media = sent.video
    elif media_type == "animation":
        sent = await context.bot.send_animation(chat_id=CARD_DATABASE_CHANNEL_ID, animation=file_id, caption=caption, parse_mode="HTML")
        media = sent.animation
    elif media_type == "document":
        sent = await context.bot.send_document(chat_id=CARD_DATABASE_CHANNEL_ID, document=file_id, caption=caption, parse_mode="HTML")
        media = sent.document
    else:
        sent = await context.bot.send_photo(chat_id=CARD_DATABASE_CHANNEL_ID, photo=file_id, caption=caption, parse_mode="HTML")
        media = sent.photo[-1] if sent.photo else None
        media_type = "photo"
    return {
        "storageChatId": sent.chat_id,
        "storageMessageId": sent.message_id,
        "fileId": media.file_id if media else file_id,
        "fileUniqueId": media.file_unique_id if media else "",
        "mediaType": media_type,
    }


async def _delete_card_database_message(context, storage: dict) -> bool:
    chat_id = storage.get("storageChatId")
    message_id = storage.get("storageMessageId")
    if not chat_id or not message_id:
        return True
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=int(message_id))
        return True
    except TelegramError as exc:
        print(f"CARD ADD ARCHIVE CLEANUP WARNING: {exc!r}", flush=True)
        return False
    except Exception as exc:
        print(f"CARD ADD ARCHIVE CLEANUP WARNING: {exc!r}", flush=True)
        return False


def _database_caption_from_doc(action: str, doc: dict) -> str:
    added_by = int(doc.get("addedBy", 0) or 0)
    adder_name = f"User {added_by}" if added_by else "Unknown"
    return (
        f"{'✅' if action == 'Saved' else '♻️'} <b>{escape_html(action)}</b>\n\n"
        f"👤 <b>Name:</b> {escape_html(doc.get('name', ''))}\n"
        f"🆔 <b>ID:</b> {escape_html(doc.get('cardId', ''))}\n"
        f"🏷 <b>Rarity:</b> {escape_html(doc.get('rarity', ''))}\n"
        f"🌴 <b>Anime:</b> {escape_html(doc.get('anime', ''))}\n\n"
        f"➕ <b>Added By:</b> <a href=\"tg://user?id={added_by}\">{escape_html(adder_name)}</a>\n"
        f"🆔 <b>Adder ID:</b> {added_by}"
    )


async def _restore_card_database_message(context, old: dict) -> bool:
    chat_id = old.get("storageChatId")
    message_id = old.get("storageMessageId")
    file_id = str(old.get("fileId") or "")
    if not chat_id or not message_id or not file_id:
        return False

    old_caption = str(old.get("storageCaption") or "").strip()
    if not old_caption:
        old_caption = _database_caption_from_doc("Saved", old)

    media_type = str(old.get("mediaType") or "photo").strip().lower()
    try:
        if media_type == "video":
            media = InputMediaVideo(media=file_id, caption=old_caption, parse_mode="HTML")
        elif media_type == "animation":
            media = InputMediaAnimation(media=file_id, caption=old_caption, parse_mode="HTML")
        elif media_type == "document":
            media = InputMediaDocument(media=file_id, caption=old_caption, parse_mode="HTML")
        else:
            media = InputMediaPhoto(media=file_id, caption=old_caption, parse_mode="HTML")
        await context.bot.edit_message_media(
            chat_id=int(chat_id),
            message_id=int(message_id),
            media=media,
        )
        return True
    except TelegramError as exc:
        print(f"CARD ADD ARCHIVE ROLLBACK WARNING: {exc!r}", flush=True)
        return False
    except Exception as exc:
        print(f"CARD ADD ARCHIVE ROLLBACK WARNING: {exc!r}", flush=True)
        return False


async def _edit_card_database_message(context, old: dict, new: dict, caption: str) -> dict | None:
    chat_id = old.get("storageChatId")
    message_id = old.get("storageMessageId")
    if not chat_id or not message_id:
        return None
    try:
        media_type = new["mediaType"]
        file_id = new["fileId"]
        if media_type == "video":
            media = InputMediaVideo(media=file_id, caption=caption, parse_mode="HTML")
        elif media_type == "animation":
            media = InputMediaAnimation(media=file_id, caption=caption, parse_mode="HTML")
        elif media_type == "document":
            media = InputMediaDocument(media=file_id, caption=caption, parse_mode="HTML")
        else:
            media = InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML")
        await context.bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media)
        return {
            "storageChatId": int(chat_id),
            "storageMessageId": int(message_id),
            "fileId": file_id,
            "fileUniqueId": new.get("fileUniqueId", ""),
            "mediaType": media_type,
        }
    except TelegramError:
        return None


def _addmode_keyboard(user_id: int, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        action_button(
            "Rarity",
            "primary",
            callback_data=f"addmode:{user_id}:rarity",
        ),
        action_button(
            "Anime Search",
            "success",
            switch_inline_query_current_chat=f"animepick:{token} ",
        ),
        action_button(
            "Close",
            "danger",
            callback_data=f"addmode:{user_id}:close",
        ),
    ]])


def _rarity_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rarities = [
        rarity
        for rarity in RARITY_ORDER
        if str(rarity).lower() != str(LIMITED_RARITY_NAME).lower()
    ]
    for i in range(0, len(rarities), 2):
        row: list[InlineKeyboardButton] = []
        for rarity in rarities[i:i + 2]:
            row.append(
                rarity_button(
                    str(rarity),
                    str(rarity),
                    "primary",
                    callback_data=f"addmode:{user_id}:rarity_select:{RARITY_ORDER.index(rarity)}",
                )
            )
        rows.append(row)
    rows.append([
        action_button("Back", "primary", callback_data=f"addmode:{user_id}:main")
    ])
    return InlineKeyboardMarkup(rows)


async def addmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if (
        not user
        or not message
        or not is_allowed_add_chat(update)
        or not await is_allowed_adder(user)
    ):
        return

    args = list(context.args or [])
    if args and args[0].lower() in {"off", "clear", "reset"}:
        from utils.card_adding import clear_add_mode
        await clear_add_mode(user.id)
        await _send_addmode_panel(message, user.id)
        return

    if args:
        parts = [part.strip() for part in " ".join(args).split("|")]
        old_anime, old_rarity = await get_add_mode(user.id)
        selected_anime = parts[0] if parts and parts[0] else old_anime
        supplied_rarity = parts[1] if len(parts) > 1 else ""

        if len(parts) > 2 and any(parts[2:]):
            await message.reply_text(
                "❌ Invalid /addmode format. Use: <code>/addmode Anime | Lg</code>",
                parse_mode="HTML",
            )
            return

        if supplied_rarity:
            selected_rarity = normalize_add_rarity(supplied_rarity)
            if not selected_rarity:
                await message.reply_text(
                    "❌ Invalid rarity. Use one of the supported rarity names or short codes.",
                    parse_mode="HTML",
                )
                return
        else:
            selected_rarity = old_rarity

        if not selected_anime:
            await message.reply_text(
                "❌ Anime is missing. Use <code>/addmode Anime | Lg</code>.",
                parse_mode="HTML",
            )
            return
        if not selected_rarity:
            await message.reply_text(
                "❌ Rarity is missing. Use <code>/addmode Anime | Lg</code> or choose Rarity below.",
                parse_mode="HTML",
            )
            return

        selected_anime = await add_anime_to_catalog(selected_anime, user.id)
        await set_add_mode(user.id, selected_anime, selected_rarity)
        await _send_addmode_panel(message, user.id)
        return

    await _send_addmode_panel(message, user.id)


async def addmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user or not query.message:
        return

    match = re.match(
        r"^addmode:(\d+):(rarity|close|main|rarity_select:\d+)$",
        query.data,
    )
    if not match or int(match.group(1)) != int(query.from_user.id):
        await query.answer("Not your add mode.", show_alert=True)
        return

    user_id = int(query.from_user.id)
    action = match.group(2)
    anime, rarity = await get_add_mode(user_id)

    if action == "close":
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "rarity":
        await query.answer()
        await query.edit_message_reply_markup(
            reply_markup=_rarity_keyboard(user_id)
        )
        return

    if action == "main":
        token = _picker_token(_ADDMODE_PANELS)
        await query.answer()
        await query.edit_message_text(
            _addmode_text(anime, rarity),
            parse_mode="HTML",
            reply_markup=_addmode_keyboard(user_id, token),
        )
        _store_addmode_panel(
            token,
            user_id,
            int(query.message.chat_id),
            int(query.message.message_id),
        )
        return

    idx = int(action.split(":", 1)[1])
    if idx < 0 or idx >= len(RARITY_ORDER):
        await query.answer("Invalid rarity.", show_alert=True)
        return

    selected = RARITY_ORDER[idx]
    if str(selected).lower() == str(LIMITED_RARITY_NAME).lower():
        await query.answer(
            "Limited requires a custom ID and is not available as a default rarity.",
            show_alert=True,
        )
        return

    await set_add_mode(user_id, anime, selected)
    anime, rarity = await get_add_mode(user_id)
    token = _picker_token(_ADDMODE_PANELS)

    await query.answer(f"{rarity} selected.")
    await query.edit_message_text(
        _addmode_text(anime, rarity),
        parse_mode="HTML",
        reply_markup=_addmode_keyboard(user_id, token),
    )
    _store_addmode_panel(
        token,
        user_id,
        int(query.message.chat_id),
        int(query.message.message_id),
    )



def _pending_keyboard(user_id: int, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("♻️ Update Existing", callback_data=f"adddup:{user_id}:{token}:update"),
        InlineKeyboardButton("➕ Create New", callback_data=f"adddup:{user_id}:{token}:new"),
    ], [
        InlineKeyboardButton("✕ Cancel", callback_data=f"adddup:{user_id}:{token}:cancel"),
    ]])


async def _save_card(
    context,
    user,
    parsed: dict,
    media_info: dict,
    force_new: bool = False,
    update_only: bool = False,
    expected_collection: str | None = None,
) -> tuple[bool, str]:
    card_id_provided = bool(parsed.pop("_cardIdProvided", False))
    parsed.pop("_animeProvided", None)
    parsed.pop("_rarityProvided", None)
    parsed["cardId"] = str(parsed.get("cardId", "")).strip()

    # Preserve the canonical Anime value selected from the database. In
    # particular, do not manufacture a [🎮] suffix during card saving.
    limited_card = is_limited_card(parsed, card_id_provided)
    if limited_card and not is_owner(user):
        return False, "❌ Limited cards can only be added/updated by the owner."
    if limited_card:
        parsed["rarity"] = str(LIMITED_RARITY_NAME)
        if not card_id_provided or not parsed["cardId"]:
            return False, "❌ Limited cards require a custom ID. Example: /add 1a | Name | Limited | Anime"
    elif not card_id_provided:
        parsed["cardId"] = await next_card_id()
    elif not parsed["cardId"].isdigit():
        return False, "❌ Non-numeric IDs are only allowed for Limited cards."

    collection_name = LIMITED_CARDS_COLLECTION if limited_card else "photos"
    other_collection_name = "photos" if limited_card else LIMITED_CARDS_COLLECTION
    db = get_db()

    if update_only and expected_collection and collection_name != expected_collection:
        return False, "❌ The selected card type cannot be changed with /update. The target card was not modified."

    existing = await db[collection_name].find_one({"cardId": parsed["cardId"]})
    if update_only and not existing:
        return False, f"❌ Target Card ID {escape_html(parsed['cardId'])} no longer exists. Run /update {escape_html(parsed['cardId'])} again."
    duplicate_other = await db[other_collection_name].find_one({"cardId": parsed["cardId"]}, {"_id": 1})
    if update_only and duplicate_other:
        return False, f"❌ Card ID {escape_html(parsed['cardId'])} exists in both card collections. The target was not modified."
    if duplicate_other and not existing:
        return False, f"❌ Card ID {parsed['cardId']} already exists in {other_collection_name}."

    media_dup = None if force_new else await find_duplicate_media(media_info.get("fileUniqueId", ""), parsed["cardId"])
    name_dup = None if force_new else await find_possible_duplicate(parsed["name"], parsed["anime"], parsed["cardId"])
    if media_dup or name_dup:
        target = media_dup or name_dup
        duplicate_message = (
            f"Name: {escape_html(target.get('name', ''))}\n"
            f"ID: {escape_html(target.get('cardId', ''))}\n"
            f"Anime: {escape_html(target.get('anime', ''))}\n"
            f"Rarity: {escape_html(target.get('rarity', ''))}"
        )
        if update_only:
            return False, (
                "⚠️ <b>UPDATE NOT APPLIED — DUPLICATE DETECTED</b>\n\n"
                f"{duplicate_message}\n\n"
                "ဒီ Card ကို Update မလုပ်ထားပါ။ Target ID ကို အတိအကျ ထိန်းထားပါတယ်။"
            )
        return False, (
            f"⚠️ POSSIBLE DUPLICATE\n\n"
            f"{duplicate_message}"
        )

    action = "Update" if existing else "Saved"
    caption = _database_caption(action, parsed, user)

    storage = None
    archive_edited_existing = False
    if existing:
        new_media = {**media_info}
        if not existing.get("storageChatId") or not existing.get("storageMessageId"):
            raise RuntimeError(
                "Existing card has no valid archive message. MongoDB was not changed; "
                "please repair the archive record before updating this card."
            )
        edited = await _edit_card_database_message(context, existing, new_media, caption)
        if not edited:
            raise RuntimeError(
                "Existing card archive could not be updated. MongoDB was not changed."
            )
        storage = edited
        archive_edited_existing = True
    else:
        storage = await _post_to_card_database_channel(
            context,
            media_info["fileId"],
            caption,
            media_info["mediaType"],
        )

    storage["storageCaption"] = caption

    now = utcnow()
    doc = {
        **parsed,
        "fileId": storage["fileId"],
        "fileUniqueId": storage.get("fileUniqueId") or media_info.get("fileUniqueId", ""),
        "mediaType": storage.get("mediaType", media_info["mediaType"]),
        "mimeType": media_info.get("mimeType", ""),
        "fileName": media_info.get("fileName", ""),
        "storageChatId": storage["storageChatId"],
        "storageMessageId": storage["storageMessageId"],
        "storageCaption": storage["storageCaption"],
        "addedBy": user.id,
        "updatedAt": now,
    }

    try:
        await db[collection_name].update_one(
            {"cardId": parsed["cardId"]},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
    except Exception:
        # Archive and MongoDB are not a single ACID transaction. Compensate the
        # Telegram archive mutation so a failed MongoDB write does not leave a
        # new orphan archive or a stale archive for an unchanged card.
        if existing and archive_edited_existing:
            restored = await _restore_card_database_message(context, existing)
            if not restored:
                print(
                    f"CARD ADD ARCHIVE ROLLBACK FAILED: card_id={parsed.get('cardId')}",
                    flush=True,
                )
        elif not existing and storage:
            cleaned = await _delete_card_database_message(context, storage)
            if not cleaned:
                print(
                    f"CARD ADD ARCHIVE CLEANUP FAILED: card_id={parsed.get('cardId')}",
                    flush=True,
                )
        raise
    try:
        await upsert_card(doc, collection_name)
    except Exception as exc:
        # SQLite is disposable; never make a successful MongoDB write look like
        # a failed card add.
        print(f"CARD ADD SQLITE SYNC WARNING: {exc!r}", flush=True)

    if not limited_card:
        await sync_counter_at_least(parsed["cardId"])

    icon = "♻️" if action == "Update" else "✅"
    return True, (
        f"{icon} <b>Card {action}</b>\n\n"
        f"🆔 ID: <b>{escape_html(parsed['cardId'])}</b>\n"
        f"🎴 Name: <b>{escape_html(parsed['name'])}</b>\n"
        f"🏷 Rarity: <b>{escape_html(parsed['rarity'])}</b>\n"
        f"🌴 Anime: <b>{escape_html(parsed['anime'])}</b>\n"
        f"🎞 Media: {escape_html(storage.get('mediaType', media_info['mediaType']))}\n"
        f"🗄 Archive Message: <code>{storage['storageMessageId']}</code>"
    )


def _rarity_prompt_keyboard(user_id: int, token: str) -> InlineKeyboardMarkup:
    aliases = rarity_aliases()
    buttons: list[InlineKeyboardButton] = []

    for code in ("Un", "Co", "Ra", "Lg", "My", "Dv", "Cv", "Ca", "Su"):
        rarity = aliases.get(code.lower())
        if not rarity or str(rarity).lower() == str(LIMITED_RARITY_NAME).lower():
            continue
        buttons.append(
            action_button(
                code,
                "primary",
                callback_data=f"addrarity:{user_id}:{token}:{rarity}",
            )
        )

    return InlineKeyboardMarkup([
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ])


async def _prompt_for_rarity(update: Update, parsed: dict, media_info: dict) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    token = _picker_token(_PENDING_RARITY)
    sent = await message.reply_text(
        "🎴 <b>RARITY REQUIRED</b>\n\n"
        f"Name: <b>{escape_html(parsed.get('name', ''))}</b>\n"
        f"Anime: <b>{escape_html(parsed.get('anime', ''))}</b>\n\n"
        "ဒီ Media အတွက် Rarity သတ်မှတ်ပေးပါ။\n"
        "Code ကို တိုက်ရိုက်ပို့နိုင်ပါတယ် — <code>Un Co Ra Lg My Dv Cv Ca Su</code>",
        parse_mode="HTML",
        reply_markup=_rarity_prompt_keyboard(user.id, token),
    )
    _PENDING_RARITY[token] = {
        "created": time.time(),
        "user_id": int(user.id),
        "chat_id": int(message.chat_id),
        "prompt_message_id": int(sent.message_id),
        "parsed": dict(parsed),
        "media_info": dict(media_info),
    }


async def _delete_message_safely(message) -> bool:
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def _delete_rarity_prompt(context: ContextTypes.DEFAULT_TYPE, item: dict) -> None:
    prompt_message_id = int(item.get("prompt_message_id", 0) or 0)
    if not prompt_message_id:
        return
    try:
        await context.bot.delete_message(
            chat_id=int(item["chat_id"]),
            message_id=prompt_message_id,
        )
    except Exception:
        pass


async def _process_pending_rarity(context: ContextTypes.DEFAULT_TYPE, token: str, rarity: str, user) -> None:
    item = _PENDING_RARITY.get(token)
    if not item or int(item.get("user_id", 0)) != int(user.id):
        return
    if time.time() - float(item.get("created", 0)) > _PENDING_TTL:
        _PENDING_RARITY.pop(token, None)
        return

    parsed = dict(item.get("parsed") or {})
    parsed["rarity"] = rarity
    parsed["_rarityProvided"] = True
    media_info = dict(item.get("media_info") or {})

    try:
        ok, result = await _save_card(context, user, parsed, media_info)
    except TelegramError as exc:
        await context.bot.send_message(
            chat_id=int(item["chat_id"]),
            text=f"❌ Telegram/archive error: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        await context.bot.send_message(
            chat_id=int(item["chat_id"]),
            text=f"❌ Card add failed: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return

    _PENDING_RARITY.pop(token, None)
    await _delete_rarity_prompt(context, item)

    if ok:
        await context.bot.send_message(
            chat_id=int(item["chat_id"]),
            text=result,
            parse_mode="HTML",
        )
        return

    duplicate_token = _token()
    _PENDING[duplicate_token] = {
        "created": time.time(),
        "user_id": int(user.id),
        "parsed": dict(parsed),
        "media_info": dict(media_info),
    }
    await context.bot.send_message(
        chat_id=int(item["chat_id"]),
        text=result + "\n\nChoose how to continue:",
        parse_mode="HTML",
        reply_markup=_pending_keyboard(user.id, duplicate_token),
    )


async def add_rarity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user or not query.message:
        return

    match = re.match(r"^addrarity:(\d+):([a-f0-9]{8}):(.+)$", query.data)
    if not match or int(match.group(1)) != int(query.from_user.id):
        await query.answer("Not your rarity selection.", show_alert=True)
        return

    rarity = normalize_add_rarity(match.group(3))
    if not rarity or str(rarity).lower() == str(LIMITED_RARITY_NAME).lower():
        await query.answer("Invalid rarity.", show_alert=True)
        return

    token = match.group(2)
    item = _PENDING_RARITY.get(token)
    if not item or int(item.get("chat_id", 0)) != int(query.message.chat_id):
        await query.answer("This rarity request expired.", show_alert=True)
        return

    await query.answer(f"{rarity} selected.")
    await _process_pending_rarity(context, token, rarity, query.from_user)


class _PendingRarityTextFilter(filters.MessageFilter):
    def filter(self, message) -> bool:
        text = str(getattr(message, "text", "") or "").strip()
        if not text or not normalize_add_rarity(text):
            return False

        user = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
        if not user or not chat:
            return False

        now = time.time()
        return any(
            int(item.get("user_id", 0)) == int(user.id)
            and int(item.get("chat_id", 0)) == int(chat.id)
            and now - float(item.get("created", 0)) <= _PENDING_TTL
            for item in _PENDING_RARITY.values()
        )


_PENDING_RARITY_TEXT_FILTER = _PendingRarityTextFilter()


async def add_rarity_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update):
        return

    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return

    rarity = normalize_add_rarity(message.text.strip())
    if not rarity or str(rarity).lower() == str(LIMITED_RARITY_NAME).lower():
        return

    _prune_pending()
    matching = [
        (token, item)
        for token, item in _PENDING_RARITY.items()
        if int(item.get("user_id", 0)) == int(user.id)
        and int(item.get("chat_id", 0)) == int(message.chat_id)
    ]
    if not matching:
        return

    token, _ = max(matching, key=lambda pair: float(pair[1].get("created", 0)))
    await _delete_message_safely(message)
    await _process_pending_rarity(context, token, rarity, user)


def _anime_article_result(token: str, anime: str, purpose: str, apply_command: str) -> InlineQueryResultArticle:
    safe_name = " ".join(str(anime or "").strip().split())
    return InlineQueryResultArticle(
        id=f"{purpose}:{md5(safe_name.encode('utf-8')).hexdigest()[:24]}",
        title=safe_name,
        description="Select this Anime.",
        input_message_content=InputTextMessageContent(
            f"/{apply_command} {token} {safe_name}"
        ),
    )


async def _answer_anime_inline(query, results: list, next_offset: str = "") -> None:
    try:
        await query.answer(
            results,
            cache_time=0,
            is_personal=True,
            next_offset=next_offset,
        )
    except Exception as exc:
        print("ADD ANIME INLINE ANSWER ERROR:", repr(exc), flush=True)


async def addmode_anime_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query or not query.from_user:
        return

    match = re.fullmatch(
        r"animepick:([a-f0-9]{8})(?:\s+(.*))?",
        (query.query or "").strip(),
        re.I,
    )
    if not match:
        return

    token = match.group(1)
    item = _ADDMODE_PANELS.get(token)
    if not item or int(item.get("user_id", 0)) != int(query.from_user.id):
        await _answer_anime_inline(query, [])
        return
    if not await is_allowed_adder(query.from_user):
        await _answer_anime_inline(query, [])
        return

    search = (match.group(2) or "").strip()
    try:
        offset = max(0, int(query.offset or "0"))
    except ValueError:
        offset = 0

    try:
        names, has_more = await search_anime_catalog(search, offset, 50)
    except Exception as exc:
        print("ADD MODE ANIME SEARCH ERROR:", repr(exc), flush=True)
        names, has_more = [], False

    results = [
        _anime_article_result(token, name, "addmodeanime", "addmodeanimeapply")
        for name in names
    ]
    await _answer_anime_inline(
        query,
        results,
        str(offset + 50) if has_more else "",
    )


async def addanime_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query or not query.from_user:
        return

    match = re.fullmatch(
        r"addanime:([a-f0-9]{8})(?:\s+(.*))?",
        (query.query or "").strip(),
        re.I,
    )
    if not match:
        return

    token = match.group(1)
    item = _ANIME_PICKERS.get(token)
    if not item or int(item.get("user_id", 0)) != int(query.from_user.id):
        await _answer_anime_inline(query, [])
        return
    if not await is_allowed_adder(query.from_user):
        await _answer_anime_inline(query, [])
        return

    search = (match.group(2) or "").strip()
    try:
        offset = max(0, int(query.offset or "0"))
    except ValueError:
        offset = 0

    names, has_more = await search_anime_catalog(search, offset, 49)
    results = [
        _anime_article_result(token, name, "addanimeexisting", "addanimeapply")
        for name in names
    ]

    if search and offset == 0 and not await anime_catalog_exists(search):
        results.append(
            InlineQueryResultArticle(
                id=f"addnew:{md5(search.encode('utf-8')).hexdigest()[:24]}",
                title=f'➕ Add "{search}"',
                description="Create this Anime and set it as the default.",
                input_message_content=InputTextMessageContent(
                    f"/addanimeapply {token} {search}"
                ),
            )
        )

    await _answer_anime_inline(
        query,
        results[:50],
        str(offset + 49) if has_more else "",
    )


async def _edit_saved_addmode_panel(
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    user_id: int,
) -> None:
    item = _ADDMODE_PANELS.get(token)
    if not item:
        return

    anime, rarity = await get_add_mode(user_id)
    try:
        await context.bot.edit_message_text(
            chat_id=int(item["chat_id"]),
            message_id=int(item["message_id"]),
            text=_addmode_text(anime, rarity),
            parse_mode="HTML",
            reply_markup=_addmode_keyboard(user_id, token),
        )
        item["created"] = time.time()
    except Exception as exc:
        print("ADD MODE PANEL UPDATE ERROR:", repr(exc), flush=True)


async def addmode_anime_apply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    args = list(context.args or [])
    if (
        not user
        or not message
        or len(args) < 2
        or not is_allowed_add_chat(update)
        or not await is_allowed_adder(user)
    ):
        return

    token = args[0]
    anime = " ".join(args[1:]).strip()
    item = _ADDMODE_PANELS.get(token)
    if not item or int(item.get("user_id", 0)) != int(user.id):
        return
    if len(anime) > 120:
        await _delete_message_safely(message)
        return

    _, rarity = await get_add_mode(user.id)
    anime = await add_anime_to_catalog(anime, user.id)
    await set_add_mode(user.id, anime, rarity)
    await _delete_message_safely(message)
    await _edit_saved_addmode_panel(context, token, user.id)


async def addanime_apply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    args = list(context.args or [])
    if (
        not user
        or not message
        or len(args) < 2
        or not is_allowed_add_chat(update)
        or not await is_allowed_adder(user)
    ):
        return

    token = args[0]
    anime = " ".join(args[1:]).strip()
    item = _ANIME_PICKERS.get(token)
    if not item or int(item.get("user_id", 0)) != int(user.id):
        return
    if len(anime) > 120:
        await _delete_message_safely(message)
        return

    _, rarity = await get_add_mode(user.id)
    anime = await add_anime_to_catalog(anime, user.id)
    await set_add_mode(user.id, anime, rarity)

    _ANIME_PICKERS.pop(token, None)
    await _delete_message_safely(message)

    panel_token = _picker_token(_ADDMODE_PANELS)
    try:
        await context.bot.edit_message_text(
            chat_id=int(item["chat_id"]),
            message_id=int(item["message_id"]),
            text=_addmode_text(anime, rarity),
            parse_mode="HTML",
            reply_markup=_addmode_keyboard(user.id, panel_token),
        )
        _store_addmode_panel(
            panel_token,
            user.id,
            int(item["chat_id"]),
            int(item["message_id"]),
        )
    except Exception as exc:
        print("ADD ANIME INLINE APPLY ERROR:", repr(exc), flush=True)


async def _handle_media_add(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: dict, media_info: dict) -> None:
    user = update.effective_user
    if not user:
        return

    anime_mode, rarity_mode = await get_add_mode(user.id)

    if parsed.get("_rarityProvided") and not parsed.get("rarity"):
        await update.effective_message.reply_text(
            "❌ Invalid rarity. Please use one of the supported rarity names or short codes.",
            parse_mode="HTML",
        )
        return

    if not parsed.get("rarity") and rarity_mode:
        parsed["rarity"] = rarity_mode
    if not parsed.get("anime") and anime_mode:
        parsed["anime"] = anime_mode

    if not parsed.get("anime"):
        await update.effective_message.reply_text(
            "❌ Anime is missing. Use <code>/add Name | Rarity | Anime</code> or set <code>/addmode Anime | Rarity</code>.",
            parse_mode="HTML",
        )
        return

    parsed["anime"] = await canonical_anime(parsed["anime"])

    if not parsed.get("rarity"):
        await _prompt_for_rarity(update, parsed, media_info)
        return

    parsed["rarity"] = normalize_add_rarity(parsed["rarity"])
    if not parsed.get("rarity"):
        await update.effective_message.reply_text(
            "❌ Invalid rarity. Please use one of the supported rarity names or short codes.",
            parse_mode="HTML",
        )
        return

    try:
        ok, result = await _save_card(context, user, parsed, media_info)
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"❌ Telegram/archive error: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Card add failed: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return

    if ok:
        await update.effective_message.reply_text(result, parse_mode="HTML")
        return

    token = _token()
    _PENDING[token] = {
        "created": time.time(),
        "user_id": user.id,
        "parsed": dict(parsed),
        "media_info": dict(media_info),
    }
    await update.effective_message.reply_text(
        result + "\n\nChoose how to continue:",
        parse_mode="HTML",
        reply_markup=_pending_keyboard(user.id, token),
    )




async def add_duplicate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not q.from_user:
        return
    match = re.match(r"^adddup:(\d+):([a-f0-9]+):(update|new|cancel)$", q.data)
    if not match or int(match.group(1)) != int(q.from_user.id):
        await q.answer("Not your add action.", show_alert=True)
        return
    await q.answer()
    token, action = match.group(2), match.group(3)
    item = _PENDING.get(token)
    if not item or item.get("user_id") != q.from_user.id or time.time() - item.get("created", 0) > _PENDING_TTL:
        _PENDING.pop(token, None)
        await q.edit_message_text("❌ This add action expired. Please send the card again.")
        return
    if action == "cancel":
        _PENDING.pop(token, None)
        await q.edit_message_text("❌ Card add cancelled.")
        return

    parsed = dict(item["parsed"])
    media_info = dict(item["media_info"])

    if action == "update":
        db = get_db()
        duplicate = await find_duplicate_media(media_info.get("fileUniqueId", ""))
        target_id = str((duplicate or {}).get("cardId", ""))
        if not target_id:
            possible = await find_possible_duplicate(parsed["name"], parsed["anime"])
            target_id = str((possible or {}).get("cardId", ""))
        if target_id:
            parsed["cardId"] = target_id
            parsed["_cardIdProvided"] = True

    elif action == "new":
        # "Create New" must never overwrite an existing card ID. When the
        # original /add explicitly supplied an ID that is already occupied,
        # normal cards receive a fresh auto-generated numeric ID. Limited
        # cards cannot safely auto-generate a replacement custom ID, so ask
        # the owner to submit a different custom ID instead of updating.
        db = get_db()
        supplied_id = str(parsed.get("cardId", "")).strip()
        supplied_id_provided = bool(parsed.get("_cardIdProvided", False))

        if supplied_id_provided and supplied_id:
            normal_exists = await db.photos.find_one({"cardId": supplied_id}, {"_id": 1})
            limited_exists = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": supplied_id}, {"_id": 1})

            if normal_exists or limited_exists:
                if is_limited_card(parsed, True):
                    _PENDING.pop(token, None)
                    await q.edit_message_text(
                        "❌ Limited card ID already exists. Create New needs a different custom Limited ID.",
                    )
                    return

                parsed["cardId"] = ""
                parsed["_cardIdProvided"] = False

    _PENDING.pop(token, None)
    try:
        ok, result = await _save_card(context, q.from_user, parsed, media_info, force_new=(action == "new"))
        await q.edit_message_text(result, parse_mode="HTML")
    except Exception as exc:
        await q.edit_message_text(f"❌ Card add failed: {escape_html(str(exc))}", parse_mode="HTML")


async def _handle_media_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    caption: str,
    media_info: dict,
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    key = _update_session_key(user.id, message.chat_id)
    _prune_pending()
    item = _PENDING_UPDATES.get(key)
    if not item or time.time() - float(item.get("created", 0)) > _PENDING_TTL:
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text(
            "❌ Update session မရှိတော့ပါ။ အရင် <code>/update ID</code> ပို့ပြီးမှ Media အသစ်ကို ပို့ပါ။",
            parse_mode="HTML",
        )
        return

    parsed = parse_update_caption(caption)
    if not parsed:
        await message.reply_text(
            "❌ Invalid update format. Media Caption ကို —\n"
            "<code>/update New Name | Lg | Anime Name</code>\n"
            "ပုံစံအတိုင်း ပို့ပါ။",
            parse_mode="HTML",
        )
        return
    if not parsed.get("rarity"):
        await message.reply_text(
            "❌ Invalid rarity. Use a supported rarity name or short code such as <code>Lg</code>.",
            parse_mode="HTML",
        )
        return
    if len(parsed["name"]) > 120 or len(parsed["anime"]) > 120:
        await message.reply_text("❌ Name နဲ့ Anime တို့ကို စာလုံး 120 ထက် မကျော်ပါစေနဲ့။")
        return

    target_id = str(item.get("card_id") or "").strip()
    collection_name = str(item.get("collection_name") or "")
    if collection_name not in {"photos", LIMITED_CARDS_COLLECTION} or not target_id:
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text("❌ Update session မမှန်ကန်တော့ပါ။ <code>/update ID</code> နဲ့ ပြန်စပါ။", parse_mode="HTML")
        return

    is_limited_target = collection_name == LIMITED_CARDS_COLLECTION
    if is_limited_target and not is_owner(user):
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text("❌ Limited cards can only be updated by the owner.")
        return
    if is_limited_target and str(parsed["rarity"]).lower() != str(LIMITED_RARITY_NAME).lower():
        await message.reply_text(
            "❌ Limited Card ရဲ့ Rarity ကို Limited အတိုင်းထားရပါမယ်။ Target Card မပြောင်းထားပါ။"
        )
        return
    if not is_limited_target and str(parsed["rarity"]).lower() == str(LIMITED_RARITY_NAME).lower():
        await message.reply_text(
            "❌ Normal Card ကို /update နဲ့ Limited Card အဖြစ် မပြောင်းနိုင်ပါ။ Target Card မပြောင်းထားပါ။"
        )
        return

    db = get_db()
    current = await db[collection_name].find_one({"cardId": target_id})
    if not current:
        _PENDING_UPDATES.pop(key, None)
        await message.reply_text(
            f"❌ Target Card ID <code>{escape_html(target_id)}</code> မရှိတော့ပါ။ <code>/update {escape_html(target_id)}</code> နဲ့ ပြန်စပါ။",
            parse_mode="HTML",
        )
        return

    parsed["cardId"] = target_id
    parsed["_cardIdProvided"] = True
    parsed["_animeProvided"] = True
    parsed["_rarityProvided"] = True
    parsed["anime"] = await canonical_anime(parsed["anime"])

    try:
        ok, result = await _save_card(
            context,
            user,
            parsed,
            media_info,
            update_only=True,
            expected_collection=collection_name,
        )
    except TelegramError as exc:
        await message.reply_text(
            f"❌ Telegram/archive error: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        await message.reply_text(
            f"❌ Card update failed: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return

    if ok:
        _PENDING_UPDATES.pop(key, None)
    await message.reply_text(result, parse_mode="HTML")


async def photo_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_add_chat(update) or not update.effective_user:
        return
    msg = update.effective_message
    if not msg:
        return
    media_info = _extract_message_media(msg)
    if not media_info:
        return
    caption = (msg.caption or "").strip()
    looks_like_add = bool(re.match(r"^/add(?:@[^\s]+)?(?:\s|$)", caption, flags=re.I))
    looks_like_update = bool(re.match(r"^/update(?:@[^\s]+)?(?:\s|$)", caption, flags=re.I))
    if is_forwarded_message(msg):
        if looks_like_add:
            await msg.reply_text("❌ Forward add is disabled. Please upload the media directly with /add.")
        elif looks_like_update:
            await msg.reply_text("❌ Forward update is disabled. Please upload the media directly with /update.")
        return
    if not looks_like_add and not looks_like_update:
        return
    if not await is_allowed_adder(update.effective_user):
        await msg.reply_text("❌ You are not allowed to add/update cards. Ask the owner to use /addadder for your account.")
        return

    if looks_like_update:
        await _handle_media_update(update, context, caption, media_info)
        return

    parsed = parse_add_caption(caption)
    if not parsed:
        await msg.reply_text(
            "❌ Invalid add format.\n\n"
            "Fast mode: <code>/add Yelan</code> (uses /addmode defaults)\n"
            "Short mode: <code>/add Yelan | Lg</code>\n"
            "Full mode: <code>/add Yelan | Lg | Genshin Impact</code>\n"
            "Explicit ID: <code>/add 2 | Yelan | Lg | Genshin Impact</code>\n"
            "Limited: <code>/add 1a | Special | Limited | Bika Limited</code>",
            parse_mode="HTML",
        )
        return
    await _handle_media_add(update, context, parsed, media_info)


def register_photo_add_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("update", update_start_cmd))
    app.add_handler(CommandHandler("addmode", addmode_cmd))
    app.add_handler(CommandHandler("addanime", addanime_cmd))
    app.add_handler(CommandHandler("addmodeanimeapply", addmode_anime_apply_handler))
    app.add_handler(CommandHandler("addanimeapply", addanime_apply_handler))

    app.add_handler(
        CallbackQueryHandler(
            addmode_callback,
            pattern=r"^addmode:\d+:(?:rarity|close|main|rarity_select:\d+)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            addanime_callback,
            pattern=r"^addanime:\d+:[a-f0-9]{8}:(?:back|next|addnew|close|pick:\d+)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            add_rarity_callback,
            pattern=r"^addrarity:\d+:[a-f0-9]{8}:.+$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            add_duplicate_callback,
            pattern=r"^adddup:\d+:[a-f0-9]+:(?:update|new|cancel)$",
        )
    )
    app.add_handler(
        InlineQueryHandler(
            addmode_anime_inline_query,
            pattern=r"^animepick:[a-f0-9]{8}(?:\s.*)?$",
        )
    )
    app.add_handler(
        InlineQueryHandler(
            addanime_inline_query,
            pattern=r"^addanime:[a-f0-9]{8}(?:\s.*)?$",
        )
    )
    app.add_handler(
        MessageHandler(
            _PENDING_RARITY_TEXT_FILTER,
            add_rarity_text_handler,
        )
    )
    app.add_handler(MessageHandler(filters.ATTACHMENT, photo_add_handler))
