from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, get_user_doc
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, utcnow


async def fav_with_args(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
    if await should_ignore_update(update):
        return
    user_doc = await ensure_user(update.effective_user)
    if not args:
        fav_id = str(user_doc.get("favoriteCardId", ""))
        card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == fav_id), None)
        if not card:
            await update.message.reply_text("💖 Favourite is not set.\nUse: /fav <card id>")
            return
        await update.message.reply_photo(
            card["fileId"],
            caption=f"💖 Your favourite character\n{get_rarity_emoji(card.get('rarity'))} {card.get('name')} [{card.get('cardId')}]\nAnime: {card.get('anime')}",
        )
        return

    card_id = str(args[0]).strip()
    card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == card_id), None)
    if not card:
        await update.message.reply_text("This character does not exist in your collection.")
        return
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🟢 Yes", callback_data=f"fav_yes:{update.effective_user.id}:{card_id}"),
            InlineKeyboardButton("🔴 No", callback_data=f"fav_no:{update.effective_user.id}"),
        ]]
    )
    await update.message.reply_photo(
        card["fileId"],
        caption=f"DO YOU WANT TO SET THIS CHARACTER AS YOUR FAVOURITE?\n↪ {card.get('name')} ({card.get('anime')})",
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
        await query.answer("Not your action.", show_alert=True)
        return
    user_doc = await get_user_doc(user_id)
    card = next((c for c in user_doc.get("cards", []) if str(c.get("cardId")) == str(card_id)), None) if user_doc else None
    if not card:
        await query.edit_message_caption("This character does not exist in your collection.")
        await query.answer("Card missing.", show_alert=True)
        return
    await get_db().users.update_one({"userId": user_id}, {"$set": {"favoriteCardId": str(card_id), "updatedAt": utcnow()}})
    await query.edit_message_caption(caption=f"💖 Favourite set to {escape_html(card.get('name'))} [{escape_html(card_id)}]", parse_mode="HTML")
    await query.answer("Favourite updated.")


async def fav_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, user_id_raw = query.data.split(":", 1)
    if query.from_user.id != int(user_id_raw):
        await query.answer("Not your action.", show_alert=True)
        return
    await query.edit_message_caption("❌ Favourite update cancelled.")
    await query.answer("Cancelled.")


def register_fav_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("fav", fav_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.fav(?:\s+\S+)?$"), fav_dot_cmd))
    app.add_handler(CallbackQueryHandler(fav_yes_callback, pattern=r"^fav_yes:\d+:.+$"))
    app.add_handler(CallbackQueryHandler(fav_no_callback, pattern=r"^fav_no:\d+$"))
