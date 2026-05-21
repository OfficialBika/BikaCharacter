from __future__ import annotations

import asyncio
import time
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    ADMIN_CHANGETIME_MAX,
    ADMIN_CHANGETIME_MIN,
    DEFAULT_CHANGETIME,
    OWNER_CHANGETIME_MAX,
    OWNER_CHANGETIME_MIN,
)
from database.mongodb import get_db
from utils.db_helpers import add_card_to_user_id, ensure_group, ensure_user, ensure_user_by_id, get_photo_by_card_id
from utils.permissions import is_global_admin, is_group_admin_or_owner, is_owner
from utils.rarity import get_rarity_emoji
from utils.text import escape_html, mention_user, safe_chat_title, uptime_text, utcnow

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
        await update.message.reply_text("❌ Group admin only.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"Usage: /changetime <number>\n"
            f"Group admin: {ADMIN_CHANGETIME_MIN}-{ADMIN_CHANGETIME_MAX}\n"
            f"Owner: {OWNER_CHANGETIME_MIN}-{OWNER_CHANGETIME_MAX}"
        )
        return

    value = int(context.args[0])
    if is_owner(user_id):
        min_v, max_v = OWNER_CHANGETIME_MIN, OWNER_CHANGETIME_MAX
    else:
        min_v, max_v = ADMIN_CHANGETIME_MIN, ADMIN_CHANGETIME_MAX

    if value < min_v or value > max_v:
        await update.message.reply_text(f"❌ changetime must be between {min_v} and {max_v}.")
        return

    await get_db().groups.update_one(
        {"groupId": update.effective_chat.id},
        {"$set": {"changeTime": value, "messageCount": 0, "updatedAt": utcnow()}},
    )
    await update.message.reply_text(f"✅ Changetime updated to {value} messages.")


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user.id):
        return
    db = get_db()
    user_count, group_count, photo_count, transfer_count, mute_count, settings = await asyncio.gather(
        db.users.count_documents({}),
        db.groups.count_documents({}),
        db.photos.count_documents({}),
        db.transfers.count_documents({}),
        db.bot_mutes.count_documents({}),
        db.bot_settings.find_one({"_id": SETTINGS_ID}),
    )
    adder_count = len((settings or {}).get("adderIds", []))
    text = (
        "⚙️ BIKA ADMIN DASHBOARD\n\n"
        f"👤 Users: {user_count}\n"
        f"👥 Groups: {group_count}\n"
        f"🖼 Cards: {photo_count}\n"
        f"🎁 Transfers: {transfer_count}\n"
        f"🤐 Active Bot Mutes: {mute_count}\n"
        f"➕ Adders: {adder_count}\n"
        f"⏱ Uptime: {uptime_text(int(time.time() - START_TIME))}\n\n"
        "Use: /admin_users /admin_groups /admin_photos\n"
        "Owner: /clmute /transfer /addadder /rmadder /give"
    )
    await update.message.reply_text(text)


async def admin_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user.id):
        return
    users = await get_db().users.find({}).sort("updatedAt", -1).limit(20).to_list(20)
    if not users:
        await update.message.reply_text("No users.")
        return
    lines = ["👤 USER LIST", ""]
    for u in users:
        total = sum(int(c.get("count", 0)) for c in u.get("cards", []))
        display = " ".join([u.get("firstName", ""), u.get("lastName", "")]).strip() or u.get("username") or u.get("userId")
        lines.append(f"• {display} | ID: {u.get('userId')} | Cards: {total}")
    await update.message.reply_text("\n".join(map(str, lines)))


async def admin_groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user.id):
        return
    groups = await get_db().groups.find({}).sort("updatedAt", -1).limit(20).to_list(20)
    if not groups:
        await update.message.reply_text("No groups.")
        return
    lines = ["👥 GROUP LIST", ""]
    for g in groups:
        lines.append(f"• {g.get('title') or g.get('groupId')} | {g.get('groupId')} | CT: {g.get('changeTime', DEFAULT_CHANGETIME)}")
    await update.message.reply_text("\n".join(lines))


async def admin_photos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_global_admin(update.effective_user.id):
        return
    photos = await get_db().photos.find({}).sort("createdAt", -1).limit(20).to_list(20)
    if not photos:
        await update.message.reply_text("No cards.")
        return
    lines = ["🖼 CARD LIST", ""]
    for p in photos:
        lines.append(f"• {p.get('cardId')} | {p.get('name')} | {p.get('rarity')} | {p.get('anime')}")
    await update.message.reply_text("\n".join(lines))


