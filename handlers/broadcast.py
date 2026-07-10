from __future__ import annotations

import asyncio
from io import BytesIO

from telegram import Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from database.mongodb import get_db
from utils.permissions import is_owner
from utils.text import escape_html, utcnow


_BROADCAST_LOCK = asyncio.Lock()
_BROADCAST_STOP = asyncio.Event()
_BROADCAST_ACTIVE = False


def _flag_set(args: list[str]) -> set[str]:
    return {str(arg or "").strip().lower() for arg in args}


async def _collect_targets(
    *,
    include_groups: bool,
    include_users: bool,
) -> tuple[list[int], set[int], set[int]]:
    db = get_db()
    group_ids: set[int] = set()
    user_ids: set[int] = set()

    if include_groups:
        async for doc in db.groups.find({}, {"groupId": 1}):
            try:
                group_id = int(doc.get("groupId", 0) or 0)
            except (TypeError, ValueError):
                continue
            if group_id:
                group_ids.add(group_id)

    if include_users:
        async for doc in db.users.find({}, {"userId": 1}):
            try:
                user_id = int(doc.get("userId", 0) or 0)
            except (TypeError, ValueError):
                continue
            if user_id:
                user_ids.add(user_id)

    # Groups first, then users. Remove duplicate chat IDs.
    targets = sorted(group_ids) + sorted(user_ids - group_ids)
    return targets, group_ids, user_ids


async def _deliver(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    source,
    target_id: int,
    copy_mode: bool,
) -> None:
    if copy_mode:
        await context.bot.copy_message(
            chat_id=int(target_id),
            from_chat_id=int(source.chat_id),
            message_id=int(source.message_id),
            reply_markup=source.reply_markup,
        )
        return

    await context.bot.forward_message(
        chat_id=int(target_id),
        from_chat_id=int(source.chat_id),
        message_id=int(source.message_id),
    )


def _status_text(
    *,
    total: int,
    processed: int,
    groups_ok: int,
    users_ok: int,
    failed: int,
    status: str,
) -> str:
    return (
        "📡 <b>𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐒𝐓𝐀𝐓𝐔𝐒</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Status: <b>{escape_html(status)}</b>\n"
        f"Processed: <code>{processed}/{total}</code>\n"
        f"Groups: <code>{groups_ok}</code>\n"
        f"Users: <code>{users_ok}</code>\n"
        f"Failed: <code>{failed}</code>"
    )


