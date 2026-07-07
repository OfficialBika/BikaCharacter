from __future__ import annotations

import asyncio
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import (
    ADMIN_CHANGETIME_MAX,
    ADMIN_CHANGETIME_MIN,
    DEFAULT_CHANGETIME,
    OWNER_CHANGETIME_MAX,
    OWNER_CHANGETIME_MIN,
    LIMITED_CARDS_COLLECTION,
)
from database.mongodb import get_db
from utils.db_helpers import add_card_to_user_id, ensure_group, ensure_user, ensure_user_by_id, get_photo_by_card_id
from utils.permissions import is_global_admin, is_group_admin_or_owner, is_owner
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, safe_chat_title, uptime_text, utcnow
from utils.i18n import t
from utils.cooldown import add_free_user, remove_free_user, is_free_user

START_TIME = time.time()
SETTINGS_ID = "config"


def _int_or_none(value: object) -> Optional[int]:
    try:
        text = str(value).strip()
        if text.startswith("+"):
            text = text[1:]
        if text.lstrip("-").isdigit():
            return int(text)
    except Exception:
        return None
    return None


def _target_user_id_from_reply_or_arg(update: Update, context: ContextTypes.DEFAULT_TYPE, arg_index: int = 0) -> Optional[int]:
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        return int(msg.reply_to_message.from_user.id)
    if len(context.args or []) > arg_index:
        return _int_or_none(context.args[arg_index])
    return None


async def changetime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return

    await ensure_group(update.effective_chat)
    user_id = update.effective_user.id
    if not await is_group_admin_or_owner(update, context):
        await update.message.reply_text(t("group_admin_only"))
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            t(
                "changetime_usage",
                admin_min=ADMIN_CHANGETIME_MIN,
                admin_max=ADMIN_CHANGETIME_MAX,
                owner_min=OWNER_CHANGETIME_MIN,
                owner_max=OWNER_CHANGETIME_MAX,
            )
        )
        return

    value = int(context.args[0])
    if is_owner(update.effective_user):
        min_v, max_v = OWNER_CHANGETIME_MIN, OWNER_CHANGETIME_MAX
    else:
        min_v, max_v = ADMIN_CHANGETIME_MIN, ADMIN_CHANGETIME_MAX

    if value < min_v or value > max_v:
        await update.message.reply_text(t("changetime_range", min_v=min_v, max_v=max_v))
        return

    await get_db().groups.update_one(
        {"groupId": update.effective_chat.id},
        {"$set": {"changeTime": value, "messageCount": 0, "updatedAt": utcnow()}},
    )
    await update.message.reply_text(t("changetime_updated", value=value))


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user):
        return
    db = get_db()
    user_count, group_count, normal_count, limited_count, transfer_count, mute_count, settings = await asyncio.gather(
        db.users.count_documents({}),
        db.groups.count_documents({}),
        db.photos.count_documents({}),
        db[LIMITED_CARDS_COLLECTION].count_documents({}),
        db.transfers.count_documents({}),
        db.bot_mutes.count_documents({}),
        db.bot_settings.find_one({"_id": SETTINGS_ID}),
    )
    photo_count = int(normal_count) + int(limited_count)
    adder_count = len((settings or {}).get("adderIds", []))
    text = t(
        "admin_dashboard",
        users=user_count,
        groups=group_count,
        cards=photo_count,
        transfers=transfer_count,
        mutes=mute_count,
        adders=adder_count,
        uptime=uptime_text(int(time.time() - START_TIME)),
    )
    await update.message.reply_text(text)


async def admin_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user):
        return
    users = await get_db().users.find({}).sort("updatedAt", -1).limit(20).to_list(20)
    if not users:
        await update.message.reply_text(t("no_users"))
        return
    lines = [t("user_list_header"), ""]
    for u in users:
        total = sum(int(c.get("count", 0)) for c in u.get("cards", []))
        display = " ".join([u.get("firstName", ""), u.get("lastName", "")]).strip() or u.get("username") or u.get("userId")
        lines.append(f"• {display} | ID: {u.get('userId')} | Cards: {total}")
    await update.message.reply_text("\n".join(map(str, lines)))


