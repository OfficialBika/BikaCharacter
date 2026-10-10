from __future__ import annotations

import asyncio
import re
import secrets
import time
from typing import Optional

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import (
    ADMIN_CHANGETIME_MAX,
    ADMIN_CHANGETIME_MIN,
    DEFAULT_CHANGETIME,
    OWNER_CHANGETIME_MAX,
    OWNER_CHANGETIME_MIN,
    ANIMES_COLLECTION,
    LIMITED_CARDS_COLLECTION,
    RARITY_ORDER,
)
from database.mongodb import get_db
from utils.db_helpers import add_card_to_user_id, ensure_group, ensure_user, ensure_user_by_id, get_photo_by_card_id
from utils.hot_lookup import delete_card as delete_hot_lookup_card, invalidate_user_rank, upsert_card
from utils.card_adding import invalidate_anime_cache
from utils.card_logs import send_card_action_log
from utils.buttons import action_button
from utils.parser import normalized_search_name
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
    invalidate_user_rank(int(old_id))
    invalidate_user_rank(int(new_id))
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


async def _is_owner_or_adder(user) -> bool:
    if is_owner(user):
        return True
    user_id = int(getattr(user, "id", 0) or 0)
    if not user_id:
        return False
    settings = await get_db().bot_settings.find_one(
        {"_id": SETTINGS_ID},
        {"adderIds": 1},
    )
    return user_id in {int(x) for x in (settings or {}).get("adderIds", [])}


