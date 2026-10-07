from __future__ import annotations

import re
import secrets
import time

import config
from pymongo.errors import DuplicateKeyError
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.mongodb import get_db
from utils.card_adding import (
    add_anime_to_catalog,
    canonical_anime,
    find_duplicate_media,
    find_possible_duplicate,
    get_add_mode,
    list_common_anime,
    next_card_id,
    normalize_add_rarity,
    set_add_mode,
    sync_counter_at_least,
)
from utils.hot_lookup import upsert_card
from utils.parser import parse_add_caption
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
_PENDING_TTL = 600
_PENDING_MAX = 2000

_ANIME_PICKERS: dict[str, dict] = {}
_ANIME_PICKER_TTL = 600
_ANIME_PICKER_MAX = 2000


def _prune_pending() -> None:
    now = time.time()
    stale = [k for k, v in _PENDING.items() if now - float(v.get("created", 0)) > _PENDING_TTL]
    for key in stale:
        _PENDING.pop(key, None)
    if len(_PENDING) > _PENDING_MAX:
        oldest = sorted(_PENDING.items(), key=lambda x: x[1].get("created", 0))
        for key, _ in oldest[: len(_PENDING) - _PENDING_MAX]:
            _PENDING.pop(key, None)


def _prune_anime_pickers() -> None:
    now = time.time()
    stale = [
        key for key, value in _ANIME_PICKERS.items()
        if now - float(value.get("created", 0)) > _ANIME_PICKER_TTL
    ]
    for key in stale:
        _ANIME_PICKERS.pop(key, None)
    if len(_ANIME_PICKERS) > _ANIME_PICKER_MAX:
        oldest = sorted(_ANIME_PICKERS.items(), key=lambda item: item[1].get("created", 0))
        for key, _ in oldest[: len(_ANIME_PICKERS) - _ANIME_PICKER_MAX]:
            _ANIME_PICKERS.pop(key, None)


def _anime_picker_token() -> str:
    _prune_anime_pickers()
    token = secrets.token_hex(4)
    while token in _ANIME_PICKERS:
        token = secrets.token_hex(4)
    return token


def _addanime_keyboard(user_id: int, token: str, anime_list: list[str], current_anime: str) -> InlineKeyboardMarkup:
    rows = []
    for index, anime in enumerate(anime_list):
        label = ("✅ " if anime.lower() == current_anime.lower() else "") + anime
        rows.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"addanime:{user_id}:{token}:{index}",
            )
        ])
    rows.append([InlineKeyboardButton("✕ Close", callback_data=f"addanime:{user_id}:{token}:close")])
    return InlineKeyboardMarkup(rows)


async def addanime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not await is_allowed_adder(user):
        return

    args = list(context.args or [])
    if args:
        raw_anime = " ".join(args).strip()
        if len(raw_anime) > 120:
            await message.reply_text("❌ Anime name is too long. Please keep it within 120 characters.")
            return
        _, rarity = await get_add_mode(user.id)
        anime = await add_anime_to_catalog(raw_anime, user.id)
        anime = await canonical_anime(anime)
        await set_add_mode(user.id, anime, rarity)
        await message.reply_text(
            "✅ <b>Anime added / selected</b>\n\n"
            f"🌴 Anime: <b>{escape_html(anime)}</b>\n"
            f"🏷 Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
            "ဒီ Anime ကို MongoDB Anime catalog ထဲမှာ သိမ်းထားပြီးသားဖြစ်ပါတယ်။\n"
            "ယခု <code>/add Name</code> သုံးလျှင် ဒီ Anime ကို default အဖြစ် အသုံးပြုပါမယ်။",
            parse_mode="HTML",
        )
        return

    anime, rarity = await get_add_mode(user.id)
    anime_list = await list_common_anime(12)
    token = _anime_picker_token()
    _ANIME_PICKERS[token] = {
        "created": time.time(),
        "user_id": user.id,
        "anime": anime,
        "anime_list": list(anime_list),
    }
    await message.reply_text(
        "🌴 <b>ADD ANIME</b>\n\n"
        f"လက်ရှိ Anime: <b>{escape_html(anime or 'Not set')}</b>\n"
        f"လက်ရှိ Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "အောက်ကစာရင်းထဲက Anime ကိုရွေးပါ။\n"
        "အသစ်တစ်ခုသတ်မှတ်ချင်ရင် <code>/addanime Anime Name</code> ကိုသုံးနိုင်ပါတယ်။",
        parse_mode="HTML",
        reply_markup=_addanime_keyboard(user.id, token, anime_list, anime),
    )