async def admin_groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user):
        return
    groups = await get_db().groups.find({}).sort("updatedAt", -1).limit(20).to_list(20)
    if not groups:
        await update.message.reply_text(t("no_groups"))
        return
    lines = [t("group_list_header"), ""]
    for g in groups:
        lines.append(f"• {g.get('title') or g.get('groupId')} | {g.get('groupId')} | CT: {g.get('changeTime', DEFAULT_CHANGETIME)}")
    await update.message.reply_text("\n".join(lines))


async def admin_photos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user):
        return
    db = get_db()
    photos = await db.photos.find({}).sort("createdAt", -1).limit(20).to_list(20)
    limited = await db[LIMITED_CARDS_COLLECTION].find({}).sort("createdAt", -1).limit(20).to_list(20)
    for p in photos:
        p["_listCollection"] = "photos"
    for p in limited:
        p["_listCollection"] = LIMITED_CARDS_COLLECTION
    cards = sorted(photos + limited, key=lambda p: p.get("createdAt") or p.get("updatedAt") or 0, reverse=True)[:20]
    if not cards:
        await update.message.reply_text(t("no_cards"))
        return
    lines = [t("card_list_header"), ""]
    for p in cards:
        collection = p.get("_listCollection", "photos")
        lines.append(f"• {p.get('cardId')} | {p.get('name')} | {p.get('rarity')} | {p.get('anime')} | {collection}")
    await update.message.reply_text("\n".join(lines))


async def clmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: clear bot-internal mutes.

    /clmute                 -> clear all bot mutes in this group
    /clmute <user_id>       -> clear one user in this group
    /clmute + reply user    -> clear replied user in this group
    """
    if not is_owner(update.effective_user):
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(t("clmute_group_only"))
        return

    db = get_db()
    group_id = int(update.effective_chat.id)
    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if target_id:
        result = await db.bot_mutes.delete_one({"groupId": group_id, "userId": int(target_id)})
        await db.groups.update_one(
            {"groupId": group_id},
            {"$set": {"lastSpeakerId": 0, "lastSpeakerCount": 0, "updatedAt": utcnow()}},
        )
        await update.effective_message.reply_text(
            t("clmute_user_cleared", user_id=target_id) if result.deleted_count else t("clmute_user_not_muted", user_id=target_id)
        )
        return

    result = await db.bot_mutes.delete_many({"groupId": group_id})
    await db.groups.update_one(
        {"groupId": group_id},
        {"$set": {"lastSpeakerId": 0, "lastSpeakerCount": 0, "updatedAt": utcnow()}},
    )
    await update.effective_message.reply_text(t("clmute_group_cleared", count=result.deleted_count))


async def transfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only full harem transfer.

    /transfer oldid newid
    /transfer oldid   (reply to new user)
    """
    if not is_owner(update.effective_user):
        return
    msg = update.effective_message
    if not context.args:
        await msg.reply_text(t("transfer_usage"))
        return

    old_id = _int_or_none(context.args[0])
    if not old_id:
        await msg.reply_text(t("transfer_invalid_old"))
        return

    new_id: Optional[int] = None
    reply_user = msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    if len(context.args) >= 2:
        new_id = _int_or_none(context.args[1])
    elif reply_user:
        new_id = int(reply_user.id)

    if not new_id:
        await msg.reply_text(t("transfer_target_missing"))
        return
    if int(old_id) == int(new_id):
        await msg.reply_text(t("transfer_same"))
        return

    db = get_db()
    source = await db.users.find_one({"userId": int(old_id)})
    if not source or not source.get("cards"):
        await msg.reply_text(t("transfer_no_cards"))
        return

    if reply_user and int(reply_user.id) == int(new_id):
        target = await ensure_user(reply_user)
    else:
        target = await ensure_user_by_id(int(new_id))

    source_cards = list(source.get("cards", []))
    target_cards = list((target or {}).get("cards", []))
    by_id = {str(c.get("cardId")): dict(c) for c in target_cards}
    for card in source_cards:
        cid = str(card.get("cardId"))
        qty = max(1, int(card.get("count", 1)))
        if cid in by_id:
            by_id[cid]["count"] = int(by_id[cid].get("count", 0)) + qty
        else:
            by_id[cid] = dict(card)

    source_exp = int(source.get("exp", 0) or 0)
    target_exp = int((target or {}).get("exp", 0) or 0)
    target_fav = str((target or {}).get("favoriteCardId", "") or "")
    source_fav = str(source.get("favoriteCardId", "") or "")
    transferred_ids = {str(c.get("cardId")) for c in source_cards}
    if not target_fav and source_fav in transferred_ids:
        target_fav = source_fav

    now = utcnow()
    await db.users.update_one(
        {"userId": int(new_id)},
        {
            "$set": {
                "cards": list(by_id.values()),
                "exp": target_exp + source_exp,
                "favoriteCardId": target_fav,
                "updatedAt": now,
            },
            "$setOnInsert": {"createdAt": now, "haremView": "default"},
        },
        upsert=True,
    )
    await db.users.update_one(
        {"userId": int(old_id)},
        {"$set": {"cards": [], "exp": 0, "favoriteCardId": "", "updatedAt": now}},
    )
    await db.harem_transfers.insert_one(
        {
            "fromUserId": int(old_id),
            "toUserId": int(new_id),
            "cardUniqueCount": len(source_cards),
            "cardTotalCount": sum(max(1, int(c.get("count", 1))) for c in source_cards),
            "exp": source_exp,
            "byOwnerId": int(update.effective_user.id),
            "createdAt": now,
        }
    )
    await msg.reply_text(
        t(
            "transfer_success",
            old_id=old_id,
            new_id=new_id,
            unique=len(source_cards),
            total=sum(max(1, int(c.get("count", 1))) for c in source_cards),
        )
    )


