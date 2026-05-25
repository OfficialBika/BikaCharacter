from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import add_card_to_user_id, ensure_user, remove_card_from_user
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, utcnow
from utils.i18n import t


async def gift_with_args(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if await should_ignore_update(update):
        return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text(t("gift_reply_target"))
        return
    if not args:
        await msg.reply_text(t("gift_usage"))
        return

    sender = update.effective_user
    receiver = msg.reply_to_message.from_user
    if sender.id == receiver.id:
        await msg.reply_text(t("gift_self"))
        return

    card_id = str(args[0]).strip()
    qty = 1
    if len(args) > 1 and args[1].isdigit():
        qty = max(1, int(args[1]))

    sender_doc = await ensure_user(sender)
    await ensure_user(receiver)
    card = next((c for c in sender_doc.get("cards", []) if str(c.get("cardId")) == card_id), None)
    if not card:
        await msg.reply_text(t("gift_card_not_found_inventory"))
        return
    if int(card.get("count", 0)) < qty:
        await msg.reply_text(t("gift_not_enough"))
        return

    preview = t(
        "gift_preview",
        sender=mention_user(sender),
        receiver=mention_user(receiver),
        emoji=get_rarity_emoji(card.get("rarity")),
        name=escape_html(card.get("name")),
        card_id=escape_html(card.get("cardId")),
        anime=escape_html(card.get("anime")),
        qty=qty,
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(t("gift_button_confirm"), callback_data=f"gift_confirm:{sender.id}:{receiver.id}:{card_id}:{qty}"),
            InlineKeyboardButton(t("gift_button_cancel"), callback_data=f"gift_cancel:{sender.id}"),
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
        await query.answer(t("gift_not_your"), show_alert=True)
        return

    removal = await remove_card_from_user(sender_id, card_id, qty)
    if not removal.get("ok"):
        await query.edit_message_text(f"❌ {removal.get('reason')}")
        await query.answer(t("failed"), show_alert=True)
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
        t("gift_success", emoji=get_rarity_emoji(card.get("rarity")), name=card.get("name"), card_id=card.get("cardId"), qty=qty)
    )
    await query.answer(t("gift_confirmed"))


async def gift_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, sender_raw = query.data.split(":", 1)
    if query.from_user.id != int(sender_raw):
        await query.answer(t("gift_not_your_cancel"), show_alert=True)
        return
    await query.edit_message_text(t("gift_cancelled"))
    await query.answer(t("cancelled"))


def register_gift_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("gift", gift_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^\.gift\s+\S+(?:\s+\d+)?$"), gift_dot_cmd))
    app.add_handler(CallbackQueryHandler(gift_confirm_callback, pattern=r"^gift_confirm:\d+:\d+:[^:]+:\d+$"))
    app.add_handler(CallbackQueryHandler(gift_cancel_callback, pattern=r"^gift_cancel:\d+$"))