async def addanime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    match = re.match(r"^addanime:(\d+):([a-f0-9]{8}):(close|\d+)$", query.data)
    if not match or int(match.group(1)) != int(query.from_user.id):
        await query.answer("Not your anime selector.", show_alert=True)
        return

    token = match.group(2)
    item = _ANIME_PICKERS.get(token)
    if not item or item.get("user_id") != query.from_user.id or time.time() - item.get("created", 0) > _ANIME_PICKER_TTL:
        _ANIME_PICKERS.pop(token, None)
        await query.answer("Anime selector expired. Use /addanime again.", show_alert=True)
        return

    if match.group(3) == "close":
        _ANIME_PICKERS.pop(token, None)
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        return

    index = int(match.group(3))
    anime_list = item.get("anime_list") or []
    if index < 0 or index >= len(anime_list):
        await query.answer("Anime selector data changed. Use /addanime again.", show_alert=True)
        return

    anime = await canonical_anime(str(anime_list[index]))
    _, rarity = await get_add_mode(query.from_user.id)
    await set_add_mode(query.from_user.id, anime, rarity)
    _ANIME_PICKERS.pop(token, None)
    await query.answer("Anime selected.")
    await query.edit_message_text(
        "✅ <b>Anime default updated</b>\n\n"
        f"🌴 Anime: <b>{escape_html(anime)}</b>\n"
        f"🏷 Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "ယခု <code>/add Name</code> နဲ့ မြန်မြန် Card ထည့်နိုင်ပါပြီ။",
        parse_mode="HTML",
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
    if not chat:
        return False
    # Card media can be added either from an authorized private DM or from
    # one of the configured adding groups. User-level authorization is checked
    # separately by is_allowed_adder(), so private DM access is not public.
    if chat.type == "private":
        return True
    return int(chat.id) in {int(x) for x in ADDER_GROUP_IDS}


async def is_allowed_adder(user) -> bool:
    if is_owner(user):
        return True
    user_id = getattr(user, "id", 0)
    if not user_id:
        return False
    settings = await get_db().bot_settings.find_one({"_id": SETTINGS_ID}, {"adderIds": 1})
    return int(user_id) in [int(x) for x in (settings or {}).get("adderIds", [])]


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


def _addmode_keyboard(user_id: int, anime_list: list[str], current_anime: str, current_rarity: str) -> InlineKeyboardMarkup:
    rows = []
    rows.append([
        InlineKeyboardButton(f"🎴 {current_rarity or 'Rarity'}", callback_data=f"addmode:{user_id}:rmenu"),
        InlineKeyboardButton("❌ Clear", callback_data=f"addmode:{user_id}:clear"),
    ])
    for anime in anime_list:
        label = ("✅ " if anime.lower() == current_anime.lower() else "") + anime
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"addmode:{user_id}:anime:{anime_list.index(anime)}")])
    rows.append([InlineKeyboardButton("✕ Close", callback_data=f"addmode:{user_id}:close")])
    return InlineKeyboardMarkup(rows)