async def raritylist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner/adder-only database rarity inventory stats."""
    if not await _is_owner_or_adder(update.effective_user):
        return

    db = get_db()
    counts = {str(rarity): 0 for rarity in RARITY_ORDER}

    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        rows = await (await db[collection_name].aggregate(
            [
                {"$group": {"_id": "$rarity", "count": {"$sum": 1}}},
            ]
        )).to_list(None)
        for row in rows:
            rarity = str(row.get("_id") or "")
            counts[rarity] = counts.get(rarity, 0) + int(row.get("count", 0) or 0)

    lines = ["🏷 <b>𝐑𝐀𝐑𝐈𝐓𝐘 𝐒𝐓𝐀𝐓𝐒</b>", ""]
    total = 0
    for rarity in RARITY_ORDER:
        count = int(counts.get(str(rarity), 0) or 0)
        total += count
        lines.append(
            f"{get_rarity_emoji(rarity)} <b>{escape_html(rarity)}</b> - "
            f"<code>{count:,}</code>"
        )

    lines.extend(["", f"🎴 <b>Total Database Cards</b> - <code>{total:,}</code>"])
    await update.effective_message.reply_html("\n".join(lines))



_DELETE_CONFIRM_TTL = 600
_DELETE_CONFIRM_MAX = 200
_DELETE_ANIME_PAGE_SIZE = 6
_PENDING_CARD_DELETIONS: dict[str, dict] = {}
_DELETE_SNAPSHOT_FIELDS = (
    "cardId", "name", "normalizedName", "rarity", "anime",
    "fileId", "fileUniqueId", "mediaType", "mimeType", "fileName",
    "storageChatId", "storageMessageId", "updatedAt",
)


def _prune_pending_card_deletions() -> None:
    now = time.time()
    for token, item in list(_PENDING_CARD_DELETIONS.items()):
        if now - float(item.get("created", 0)) > _DELETE_CONFIRM_TTL:
            _PENDING_CARD_DELETIONS.pop(token, None)
    if len(_PENDING_CARD_DELETIONS) > _DELETE_CONFIRM_MAX:
        oldest = sorted(
            _PENDING_CARD_DELETIONS.items(),
            key=lambda pair: float(pair[1].get("created", 0)),
        )
        for token, _ in oldest[:len(_PENDING_CARD_DELETIONS) - _DELETE_CONFIRM_MAX]:
            _PENDING_CARD_DELETIONS.pop(token, None)


def _delete_token() -> str:
    _prune_pending_card_deletions()
    token = secrets.token_hex(5)
    while token in _PENDING_CARD_DELETIONS:
        token = secrets.token_hex(5)
    return token


def _delete_snapshot(card: dict) -> dict:
    return {key: card.get(key) for key in _DELETE_SNAPSHOT_FIELDS}


def _delete_snapshot_matches(card: dict, snapshot: dict) -> bool:
    return all(card.get(key) == snapshot.get(key) for key in _DELETE_SNAPSHOT_FIELDS)


def _delete_confirm_keyboard(
    token: str,
    action: str,
    page: int = 0,
    total_pages: int = 1,
    card_count: int | None = None,
) -> InlineKeyboardMarkup:
    prefix = "carddel" if action == "card" else "animedel"
    rows = []
    if action == "anime" and total_pages > 1:
        nav = []
        if page > 0:
            nav.append(action_button(
                "⬅️ Previous", "primary",
                callback_data=f"animedel:page:{token}:{page - 1}",
            ))
        if page + 1 < total_pages:
            nav.append(action_button(
                "Next ➡️", "primary",
                callback_data=f"animedel:page:{token}:{page + 1}",
            ))
        if nav:
            rows.append(nav)
    if action == "anime":
        confirm_label = (
            f"✅ Delete {int(card_count)} Exact-Match Cards"
            if card_count
            else "✅ Delete Anime Entry"
        )
    else:
        confirm_label = "✅ Confirm Delete"
    rows.append([
        action_button(confirm_label, "danger", callback_data=f"{prefix}:confirm:{token}"),
        action_button("✖️ Cancel", "primary", callback_data=f"{prefix}:cancel:{token}"),
    ])
    return InlineKeyboardMarkup(rows)


def _delete_card_prompt_text(card_id: str, card: dict) -> str:
    return (
        "⚠️ <b>DELETE CARD CONFIRMATION</b>\n\n"
        f"🆔 <b>ID:</b> <code>{escape_html(card_id)}</code>\n"
        f"🎴 <b>Name:</b> {escape_html(card.get('name', 'Unknown'))}\n"
        f"🏷 <b>Rarity:</b> {escape_html(card.get('rarity', 'Unknown'))}\n"
        f"🌴 <b>Anime:</b> {escape_html(card.get('anime', 'Unknown'))}\n"
        f"🎞 <b>Media:</b> {escape_html(_detect_card_media_type(card))}\n\n"
        "ဖျက်ရန် <b>Confirm Delete</b> ကိုနှိပ်ပါ။ မဖျက်လိုပါက <b>Cancel</b> ကိုနှိပ်ပါ။\n"
        "Confirm မနှိပ်မချင်း Database ထဲက Card ကို မဖျက်ပါ။"
    )


async def _reply_delete_card_preview(message, card: dict, token: str) -> None:
    card_id = str(card.get("cardId", "") or "")
    text = _delete_card_prompt_text(card_id, card)
    keyboard = _delete_confirm_keyboard(token, "card")
    file_id = str(card.get("fileId") or "").strip()
    media_type = _detect_card_media_type(card)
    if file_id:
        try:
            if media_type == "video":
                await message.reply_video(video=file_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
                return
            if media_type == "animation":
                await message.reply_animation(animation=file_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
                return
            if media_type == "document":
                await message.reply_document(document=file_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
                return
            await message.reply_photo(photo=file_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception as exc:
            print(f"CARD DELETE PREVIEW FAILED: card_id={card_id} error={exc!r}", flush=True)
    await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


def _anime_delete_page_text(item: dict, page: int) -> str:
    cards = list(item.get("cards", []))
    total = len(cards)
    total_pages = max(1, (total + _DELETE_ANIME_PAGE_SIZE - 1) // _DELETE_ANIME_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * _DELETE_ANIME_PAGE_SIZE
    selected = cards[start:start + _DELETE_ANIME_PAGE_SIZE]

    stored_anime_values = sorted(
        {
            str((entry.get("snapshot") or {}).get("anime") or "").strip()
            for entry in cards
            if str((entry.get("snapshot") or {}).get("anime") or "").strip()
        },
        key=lambda value: (value.casefold(), value),
    )
    stored_anime_text = ", ".join(
        escape_html(value) for value in stored_anime_values[:3]
    )
    if len(stored_anime_values) > 3:
        stored_anime_text += f" (+{len(stored_anime_values) - 3} more)"

    lines = [
        "⚠️ <b>DELETE ANIME CONFIRMATION</b>",
        "",
        f"🌴 <b>Anime:</b> {escape_html(item.get('anime_name', ''))}",
        f"🎴 <b>Exact-name cards to delete:</b> <code>{total}</code>",
        f"📄 <b>Page:</b> <code>{page + 1}/{total_pages}</code>",
        "",
    ]
    if stored_anime_text:
        lines.insert(3, f"🧾 <b>Stored Anime value(s):</b> {stored_anime_text}")
    if selected:
        for card_item in selected:
            card = card_item["snapshot"]
            lines.extend([
                f"🆔 <code>{escape_html(card.get('cardId', ''))}</code> — <b>{escape_html(card.get('name', 'Unknown'))}</b>",
                f"   Rarity: {escape_html(card.get('rarity', 'Unknown'))} | Media: {escape_html(_detect_card_media_type(card))}",
            ])
        if total > _DELETE_ANIME_PAGE_SIZE:
            lines.extend(["", f"… စုစုပေါင်း Card {total} ခုကို စာမျက်နှာ {total_pages} မျက်နှာဖြင့် ပြထားပါတယ်။"])
    else:
        lines.append("ဒီ Anime အောက်မှာ Card မရှိပါ။ Anime catalog entry ကိုသာ ဖျက်ပါမယ်။")
    lines.extend([
        "",
        f"Confirm လုပ်လျှင် Database ထဲတွင် <b>{escape_html(item.get('anime_name', ''))}</b> နဲ့ အမည်အတိအကျတူတဲ့ Card {total} ခုကိုသာ ဖျက်ပါမယ်။",
        "[🎮] ပါ/မပါတဲ့ Anime name တွေကို မတူညီတဲ့တန်ဖိုးအဖြစ် သတ်မှတ်ထားပါတယ်။ အခြား variant ကို မဖျက်ပါ။",
        "စာမျက်နှာအားလုံးကို ပြန်စစ်ပြီးမှ Delete အတည်ပြုနိုင်ပါမယ်။ Confirm မနှိပ်မချင်း Data မဖျက်ပါ။",
    ])
    return "\n".join(lines)


async def _send_anime_delete_preview(message, token: str, item: dict, page: int = 0) -> None:
    cards = list(item.get("cards", []))
    total_pages = max(1, (len(cards) + _DELETE_ANIME_PAGE_SIZE - 1) // _DELETE_ANIME_PAGE_SIZE)
    item["page"] = max(0, min(int(page), total_pages - 1))
    await message.reply_text(
        _anime_delete_page_text(item, item["page"]),
        parse_mode="HTML",
        reply_markup=_delete_confirm_keyboard(token, "anime", item["page"], total_pages, len(cards)),
        disable_web_page_preview=True,
    )


async def delete_card_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: preview one card and wait for explicit confirmation."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_owner(user):
        return

    args = list(context.args or [])
    if len(args) != 1 or not str(args[0]).strip():
        await msg.reply_text("Usage: <code>/delete ID</code>\nExample: <code>/delete 25</code>", parse_mode="HTML")
        return

    card_id = str(args[0]).strip()
    db = get_db()
    normal = await db.photos.find_one({"cardId": card_id})
    limited = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": card_id})
    if normal and limited:
        await msg.reply_text(
            f"❌ Card ID <code>{escape_html(card_id)}</code> is present in both card collections. "
            "Resolve the duplicate ID first; nothing was deleted.",
            parse_mode="HTML",
        )
        return
    card = normal or limited
    if not card:
        await msg.reply_text(f"❌ Card ID <code>{escape_html(card_id)}</code> not found. Nothing was deleted.", parse_mode="HTML")
        return

    collection_name = "photos" if normal else LIMITED_CARDS_COLLECTION
    token = _delete_token()
    _PENDING_CARD_DELETIONS[token] = {
        "created": time.time(),
        "actor_id": int(user.id),
        "chat_id": int(msg.chat_id),
        "action": "card",
        "card_id": card_id,
        "collection_name": collection_name,
        "document_id": card.get("_id"),
        "snapshot": _delete_snapshot(card),
    }
    await _reply_delete_card_preview(msg, card, token)


async def delete_anime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: preview all cards for an Anime before deleting any of them."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_owner(user):
        return

    raw_name = " ".join(str(part) for part in (context.args or [])).strip()
    if not raw_name:
        await msg.reply_text(
            "Usage: <code>/deleteanime Anime Name</code>\nExample: <code>/deleteanime Genshin Impact</code>",
            parse_mode="HTML",
        )
        return
    if len(raw_name) > 120:
        await msg.reply_text("❌ Anime name is too long (maximum 120 characters).")
        return

    db = get_db()
    normalized = normalized_search_name(raw_name)
    if not normalized:
        await msg.reply_text("❌ Invalid Anime name.")
        return
    # Anime names that differ by the trailing [🎮] marker are distinct
    # delete targets. Do not use normalizedName here because normalization
    # intentionally removes bracketed markers.
    # Use literal equality, not normalizedName or a regex: the trailing
    # [🎮] marker (and the exact stored spelling) is part of this delete target.
    catalog = await db[ANIMES_COLLECTION].find_one(
        {"name": raw_name},
        {"_id": 1, "name": 1, "normalizedName": 1},
    )
    anime_name = raw_name
    cards = []
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].find({"anime": raw_name}).to_list(None)
        for card in docs:
            cards.append({
                "collection_name": collection_name,
                "document_id": card.get("_id"),
                "card_id": str(card.get("cardId", "")),
                "snapshot": _delete_snapshot(card),
            })
    cards.sort(key=lambda item: (
        str(item["snapshot"].get("cardId", "")).lower(),
        item["collection_name"],
    ))

    if not cards and not catalog:
        await msg.reply_text(f"❌ Anime <b>{escape_html(raw_name)}</b> not found in the catalog or cards.", parse_mode="HTML")
        return

    token = _delete_token()
    item = {
        "created": time.time(),
        "actor_id": int(user.id),
        "chat_id": int(msg.chat_id),
        "action": "anime",
        "anime_name": anime_name,
        "anime_exact_name": raw_name,
        "anime_normalized": normalized,
        "catalog_document_id": (catalog or {}).get("_id"),
        "catalog_snapshot": {
            "name": (catalog or {}).get("name"),
            "normalizedName": (catalog or {}).get("normalizedName"),
        } if catalog else None,
        "cards": cards,
        "page": 0,
        "viewed_pages": {0},
    }
    _PENDING_CARD_DELETIONS[token] = item
    await _send_anime_delete_preview(msg, token, item, 0)


async def _edit_delete_prompt(query, text: str, reply_markup=None) -> None:
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
            disable_web_page_preview=True,
        )
    except Exception as exc:
        print(f"DELETE CONFIRMATION MESSAGE EDIT FAILED: {exc!r}", flush=True)


async def _delete_archive_message(context: ContextTypes.DEFAULT_TYPE, card: dict) -> str:
    storage_chat_id = card.get("storageChatId")
    storage_message_id = card.get("storageMessageId")
    if not storage_chat_id or not storage_message_id:
        return "no archive reference"
    try:
        await context.bot.delete_message(
            chat_id=storage_chat_id,
            message_id=int(storage_message_id),
        )
        return "deleted"
    except Exception as exc:
        print(f"CARD ARCHIVE DELETE FAILED: card_id={card.get('cardId')} error={exc!r}", flush=True)
        return "failed"


async def _cleanup_deleted_card_references(db, card_ids: list[str]) -> dict:
    unique_ids = sorted({str(card_id) for card_id in card_ids if str(card_id)})
    removable_ids = []
    for card_id in unique_ids:
        normal = await db.photos.find_one({"cardId": card_id}, {"_id": 1})
        limited = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": card_id}, {"_id": 1})
        if not normal and not limited:
            removable_ids.append(card_id)
    if not removable_ids:
        return {"users": 0, "favorites": 0, "drops": 0}

    now = utcnow()
    users = await db.users.update_many(
        {"cards.cardId": {"$in": removable_ids}},
        {"$pull": {"cards": {"cardId": {"$in": removable_ids}}}, "$set": {"updatedAt": now}},
    )
    favorites = await db.users.update_many(
        {"favoriteCardId": {"$in": removable_ids}},
        {"$set": {"favoriteCardId": "", "updatedAt": now}},
    )
    drops = await db.groups.update_many(
        {"activeDrop.cardId": {"$in": removable_ids}},
        {"$set": {"activeDrop": None, "updatedAt": now}},
    )
    return {
        "users": int(getattr(users, "modified_count", 0) or 0),
        "favorites": int(getattr(favorites, "modified_count", 0) or 0),
        "drops": int(getattr(drops, "modified_count", 0) or 0),
    }


async def _refresh_hot_lookup_after_deletion(db, deleted_cards: list[dict]) -> None:
    seen: set[str] = set()
    for card in deleted_cards:
        card_id = str(card.get("cardId", "") or "")
        if not card_id:
            continue
        try:
            await delete_hot_lookup_card(card_id, str(card.get("_deleteCollection") or ""))
        except Exception as exc:
            print(f"HOT LOOKUP DELETE FAILED: card_id={card_id} error={exc!r}", flush=True)
        if card_id in seen:
            continue
        seen.add(card_id)
        normal = await db.photos.find_one({"cardId": card_id})
        limited = await db[LIMITED_CARDS_COLLECTION].find_one({"cardId": card_id})
        remaining = normal or limited
        if remaining:
            try:
                await upsert_card(remaining, "photos" if normal else LIMITED_CARDS_COLLECTION)
            except Exception as exc:
                print(f"HOT LOOKUP REPAIR FAILED: card_id={card_id} error={exc!r}", flush=True)


async def _confirm_delete_card(context: ContextTypes.DEFAULT_TYPE, item: dict) -> str:
    db = get_db()
    card_id = str(item.get("card_id", ""))
    collection_name = str(item.get("collection_name", ""))
    if collection_name not in {"photos", LIMITED_CARDS_COLLECTION}:
        return "❌ Invalid deletion session. Nothing was deleted."
    query = {"cardId": card_id}
    if item.get("document_id") is not None:
        query["_id"] = item["document_id"]
    current = await db[collection_name].find_one(query)
    if not current or not _delete_snapshot_matches(current, item.get("snapshot", {})):
        return "⚠️ Card details changed or the card disappeared since preview. Nothing was deleted; run /delete ID again."
    other_name = LIMITED_CARDS_COLLECTION if collection_name == "photos" else "photos"
    if await db[other_name].find_one({"cardId": card_id}, {"_id": 1}):
        return "⚠️ This Card ID now exists in both collections. Nothing was deleted; resolve the duplicate ID first."

    # Delete only the exact snapshot that was previewed. If the document has
    # changed after the freshness check, MongoDB will refuse this deletion.
    delete_query = {"cardId": card_id}
    if item.get("document_id") is not None:
        delete_query["_id"] = item["document_id"]
    delete_query.update(item.get("snapshot", {}))
    result = await db[collection_name].delete_one(delete_query)
    if int(getattr(result, "deleted_count", 0) or 0) != 1:
        return "⚠️ Card changed during confirmation. Nothing was deleted; please run /delete ID again."

    archive_status = await _delete_archive_message(context, current)
    refs_failed = False
    try:
        refs = await _cleanup_deleted_card_references(db, [card_id])
    except Exception as exc:
        refs = {"users": 0, "favorites": 0, "drops": 0}
        refs_failed = True
        print(f"CARD DELETE REFERENCE CLEANUP FAILED: card_id={card_id} error={exc!r}", flush=True)
    removed = dict(current)
    removed["_deleteCollection"] = collection_name
    await _refresh_hot_lookup_after_deletion(db, [removed])

    partial = archive_status == "failed" or refs_failed
    await send_card_action_log(
        context.bot,
        "Card Delete Partial" if partial else "Card Deleted",
        int(item["actor_id"]),
        {
            "Card ID": card_id,
            "Name": current.get("name", ""),
            "Rarity": current.get("rarity", ""),
            "Anime": current.get("anime", ""),
            "Archive media": archive_status,
            "Reference cleanup failed": refs_failed,
            "Harem users modified": refs["users"],
            "Favorites cleared": refs["favorites"],
            "Active drops cleared": refs["drops"],
        },
    )
    status = "Archive media deleted." if archive_status == "deleted" else f"Archive media: {archive_status}."
    if refs_failed:
        status += " Harem/reference cleanup needs review."
    return (
        f"{'⚠️' if partial else '✅'} <b>{'Card Deleted with Warnings' if partial else 'Card Deleted'}</b>\n\n"
        f"🆔 ID: <code>{escape_html(card_id)}</code>\n"
        f"🎴 Name: {escape_html(current.get('name', ''))}\n"
        f"🌴 Anime: {escape_html(current.get('anime', ''))}\n"
        f"{escape_html(status)}"
    )


async def _confirm_delete_anime(context: ContextTypes.DEFAULT_TYPE, item: dict) -> str:
    db = get_db()
    exact_anime_name = str(item.get("anime_exact_name") or item.get("anime_name") or "")
    if not exact_anime_name:
        return "❌ Invalid Anime deletion session. Nothing was deleted."

    catalog_id = item.get("catalog_document_id")
    catalog_snapshot = item.get("catalog_snapshot") or {}
    if catalog_id is not None:
        current_catalog = await db[ANIMES_COLLECTION].find_one({"_id": catalog_id})
        if not current_catalog or (
            current_catalog.get("name") != catalog_snapshot.get("name")
            or current_catalog.get("normalizedName") != catalog_snapshot.get("normalizedName")
        ):
            return "⚠️ Anime catalog changed since preview. Nothing was deleted; run /deleteanime again."
    else:
        current_catalog = await db[ANIMES_COLLECTION].find_one({"name": exact_anime_name})
        if current_catalog:
            return "⚠️ Anime catalog changed since preview. Nothing was deleted; run /deleteanime again."

    current_items = []
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].find({"anime": exact_anime_name}).to_list(None)
        current_items.extend((collection_name, card) for card in docs)

    expected = {
        (entry["collection_name"], str(entry.get("document_id"))): entry["snapshot"]
        for entry in item.get("cards", [])
    }
    actual = {
        (collection_name, str(card.get("_id"))): card
        for collection_name, card in current_items
    }
    if set(expected) != set(actual) or any(
        not _delete_snapshot_matches(actual[key], snapshot)
        for key, snapshot in expected.items()
    ):
        return "⚠️ Anime cards changed since preview. Nothing was deleted; run /deleteanime again to review the latest list."

    deleted_cards = []
    archive_deleted = 0
    archive_failed = 0
    failed_ids = []
    for collection_name, card in current_items:
        # Include the preview snapshot in the delete filter so a concurrent
        # edit cannot be deleted after we have inspected the old values.
        delete_query = {"_id": card.get("_id"), "cardId": str(card.get("cardId", ""))}
        delete_query.update(_delete_snapshot(card))
        try:
            result = await db[collection_name].delete_one(delete_query)
        except Exception as exc:
            print(f"ANIME CARD DELETE FAILED: card_id={card.get('cardId')} error={exc!r}", flush=True)
            failed_ids.append(str(card.get("cardId", "")))
            continue
        if int(getattr(result, "deleted_count", 0) or 0) != 1:
            failed_ids.append(str(card.get("cardId", "")))
            continue

        removed = dict(card)
        removed["_deleteCollection"] = collection_name
        deleted_cards.append(removed)
        archive_status = await _delete_archive_message(context, card)
        if archive_status == "deleted":
            archive_deleted += 1
        elif archive_status == "failed":
            archive_failed += 1
        if card.get("storageChatId") and card.get("storageMessageId"):
            await asyncio.sleep(0.05)

    cleanup_failed = False
    try:
        refs = await _cleanup_deleted_card_references(
            db, [str(card.get("cardId", "")) for card in deleted_cards]
        )
    except Exception as exc:
        refs = {"users": 0, "favorites": 0, "drops": 0}
        cleanup_failed = True
        print(f"ANIME DELETE REFERENCE CLEANUP FAILED: {exc!r}", flush=True)
    await _refresh_hot_lookup_after_deletion(db, deleted_cards)

    remaining_cards = []
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].find({"anime": exact_anime_name}).to_list(None)
        remaining_cards.extend(docs)

    # The catalog uses normalizedName, which strips [🎮], so one catalog row
    # can be shared by cards whose stored Anime values differ by that marker.
    # Keep that row while any card with the alternate marker form remains.
    selected_has_marker = bool(re.search(r"\s*\[🎮\]\s*$", exact_anime_name))
    base_name = re.sub(r"\s*\[🎮\]\s*$", "", exact_anime_name).strip()
    if selected_has_marker:
        sibling_pattern = rf"^{re.escape(base_name)}$"
    else:
        sibling_pattern = rf"^{re.escape(base_name)}\s*\[🎮\]$"
    sibling_cards = []
    for collection_name in ("photos", LIMITED_CARDS_COLLECTION):
        docs = await db[collection_name].find(
            {"anime": {"$regex": sibling_pattern, "$options": "i"}}
        ).to_list(None)
        sibling_cards.extend(docs)

    catalog_deleted = 0
    catalog_delete_failed = False
    catalog_preserved_for_sibling = False
    if not remaining_cards and catalog_id is not None:
        if sibling_cards:
            catalog_preserved_for_sibling = True
        else:
            catalog_query = {
                "_id": catalog_id,
                "name": catalog_snapshot.get("name"),
                "normalizedName": catalog_snapshot.get("normalizedName"),
            }
            try:
                catalog_result = await db[ANIMES_COLLECTION].delete_one(catalog_query)
                catalog_deleted = int(getattr(catalog_result, "deleted_count", 0) or 0)
                catalog_delete_failed = catalog_deleted != 1
            except Exception as exc:
                catalog_delete_failed = True
                print(f"ANIME CATALOG DELETE FAILED: {exc!r}", flush=True)
            if catalog_deleted:
                invalidate_anime_cache(str(item.get("anime_name", "")))
    elif not remaining_cards and catalog_id is None:
        # Do not treat a catalog row for the other marker variant as the
        # target row: check only the exact value entered by the owner.
        catalog_after = await db[ANIMES_COLLECTION].find_one({"name": exact_anime_name})
        catalog_delete_failed = bool(catalog_after)

    partial = bool(
        failed_ids or remaining_cards or archive_failed or cleanup_failed or catalog_delete_failed
    )
    action = "Anime Delete Partial" if partial else "Anime Deleted"
    ids_preview = ", ".join(str(card.get("cardId", "")) for card in deleted_cards[:25]) or "-"
    await send_card_action_log(
        context.bot,
        action,
        int(item["actor_id"]),
        {
            "Anime": item.get("anime_name", ""),
            "Cards in preview": len(item.get("cards", [])),
            "Cards deleted": len(deleted_cards),
            "Card IDs deleted": ids_preview,
            "Remaining cards": len(remaining_cards),
            "Failed card IDs": ", ".join(failed_ids[:25]) or "-",
            "Anime catalog deleted": catalog_deleted,
            "Catalog preserved for alternate marker cards": catalog_preserved_for_sibling,
            "Catalog delete failed": catalog_delete_failed,
            "Archive messages deleted": archive_deleted,
            "Archive deletes failed": archive_failed,
            "Reference cleanup failed": cleanup_failed,
            "Harem users modified": refs["users"],
            "Favorites cleared": refs["favorites"],
            "Active drops cleared": refs["drops"],
        },
    )

    if remaining_cards:
        final_note = "Some exact-name cards remain. The Anime catalog entry was kept; run /deleteanime again to review the current list."
    elif partial:
        final_note = "Some cleanup steps failed. Check the card action log before retrying the command."
    elif catalog_preserved_for_sibling:
        final_note = "All exact-name cards were removed. The shared Anime catalog entry was kept because cards using the alternate [🎮] marker still exist."
    else:
        final_note = "The exact Anime value and all matching cards have been removed."

    return (
        f"{'⚠️' if partial else '✅'} <b>{'Anime Delete Partially Completed' if partial else 'Anime Deleted'}</b>\n\n"
        f"🌴 Anime: {escape_html(item.get('anime_name', ''))}\n"
        f"🎴 Exact-name cards deleted: <code>{len(deleted_cards)}/{len(item.get('cards', []))}</code>\n"
        f"📚 Remaining exact-name cards: <code>{len(remaining_cards)}</code>\n"
        f"🗂 Anime catalog entry deleted: <code>{'Yes' if catalog_deleted else 'No'}</code>\n"
        f"🛡 Shared catalog kept for alternate marker cards: <code>{'Yes' if catalog_preserved_for_sibling else 'No'}</code>\n"
        f"🗄 Archive messages deleted: <code>{archive_deleted}</code>\n"
        f"⚠️ Archive delete failures: <code>{archive_failed}</code>\n"
        f"⚠️ Card delete failures: <code>{len(failed_ids)}</code>\n"
        f"⚠️ Reference cleanup failed: <code>{'Yes' if cleanup_failed else 'No'}</code>\n\n"
        f"{escape_html(final_note)}"
    )


async def delete_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user or not query.message:
        return
    match = re.fullmatch(
        r"(carddel|animedel):(confirm|cancel|page):([a-f0-9]{10})(?::(\d+))?",
        query.data,
    )
    if not match:
        await query.answer("Invalid delete confirmation.", show_alert=True)
        return

    prefix, decision, token, page_value = match.groups()
    _prune_pending_card_deletions()
    item = _PENDING_CARD_DELETIONS.get(token)
    expected_action = "card" if prefix == "carddel" else "anime"
    if not item or item.get("action") != expected_action:
        await query.answer("Confirmation expired. Run the command again.", show_alert=True)
        return
    if (
        int(item.get("actor_id", 0)) != int(query.from_user.id)
        or int(item.get("chat_id", 0)) != int(query.message.chat_id)
        or not is_owner(query.from_user)
    ):
        await query.answer("Only the original owner can use this confirmation.", show_alert=True)
        return

    if decision == "page":
        cards = list(item.get("cards", []))
        total_pages = max(1, (len(cards) + _DELETE_ANIME_PAGE_SIZE - 1) // _DELETE_ANIME_PAGE_SIZE)
        page = max(0, min(int(page_value or 0), total_pages - 1))
        item["page"] = page
        viewed_pages = set(item.get("viewed_pages") or {0})
        viewed_pages.add(page)
        item["viewed_pages"] = viewed_pages
        item["created"] = time.time()
        await query.answer()
        await _edit_delete_prompt(
            query,
            _anime_delete_page_text(item, page),
            _delete_confirm_keyboard(token, "anime", page, total_pages, len(cards)),
        )
        return

    if decision == "cancel":
        _PENDING_CARD_DELETIONS.pop(token, None)
        await query.answer("Cancelled. No data was deleted.")
        await _edit_delete_prompt(
            query,
            "✅ <b>Cancelled</b>\n\nNo card or Anime data was deleted.",
            None,
        )
        return

    if decision == "confirm" and expected_action == "anime":
        cards = list(item.get("cards", []))
        total_pages = max(1, (len(cards) + _DELETE_ANIME_PAGE_SIZE - 1) // _DELETE_ANIME_PAGE_SIZE)
        viewed_pages = set(item.get("viewed_pages") or {int(item.get("page", 0) or 0)})
        missing_pages = set(range(total_pages)) - viewed_pages
        if missing_pages:
            reviewed = len(set(range(total_pages)) - missing_pages)
            await query.answer(
                f"Review all preview pages before deleting ({reviewed}/{total_pages} pages reviewed).",
                show_alert=True,
            )
            return

    await query.answer("Processing deletion…")
    item["created"] = time.time()
    try:
        if expected_action == "card":
            result = await _confirm_delete_card(context, item)
        else:
            result = await _confirm_delete_anime(context, item)
    except Exception as exc:
        print(f"CONFIRMED DELETION FAILED: action={expected_action} token={token} error={exc!r}", flush=True)
        result = (
            "⚠️ <b>Deletion stopped with an error.</b>\n\n"
            "Some steps may already have completed. Check the card action log and current data before trying again."
        )
    finally:
        _PENDING_CARD_DELETIONS.pop(token, None)
    await _edit_delete_prompt(query, result, None)


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
    app.add_handler(CommandHandler("raritylist", raritylist_cmd))
    app.add_handler(CommandHandler("delete", delete_card_cmd))
    app.add_handler(CommandHandler("deleteanime", delete_anime_cmd))
    app.add_handler(
        CallbackQueryHandler(
            delete_confirmation_callback,
            pattern=r"^(?:carddel|animedel):",
        )
    )
    app.add_handler(CommandHandler("give", give_cmd))
    app.add_handler(CommandHandler("free", free_cmd))
    app.add_handler(CommandHandler("rmfree", rmfree_cmd))
