from __future__ import annotations

import asyncio
from io import BytesIO

from telegram import Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    ENABLE_BROADCAST,
    BROADCAST_DELAY,
    BROADCAST_MAX_RETRY,
    BROADCAST_WORKERS,
    ENABLE_BROADCAST_LOG,
)
from database.mongodb import get_db
from utils.permissions import is_owner
from utils.text import escape_html, utcnow


_BROADCAST_LOCK = asyncio.Lock()
_BROADCAST_STOP = asyncio.Event()
_BROADCAST_ACTIVE = False
_BROADCAST_SEMAPHORE = asyncio.Semaphore(BROADCAST_WORKERS)


def _flag_set(args: list[str]) -> set[str]:
    return {str(arg or "").strip().lower() for arg in args}


async def _collect_targets(
    *,
    include_groups: bool,
    include_users: bool,
    harem_only: bool = False,
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
        user_query = {"cards.0": {"$exists": True}} if harem_only else {}

        async for doc in db.users.find(user_query, {"userId": 1}):
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
    async with _BROADCAST_SEMAPHORE:
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


async def _broadcast_worker(
    *,
    context,
    source,
    queue: asyncio.Queue,
    copy_mode: bool,
    result: dict,
    counter_lock: asyncio.Lock,
):
    while True:
        target_id = await queue.get()
        if target_id is None:
            queue.task_done()
            break

        delivered = False
        skipped = False
        failure_reason = ""

        try:
            # Stop requests prevent any not-yet-started target from being sent.
            if _BROADCAST_STOP.is_set():
                skipped = True
            else:
                for attempt in range(BROADCAST_MAX_RETRY + 1):
                    if attempt > 0 and _BROADCAST_STOP.is_set():
                        skipped = True
                        break

                    try:
                        await _deliver(
                            context=context,
                            source=source,
                            target_id=target_id,
                            copy_mode=copy_mode,
                        )
                        delivered = True
                        break

                    except RetryAfter as exc:
                        failure_reason = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        if attempt >= BROADCAST_MAX_RETRY or _BROADCAST_STOP.is_set():
                            break
                        retry_after = max(
                            1.0,
                            float(getattr(exc, "retry_after", 1) or 1),
                        )
                        await asyncio.sleep(retry_after + 1)

                    except (Forbidden, BadRequest) as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        break

                    except TelegramError as exc:
                        # Remaining TelegramError subclasses are generally
                        # transport/API errors. Retry them within the configured
                        # bounded retry budget, but never after a stop request.
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        if attempt >= BROADCAST_MAX_RETRY or _BROADCAST_STOP.is_set():
                            break
                        backoff = min(2.0, 0.25 * (2 ** attempt))
                        await asyncio.sleep(backoff)

                    except Exception as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        break

        finally:
            async with counter_lock:
                result["processed"] += 1
                if delivered:
                    result["success"] += 1
                elif skipped:
                    result["skipped"] += 1
                else:
                    result["failed"] += 1
                    if failure_reason:
                        result["failures"].append(
                            f"{target_id} - {failure_reason}"
                        )

            queue.task_done()


def _status_text(
    *,
    total: int,
    processed: int,
    groups_ok: int,
    users_ok: int,
    failed: int,
    skipped: int,
    status: str,
) -> str:
    return (
        "📡 <b>𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓 𝐒𝐓𝐀𝐓𝐔𝐒</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Status: <b>{escape_html(status)}</b>\n"
        f"Processed: <code>{processed}/{total}</code>\n"
        f"Groups: <code>{groups_ok}</code>\n"
        f"Users: <code>{users_ok}</code>\n"
        f"Failed: <code>{failed}</code>\n"
        f"Skipped: <code>{skipped}</code>"
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

    if not ENABLE_BROADCAST:
        await msg.reply_text(
            "⚠️ Broadcast system is currently disabled."
        )
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
    harem_only = "-h" in flags
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
        harem_only=harem_only,
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
                    skipped=0,
                    status="Running",
                ),
                parse_mode="HTML",
            )

            queue = asyncio.Queue()
            for target_id in targets:
                await queue.put(target_id)

            counter_lock = asyncio.Lock()
            worker_results = {
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "failures": failures,
                "sent_ids": [],
            }

            workers = [
                asyncio.create_task(
                    _broadcast_worker(
                        context=context,
                        source=source,
                        queue=queue,
                        copy_mode=copy_mode,
                        result=worker_results,
                        counter_lock=counter_lock,
                    )
                )
                for _ in range(BROADCAST_WORKERS)
            ]

            while not queue.empty() or worker_results["processed"] < len(targets):
                await asyncio.sleep(1)

                processed = worker_results["processed"]
                failed = worker_results["failed"]
                skipped = worker_results["skipped"]

                if processed % 25 == 0 or processed == len(targets):
                    try:
                        await status_msg.edit_text(
                            _status_text(
                                total=len(targets),
                                processed=processed,
                                groups_ok=groups_ok,
                                users_ok=users_ok,
                                failed=failed,
                                skipped=skipped,
                                status="Stopped" if _BROADCAST_STOP.is_set() else "Running",
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

            await queue.join()

            for _ in workers:
                await queue.put(None)

            await asyncio.gather(*workers)

            processed = worker_results["processed"]
            failed = worker_results["failed"]
            skipped = worker_results["skipped"]

            # Recalculate success counters from completed results.
            groups_ok = min(worker_results["success"], len(group_ids))
            users_ok = max(
                0,
                worker_results["success"] - groups_ok,
            )

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
                    "skipped": skipped,
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
                    skipped=skipped,
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

    if not ENABLE_BROADCAST:
        await msg.reply_text(
            "⚠️ Broadcast system is currently disabled."
        )
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