async def broadcast_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    global _BROADCAST_ACTIVE

    user = update.effective_user
    msg = update.effective_message

    if not user or not msg or not is_owner(user):
        return

    if not msg.reply_to_message:
        await msg.reply_text(
            "Reply to the message you want to broadcast, then use:\n\n"
            "/broadcast\n"
            "/broadcast -copy\n"
            "/broadcast -user\n"
            "/broadcast -user -copy\n"
            "/broadcast -nochat -user\n\n"
            "Default: groups only.\n"
            "-user: include users.\n"
            "-nochat: exclude groups.\n"
            "-copy: copy instead of forward."
        )
        return

    if _BROADCAST_ACTIVE or _BROADCAST_LOCK.locked():
        await msg.reply_text("⚠️ A broadcast is already running.")
        return

    flags = _flag_set(list(context.args or []))
    include_groups = "-nochat" not in flags
    include_users = "-user" in flags
    copy_mode = "-copy" in flags

    if not include_groups and not include_users:
        await msg.reply_text(
            "❌ No targets selected.\n"
            "Use /broadcast -nochat -user for users only."
        )
        return

    status_msg = await msg.reply_html(
        "⏳ <b>Preparing broadcast targets...</b>"
    )

    targets, group_ids, user_ids = await _collect_targets(
        include_groups=include_groups,
        include_users=include_users,
    )

    if not targets:
        await status_msg.edit_text("❌ No broadcast targets found.")
        return

    async with _BROADCAST_LOCK:
        _BROADCAST_ACTIVE = True
        _BROADCAST_STOP.clear()

        groups_ok = 0
        users_ok = 0
        failed = 0
        processed = 0
        failures: list[str] = []

        source = msg.reply_to_message

        try:
            await status_msg.edit_text(
                _status_text(
                    total=len(targets),
                    processed=0,
                    groups_ok=0,
                    users_ok=0,
                    failed=0,
                    status="Running",
                ),
                parse_mode="HTML",
            )

            for target_id in targets:
                if _BROADCAST_STOP.is_set():
                    break

                delivered = False
                retry_attempts = 0

                while not delivered and retry_attempts < 3:
                    try:
                        await _deliver(
                            context=context,
                            source=source,
                            target_id=target_id,
                            copy_mode=copy_mode,
                        )
                        delivered = True

                    except RetryAfter as exc:
                        retry_attempts += 1
                        delay = int(
                            getattr(exc, "retry_after", 1) or 1
                        ) + 1
                        await asyncio.sleep(delay)

                    except (Forbidden, BadRequest, TelegramError) as exc:
                        failures.append(
                            f"{target_id} - {type(exc).__name__}: {exc}"
                        )
                        break

                    except Exception as exc:
                        failures.append(
                            f"{target_id} - {type(exc).__name__}: {exc}"
                        )
                        break

                processed += 1

                if delivered:
                    if target_id in group_ids:
                        groups_ok += 1
                    elif target_id in user_ids:
                        users_ok += 1
                else:
                    failed += 1

                # Gentle pacing; RetryAfter remains authoritative.
                await asyncio.sleep(0.08)

                if processed % 25 == 0 or processed == len(targets):
                    try:
                        await status_msg.edit_text(
                            _status_text(
                                total=len(targets),
                                processed=processed,
                                groups_ok=groups_ok,
                                users_ok=users_ok,
                                failed=failed,
                                status="Running",
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

            final_status = (
                "Stopped" if _BROADCAST_STOP.is_set() else "Completed"
            )

            await get_db().broadcast_logs.insert_one(
                {
                    "ownerId": int(user.id),
                    "sourceChatId": int(source.chat_id),
                    "sourceMessageId": int(source.message_id),
                    "copyMode": bool(copy_mode),
                    "includeGroups": bool(include_groups),
                    "includeUsers": bool(include_users),
                    "targetCount": len(targets),
                    "processed": processed,
                    "groupsSent": groups_ok,
                    "usersSent": users_ok,
                    "failed": failed,
                    "status": final_status.lower(),
                    "createdAt": utcnow(),
                }
            )

            await status_msg.edit_text(
                _status_text(
                    total=len(targets),
                    processed=processed,
                    groups_ok=groups_ok,
                    users_ok=users_ok,
                    failed=failed,
                    status=final_status,
                ),
                parse_mode="HTML",
            )

            if failures:
                report = BytesIO(
                    "\n".join(failures).encode(
                        "utf-8",
                        errors="replace",
                    )
                )
                report.name = "broadcast_errors.txt"

                await msg.reply_document(
                    document=report,
                    caption=(
                        "📄 Broadcast error report\n"
                        f"Failed: {failed}"
                    ),
                )

        finally:
            _BROADCAST_ACTIVE = False
            _BROADCAST_STOP.clear()


async def stop_broadcast_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    msg = update.effective_message

    if not user or not msg or not is_owner(user):
        return

    if not _BROADCAST_ACTIVE:
        await msg.reply_text("ℹ️ No broadcast is currently running.")
        return

    _BROADCAST_STOP.set()
    await msg.reply_text(
        "🛑 Broadcast stop requested.\n"
        "The current send attempt will finish, then broadcasting will stop."
    )


def register_broadcast_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(
        CommandHandler("stop_broadcast", stop_broadcast_cmd)
    )
    app.add_handler(
        CommandHandler("stop_gcast", stop_broadcast_cmd)
    )
