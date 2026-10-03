from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import RARITY_ORDER
from database.mongodb import get_db
from utils.cooldown import should_ignore_update
from utils.db_helpers import ensure_user
from utils.rarity import get_rarity_button_emoji, get_rarity_emoji
from utils.text import utcnow
from utils.i18n import t
from utils.buttons import action_button, rarity_button


def build_hmode_menu(user_id: int, *, include_home: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            action_button(t("hmode_sort_by_anime"), "primary", callback_data=f"hmode:{user_id}:anime"),
            action_button(t("hmode_sort_by_rarity"), "primary", callback_data=f"hmode:{user_id}:rarity_menu"),
        ],
        [
            action_button(t("hmode_sort_compact"), "primary", callback_data=f"hmode:{user_id}:compact"),
        ],
    ]
    if include_home:
        rows.append([action_button(t("hmode_home"), "primary", callback_data=f"hmode:{user_id}:home")])
    rows.append([action_button(t("hmode_close"), "danger", callback_data=f"hmode:{user_id}:close")])
    return InlineKeyboardMarkup(rows)


def _rarity_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for rarity in RARITY_ORDER:
        current.append(
            rarity_button(
                t("hmode_rarity_button", emoji=get_rarity_button_emoji(rarity), rarity=rarity),
                rarity,
                "primary",
                callback_data=f"hmode:{user_id}:rarity:{rarity}",
            )
        )
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([action_button(t("hmode_back"), "primary", callback_data=f"hmode:{user_id}:main")])
    rows.append([action_button(t("hmode_close"), "danger", callback_data=f"hmode:{user_id}:close")])
    return InlineKeyboardMarkup(rows)


async def _reply_hmode(message, user_id: int) -> None:
    await message.reply_text(
        t("hmode_choose_sort"),
        parse_mode="HTML",
        reply_markup=build_hmode_menu(user_id),
    )


async def hmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    await ensure_user(update.effective_user)
    await _reply_hmode(update.effective_message, int(update.effective_user.id))


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await should_ignore_update(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    await ensure_user(update.effective_user)
    user_id = int(update.effective_user.id)
    await update.effective_message.reply_text(
        t("settings_message"),
        parse_mode="HTML",
        reply_markup=build_hmode_menu(user_id, include_home=False),
    )


async def hmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":", 3)
    if len(parts) < 3:
        await query.answer(t("invalid_mode"), show_alert=True)
        return

    _, user_id_raw, action = parts[:3]
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await query.answer(t("invalid_mode"), show_alert=True)
        return

    if int(query.from_user.id) != user_id:
        await query.answer(t("not_your_action"), show_alert=True)
        return

    if action == "close":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(t("cancelled"))
        await query.answer(t("cancelled"))
        return

    if action == "home":
        from handlers.start import _start_keyboard
        mention = f'<a href="tg://user?id={user_id}">{query.from_user.full_name or query.from_user.username or "User"}</a>'
        await query.edit_message_text(
            t("start_message", mention=mention),
            parse_mode="HTML",
            reply_markup=_start_keyboard(user_id),
            disable_web_page_preview=True,
        )
        await query.answer()
        return

    if action == "main":
        await query.edit_message_text(
            t("hmode_choose_sort"),
            parse_mode="HTML",
            reply_markup=build_hmode_menu(user_id),
        )
        await query.answer()
        return

    if action == "rarity_menu":
        await query.edit_message_text(
            t("hmode_choose_rarity"),
            parse_mode="HTML",
            reply_markup=_rarity_keyboard(user_id),
        )
        await query.answer()
        return

    now = utcnow()
    if action == "anime":
        await get_db().users.update_one(
            {"userId": user_id},
            {"$set": {
                "haremSort": "anime",
                "haremRarity": "",
                "haremView": "anime",
                "updatedAt": now,
            }},
            upsert=True,
        )
        await query.edit_message_text(
            t("hmode_set_anime"),
            parse_mode="HTML",
            reply_markup=build_hmode_menu(user_id),
        )
        await query.answer(t("updated"))
        return

    if action == "compact":
        await get_db().users.update_one(
            {"userId": user_id},
            {"$set": {
                "haremSort": "compact",
                "haremRarity": "",
                "haremView": "compact",
                "updatedAt": now,
            }},
            upsert=True,
        )
        await query.edit_message_text(
            t("hmode_set_compact"),
            parse_mode="HTML",
            reply_markup=build_hmode_menu(user_id),
        )
        await query.answer(t("updated"))
        return

    if action == "rarity":
        if len(parts) < 4:
            await query.answer(t("invalid_mode"), show_alert=True)
            return
        rarity = parts[3]
        if rarity not in RARITY_ORDER:
            await query.answer(t("invalid_mode"), show_alert=True)
            return
        await get_db().users.update_one(
            {"userId": user_id},
            {"$set": {
                "haremSort": "rarity",
                "haremRarity": rarity,
                "haremView": "rarity",
                "updatedAt": now,
            }},
            upsert=True,
        )
        await query.edit_message_text(
            t("hmode_set_rarity", emoji=get_rarity_emoji(rarity), rarity=rarity),
            parse_mode="HTML",
            reply_markup=build_hmode_menu(user_id),
        )
        await query.answer(t("updated"))
        return

    if action in ("default", "detailed", "reset"):
        await get_db().users.update_one(
            {"userId": user_id},
            {"$set": {
                "haremSort": "anime",
                "haremRarity": "",
                "haremView": "anime",
                "updatedAt": now,
            }},
            upsert=True,
        )
        await query.edit_message_text(
            t("hmode_set_anime"),
            parse_mode="HTML",
            reply_markup=build_hmode_menu(user_id),
        )
        await query.answer(t("updated"))
        return

    await query.answer(t("invalid_mode"), show_alert=True)


def register_hmode_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("hmode", hmode_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CallbackQueryHandler(hmode_callback, pattern=r"^hmode:\d+:.+$"))
