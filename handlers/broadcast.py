from __future__ import annotations

import asyncio
import time
from collections import deque
from io import BytesIO

from telegram import Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    ENABLE_BROADCAST,
    BROADCAST_MAX_RETRY,
    BROADCAST_PAID_RATE,
    BROADCAST_RATE,
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


class _BroadcastRateLimiter:
    """Small asyncio token-window limiter for Telegram's global broadcast cap."""

    def __init__(self, rate: int):
        self.rate = max(1, int(rate))
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - 1.0
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()

                if len(self._timestamps) < self.rate:
                    self._timestamps.append(now)
                    return

                wait_for = max(0.01, 1.0 - (now - self._timestamps[0]))

            await asyncio.sleep(wait_for)


_FREE_RATE_LIMITER = _BroadcastRateLimiter(BROADCAST_RATE)
_PAID_RATE_LIMITER = _BroadcastRateLimiter(BROADCAST_PAID_RATE)


def _flag_set(args: list[str]) -> set[str]:
    return {str(arg or "").strip().lower() for arg in args}


async def _sleep_until_or_stop(seconds: float) -> bool:
    try:
        await asyncio.wait_for(
            _BROADCAST_STOP.wait(),
            timeout=max(0.0, float(seconds)),
        )
        return True
    except asyncio.TimeoutError:
        return False


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

    targets = sorted(group_ids) + sorted(user_ids - group_ids)
    return targets, group_ids, user_ids


async def _deliver(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    source,
    target_id: int,
    copy_mode: bool,
    paid_mode: bool,
) -> None:
    async with _BROADCAST_SEMAPHORE:
        # Telegram's paid broadcast is intended for bulk user notifications.
        # Never enable it for group targets or forward mode.
        if paid_mode:
            await _PAID_RATE_LIMITER.acquire()
        else:
            await _FREE_RATE_LIMITER.acquire()

        if copy_mode:
            await context.bot.copy_message(
                chat_id=int(target_id),
                from_chat_id=int(source.chat_id),
                message_id=int(source.message_id),
                reply_markup=source.reply_markup,
                allow_paid_broadcast=bool(paid_mode),
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
    paid_mode: bool,
    group_ids: set[int],
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
                            paid_mode=paid_mode,
                        )
                        delivered = True
                        break

                    except RetryAfter as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        if attempt >= BROADCAST_MAX_RETRY or _BROADCAST_STOP.is_set():
                            break
                        retry_after = max(
                            1.0,
                            float(getattr(exc, "retry_after", 1) or 1),
                        )
                        if await _sleep_until_or_stop(retry_after + 0.25):
                            break

                    except (Forbidden, BadRequest) as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        break

                    except TelegramError as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        if attempt >= BROADCAST_MAX_RETRY or _BROADCAST_STOP.is_set():
                            break
                        backoff = min(4.0, 0.25 * (2 ** attempt))
                        if await _sleep_until_or_stop(backoff):
                            break

                    except Exception as exc:
                        failure_reason = f"{type(exc).__name__}: {exc}"
                        break

        finally:
            async with counter_lock:
                result["processed"] += 1

                if target_id in group_ids:
                    result["group_total_processed"] += 1
                    if delivered:
                        result["groups_success"] += 1
                else:
                    result["user_total_processed"] += 1
                    if delivered:
                        result["users_success"] += 1

                if delivered:
                    result["success"] += 1
                elif skipped:
                    result["skipped"] += 1
                else:
                    result["failed"] += 1
                    if failure_reason and len(result["failures"]) < 5000:
                        result["failures"].append(
                            f"{target_id} - {failure_reason}"
                        )

            queue.task_done()