def _rarity_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    current = RARITY_ORDER
    for i in range(0, len(current), 2):
        row = []
        for rarity in current[i:i + 2]:
            row.append(InlineKeyboardButton(rarity[:30], callback_data=f"addmode:{user_id}:rarity:{i + len(row)}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("« Back", callback_data=f"addmode:{user_id}:main")])
    return InlineKeyboardMarkup(rows)


async def addmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not await is_allowed_adder(update.effective_user):
        return
    args = list(context.args or [])
    if args and args[0].lower() in {"off", "clear", "reset"}:
        from utils.card_adding import clear_add_mode
        await clear_add_mode(update.effective_user.id)
        await update.effective_message.reply_text("✅ Add mode cleared. /add now needs its normal rarity + anime fields.")
        return
    if args:
        raw = " ".join(args)
        parts = [x.strip() for x in raw.split("|") if x.strip()]
        anime = parts[0] if parts else ""
        rarity = normalize_add_rarity(parts[1]) if len(parts) > 1 else ""
        if not rarity:
            _, old_rarity = await get_add_mode(update.effective_user.id)
            rarity = old_rarity
        if not anime:
            old_anime, _ = await get_add_mode(update.effective_user.id)
            anime = old_anime
        if not anime or not rarity:
            await update.effective_message.reply_text("Usage: /addmode <Anime> | <Rarity>\nExample: /addmode Genshin Impact | Lg")
            return
        await set_add_mode(update.effective_user.id, anime, rarity)
        await update.effective_message.reply_text(
            f"⚙️ ADD MODE ACTIVE\n\nAnime: {anime}\nRarity: {rarity}\n\n"
            "Now send media with /add Name, /add Name | Rarity, or /add Name | Rarity | Anime."
        )
        return

    anime, rarity = await get_add_mode(update.effective_user.id)
    anime_list = await list_common_anime(12)
    await update.effective_message.reply_text(
        "⚙️ <b>CARD ADD MODE</b>\n\n"
        f"Anime: <b>{escape_html(anime or 'Not set')}</b>\n"
        f"Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "Set a default Anime + Rarity, then add many cards quickly.\n"
        "You can also use: <code>/addmode Anime | Lg</code>",
        parse_mode="HTML",
        reply_markup=_addmode_keyboard(update.effective_user.id, anime_list, anime, rarity),
    )


async def addmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not q.from_user:
        return
    m = re.match(r"^addmode:(\d+):(.+)$", q.data)
    if not m or int(m.group(1)) != int(q.from_user.id):
        await q.answer("Not your add mode.", show_alert=True)
        return
    await q.answer()
    action = m.group(2)
    anime, rarity = await get_add_mode(q.from_user.id)
    if action == "clear":
        from utils.card_adding import clear_add_mode
        await clear_add_mode(q.from_user.id)
        await q.edit_message_text("✅ Add mode cleared.")
        return
    if action == "close":
        await q.edit_message_reply_markup(reply_markup=None)
        return
    if action == "main":
        anime_list = await list_common_anime(12)
        await q.edit_message_text(
            "⚙️ <b>CARD ADD MODE</b>\n\n"
            f"Anime: <b>{escape_html(anime or 'Not set')}</b>\nRarity: <b>{escape_html(rarity or 'Not set')}</b>",
            parse_mode="HTML",
            reply_markup=_addmode_keyboard(q.from_user.id, anime_list, anime, rarity),
        )
        return
    if action == "rmenu":
        await q.edit_message_reply_markup(reply_markup=_rarity_keyboard(q.from_user.id))
        return
    if action.startswith("rarity:"):
        idx = int(action.split(":", 1)[1])
        if idx < 0 or idx >= len(RARITY_ORDER):
            return
        await set_add_mode(q.from_user.id, anime, RARITY_ORDER[idx])
    elif action.startswith("anime:"):
        idx = int(action.split(":", 1)[1])
        anime_list = await list_common_anime(12)
        if idx < 0 or idx >= len(anime_list):
            await q.answer("Anime list changed. Open /addmode again.", show_alert=True)
            return
        await set_add_mode(q.from_user.id, anime_list[idx], rarity)
    else:
        return
    anime, rarity = await get_add_mode(q.from_user.id)
    anime_list = await list_common_anime(12)
    await q.edit_message_text(
        "⚙️ <b>CARD ADD MODE</b>\n\n"
        f"Anime: <b>{escape_html(anime or 'Not set')}</b>\n"
        f"Rarity: <b>{escape_html(rarity or 'Not set')}</b>\n\n"
        "Send media with /add Name for fast adding.",
        parse_mode="HTML",
        reply_markup=_addmode_keyboard(q.from_user.id, anime_list, anime, rarity),
    )


def _pending_keyboard(user_id: int, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("♻️ Update Existing", callback_data=f"adddup:{user_id}:{token}:update"),
        InlineKeyboardButton("➕ Create New", callback_data=f"adddup:{user_id}:{token}:new"),
    ], [
        InlineKeyboardButton("✕ Cancel", callback_data=f"adddup:{user_id}:{token}:cancel"),
    ]])


