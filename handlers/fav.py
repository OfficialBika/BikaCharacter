from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_user_doc
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, utcnow
from utils.i18n import t

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


async def _hydrate_card_media(card: dict | None) -> dict | None:
    if not card:
        return None
    card = dict(card)
    doc = await get_db().photos.find_one(
        {"cardId": str(card.get("cardId", ""))},
        {"fileId": 1, "fileUniqueId": 1, "mediaType": 1, "mimeType": 1, "fileName": 1},
    )
    if doc:
        for field in MEDIA_FIELDS:
            value = doc.get(field)
            if value not in (None, ""):
                card[field] = value
    card.setdefault("mediaType", "photo")
    return card


async def _reply_card_media(message, card: dict, caption: str, reply_markup=None):
    media_type = _media_type(card)
    file_id = card["fileId"]
    if media_type == "video":
        return await message.reply_video(file_id, caption=caption, reply_markup=reply_markup)
    if media_type == "animation":
        return await message.reply_animation(file_id, caption=caption, reply_markup=reply_markup)
    if media_type == "document":
        return await message.reply_document(file_id, caption=caption, reply_markup=reply_markup)
    return await message.reply_photo(file_id, caption=caption, reply_markup=reply_markup)


async def fav_with_args(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
    if await should_ignore_update(update):
        return
    user_doc = await ensure_user(update.effective_user)
    if not args:
        fav_id = str(user_doc.get("favoriteCardId", ""))
        card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == fav_id), None)
        card = await _hydrate_card_media(card)
        if not card:
            await update.message.reply_text(t("fav_not_set"))
            return
        await _reply_card_media(
            update.message,
            card,
            caption=t("fav_current_caption", emoji=get_rarity_emoji(card.get("rarity")), name=card.get("name"), card_id=card.get("cardId"), anime=card.get("anime")),
        )
        return

    card_id = str(args[0]).strip()
    card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == card_id), None)
    card = await _hydrate_card_media(card)
    if not card:
        await update.message.reply_text(t("fav_missing_collection"))
        return
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(t("fav_button_yes"), callback_data=f"fav_yes:{update.effective_user.id}:{card_id}"),
            InlineKeyboardButton(t("fav_button_no"), callback_data=f"fav_no:{update.effective_user.id}"),
        ]]
    )
    await _reply_card_media(
        update.message,
        card,
        caption=t("fav_confirm", name=card.get("name"), anime=card.get("anime")),
        reply_markup=keyboard,
    )


async def fav_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await fav_with_args(update, context, list(context.args or []))


async def fav_dot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    await fav_with_args(update, context, text.split()[1:])


async def fav_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, user_id_raw, card_id = query.data.split(":", 2)
    user_id = int(user_id_raw)
    if query.from_user.id != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return
    user_doc = await get_user_doc(user_id)
    card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == str(card_id)), None) if user_doc else None
    if not card:
        await query.edit_message_caption(t("fav_missing_collection"))
        await query.answer(t("fav_card_missing"), show_alert=True)
        return
    await get_db().users.update_one({"userId": user_id}, {"$set": {"favoriteCardId": str(card_id), "updatedAt": utcnow()}})
    await query.edit_message_caption(caption=t("fav_set", name=escape_html(card.get("name")), card_id=escape_html(card_id)), parse_mode="HTML")
    await query.answer(t("fav_updated"))


async def fav_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, user_id_raw = query.data.split(":", 1)
    if query.from_user.id != int(user_id_raw):
        await query.answer(t("not_your_action"), show_alert=True)
        return
    await query.edit_message_caption(t("fav_cancelled"))
    await query.answer(t("cancelled"))


def register_fav_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("fav", fav_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.fav(?:\s+\S+)?$"), fav_dot_cmd))
    app.add_handler(CallbackQueryHandler(fav_yes_callback, pattern=r"^fav_yes:\d+:.+$"))
    app.add_handler(CallbackQueryHandler(fav_no_callback, pattern=r"^fav_no:\d+$"))