def _status_text(
    *,
    total: int,
    processed: int,
    groups_success: int,
    users_success: int,
    failed: int,
    skipped: int,
    started_at: float,
    status: str,
) -> str:
    elapsed = max(0.1, time.monotonic() - started_at)
    rate = processed / elapsed
    remaining = max(0, total - processed)
    eta = int(remaining / rate) if rate > 0 else 0
    percent = (processed / total * 100.0) if total else 100.0

    if eta >= 3600:
        eta_text = f"{eta // 3600}h {(eta % 3600) // 60}m"
    elif eta >= 60:
        eta_text = f"{eta // 60}m {eta % 60}s"
    else:
        eta_text = f"{eta}s"

    return (
        "📡 <b>𝐁𝐈𝐊𝐀 𝐁𝐑𝐎𝐀𝐃𝐂𝐀𝐒𝐓</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Status: <b>{escape_html(status)}</b>\n"
        f"📊 Progress: <b>{percent:.1f}%</b>  "
        f"<code>{processed}/{total}</code>\n"
        f"👥 Groups: <code>{groups_success}</code>  "
        f"👤 Users: <code>{users_success}</code>\n"
        f"❌ Failed: <code>{failed}</code>  "
        f"⏭ Skipped: <code>{skipped}</code>\n"
        f"🚀 Speed: <code>{rate:.1f}/s</code>  "
        f"⏱ ETA: <code>{eta_text}</code>"
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
        await msg.reply_text("⚠️ Broadcast system is currently disabled.")
        return

    if not msg.reply_to_message:
        await msg.reply_text(
            "Reply to the message you want to broadcast, then use:\n\n"
            "/broadcast\n"
            "/broadcast -copy\n"
            "/broadcast -user\n"
            "/broadcast -user -copy\n"
            "/broadcast -user -copy -paid\n"
            "/broadcast -nochat -user\n"
            "/broadcast -nochat -user -h\n\n"
            "Default: groups only.\n"
            "-user: include users.\n"
            "-nochat: exclude groups.\n"
            "-h: users with harem only.\n"
            "-copy: copy instead of forward.\n"
            "-paid: optional paid broadcast for private users only."
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
    paid_requested = "-paid" in flags

    if not include_groups and not include_users:
        await msg.reply_text(
            "❌ No targets selected.\n"
            "Use /broadcast -nochat -user for users only."
        )
        return

    if paid_requested and (not include_users or include_groups or not copy_mode):
        await msg.reply_text(
            "❌ <b>-paid</b> is only available for <b>user-only copy broadcasts</b>.\n"
            "Use: <code>/broadcast -nochat -user -copy -paid</code>",
            parse_mode="HTML",
        )
        return

    status_msg = await msg.reply_html("⏳ <b>Preparing broadcast targets...</b>")

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

        source = msg.reply_to_message
        started_at = time.monotonic()
        failures: list[str] = []
        worker_results = {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "groups_success": 0,
            "users_success": 0,
            "group_total_processed": 0,
            "user_total_processed": 0,
            "failures": failures,
        }

        try:
            mode_text = "PAID • users" if paid_requested else (
                "COPY" if copy_mode else "FORWARD"
            )
            await status_msg.edit_text(
                _status_text(
                    total=len(targets),
                    processed=0,
                    groups_success=0,
                    users_success=0,
                    failed=0,
                    skipped=0,
                    started_at=started_at,
                    status=f"Running • {mode_text}",
                ),
                parse_mode="HTML",
            )

            queue: asyncio.Queue = asyncio.Queue(maxsize=max(100, BROADCAST_WORKERS * 4))
            for target_id in targets:
                await queue.put(target_id)

            counter_lock = asyncio.Lock()
            workers = [
                asyncio.create_task(
                    _broadcast_worker(
                        context=context,
                        source=source,
                        queue=queue,
                        copy_mode=copy_mode,
                        paid_mode=paid_requested,
                        group_ids=group_ids,
                        result=worker_results,
                        counter_lock=counter_lock,
                    ),
                    name=f"bika-broadcast-{index + 1}",
                )
                for index in range(BROADCAST_WORKERS)
            ]

            last_ui_update = 0.0
            last_processed = -1

            while worker_results["processed"] < len(targets):
                await asyncio.sleep(0.25)
                processed = worker_results["processed"]
                now = time.monotonic()

                # Keep Telegram UI traffic low while still feeling live.
                if (
                    processed == len(targets)
                    or processed - last_processed >= 25
                    or now - last_ui_update >= 2.0
                ):
                    last_processed = processed
                    last_ui_update = now
                    try:
                        await status_msg.edit_text(
                            _status_text(
                                total=len(targets),
                                processed=processed,
                                groups_success=worker_results["groups_success"],
                                users_success=worker_results["users_success"],
                                failed=worker_results["failed"],
                                skipped=worker_results["skipped"],
                                started_at=started_at,
                                status=(
                                    "Stopped"
                                    if _BROADCAST_STOP.is_set()
                                    else f"Running • {mode_text}"
                                ),
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

            await queue.join()

            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)

            final_status = "Stopped" if _BROADCAST_STOP.is_set() else "Completed"

            if ENABLE_BROADCAST_LOG:
                await get_db().broadcast_logs.insert_one(
                    {
                        "ownerId": int(user.id),
                        "sourceChatId": int(source.chat_id),
                        "sourceMessageId": int(source.message_id),
                        "copyMode": bool(copy_mode),
                        "paidMode": bool(paid_requested),
                        "includeGroups": bool(include_groups),
                        "includeUsers": bool(include_users),
                        "haremOnly": bool(harem_only),
                        "targetCount": len(targets),
                        "processed": worker_results["processed"],
                        "groupsSent": worker_results["groups_success"],
                        "usersSent": worker_results["users_success"],
                        "failed": worker_results["failed"],
                        "skipped": worker_results["skipped"],
                        "status": final_status.lower(),
                        "durationSeconds": round(time.monotonic() - started_at, 3),
                        "createdAt": utcnow(),
                    }
                )

            await status_msg.edit_text(
                _status_text(
                    total=len(targets),
                    processed=worker_results["processed"],
                    groups_success=worker_results["groups_success"],
                    users_success=worker_results["users_success"],
                    failed=worker_results["failed"],
                    skipped=worker_results["skipped"],
                    started_at=started_at,
                    status=final_status,
                ),
                parse_mode="HTML",
            )

            if failures:
                report = BytesIO(
                    "\n".join(failures).encode("utf-8", errors="replace")
                )
                report.name = "broadcast_errors.txt"
                await msg.reply_document(
                    document=report,
                    caption=(
                        "📄 <b>Broadcast error report</b>\n"
                        f"Failed: {worker_results['failed']}"
                    ),
                    parse_mode="HTML",
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
        await msg.reply_text("⚠️ Broadcast system is currently disabled.")
        return

    if not _BROADCAST_ACTIVE:
        await msg.reply_text("ℹ️ No broadcast is currently running.")
        return

    _BROADCAST_STOP.set()
    await msg.reply_text(
        "🛑 Broadcast stop requested.\n"
        "The current Telegram request will finish; remaining targets will be skipped."
    )


def register_broadcast_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("stop_broadcast", stop_broadcast_cmd))
    app.add_handler(CommandHandler("stop_gcast", stop_broadcast_cmd))
