from __future__ import annotations

import secrets

from pymongo import ReturnDocument
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user, public_card_snapshot
from utils.rarity import get_rarity_emoji, get_rarity_exp
from utils.text import escape_html, mention_user, utcnow
from utils.i18n import t
from utils.buttons import action_button


GIFT_REQUESTS_COLLECTION = "gift_requests"


async def _create_gift_request(
    *,
    sender,
    receiver,
    card: dict,
    qty: int,
) -> str:
    token = secrets.token_hex(12)
    now = utcnow()

    await get_db()[GIFT_REQUESTS_COLLECTION].insert_one(
        {
            "_id": token,
            "status": "pending",
            "senderId": int(sender.id),
            "receiverId": int(receiver.id),
            "cardId": str(card.get("cardId", "")),
            "qty": int(qty),
            "cardSnapshot": public_card_snapshot(card, 1),
            "senderName": " ".join(
                [sender.first_name or "", sender.last_name or ""]
            ).strip(),
            "receiverName": " ".join(
                [receiver.first_name or "", receiver.last_name or ""]
            ).strip(),
            "createdAt": now,
            "updatedAt": now,
        }
    )
    return token


async def _reserve_gift_request(token: str, sender_id: int) -> dict | None:
    """Atomically reserve one pending request.

    This is the key double-tap / duplicate-callback guard: only one callback can
    move the request from pending -> processing.
    """
    return await get_db()[GIFT_REQUESTS_COLLECTION].find_one_and_update(
        {
            "_id": str(token),
            "senderId": int(sender_id),
            "status": "pending",
        },
        {
            "$set": {
                "status": "processing",
                "processingAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _mark_gift_request(token: str, status: str, **extra) -> None:
    data = {
        "status": str(status),
        "updatedAt": utcnow(),
    }
    data.update(extra)
    await get_db()[GIFT_REQUESTS_COLLECTION].update_one(
        {"_id": str(token)},
        {"$set": data},
    )


async def _debit_sender_card(
    sender_id: int,
    card_id: str,
    qty: int,
) -> dict:
    """Atomically decrement one inventory card quantity.

    The quantity guard is inside the MongoDB update filter, so concurrent gifts
    cannot both spend the same copies.
    """
    db = get_db()
    sender_before = await db.users.find_one(
        {
            "userId": int(sender_id),
            "cards": {
                "$elemMatch": {
                    "cardId": str(card_id),
                    "count": {"$gte": int(qty)},
                }
            },
        },
        {
            "cards.$": 1,
            "favoriteCardId": 1,
        },
    )
    if not sender_before or not sender_before.get("cards"):
        return {"ok": False, "reason": "Not enough quantity or card not found."}

    snapshot = dict(sender_before["cards"][0])

    result = await db.users.update_one(
        {
            "userId": int(sender_id),
            "cards": {
                "$elemMatch": {
                    "cardId": str(card_id),
                    "count": {"$gte": int(qty)},
                }
            },
        },
        {
            "$inc": {"cards.$[giftcard].count": -int(qty)},
            "$set": {"updatedAt": utcnow()},
        },
        array_filters=[{"giftcard.cardId": str(card_id)}],
    )
    if result.modified_count != 1:
        return {"ok": False, "reason": "Gift race detected. Please try again."}

    # Remove zero-count entry. This is safe because the debit above is atomic.
    await db.users.update_one(
        {
            "userId": int(sender_id),
            "cards": {
                "$elemMatch": {
                    "cardId": str(card_id),
                    "count": {"$lte": 0},
                }
            },
        },
        {
            "$pull": {
                "cards": {
                    "cardId": str(card_id),
                    "count": {"$lte": 0},
                }
            },
            "$set": {"updatedAt": utcnow()},
        },
    )

    # Clear favourite only if the sender no longer owns the card.
    await db.users.update_one(
        {
            "userId": int(sender_id),
            "favoriteCardId": str(card_id),
            "cards.cardId": {"$ne": str(card_id)},
        },
        {
            "$set": {
                "favoriteCardId": "",
                "updatedAt": utcnow(),
            }
        },
    )

    return {"ok": True, "snapshot": snapshot}


async def _credit_receiver_card(
    receiver_id: int,
    card_snapshot: dict,
    qty: int,
) -> bool:
    """Credit receiver without creating duplicate card-array entries.

    Existing card -> atomic increment.
    Missing card -> guarded push.
    If another concurrent operation pushes first, retry as increment.
    """
    db = get_db()
    card_id = str(card_snapshot.get("cardId", ""))
    qty = int(qty)
    exp_inc = int(get_rarity_exp(card_snapshot.get("rarity"))) * qty

    # Fast path: receiver already owns the card.
    result = await db.users.update_one(
        {
            "userId": int(receiver_id),
            "cards.cardId": card_id,
        },
        {
            "$inc": {
                "cards.$[giftcard].count": qty,
                "exp": exp_inc,
            },
            "$set": {"updatedAt": utcnow()},
        },
        array_filters=[{"giftcard.cardId": card_id}],
    )
    if result.modified_count == 1:
        return True

    # Missing-card path. Guarded so two concurrent gifts cannot both push.
    new_card = public_card_snapshot(card_snapshot, qty)
    result = await db.users.update_one(
        {
            "userId": int(receiver_id),
            "cards.cardId": {"$ne": card_id},
        },
        {
            "$push": {"cards": new_card},
            "$inc": {"exp": exp_inc},
            "$set": {"updatedAt": utcnow()},
        },
    )
    if result.modified_count == 1:
        return True

    # A concurrent operation may have inserted the card between our two updates.
    result = await db.users.update_one(
        {
            "userId": int(receiver_id),
            "cards.cardId": card_id,
        },
        {
            "$inc": {
                "cards.$[giftcard].count": qty,
                "exp": exp_inc,
            },
            "$set": {"updatedAt": utcnow()},
        },
        array_filters=[{"giftcard.cardId": card_id}],
    )
    return result.modified_count == 1


async def _rollback_sender_card(
    sender_id: int,
    card_snapshot: dict,
    qty: int,
) -> None:
    """Best-effort compensation if receiver credit fails."""
    db = get_db()
    card_id = str(card_snapshot.get("cardId", ""))

    result = await db.users.update_one(
        {
            "userId": int(sender_id),
            "cards.cardId": card_id,
        },
        {
            "$inc": {"cards.$[giftcard].count": int(qty)},
            "$set": {"updatedAt": utcnow()},
        },
        array_filters=[{"giftcard.cardId": card_id}],
    )
    if result.modified_count == 1:
        return

    rollback_card = public_card_snapshot(card_snapshot, int(qty))
    await db.users.update_one(
        {
            "userId": int(sender_id),
            "cards.cardId": {"$ne": card_id},
        },
        {
            "$push": {"cards": rollback_card},
            "$set": {"updatedAt": utcnow()},
        },
    )


async def gift_with_args(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    args: list[str],
) -> None:
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

    card = next(
        (
            c
            for c in sender_doc.get("cards", [])
            if str(c.get("cardId")) == card_id
        ),
        None,
    )
    if not card:
        await msg.reply_text(t("gift_card_not_found_inventory"))
        return

    if int(card.get("count", 0) or 0) < qty:
        await msg.reply_text(t("gift_not_enough"))
        return

    token = await _create_gift_request(
        sender=sender,
        receiver=receiver,
        card=card,
        qty=qty,
    )

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
            action_button(
                t("gift_button_confirm"),
                "success",
                callback_data=f"gift_confirm:{token}",
            ),
            action_button(
                t("gift_button_cancel"),
                "danger",
                callback_data=f"gift_cancel:{token}",
            ),
        ]]
    )
    await msg.reply_html(preview, reply_markup=keyboard)


