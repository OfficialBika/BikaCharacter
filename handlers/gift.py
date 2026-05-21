from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import add_card_to_user_id, ensure_user, remove_card_from_user
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, utcnow


async def gift_with_args(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if await should_ignore_update(update):
        return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("❌ Reply to the target user's message.\nExample: .gift 1001")
        return
    if not args:
        await msg.reply_text("Usage: .gift <card id> [qty]")
        return

    sender = update.effective_user
    receiver = msg.reply_to_message.from_user
    if sender.id == receiver.id:
        await msg.reply_text("❌ You can't gift to yourself.")
        return

    card_id = str(args[0]).strip()
    qty = 1
    if len(args) > 1 and args[1].isdigit():
        qty = max(1, int(args[1]))

    sender_doc = await ensure_user(sender)
    await ensure_user(receiver)
    card = next((c for c in sender_doc.get("cards", []) if str(c.get("cardId")) == card_id), None)
    if not card:
        await msg.reply_text("❌ Card not found in your inventory.")
        return
    if int(card.get("count", 0)) < qty:
        await msg.reply_text("❌ Not enough quantity.")
        return

    preview = (
        "🎁 <b>GIFT PREVIEW</b>\n\n"
        f"From: {mention_user(sender)}\n"
        f"To: {mention_user(receiver)}\n"
        f"Card: {get_rarity_emoji(card.get('rarity'))} {escape_html(card.get('name'))}\n"
        f"ID: {escape_html(card.get('cardId'))}\n"
        f"Anime: {escape_html(card.get('anime'))}\n"
        f"Qty: {qty}\n\n"
        "Are you sure you want to send this card?"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Confirm", callback_data=f"gift_confirm:{sender.id}:{receiver.id}:{card_id}:{qty}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"gift_cancel:{sender.id}"),
        ]]
    )
    await msg.reply_html(preview, reply_markup=keyboard)


async def gift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await gift_with_args(update, context, list(context.args or []))


async def gift_dot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    await gift_with_args(update, context, text.split()[1:])


async def gift_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, sender_raw, receiver_raw, card_id, qty_raw = query.data.split(":", 4)
    sender_id = int(sender_raw)
    receiver_id = int(receiver_raw)
    qty = max(1, int(qty_raw))
    if query.from_user.id != sender_id:
        await query.answer("Not your gift action.", show_alert=True)
        return

    removal = await remove_card_from_user(sender_id, card_id, qty)
    if not removal.get("ok"):
        await query.edit_message_text(f"❌ {removal.get('reason')}")
        await query.answer("Failed.", show_alert=True)
        return

    card = removal["removedCardSnapshot"]
    await add_card_to_user_id(receiver_id, card, qty)
    await get_db().transfers.insert_one(
        {
            "fromUserId": sender_id,
            "toUserId": receiver_id,
            "cardId": str(card.get("cardId")),
            "name": str(card.get("name")),
            "rarity": str(card.get("rarity")),
            "anime": str(card.get("anime")),
            "qty": qty,
            "createdAt": utcnow(),
        }
    )
    await query.edit_message_text(
        f"✅ Gift sent successfully.\n\nCard: {get_rarity_emoji(card.get('rarity'))} {card.get('name')}\nID: {card.get('cardId')}\nQty: {qty}"
    )
    await query.answer("Gift confirmed.")


async def gift_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, sender_raw = query.data.split(":", 1)
    if query.from_user.id != int(sender_raw):
        await query.answer("Not your cancel action.", show_alert=True)
        return
    await query.edit_message_text("❌ Gift cancelled.")
    await query.answer("Cancelled.")


def register_gift_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("gift", gift_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.gift\s+\S+(?:\s+\d+)?$"), gift_dot_cmd))
    app.add_handler(CallbackQueryHandler(gift_confirm_callback, pattern=r"^gift_confirm:\d+:\d+:[^:]+:\d+$"))
    app.add_handler(CallbackQueryHandler(gift_cancel_callback, pattern=r"^gift_cancel:\d+$"))
