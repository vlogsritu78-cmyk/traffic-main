"""Telegram redirect bot — long polling. Single purpose: send the bilingual
welcome + one inline button that opens the official account.
Destination account and welcome copy are read live from bot_settings (admin panel)."""

import asyncio
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot_content import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION, welcome_text

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OFFICIAL_URL = os.environ["OFFICIAL_TELEGRAM_URL"]
OFFICIAL_USERNAME = os.environ["OFFICIAL_TELEGRAM_USERNAME"]
BUTTON_TEXT = os.environ["OFFICIAL_BUTTON_TEXT"]
# Full TrafficStars S2S postback URL containing a {clickid} placeholder.
# Empty string = postback disabled.
POSTBACK_URL = os.environ.get("TRAFFICSTARS_POSTBACK_URL", "").strip()

# Emergent Object Storage — used to hold a broadcast's attached file just long
# enough for the background job to read it once (to get Telegram's file_id);
# pod-local disk isn't reliable storage for a deployed app.
STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
_storage_key: str | None = None


async def _init_storage(force: bool = False) -> str:
    global _storage_key
    if _storage_key and not force:
        return _storage_key
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY})
        resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    return _storage_key


async def put_broadcast_file(path: str, data: bytes, content_type: str) -> dict:
    key = await _init_storage()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.put(
            f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key, "Content-Type": content_type}, content=data
        )
        resp.raise_for_status()
    return resp.json()


async def get_broadcast_file(path: str) -> bytes:
    key = await _init_storage()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
        if resp.status_code == 404:
            key = await _init_storage(force=True)
            resp = await client.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
        resp.raise_for_status()
    return resp.content

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("redirect-bot")

mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = mongo[os.environ["DB_NAME"]]

_settings_cache = {"at": 0.0, "data": None}
SETTINGS_TTL = 5.0


async def get_settings() -> dict:
    """Live settings with a short TTL so panel edits apply almost immediately."""
    now = time.monotonic()
    if _settings_cache["data"] is not None and now - _settings_cache["at"] < SETTINGS_TTL:
        return _settings_cache["data"]
    try:
        doc = await db.bot_settings.find_one({"_id": "config"}) or {}
    except Exception as exc:
        logger.warning("settings read failed: %s", exc)
        doc = _settings_cache["data"] or {}
    _settings_cache.update(at=now, data=doc)
    return doc


async def track(update: Update, event: str, source: str | None = None) -> None:
    user = update.effective_user
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.bot_events.insert_one(
            {
                "event": event,
                "source": source,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "language_code": user.language_code,
                "created_at": now,
            }
        )
        await db.bot_users.update_one(
            {"user_id": user.id},
            {
                "$setOnInsert": {"user_id": user.id, "first_seen_at": now, "source": source},
                # Interacting again proves they haven't blocked the bot, even
                # if a past broadcast to them had failed — un-mark them so
                # future broadcasts include them again.
                "$set": {"username": user.username, "first_name": user.first_name, "last_seen_at": now, "blocked": False},
                "$inc": {"interactions": 1},
            },
            upsert=True,
        )
    except Exception as exc:  # never let analytics break the reply
        logger.warning("tracking failed: %s", exc)


async def resolve_click_id(payload: str, user_id: int) -> str:
    """A /start payload is either a bridge token (long click ids) or a raw click id."""
    try:
        doc = await db.bot_clicks.find_one_and_update(
            {"token": payload},
            {"$set": {"used_at": datetime.now(timezone.utc).isoformat(), "user_id": user_id}},
        )
    except Exception as exc:
        logger.warning("token lookup failed: %s", exc)
        return payload
    if doc:
        logger.info("token %s resolved to click id len=%s", payload, len(doc["click_id"]))
        return doc["click_id"]
    return payload


async def fire_postback(click_id: str, user_id: int) -> None:
    """Report the /start conversion to TrafficStars. One postback per click id."""
    now = datetime.now(timezone.utc).isoformat()
    url = POSTBACK_URL.replace("{clickid}", click_id).replace("{click_id}", click_id)
    try:
        claim = await db.bot_postbacks.update_one(
            {"click_id": click_id},
            {"$setOnInsert": {"click_id": click_id, "user_id": user_id, "url": url, "created_at": now}},
            upsert=True,
        )
        if claim.upserted_id is None:
            logger.info("postback skipped — duplicate click id %s", click_id)
            return
    except Exception as exc:
        logger.warning("postback claim failed: %s", exc)
        return

    result = {}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url)
        result = {"status_code": resp.status_code, "response": resp.text[:500], "ok": resp.is_success}
        logger.info("postback %s -> %s %s", click_id, resp.status_code, resp.text[:120])
    except Exception as exc:
        result = {"ok": False, "error": repr(exc)}
        logger.warning("postback failed for %s: %r", click_id, exc)
    try:
        await db.bot_postbacks.update_one({"click_id": click_id}, {"$set": result})
    except Exception as exc:
        logger.warning("postback log failed: %s", exc)


