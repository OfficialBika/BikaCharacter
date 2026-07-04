from __future__ import annotations

import random
import re

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import LIMITED_CARDS_COLLECTION
from database.mongodb import get_db
from handlers.profile import build_profile_text, hydrate_card_media, reply_profile_media
from utils.buttons import action_button
from utils.db_helpers import ensure_user, remove_card_from_user
from utils.permissions import is_owner
from utils.text import escape_html, utcnow
from utils.user_bans import (
    format_release_date,
    log_owner_action,
    set_user_ban,
    unban_user,
)


OWNER_TOOLS_GROUP = -900
BAN_PENDING_KEY = "owner_timed_ban_pending"
_DURATION_RE = re.compile(r"^(\d+)\s*days?$", re.IGNORECASE)


def _target_name(user_doc: dict | None) -> str:
    user_doc = user_doc or {}
    name = " ".join(
        [
            str(user_doc.get("firstName", "") or ""),
            str(user_doc.get("lastName", "") or ""),
        ]
    ).strip()
    return name or str(user_doc.get("username", "") or "") or f"User {user_doc.get('userId', '')}"


def _target_mention(user_doc: dict) -> str:
    user_id = int(user_doc.get("userId", 0) or 0)
    return f'<a href="tg://user?id={user_id}">{escape_html(_target_name(user_doc))}</a>'


def _target_is_owner(user_doc: dict | None) -> bool:
    user_doc = user_doc or {}
    return is_owner(
        user_doc.get("userId"),
        str(user_doc.get("username", "") or ""),
    )


async def _resolve_target(
    update: Update,
    token: str | None = None,
    *,
    allow_unknown_numeric: bool = False,
) -> dict | None:
    msg = update.effective_message
    db = get_db()

    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        tg_user = msg.reply_to_message.from_user
        if tg_user.is_bot:
            return None
        return await ensure_user(tg_user)

    raw = str(token or "").strip()
    if not raw:
        return None

    if raw.startswith("+"):
        raw = raw[1:]

    if raw.isdigit():
        user_id = int(raw)
        user_doc = await db.users.find_one({"userId": user_id})
        if user_doc:
            return user_doc
        if allow_unknown_numeric:
            return {
                "userId": user_id,
                "username": "",
                "firstName": "",
                "lastName": "",
                "cards": [],
            }
        return None

    username = raw.lstrip("@").strip()
    if not username:
        return None

    return await db.users.find_one(
        {
            "username": {
                "$regex": f"^{re.escape(username)}$",
                "$options": "i",
            }
        }
    )


def _cp_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                action_button("Ban", "danger", callback_data=f"ocp:ban:{int(user_id)}"),
                action_button("Delete Harem", "danger", callback_data=f"ocp:delete:{int(user_id)}"),
            ],
            [
                action_button("Close", "primary", callback_data=f"ocp:close:{int(user_id)}"),
            ],
        ]
    )


def _ban_type_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            action_button("Lifetime", "danger", callback_data=f"oban:lifetime:{int(user_id)}"),
            action_button("By Date", "primary", callback_data=f"oban:date:{int(user_id)}"),
        ]]
    )


def _delete_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            action_button("Confirm", "danger", callback_data=f"oharem:confirm:{int(user_id)}"),
            action_button("Cancel", "primary", callback_data=f"oharem:cancel:{int(user_id)}"),
        ]]
    )