async def addadder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user):
        return
    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await update.effective_message.reply_text(t("addadder_usage"))
        return
    now = utcnow()
    await get_db().bot_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$addToSet": {"adderIds": int(target_id)},
            "$set": {"updatedAt": now},
            "$setOnInsert": {"createdAt": now},
        },
        upsert=True,
    )
    await update.effective_message.reply_text(t("addadder_success", user_id=target_id))


async def rmadder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user):
        return
    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await update.effective_message.reply_text(t("rmadder_usage"))
        return
    await get_db().bot_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$pull": {"adderIds": int(target_id)}, "$set": {"updatedAt": utcnow()}},
        upsert=True,
    )
    await update.effective_message.reply_text(t("rmadder_success", user_id=target_id))


async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: exempt a user from bot anti-spam mute in the current group.

    Usage:
      /free <user_id>
      /free  (reply to target user)
    """
    if not is_owner(update.effective_user):
        return

    msg = update.effective_message
    if update.effective_chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ ᴜꜱᴇ /free ɪɴ ᴀ ɢʀᴏᴜᴘ.")
        return

    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await msg.reply_text(
            "ᴜꜱᴀɢᴇ: /free <user_id>\n"
            "ᴏʀ ʀᴇᴘʟʏ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ ᴡɪᴛʜ /free"
        )
        return

    group_id = int(update.effective_chat.id)
    already_free = await is_free_user(group_id, int(target_id))

    await add_free_user(
        group_id=group_id,
        user_id=int(target_id),
        by_owner_id=int(update.effective_user.id),
    )

    if already_free:
        await msg.reply_text(
            f"ℹ️ ᴜꜱᴇʀ ɪᴅ {target_id} ɪꜱ ᴀʟʀᴇᴀᴅʏ ꜰʀᴇᴇ ɪɴ ᴛʜɪꜱ ɢʀᴏᴜᴘ."
        )
    else:
        await msg.reply_text(
            f"✅ ᴜꜱᴇʀ ɪᴅ {target_id} ɪꜱ ɴᴏᴡ ꜰʀᴇᴇ.\n"
            "ʙᴏᴛ ᴡɪʟʟ ɴᴏᴛ 10 ᴍɪɴꜱ ᴍᴜᴛᴇ ᴛʜɪꜱ ᴜꜱᴇʀ ɪɴ ᴛʜɪꜱ ɢʀᴏᴜᴘ."
        )


async def rmfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: remove a user from the bot anti-spam free list.

    Usage:
      /rmfree <user_id>
      /rmfree  (reply to target user)
    """
    if not is_owner(update.effective_user):
        return

    msg = update.effective_message
    if update.effective_chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ ᴜꜱᴇ /rmfree ɪɴ ᴀ ɢʀᴏᴜᴘ.")
        return

    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await msg.reply_text(
            "ᴜꜱᴀɢᴇ: /rmfree <user_id>\n"
            "ᴏʀ ʀᴇᴘʟʏ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ ᴡɪᴛʜ /rmfree"
        )
        return

    removed = await remove_free_user(update.effective_chat.id, int(target_id))
    if removed:
        await msg.reply_text(f"✅ ᴜꜱᴇʀ ɪᴅ {target_id} ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ꜰʀᴇᴇ ʟɪꜱᴛ.")
    else:
        await msg.reply_text(f"ℹ️ ᴜꜱᴇʀ ɪᴅ {target_id} ɪꜱ ɴᴏᴛ ɪɴ ꜰʀᴇᴇ ʟɪꜱᴛ.")