async def gift_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await gift_with_args(update, context, list(context.args or []))


async def gift_dot_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = update.effective_message.text or ""
    await gift_with_args(update, context, text.split()[1:])


async def gift_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    try:
        _, token = str(query.data or "").split(":", 1)
    except ValueError:
        await query.answer("Invalid gift request.", show_alert=True)
        return

    request = await get_db()[GIFT_REQUESTS_COLLECTION].find_one(
        {"_id": token},
        {"senderId": 1, "status": 1},
    )
    if not request:
        await query.answer("Gift request not found or expired.", show_alert=True)
        return

    sender_id = int(request.get("senderId", 0) or 0)
    if int(query.from_user.id) != sender_id:
        await query.answer(t("gift_not_your"), show_alert=True)
        return

    reserved = await _reserve_gift_request(token, sender_id)
    if not reserved:
        latest = await get_db()[GIFT_REQUESTS_COLLECTION].find_one(
            {"_id": token},
            {"status": 1},
        )
        status = str((latest or {}).get("status", "unknown"))
        if status == "completed":
            await query.answer("Gift already completed.", show_alert=True)
        elif status == "cancelled":
            await query.answer("Gift was cancelled.", show_alert=True)
        else:
            await query.answer("Gift is already being processed.", show_alert=True)
        return

    receiver_id = int(reserved["receiverId"])
    card_id = str(reserved["cardId"])
    qty = int(reserved["qty"])

    debit = await _debit_sender_card(
        sender_id,
        card_id,
        qty,
    )
    if not debit.get("ok"):
        await _mark_gift_request(
            token,
            "failed",
            failureReason=str(debit.get("reason", "Debit failed")),
        )
        await query.edit_message_text(
            f"❌ {debit.get('reason', 'Gift failed.')}"
        )
        await query.answer(t("failed"), show_alert=True)
        return

    card_snapshot = dict(
        reserved.get("cardSnapshot")
        or debit.get("snapshot")
        or {}
    )

    credited = False
    try:
        credited = await _credit_receiver_card(
            receiver_id,
            card_snapshot,
            qty,
        )
    except Exception as exc:
        print("GIFT RECEIVER CREDIT ERROR:", repr(exc), flush=True)

    if not credited:
        try:
            await _rollback_sender_card(
                sender_id,
                card_snapshot,
                qty,
            )
        finally:
            await _mark_gift_request(
                token,
                "failed",
                failureReason="Receiver credit failed; sender rollback attempted.",
            )

        await query.edit_message_text(
            "❌ Gift failed safely. Sender inventory was restored."
        )
        await query.answer(t("failed"), show_alert=True)
        return

    now = utcnow()
    transfer_doc = {
        "_id": token,
        "fromUserId": sender_id,
        "toUserId": receiver_id,
        "cardId": str(card_snapshot.get("cardId", card_id)),
        "name": str(card_snapshot.get("name", "")),
        "rarity": str(card_snapshot.get("rarity", "")),
        "anime": str(card_snapshot.get("anime", "")),
        "qty": qty,
        "createdAt": now,
    }

    # Idempotent transfer log: token is unique.
    await get_db().transfers.update_one(
        {"_id": token},
        {"$setOnInsert": transfer_doc},
        upsert=True,
    )

    await _mark_gift_request(
        token,
        "completed",
        completedAt=now,
    )

    await query.edit_message_text(
        t(
            "gift_success",
            emoji=get_rarity_emoji(card_snapshot.get("rarity")),
            name=escape_html(card_snapshot.get("name")),
            card_id=escape_html(card_snapshot.get("cardId")),
            qty=qty,
        ),
        parse_mode="HTML",
    )
    await query.answer(t("gift_confirmed"))