async def _edit_query_message(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_caption(
            caption=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return
    except Exception:
        pass

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception:
        # Last-resort fallback: keep the owner flow usable even if the old panel
        # message cannot be edited anymore.
        if query.message:
            await query.message.reply_text(
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )


async def dc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: remove exactly one copy of a card from a user's harem."""
    if not update.effective_user or not is_owner(update.effective_user):
        return

    msg = update.effective_message
    args = list(context.args or [])
    reply_target = bool(msg and msg.reply_to_message and msg.reply_to_message.from_user)

    if reply_target:
        if len(args) < 1:
            await msg.reply_text(
                "Usage:\n"
                "Reply user: /dc <card_id>\n"
                "Or: /dc <username|user_id> <card_id>"
            )
            return
        target = await _resolve_target(update)
        card_id = str(args[0]).strip()
    else:
        if len(args) < 2:
            await msg.reply_text(
                "Usage:\n"
                "Reply user: /dc <card_id>\n"
                "Or: /dc <username|user_id> <card_id>"
            )
            return
        target = await _resolve_target(update, args[0])
        card_id = str(args[1]).strip()

    if not target:
        await msg.reply_text("❌ User not found in the bot database.")
        return
    if not card_id:
        await msg.reply_text("❌ Card ID is required.")
        return

    target_id = int(target.get("userId", 0) or 0)
    before_card = next(
        (c for c in target.get("cards", []) if str(c.get("cardId")) == card_id),
        None,
    )
    if not before_card:
        await msg.reply_text("❌ This card was not found in the user's harem.")
        return

    before_count = int(before_card.get("count", 0) or 0)
    result = await remove_card_from_user(target_id, card_id, 1)
    if not result.get("ok"):
        await msg.reply_text(f"❌ {result.get('reason', 'Failed to remove card.')}")
        return

    updated_user = result.get("userDoc") or {}
    after_card = next(
        (c for c in updated_user.get("cards", []) if str(c.get("cardId")) == card_id),
        None,
    )
    after_count = int((after_card or {}).get("count", 0) or 0)
    removed_snapshot = result.get("removedCardSnapshot") or before_card

    await log_owner_action(
        owner_id=int(update.effective_user.id),
        action="delete_one_card",
        user_id=target_id,
        details={
            "cardId": card_id,
            "name": str(removed_snapshot.get("name", "")),
            "beforeCount": before_count,
            "afterCount": after_count,
        },
    )

    await msg.reply_html(
        "✅ <b>Card removed from harem.</b>\n\n"
        f"User: {_target_mention(target)}\n"
        f"Card: <b>{escape_html(removed_snapshot.get('name', 'Unknown'))}</b> "
        f"<code>[{escape_html(card_id)}]</code>\n"
        f"Quantity: <b>{before_count}</b> → <b>{after_count}</b>"
    )


async def cp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only control panel for one user."""
    if not update.effective_user or not is_owner(update.effective_user):
        return

    msg = update.effective_message
    args = list(context.args or [])
    reply_target = bool(msg and msg.reply_to_message and msg.reply_to_message.from_user)

    target = await _resolve_target(update, None if reply_target else (args[0] if args else None))
    if not target:
        await msg.reply_text(
            "Usage:\n"
            "Reply user: /cp\n"
            "Or: /cp <username|user_id>\n\n"
            "❌ User must already exist in the bot database."
        )
        return

    db = get_db()
    total = (
        await db.photos.count_documents({})
        + await db[LIMITED_CARDS_COLLECTION].count_documents({})
    )
    text = build_profile_text(target, total)
    keyboard = _cp_keyboard(int(target["userId"]))

    cards = list(target.get("cards", []))
    cover = None
    fav_id = str(target.get("favoriteCardId", "") or "")
    if fav_id:
        cover = next((c for c in cards if str(c.get("cardId")) == fav_id), None)
    if not cover and cards:
        cover = random.choice(cards)

    cover = await hydrate_card_media(cover)
    if cover and cover.get("fileId"):
        await reply_profile_media(msg, cover, text, reply_markup=keyboard)
    else:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def cban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: release a currently banned user."""
    if not update.effective_user or not is_owner(update.effective_user):
        return

    msg = update.effective_message
    args = list(context.args or [])
    reply_target = bool(msg and msg.reply_to_message and msg.reply_to_message.from_user)
    token = None if reply_target else (args[0] if args else None)

    target = await _resolve_target(update, token, allow_unknown_numeric=True)
    if not target:
        await msg.reply_text(
            "Usage:\n"
            "Reply banned user: /cban\n"
            "Or: /cban <username|user_id>"
        )
        return

    target_id = int(target.get("userId", 0) or 0)
    changed, previous = await unban_user(target_id, int(update.effective_user.id))
    if not changed:
        await msg.reply_text("ℹ️ This user is not currently banned.")
        return

    await log_owner_action(
        owner_id=int(update.effective_user.id),
        action="manual_unban",
        user_id=target_id,
        details={
            "previousBanType": str((previous or {}).get("banType", "")),
            "previousReason": str((previous or {}).get("reason", "")),
        },
    )

    await msg.reply_html(
        "✅ <b>User unbanned successfully.</b>\n\n"
        f"User: {_target_mention(target)}\n"
        "The user can use the bot normally again."
    )


async def cp_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not is_owner(query.from_user):
        if query:
            await query.answer("Owner only.", show_alert=True)
        return

    parts = str(query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("Invalid action.", show_alert=True)
        return

    _, action, user_id_raw = parts
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await query.answer("Invalid user ID.", show_alert=True)
        return

    await query.answer()

    if action == "ban":
        await _edit_query_message(
            query,
            f"🚫 <b>Choose ban type</b>\n\nUser ID: <code>{user_id}</code>",
            _ban_type_keyboard(user_id),
        )
        return

    if action == "delete":
        await _edit_query_message(
            query,
            "⚠️ <b>Delete entire harem?</b>\n\n"
            f"User ID: <code>{user_id}</code>\n"
            "This removes all cards and clears the favourite card.\n"
            "The user account itself will not be deleted.",
            _delete_confirm_keyboard(user_id),
        )
        return

    if action == "close":
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return


async def ban_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not is_owner(query.from_user):
        if query:
            await query.answer("Owner only.", show_alert=True)
        return

    parts = str(query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("Invalid action.", show_alert=True)
        return

    _, action, user_id_raw = parts
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await query.answer("Invalid user ID.", show_alert=True)
        return

    target = await get_db().users.find_one({"userId": user_id}) or {
        "userId": user_id,
        "username": "",
        "firstName": "",
        "lastName": "",
    }

    if action == "lifetime":
        if _target_is_owner(target):
            await query.answer("The owner account cannot be banned.", show_alert=True)
            return

        ban = await set_user_ban(
            user_id=user_id,
            banned_by=int(query.from_user.id),
            reason="Owner Decision",
            days=None,
            target_username=str(target.get("username", "") or ""),
            target_name=_target_name(target),
        )
        await log_owner_action(
            owner_id=int(query.from_user.id),
            action="ban_lifetime",
            user_id=user_id,
            details={"reason": str(ban.get("reason", ""))},
        )
        await query.answer("Lifetime ban applied.", show_alert=True)
        await _edit_query_message(
            query,
            "🚫 <b>Lifetime ban applied.</b>\n\n"
            f"User: {_target_mention(target)}\n"
            "Release Date: <b>Lifetime</b>\n"
            f"Reason: {escape_html(ban.get('reason', 'Owner Decision'))}",
            None,
        )
        return

    if action == "date":
        chat_id = int(query.message.chat_id) if query.message else 0
        context.user_data[BAN_PENDING_KEY] = {
            "targetUserId": user_id,
            "chatId": chat_id,
        }
        await query.answer("Send reason and duration.", show_alert=False)
        await _edit_query_message(
            query,
            "⏳ <b>Waiting for ban details...</b>\n\n"
            f"User ID: <code>{user_id}</code>",
            None,
        )
        if query.message:
            await query.message.reply_text(
                "Please send the ban reason and duration in this format:\n\n"
                "Auto Spam\n"
                "7 days\n\n"
                "Examples: 1 day, 3 days, 7 days, 30 days\n"
                "Send 'cancel' to cancel this pending ban."
            )
        return


async def harem_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not is_owner(query.from_user):
        if query:
            await query.answer("Owner only.", show_alert=True)
        return

    parts = str(query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("Invalid action.", show_alert=True)
        return

    _, action, user_id_raw = parts
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await query.answer("Invalid user ID.", show_alert=True)
        return

    if action == "cancel":
        await query.answer("Cancelled.")
        await _edit_query_message(query, "❎ <b>Harem deletion cancelled.</b>", None)
        return

    user_doc = await get_db().users.find_one({"userId": user_id})
    if not user_doc:
        await query.answer("User not found.", show_alert=True)
        return

    cards = list(user_doc.get("cards", []))
    unique_count = len(cards)
    total_count = sum(int(c.get("count", 0) or 0) for c in cards)

    await get_db().users.update_one(
        {"userId": user_id},
        {
            "$set": {
                "cards": [],
                "favoriteCardId": "",
                "updatedAt": utcnow(),
            }
        },
    )

    await log_owner_action(
        owner_id=int(query.from_user.id),
        action="delete_harem",
        user_id=user_id,
        details={
            "uniqueCardsDeleted": unique_count,
            "totalCardsDeleted": total_count,
        },
    )

    await query.answer("Harem deleted.", show_alert=True)
    await _edit_query_message(
        query,
        "✅ <b>Harem deleted successfully.</b>\n\n"
        f"User: {_target_mention(user_doc)}\n"
        f"Unique cards deleted: <b>{unique_count}</b>\n"
        f"Total cards deleted: <b>{total_count}</b>",
        None,
    )


async def owner_ban_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_owner(user):
        return

    pending = context.user_data.get(BAN_PENDING_KEY)
    if not pending:
        return

    pending_chat_id = int(pending.get("chatId", 0) or 0)
    if pending_chat_id and update.effective_chat and int(update.effective_chat.id) != pending_chat_id:
        return

    text = str(msg.text or "").strip()
    if text.casefold() == "cancel":
        context.user_data.pop(BAN_PENDING_KEY, None)
        await msg.reply_text("✅ Pending timed ban cancelled.")
        return

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        await msg.reply_text(
            "❌ Invalid format.\n\n"
            "Please use:\n"
            "Auto Spam\n"
            "7 days"
        )
        return

    duration_line = lines[-1]
    match = _DURATION_RE.fullmatch(duration_line)
    if not match:
        await msg.reply_text(
            "❌ Invalid duration.\n\n"
            "Examples: 1 day, 3 days, 7 days, 30 days"
        )
        return

    days = int(match.group(1))
    if days <= 0 or days > 36500:
        await msg.reply_text("❌ Days must be between 1 and 36500.")
        return

    reason = "\n".join(lines[:-1]).strip()
    if not reason:
        await msg.reply_text("❌ Ban reason is required.")
        return
    if len(reason) > 500:
        await msg.reply_text("❌ Ban reason is too long. Maximum: 500 characters.")
        return

    target_id = int(pending.get("targetUserId", 0) or 0)
    target = await get_db().users.find_one({"userId": target_id}) or {
        "userId": target_id,
        "username": "",
        "firstName": "",
        "lastName": "",
    }

    if _target_is_owner(target):
        context.user_data.pop(BAN_PENDING_KEY, None)
        await msg.reply_text("❌ The owner account cannot be banned.")
        return

    ban = await set_user_ban(
        user_id=target_id,
        banned_by=int(user.id),
        reason=reason,
        days=days,
        target_username=str(target.get("username", "") or ""),
        target_name=_target_name(target),
    )
    context.user_data.pop(BAN_PENDING_KEY, None)

    await log_owner_action(
        owner_id=int(user.id),
        action="ban_timed",
        user_id=target_id,
        details={
            "reason": reason,
            "days": days,
            "expiresAt": ban.get("expiresAt"),
        },
    )

    await msg.reply_html(
        "🚫 <b>Timed ban applied.</b>\n\n"
        f"User: {_target_mention(target)}\n"
        f"Reason: {escape_html(reason)}\n"
        f"Release Date: <b>{escape_html(format_release_date(ban.get('expiresAt')))}</b>"
    )


def register_owner_tools_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("dc", dc_cmd), group=OWNER_TOOLS_GROUP)
    app.add_handler(CommandHandler("cp", cp_cmd), group=OWNER_TOOLS_GROUP)
    app.add_handler(CommandHandler("cban", cban_cmd), group=OWNER_TOOLS_GROUP)

    app.add_handler(
        CallbackQueryHandler(cp_action_callback, pattern=r"^ocp:(?:ban|delete|close):\d+$"),
        group=OWNER_TOOLS_GROUP,
    )
    app.add_handler(
        CallbackQueryHandler(ban_action_callback, pattern=r"^oban:(?:lifetime|date):\d+$"),
        group=OWNER_TOOLS_GROUP,
    )
    app.add_handler(
        CallbackQueryHandler(harem_action_callback, pattern=r"^oharem:(?:confirm|cancel):\d+$"),
        group=OWNER_TOOLS_GROUP,
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, owner_ban_input),
        group=OWNER_TOOLS_GROUP,
    )