async def reply(update: Update) -> None:
    s = await get_settings()
    url = s.get("official_url") or OFFICIAL_URL
    username = s.get("official_username") or OFFICIAL_USERNAME.lstrip("@")
    image_path = s.get("welcome_image_path")
    if image_path:
        try:
            data = await get_broadcast_file(image_path)
            await update.effective_message.reply_photo(data)
        except Exception as exc:
            logger.warning("welcome image send failed: %s", exc)
    await update.effective_message.reply_text(
        welcome_text(f"@{username.lstrip('@')}", s.get("welcome_ta"), s.get("welcome_en")),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BUTTON_TEXT, url=url)]]),
        disable_web_page_preview=True,
    )


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    payload = context.args[0] if context.args else None
    logger.info("/start user=%s payload=%r len=%s", user_id, payload, len(payload or ""))
    click_id = await resolve_click_id(payload, user_id) if payload else None
    await track(update, "start", click_id)
    await reply(update)
    if POSTBACK_URL and click_id:
        await fire_postback(click_id, user_id)


async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track(update, "message")
    await reply(update)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("update error", exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    # no menus, no extra commands — Telegram shows only the default START button
    await app.bot.delete_my_commands()
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await app.bot.set_my_description(BOT_DESCRIPTION)
    await app.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    logger.info("bot @%s online", me.username)
    logger.info("trafficstars postback: %s", "ENABLED" if POSTBACK_URL else "disabled (no URL set)")
    try:
        await db.bot_postbacks.create_index("click_id", unique=True)
        await db.bot_clicks.create_index("token", unique=True)
    except Exception as exc:
        logger.warning("index setup failed: %s", exc)
    try:
        await db.bot_meta.update_one(
            {"_id": "runtime"},
            {
                "$set": {
                    "bot_username": me.username,
                    "bot_name": me.first_name,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("meta write failed: %s", exc)


def _build_application() -> Application:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(filters.ALL, on_any_message))
    app.add_error_handler(on_error)
    return app


_app: Application | None = None
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]


async def init_bot() -> None:
    """Build + initialize the bot client inside the FastAPI process. This does
    NOT start long-polling — it only prepares the bot so incoming webhook
    updates can be dispatched (process_webhook_update) and outbound calls
    (broadcast, send_message) work. Safe to run in every environment at once:
    it never calls Telegram's getUpdates, so there is nothing to conflict
    over, unlike polling (which only allows one active connection per token
    and can't be reliably scoped to a single environment via env vars, since
    redeploys copy .env as-is)."""
    global _app
    app = _build_application()
    await app.initialize()
    await post_init(app)
    await app.start()
    _app = app


async def shutdown_bot() -> None:
    global _app
    if _app is None:
        return
    await _app.stop()
    await _app.shutdown()
    _app = None


async def process_webhook_update(payload: dict) -> None:
    """Feed one Telegram Update (already-parsed JSON body) into the same
    handlers used for /start and the catch-all message."""
    if _app is None:
        raise RuntimeError("bot is not initialized in this environment")
    update = Update.de_json(payload, _app.bot)
    await _app.process_update(update)


async def activate_webhook(base_url: str) -> dict:
    """Point Telegram's webhook at THIS environment's own public URL, making
    it the sole live receiver of /start events. Setting a webhook always
    atomically replaces whatever URL was set before — e.g. switching from
    preview to production — so there is never a window where two receivers
    are both active, unlike long-polling's race condition."""
    if _app is None:
        raise RuntimeError("bot is not initialized in this environment")
    url = f"{base_url.rstrip('/')}/api/telegram/webhook/{WEBHOOK_SECRET}"
    await _app.bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
    return await webhook_info()


async def webhook_info() -> dict:
    if _app is None:
        raise RuntimeError("bot is not initialized in this environment")
    info = await _app.bot.get_webhook_info()
    return {"webhook_url": info.url or "", "pending_update_count": info.pending_update_count}


def is_ready() -> bool:
    return _app is not None


BROADCAST_CONCURRENCY = 12
BROADCAST_BATCH_SIZE = 300


async def _send_to_user(bot, uid: int, text: str, kind: str | None, file_ref) -> tuple[bool, str]:
    """Returns (ok, error_category). error_category is a short, aggregable
    label (not the raw exception string) so many failures can be summarized
    -- e.g. 'blocked_bot: 190, invalid_chat: 18' -- without needing raw
    server logs, which aren't reachable on a deployed environment."""
    for attempt in range(2):
        try:
            if kind:
                sender = getattr(bot, f"send_{kind}")
                await sender(uid, **{kind: file_ref, "caption": text or None})
            else:
                await bot.send_message(uid, text)
            return True, ""
        except RetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(exc.retry_after + 0.5)
                continue
            return False, "rate_limited"
        except Forbidden:
            return False, "blocked_bot"
        except BadRequest:
            return False, "invalid_chat"
        except (TimedOut, NetworkError):
            return False, "network_timeout"
        except Exception:
            return False, "other"
    return False, "other"


async def _mark_blocked(uid: int, reason: str) -> None:
    """Record that this user is unreachable (blocked the bot / deleted their
    account) so future broadcasts skip them instead of wasting an attempt.
    Cleared automatically the next time they interact with the bot again
    (see track())."""
    await db.bot_users.update_one(
        {"user_id": uid},
        {"$set": {"blocked": True, "blocked_reason": reason, "blocked_at": datetime.now(timezone.utc).isoformat()}},
    )


async def run_broadcast_job(job_id: str) -> None:
    """Send (or resume sending) one broadcast job to every user who hasn't
    been processed by it yet. Safe to call again after an interruption (a
    backend restart mid-send, a redeploy, etc.) — it skips whoever is already
    recorded as sent/failed, so nobody is double-messaged or missed. Scales to
    tens of thousands of recipients via bounded concurrency + batched
    progress checkpoints (so a crash only loses at most one in-flight batch,
    not the whole job)."""
    if _app is None:
        return
    oid = ObjectId(job_id)
    job = await db.bot_broadcasts.find_one({"_id": oid})
    if not job or job.get("status") != "running":
        return

    text = job["text"]
    kind = job.get("media_kind")
    file_ref = job.get("media_file_id")
    file_path = job.get("media_storage_path")
    done_ids = set(job.get("processed_user_ids", []))
    sent = job.get("sent", 0)
    failed = job.get("failed", 0)

    # Skip anyone already known to have blocked the bot / deleted their
    # account (learned from a previous broadcast's failures) — no point
    # wasting an attempt, and it keeps failure rates accurate going forward.
    all_user_ids = [
        u["user_id"] async for u in db.bot_users.find({"blocked": {"$ne": True}}, {"user_id": 1, "_id": 0})
    ]
    pending = [uid for uid in all_user_ids if uid not in done_ids]

    # A file must be uploaded once to get Telegram's file_id back; every other
    # recipient then reuses that file_id instead of re-uploading the bytes.
    if kind and not file_ref and pending:
        uid = pending.pop(0)
        update = {"$push": {"processed_user_ids": uid}}
        category = None
        try:
            data = await get_broadcast_file(file_path)
            bio = BytesIO(data)
            bio.name = job.get("media_filename", "file")
            sender = getattr(_app.bot, f"send_{kind}")
            msg = await sender(uid, **{kind: bio, "caption": text or None})
            obj = getattr(msg, kind)
            file_ref = obj[-1].file_id if kind == "photo" else obj.file_id
            sent += 1
            update["$set"] = {"media_file_id": file_ref, "sent": sent, "failed": failed}
        except Forbidden:
            category = "blocked_bot"
        except BadRequest:
            category = "invalid_chat"
        except (TimedOut, NetworkError):
            category = "network_timeout"
        except Exception as exc:
            category = "other"
            logger.warning("broadcast %s: first upload to %s failed: %r", job_id, uid, exc)
        if category:
            failed += 1
            update["$set"] = {"media_file_id": file_ref, "sent": sent, "failed": failed}
            update["$inc"] = {f"error_counts.{category}": 1}
            if category in ("blocked_bot", "invalid_chat"):
                await _mark_blocked(uid, category)
        await db.bot_broadcasts.update_one({"_id": oid}, update)

    sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)

    async def worker(uid: int) -> tuple[int, bool, str]:
        async with sem:
            ok, category = await _send_to_user(_app.bot, uid, text, kind, file_ref)
            if not ok:
                logger.warning("broadcast %s: send to %s failed: %s", job_id, uid, category)
            return uid, ok, category

    for i in range(0, len(pending), BROADCAST_BATCH_SIZE):
        batch = pending[i : i + BROADCAST_BATCH_SIZE]
        results = await asyncio.gather(*(worker(uid) for uid in batch))
        sent += sum(1 for _, ok, _ in results if ok)
        failed += sum(1 for _, ok, _ in results if not ok)
        batch_errors = Counter(category for _, ok, category in results if not ok)
        update = {
            "$set": {"sent": sent, "failed": failed, "updated_at": datetime.now(timezone.utc).isoformat()},
            "$push": {"processed_user_ids": {"$each": [uid for uid, _, _ in results]}},
        }
        if batch_errors:
            update["$inc"] = {f"error_counts.{cat}": n for cat, n in batch_errors.items()}
        await db.bot_broadcasts.update_one({"_id": oid}, update)

        for uid, ok, category in results:
            if not ok and category in ("blocked_bot", "invalid_chat"):
                await _mark_blocked(uid, category)

    await db.bot_broadcasts.update_one(
        {"_id": oid},
        {"$set": {"status": "done", "completed_at": datetime.now(timezone.utc).isoformat()}},
    )
    logger.info("broadcast %s finished -> sent=%s failed=%s total=%s", job_id, sent, failed, len(all_user_ids))


async def resume_pending_broadcasts() -> None:
    """Called on startup: re-launch any broadcast job that was still 'running'
    when the process last stopped, so a redeploy/restart never silently drops
    a large in-progress send."""
    async for job in db.bot_broadcasts.find({"status": "running"}, {"_id": 1}):
        asyncio.create_task(run_broadcast_job(str(job["_id"])))


def main() -> None:
    """Standalone LOCAL DEV convenience only: run via long-polling directly.
    The deployed app (server.py) always uses the webhook flow above instead."""
    _build_application().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
