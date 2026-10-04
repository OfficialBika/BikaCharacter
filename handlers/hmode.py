from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

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


RICH_API_TIMEOUT_SECONDS = 20


def _bot_api_json_sync(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=RICH_API_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} HTTP {exc.code}: {detail}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"{method}: {result.get('description')}")
    return result


async def _edit_hmode_message(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    """Edit both normal and Rich Messages reliably.

    Harem table mode uses Telegram's new Rich Message API, while PTB 22.8 does
    not expose the Bot API's rich_message parameter on editMessageText.
    Calling the Bot API directly with normal text is supported for editing a
    Rich Message and prevents the Settings/Harem Mode callback from appearing
    to do nothing.
    """
    payload = {
        "chat_id": int(query.message.chat.id),
        "message_id": int(query.message.message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup.to_dict(),
    }
    try:
        await asyncio.to_thread(
            _bot_api_json_sync,
            str(context.bot.token),
            "editMessageText",
            payload,
        )
    except Exception as exc:
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except Exception:
            print("HMODE EDIT ERROR:", repr(exc), flush=True)
            raise


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
    if not query or not query.data or not query.message:
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

    # Answer immediately so Telegram stops the callback spinner.
    await query.answer()

    if action == "close":
        try:
            await query.message.delete()
        except Exception:
            await _edit_hmode_message(
                query, context, t("cancelled"), InlineKeyboardMarkup([])
            )
        return

    if action == "home":
        from handlers.start import _start_keyboard
        mention = f'<a href="tg://user?id={user_id}">{query.from_user.full_name or query.from_user.username or "User"}</a>'
        await _edit_hmode_message(
            query,
            context,
            t("start_message", mention=mention),
            _start_keyboard(user_id),
        )
        return

    if action == "main":
        await _edit_hmode_message(
            query,
            context,
            t("hmode_choose_sort"),
            build_hmode_menu(user_id),
        )
        return

    if action == "rarity_menu":
        await _edit_hmode_message(
            query,
            context,
            t("hmode_choose_rarity"),
            _rarity_keyboard(user_id),
        )
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
        await _edit_hmode_message(
            query,
            context,
            t("hmode_set_anime"),
            build_hmode_menu(user_id),
        )
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
        await _edit_hmode_message(
            query,
            context,
            t("hmode_set_compact"),
            build_hmode_menu(user_id),
        )
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
        await _edit_hmode_message(
            query,
            context,
            t("hmode_set_rarity", emoji=get_rarity_emoji(rarity), rarity=rarity),
            build_hmode_menu(user_id),
        )
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
        await _edit_hmode_message(
            query,
            context,
            t("hmode_set_anime"),
            build_hmode_menu(user_id),
        )
        return

    await context.bot.answer_callback_query(
        callback_query_id=query.id,
        text=t("invalid_mode"),
        show_alert=True,
    )


def register_hmode_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("hmode", hmode_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CallbackQueryHandler(hmode_callback, pattern=r"^hmode:\d+:.+$"))