async def _find_delete_card(card_id: str) -> tuple[dict | None, str]:
    """Find a normal or limited card and return (document, collection_name)."""
    db = get_db()

    photo = await db.photos.find_one({"cardId": str(card_id)})
    if photo:
        return photo, "photos"

    photo = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": str(card_id)})
    if photo:
        return photo, LIMITED_CARDS_COLLECTION

    return None, ""


def _delete_confirm_keyboard(card_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data=f"delete_card:confirm:{card_id}",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"delete_card:cancel:{card_id}",
                ),
            ]
        ]
    )


def _delete_confirm_text(photo: dict) -> str:
    return (
        "⚠️ <b>𝐂𝐀𝐑𝐃 𝐃𝐄𝐋𝐄𝐓𝐄 𝐂𝐎𝐍𝐅𝐈𝐑𝐌𝐀𝐓𝐈𝐎𝐍</b>\n\n"
        f"🆔 ID: <code>{escape_html(photo.get('cardId', ''))}</code>\n"
        f"👤 Name: <b>{escape_html(photo.get('name', 'Unknown'))}</b>\n"
        f"🏷 Rarity: <b>{escape_html(photo.get('rarity', 'Unknown'))}</b>\n"
        f"🌴 Anime: <b>{escape_html(photo.get('anime', 'Unknown'))}</b>\n\n"
        "⚠️ This action will permanently remove the card from the database, "
        "all user harems, favourites, and active drops.\n\n"
        "Are you sure?"
    )