async def gift_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    try:
        _, token = str(query.data or "").split(":", 1)
    except ValueError:
        await query.answer("Invalid gift request.", show_alert=True)
        return

    cancelled = await get_db()[GIFT_REQUESTS_COLLECTION].find_one_and_update(
        {
            "_id": token,
            "senderId": int(query.from_user.id),
            "status": "pending",
        },
        {
            "$set": {
                "status": "cancelled",
                "cancelledAt": utcnow(),
                "updatedAt": utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not cancelled:
        request = await get_db()[GIFT_REQUESTS_COLLECTION].find_one(
            {"_id": token},
            {"senderId": 1, "status": 1},
        )
        if not request:
            await query.answer("Gift request not found.", show_alert=True)
            return
        if int(request.get("senderId", 0) or 0) != int(query.from_user.id):
            await query.answer(t("gift_not_your_cancel"), show_alert=True)
            return

        await query.answer(
            f"Cannot cancel: {request.get('status', 'unknown')}.",
            show_alert=True,
        )
        return

    await query.edit_message_text(t("gift_cancelled"))
    await query.answer(t("cancelled"))


def register_gift_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("gift", gift_cmd))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^\.gift\s+\S+(?:\s+\d+)?$"),
            gift_dot_cmd,
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            gift_confirm_callback,
            pattern=r"^gift_confirm:[a-f0-9]{24}$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            gift_cancel_callback,
            pattern=r"^gift_cancel:[a-f0-9]{24}$",
        )
    )