async def clmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: clear bot-internal mutes.

    /clmute                 -> clear all bot mutes in this group
    /clmute <user_id>       -> clear one user in this group
    /clmute + reply user    -> clear replied user in this group
    """
    if not is_owner(update.effective_user.id):
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("❌ Use /clmute in a group.")
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
            f"✅ Bot mute cleared for user ID {target_id}." if result.deleted_count else f"ℹ️ User ID {target_id} is not bot-muted."
        )
        return

    result = await db.bot_mutes.delete_many({"groupId": group_id})
    await db.groups.update_one(
        {"groupId": group_id},
        {"$set": {"lastSpeakerId": 0, "lastSpeakerCount": 0, "updatedAt": utcnow()}},
    )
    await update.effective_message.reply_text(f"✅ Cleared {result.deleted_count} bot mute(s) in this group.")


async def transfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only full harem transfer.

    /transfer oldid newid
    /transfer oldid   (reply to new user)
    """
    if not is_owner(update.effective_user.id):
        return
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage:\n/transfer <old_user_id> <new_user_id>\n/transfer <old_user_id> + reply target user")
        return

    old_id = _int_or_none(context.args[0])
    if not old_id:
        await msg.reply_text("❌ Invalid old user ID.")
        return

    new_id: Optional[int] = None
    reply_user = msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    if len(context.args) >= 2:
        new_id = _int_or_none(context.args[1])
    elif reply_user:
        new_id = int(reply_user.id)

    if not new_id:
        await msg.reply_text("❌ Target user missing. Use /transfer oldid newid or reply user with /transfer oldid")
        return
    if int(old_id) == int(new_id):
        await msg.reply_text("❌ Old ID and new ID are the same.")
        return

    db = get_db()
    source = await db.users.find_one({"userId": int(old_id)})
    if not source or not source.get("cards"):
        await msg.reply_text("❌ Old user has no harem/cards to transfer.")
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
        "✅ Harem transferred successfully.\n\n"
        f"Old ID: {old_id}\n"
        f"New ID: {new_id}\n"
        f"Unique cards: {len(source_cards)}\n"
        f"Total cards: {sum(max(1, int(c.get('count', 1))) for c in source_cards)}"
    )


async def addadder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await update.effective_message.reply_text("Usage: /addadder <user_id> or reply user with /addadder")
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
    await update.effective_message.reply_text(f"✅ User ID {target_id} can now add/update cards.")


async def rmadder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    target_id = _target_user_id_from_reply_or_arg(update, context, 0)
    if not target_id:
        await update.effective_message.reply_text("Usage: /rmadder <user_id> or reply user with /rmadder")
        return
    await get_db().bot_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$pull": {"adderIds": int(target_id)}, "$set": {"updatedAt": utcnow()}},
        upsert=True,
    )
    await update.effective_message.reply_text(f"✅ User ID {target_id} removed from adders.")


async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: /give <card_id> + reply target user")
        return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("❌ Reply to the target user's message.\nExample: /give 1001")
        return

    card_id = str(context.args[0]).strip()
    target = msg.reply_to_message.from_user
    if target.is_bot:
        await msg.reply_text("❌ Cannot give cards to bot accounts.")
        return
    photo = await get_photo_by_card_id(card_id)
    if not photo:
        await msg.reply_text(f"❌ Card ID {card_id} not found.")
        return

    await ensure_user(target)
    await add_card_to_user_id(int(target.id), photo, 1)
    caption = (
        "🎁 <b>OWNER GIVE</b>\n\n"
        f"To: {mention_user(target)}\n"
        f"Card: {get_rarity_emoji(photo.get('rarity'))} <b>{escape_html(photo.get('name'))}</b>\n"
        f"ID: <b>{escape_html(photo.get('cardId'))}</b>\n"
        f"Anime: <b>{escape_html(photo.get('anime'))}</b>\n"
        "Qty: <b>1</b>"
    )
    try:
        await msg.reply_photo(photo=photo.get("fileId"), caption=caption, parse_mode="HTML")
    except Exception:
        await msg.reply_html(caption)


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
    app.add_handler(CommandHandler("give", give_cmd))