async def delete_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only delete command with Confirm / Cancel protection."""
    if not is_owner(update.effective_user):
        return

    msg = update.effective_message

    if not context.args:
        await msg.reply_text(t("delete_usage"))
        return

    card_id = str(context.args[0]).strip()
    if not card_id:
        await msg.reply_text(t("delete_invalid"))
        return

    photo, _ = await _find_delete_card(card_id)
    if not photo:
        await msg.reply_text(t("delete_not_found", card_id=card_id))
        return

    await msg.reply_html(
        _delete_confirm_text(photo),
        reply_markup=_delete_confirm_keyboard(card_id),
    )


async def _perform_confirmed_card_delete(
    context: ContextTypes.DEFAULT_TYPE,
    card_id: str,
    photo: dict,
    collection_name: str,
) -> dict:
    """Delete one card and clean every related reference.

    The source card deletion is atomic. If another confirm already deleted the
    same card, no second cleanup run is started.
    """
    db = get_db()

    # Atomic source-card delete first. This makes double Confirm safe.
    deleted_photo = await db[collection_name].find_one_and_delete(
        {"cardId": str(card_id)}
    )
    if not deleted_photo:
        return {
            "already_deleted": True,
            "photo_deleted": 0,
            "users_modified": 0,
            "fav_modified": 0,
            "drop_modified": 0,
            "channel_status": t("delete_status_skipped"),
        }

    channel_delete_status = t("delete_status_skipped")
    storage_chat_id = deleted_photo.get("storageChatId")
    storage_message_id = deleted_photo.get("storageMessageId")

    if storage_chat_id and storage_message_id:
        try:
            await context.bot.delete_message(
                chat_id=storage_chat_id,
                message_id=int(storage_message_id),
            )
            channel_delete_status = t("delete_status_deleted")
        except Exception as exc:
            channel_delete_status = t("delete_status_failed", error=exc)

    users_result, fav_result, drop_result = await asyncio.gather(
        db.users.update_many(
            {"cards.cardId": str(card_id)},
            {
                "$pull": {"cards": {"cardId": str(card_id)}},
                "$set": {"updatedAt": utcnow()},
            },
        ),
        db.users.update_many(
            {"favoriteCardId": str(card_id)},
            {
                "$set": {
                    "favoriteCardId": "",
                    "updatedAt": utcnow(),
                }
            },
        ),
        db.groups.update_many(
            {"activeDrop.cardId": str(card_id)},
            {
                "$set": {
                    "activeDrop": None,
                    "updatedAt": utcnow(),
                }
            },
        ),
    )

    return {
        "already_deleted": False,
        "photo_deleted": 1,
        "users_modified": users_result.modified_count,
        "fav_modified": fav_result.modified_count,
        "drop_modified": drop_result.modified_count,
        "channel_status": channel_delete_status,
    }


async def delete_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle owner-only Confirm / Cancel buttons for /delete."""
    query = update.callback_query
    if not query or not query.data:
        return

    # Only the configured owner can confirm or cancel a destructive delete action.
    if not is_owner(query.from_user):
        await query.answer("Owner only.", show_alert=True)
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer("Invalid delete action.", show_alert=True)
        return

    _, action, card_id = parts
    card_id = str(card_id).strip()

    if action == "cancel":
        await query.answer("Cancelled.")
        try:
            await query.edit_message_text(
                f"❌ <b>Card deletion cancelled.</b>\n\n"
                f"🆔 ID: <code>{escape_html(card_id)}</code>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return

    if action != "confirm":
        await query.answer("Invalid delete action.", show_alert=True)
        return

    photo, collection_name = await _find_delete_card(card_id)
    if not photo or not collection_name:
        await query.answer("Card not found or already deleted.", show_alert=True)
        try:
            await query.edit_message_text(
                f"ℹ️ <b>Card ID {escape_html(card_id)} is not available anymore.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return

    await query.answer("Deleting...")

    try:
        await query.edit_message_text(
            f"⏳ <b>Deleting card {escape_html(card_id)}...</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    result = await _perform_confirmed_card_delete(
        context=context,
        card_id=card_id,
        photo=photo,
        collection_name=collection_name,
    )

    if result.get("already_deleted"):
        final_text = (
            f"ℹ️ <b>Card ID {escape_html(card_id)} was already deleted.</b>"
        )
    else:
        final_text = t(
            "delete_success",
            card_id=escape_html(card_id),
            name=escape_html(photo.get("name", "Unknown")),
            rarity=escape_html(photo.get("rarity", "Unknown")),
            anime=escape_html(photo.get("anime", "Unknown")),
            photo_deleted=result.get("photo_deleted", 0),
            users_modified=result.get("users_modified", 0),
            fav_modified=result.get("fav_modified", 0),
            drop_modified=result.get("drop_modified", 0),
            channel_status=escape_html(result.get("channel_status", "")),
        )

    try:
        await query.edit_message_text(
            final_text,
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        if query.message:
            await query.message.reply_html(final_text)


def _give_usage_text() -> str:
    return (
        "ᴜꜱᴀɢᴇ:\n"
        "• Reply target user with: /give <card_id>\n"
        "• Or use: /give <user_id> <card_id>\n\n"
        "ᴇxᴀᴍᴘʟᴇ:\n"
        "/give 123456789 60\n"
        "/give 123456789 1a  (Limited)"
    )


def _mention_user_id_html(user_id: int, user_doc: dict | None = None) -> str:
    user_doc = user_doc or {}
    display = " ".join(
        [
            str(user_doc.get("firstName", "") or ""),
            str(user_doc.get("lastName", "") or ""),
        ]
    ).strip()

    if not display:
        display = str(user_doc.get("username", "") or "").strip()
    if not display:
        display = str(user_id)

    return f'<a href="tg://user?id={int(user_id)}">{escape_html(display)}</a>'


def _detect_card_media_type(card: dict) -> str:
    media_type = str(card.get("mediaType") or "").strip().lower()
    if media_type == "gif":
        return "animation"
    if media_type in {"photo", "video", "animation", "document"}:
        return media_type

    mime_type = str(card.get("mimeType") or "").strip().lower()
    file_name = str(card.get("fileName") or "").strip().lower()

    if mime_type.startswith("video/") or file_name.endswith((".mp4", ".mov", ".mkv", ".webm")):
        return "video"
    if mime_type == "image/gif" or file_name.endswith(".gif"):
        return "animation"
    if mime_type and not mime_type.startswith("image/"):
        return "document"

    return "photo"


async def _reply_give_preview(msg, photo: dict, caption: str) -> None:
    file_id = str(photo.get("fileId") or "")
    media_type = _detect_card_media_type(photo)

    try:
        if media_type == "video":
            await msg.reply_video(video=file_id, caption=caption, parse_mode="HTML")
            return
        if media_type == "animation":
            await msg.reply_animation(animation=file_id, caption=caption, parse_mode="HTML")
            return
        if media_type == "document":
            await msg.reply_document(document=file_id, caption=caption, parse_mode="HTML")
            return
        await msg.reply_photo(photo=file_id, caption=caption, parse_mode="HTML")
        return
    except Exception:
        await msg.reply_html(caption)


async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: give one card to a user.

    Supported:
      /give <card_id>              (reply to target user)
      /give <user_id> <card_id>    (without reply)
    """
    if not is_owner(update.effective_user):
        return

    msg = update.effective_message
    args = list(context.args or [])

    if not args:
        await msg.reply_text(_give_usage_text())
        return

    target_id: Optional[int] = None
    target_html = ""
    card_id = ""

    reply_user = msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None

    if reply_user:
        # Old system: reply to a user + /give cardid
        card_id = str(args[0]).strip()
        if reply_user.is_bot:
            await msg.reply_text(t("give_bot_account"))
            return

        await ensure_user(reply_user)
        target_id = int(reply_user.id)
        target_html = mention_user(reply_user)

    else:
        # New system: /give userid cardid
        if len(args) < 2:
            await msg.reply_text(_give_usage_text())
            return

        target_id = _int_or_none(args[0])
        if not target_id:
            await msg.reply_text("❌ ɪɴᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ.\n\n" + _give_usage_text())
            return

        card_id = str(args[1]).strip()
        target_doc = await ensure_user_by_id(int(target_id))
        target_html = _mention_user_id_html(int(target_id), target_doc)

    if not card_id:
        await msg.reply_text(_give_usage_text())
        return

    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await msg.reply_text(t("give_not_found", card_id=card_id))
        return

    await add_card_to_user_id(int(target_id), photo, 1)

    caption = t(
        "give_caption",
        target=target_html,
        emoji=get_rarity_emoji(photo.get("rarity")),
        name=escape_html(photo.get("name")),
        card_id=escape_html(photo.get("cardId")),
        anime=escape_html(photo.get("anime")),
    )

    await _reply_give_preview(msg, photo, caption)



def register_admin_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("changetime", changetime_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("admin_users", admin_users_cmd))
    app.add_handler(CommandHandler("admin_groups", admin_groups_cmd))
    app.add_handler(CommandHandler("admin_photos", admin_photos_cmd))
    app.add_handler(CommandHandler("clmute", clmute_cmd))
    app.add_handler(CommandHandler("transfer", transfer_cmd))
    app.add_handler(CommandHandler("addadder", addadder_cmd))
    app.add_handler(CommandHandler("rmadder", rmadder_cmd))
    app.add_handler(CommandHandler("delete", delete_card_cmd))
    app.add_handler(CallbackQueryHandler(delete_card_callback, pattern=r"^delete_card:(confirm|cancel):"))
    app.add_handler(CommandHandler("give", give_cmd))
    app.add_handler(CommandHandler("free", free_cmd))
    app.add_handler(CommandHandler("rmfree", rmfree_cmd))