async def _save_card(context, user, parsed: dict, media_info: dict, force_new: bool = False) -> tuple[bool, str]:
    card_id_provided = bool(parsed.pop("_cardIdProvided", False))
    parsed.pop("_animeProvided", None)
    parsed.pop("_rarityProvided", None)
    parsed["cardId"] = str(parsed.get("cardId", "")).strip()

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

    existing = await db[collection_name].find_one({"cardId": parsed["cardId"]})
    duplicate_other = await db[other_collection_name].find_one({"cardId": parsed["cardId"]}, {"_id": 1})
    if duplicate_other and not existing:
        return False, f"❌ Card ID {parsed['cardId']} already exists in {other_collection_name}."

    media_dup = None if force_new else await find_duplicate_media(media_info.get("fileUniqueId", ""), parsed["cardId"])
    name_dup = None if force_new else await find_possible_duplicate(parsed["name"], parsed["anime"], parsed["cardId"])
    if media_dup or name_dup:
        target = media_dup or name_dup
        return False, (
            f"⚠️ POSSIBLE DUPLICATE\n\n"
            f"Name: {target.get('name', '')}\nID: {target.get('cardId', '')}\n"
            f"Anime: {target.get('anime', '')}\nRarity: {target.get('rarity', '')}"
        )

    action = "Update" if existing else "Saved"
    caption = _database_caption(action, parsed, user)

    storage = None
    if existing:
        new_media = {**media_info}
        edited = await _edit_card_database_message(context, existing, new_media, caption)
        if edited:
            storage = edited
        else:
            storage = await _post_to_card_database_channel(context, media_info["fileId"], caption, media_info["mediaType"])
    else:
        storage = await _post_to_card_database_channel(context, media_info["fileId"], caption, media_info["mediaType"])

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
        "addedBy": user.id,
        "updatedAt": now,
    }

    await db[collection_name].update_one(
        {"cardId": parsed["cardId"]},
        {"$set": doc, "$setOnInsert": {"createdAt": now}},
        upsert=True,
    )
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


async def _handle_media_add(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed: dict, media_info: dict) -> None:
    user = update.effective_user
    anime_mode, rarity_mode = await get_add_mode(user.id)

    # An explicitly supplied rarity that failed parsing is an invalid value,
    # not a missing field. Reject it before /addmode can fill a default.
    if parsed.get("_rarityProvided") and not parsed.get("rarity"):
        await update.effective_message.reply_text(
            "❌ Invalid rarity. Please use one of the supported rarity names or short codes.",
            parse_mode="HTML",
        )
        return

    if not parsed.get("rarity"):
        if rarity_mode:
            parsed["rarity"] = rarity_mode
        else:
            await update.effective_message.reply_text(
                "❌ Rarity is missing. Use a short code such as <code>Lg</code>, or set /addmode.",
                parse_mode="HTML",
            )
            return
    if not parsed.get("anime"):
        if anime_mode:
            parsed["anime"] = anime_mode
        else:
            await update.effective_message.reply_text(
                "❌ Anime is missing. Use <code>/add Name | Rarity | Anime</code> or set <code>/addmode Anime | Rarity</code>.",
                parse_mode="HTML",
            )
            return

    parsed["rarity"] = normalize_add_rarity(parsed["rarity"]) if parsed.get("rarity") else parsed["rarity"]
    if not parsed.get("rarity"):
        await update.effective_message.reply_text(
            "❌ Rarity is missing. Use a short code such as <code>Lg</code>, or set /addmode.",
            parse_mode="HTML",
        )
        return

    parsed["anime"] = await canonical_anime(parsed["anime"])

    try:
        ok, result = await _save_card(context, user, parsed, media_info)
    except TelegramError as exc:
        await update.effective_message.reply_text(f"❌ Telegram/archive error: {escape_html(str(exc))}", parse_mode="HTML")
        return
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Card add failed: {escape_html(str(exc))}", parse_mode="HTML")
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
    if is_forwarded_message(msg):
        if looks_like_add:
            await msg.reply_text("❌ Forward add is disabled. Please upload the media directly with /add.")
        return
    if not looks_like_add:
        return
    if not await is_allowed_adder(update.effective_user):
        await msg.reply_text("❌ You are not allowed to add/update cards. Ask the owner to use /addadder for your account.")
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
    app.add_handler(CommandHandler("addmode", addmode_cmd))
    app.add_handler(CommandHandler("addanime", addanime_cmd))
    app.add_handler(CallbackQueryHandler(addmode_callback, pattern=r"^addmode:\d+:.+$"))
    app.add_handler(CallbackQueryHandler(addanime_callback, pattern=r"^addanime:\d+:[a-f0-9]{8}:(?:close|\d+)$"))
    app.add_handler(CallbackQueryHandler(add_duplicate_callback, pattern=r"^adddup:\d+:[a-f0-9]+:(?:update|new|cancel)$"))
    app.add_handler(MessageHandler(filters.ATTACHMENT, photo_add_handler))
