from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import asyncio
import bcrypt
import jwt
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from bot_content import DEFAULT_EN, DEFAULT_TA

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BOT_LINK = os.environ["TELEGRAM_BOT_LINK"]
BUTTON_TEXT = os.environ["OFFICIAL_BUTTON_TEXT"]
JWT_ALG = "HS256"
TOKEN_TTL_HOURS = 8
MAX_ATTEMPTS = 5
GLOBAL_MAX_ATTEMPTS = 20
LOCKOUT_MINUTES = 15
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
CLICK_ID_KEYS = ("click_id", "clickid", "cid")
bearer = HTTPBearer(auto_error=False)


# ── settings store ──────────────────────────────────────────────────────────
def build_url(username: str) -> str:
    return f"https://t.me/{username}?text=hi"


def clean_username(raw: str) -> str:
    """Accepts '@name', 'name' or a pasted t.me link and returns the bare username."""
    value = (raw or "").strip()
    value = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", value, flags=re.I)
    value = value.split("?")[0].split("/")[0].lstrip("@").strip()
    if not USERNAME_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Telegram username must be 5-32 characters: letters, numbers or underscore.",
        )
    return value


async def ensure_settings() -> dict:
    doc = await db.bot_settings.find_one({"_id": "config"})
    if doc:
        return doc
    username = clean_username(os.environ["OFFICIAL_TELEGRAM_USERNAME"])
    doc = {
        "_id": "config",
        "official_username": username,
        "official_url": os.environ["OFFICIAL_TELEGRAM_URL"],
        "welcome_ta": DEFAULT_TA,
        "welcome_en": DEFAULT_EN,
        "pin_hash": bcrypt.hashpw(os.environ["ADMIN_PIN"].encode(), bcrypt.gensalt()).decode(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.bot_settings.update_one({"_id": "config"}, {"$setOnInsert": doc}, upsert=True)
    return await db.bot_settings.find_one({"_id": "config"})


# ── admin auth ──────────────────────────────────────────────────────────────
def create_admin_token() -> str:
    payload = {
        "sub": "admin",
        "type": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALG)


async def require_admin(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> bool:
    if creds is None:
        raise HTTPException(status_code=401, detail="Panel is locked. Enter your PIN to edit.")
    try:
        payload = jwt.decode(creds.credentials, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Enter your PIN again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session. Enter your PIN again.")
    if payload.get("type") != "admin":
        raise HTTPException(status_code=401, detail="Invalid session token.")
    return True


def client_ip(request: Request) -> str:
    """Behind the k8s ingress request.client.host is a rotating proxy pod IP,
    so trust the left-most X-Forwarded-For entry instead."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"


async def _locked_for(key: str, limit: int) -> int:
    """Remaining lockout minutes for a key, or 0 if not locked."""
    doc = await db.admin_attempts.find_one({"_id": key})
    if not doc or doc.get("count", 0) < limit:
        return 0
    unlock_at = datetime.fromisoformat(doc["last_at"]) + timedelta(minutes=LOCKOUT_MINUTES)
    now = datetime.now(timezone.utc)
    if now >= unlock_at:
        await db.admin_attempts.delete_one({"_id": key})
        return 0
    return int((unlock_at - now).total_seconds() // 60) + 1


async def check_lockout(ip: str) -> None:
    for key, limit in ((ip, MAX_ATTEMPTS), ("__global__", GLOBAL_MAX_ATTEMPTS)):
        wait = await _locked_for(key, limit)
        if wait:
            raise HTTPException(
                status_code=429, detail=f"Too many attempts. Try again in {wait} min."
            )


async def record_failure(ip: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for key in (ip, "__global__"):
        await db.admin_attempts.update_one(
            {"_id": key}, {"$inc": {"count": 1}, "$set": {"last_at": now}}, upsert=True
        )


async def clear_failures(ip: str) -> None:
    await db.admin_attempts.delete_many({"_id": {"$in": [ip, "__global__"]}})


class UnlockRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=64)


class SettingsUpdate(BaseModel):
    official_username: str | None = Field(default=None, max_length=120)
    welcome_ta: str | None = Field(default=None, max_length=900)
    welcome_en: str | None = Field(default=None, max_length=900)


class PinUpdate(BaseModel):
    new_pin: str = Field(min_length=4, max_length=64)


@api_router.post("/admin/unlock")
async def unlock(body: UnlockRequest, request: Request):
    ip = client_ip(request)
    await check_lockout(ip)
    settings = await ensure_settings()
    if not bcrypt.checkpw(body.pin.encode(), settings["pin_hash"].encode()):
        await record_failure(ip)
        raise HTTPException(status_code=401, detail="Wrong PIN.")
    await clear_failures(ip)
    return {"token": create_admin_token(), "expires_in_hours": TOKEN_TTL_HOURS}


@api_router.post("/admin/pin")
async def change_pin(body: PinUpdate, _: bool = Depends(require_admin)):
    if not body.new_pin.strip():
        raise HTTPException(status_code=400, detail="PIN cannot be empty.")
    await ensure_settings()
    await db.bot_settings.update_one(
        {"_id": "config"},
        {"$set": {"pin_hash": bcrypt.hashpw(body.new_pin.encode(), bcrypt.gensalt()).decode()}},
    )
    return {"updated": True}


MAX_BROADCAST_FILE_BYTES = 50 * 1024 * 1024  # Telegram's own hard limit for bot-sent files


@api_router.post("/admin/broadcast")
async def broadcast(
    message: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    _: bool = Depends(require_admin),
):
    text = message.strip()
    if not text and not file:
        raise HTTPException(status_code=400, detail="Write a message or attach a file.")

    import bot as bot_module

    if not bot_module.is_ready():
        raise HTTPException(
            status_code=503,
            detail="The bot isn't running in this environment, so it can't send messages here.",
        )

    media_kind = media_path = media_filename = None
    if file:
        content = await file.read()
        if len(content) > MAX_BROADCAST_FILE_BYTES:
            raise HTTPException(status_code=400, detail="File too large (max 50MB — Telegram's own limit for bots).")
        content_type = file.content_type or "application/octet-stream"
        media_kind = (
            "photo" if content_type.startswith("image/") else "video" if content_type.startswith("video/") else "document"
        )
        media_filename = file.filename or "file"
        media_path = f"trafficstars-bot/broadcast_uploads/{secrets.token_hex(8)}_{media_filename}"
        await bot_module.put_broadcast_file(media_path, content, content_type)

    total = await db.bot_users.count_documents({"blocked": {"$ne": True}})
    job = {
        "text": text,
        "media_kind": media_kind,
        "media_storage_path": media_path,
        "media_filename": media_filename,
        "media_file_id": None,
        "total": total,
        "sent": 0,
        "failed": 0,
        "error_counts": {},
        "processed_user_ids": [],
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.bot_broadcasts.insert_one(job)
    job_id = str(result.inserted_id)

    # 100k+ recipients can take a while even with concurrent sending -- this
    # runs as an independent task (not tied to this request), and resumes
    # automatically on its own if the backend restarts mid-send (see
    # resume_pending_broadcasts(), called on startup).
    asyncio.create_task(bot_module.run_broadcast_job(job_id))
    return {"queued": True, "job_id": job_id, "total": total}


@api_router.get("/admin/broadcast/latest")
async def broadcast_latest(_: bool = Depends(require_admin)):
    job = await db.bot_broadcasts.find_one({}, sort=[("created_at", -1)])
    if not job:
        return None
    return {
        "status": job["status"],
        "sent": job["sent"],
        "failed": job["failed"],
        "total": job["total"],
        "error_counts": job.get("error_counts", {}),
        "created_at": job["created_at"],
        "completed_at": job.get("completed_at"),
    }


@api_router.get("/admin/broadcast/history")
async def broadcast_history(_: bool = Depends(require_admin)):
    jobs = await db.bot_broadcasts.find({}).sort("created_at", -1).limit(20).to_list(20)
    return [
        {
            "id": str(job["_id"]),
            "text_preview": (job.get("text") or "")[:80],
            "media_filename": job.get("media_filename"),
            "status": job.get("status", "unknown"),
            "sent": job.get("sent", 0),
            "failed": job.get("failed", 0),
            "total": job.get("total", 0),
            "error_counts": job.get("error_counts", {}),
            "created_at": job.get("created_at"),
            "completed_at": job.get("completed_at"),
        }
        for job in jobs
    ]


@api_router.post("/admin/telegram/activate")
async def activate_telegram(request: Request, _: bool = Depends(require_admin)):
    """Make THIS environment (wherever the admin is currently viewing the
    dashboard from) the one live receiver of Telegram updates. Derives the
    public URL from the actual incoming request, so it's always correct
    regardless of any .env sync between preview and production."""
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        raise HTTPException(status_code=400, detail="Could not determine this environment's public URL.")
    base_url = f"{proto}://{host}"

    import bot as bot_module

    if not bot_module.is_ready():
        raise HTTPException(
            status_code=503,
            detail="Bot isn't initialized in this environment — TELEGRAM_BOT_TOKEN is likely missing or invalid here. Check the token in this environment's .env and restart.",
        )
    result = await bot_module.activate_webhook(base_url)
    logger.info("telegram webhook activated -> %s", result.get("webhook_url"))
    return result


@api_router.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != os.environ["TELEGRAM_WEBHOOK_SECRET"]:
        raise HTTPException(status_code=404)
    if request.headers.get("x-telegram-bot-api-secret-token") != os.environ["TELEGRAM_WEBHOOK_SECRET"]:
        raise HTTPException(status_code=404)
    payload = await request.json()

    import bot as bot_module

    await bot_module.process_webhook_update(payload)
    return {"ok": True}


# ── public settings ─────────────────────────────────────────────────────────
def public_settings(doc: dict) -> dict:
    return {
        "official_username": doc["official_username"],
        "official_url": doc["official_url"],
        "welcome_ta": doc["welcome_ta"],
        "welcome_en": doc["welcome_en"],
        "button_text": BUTTON_TEXT,
        "updated_at": doc.get("updated_at"),
        "has_welcome_image": bool(doc.get("welcome_image_path")),
    }


@api_router.get("/settings")
async def read_settings():
    return public_settings(await ensure_settings())


@api_router.put("/settings")
async def write_settings(body: SettingsUpdate, _: bool = Depends(require_admin)):
    """Partial update — only the fields actually sent are validated/changed.
    Editing the welcome text must never touch/re-validate the account
    username, and vice versa (that cross-contamination was the reported bug:
    each save used to require + re-validate ALL fields together)."""
    current = await ensure_settings()
    updates = {}
    if body.official_username is not None:
        username = clean_username(body.official_username)
        updates["official_username"] = username
        updates["official_url"] = build_url(username)
    if body.welcome_ta is not None or body.welcome_en is not None:
        ta = (body.welcome_ta if body.welcome_ta is not None else current["welcome_ta"]).strip()
        en = (body.welcome_en if body.welcome_en is not None else current["welcome_en"]).strip()
        if not ta or not en:
            raise HTTPException(status_code=400, detail="Both the Tamil and English blocks are required.")
        updates["welcome_ta"] = ta
        updates["welcome_en"] = en
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.bot_settings.update_one({"_id": "config"}, {"$set": updates})
    logger.info("settings updated -> %s", list(updates.keys()))
    return public_settings(await db.bot_settings.find_one({"_id": "config"}))


MAX_WELCOME_IMAGE_BYTES = 10 * 1024 * 1024  # generous cap for a single ad creative image


@api_router.post("/admin/welcome-image")
async def upload_welcome_image(file: UploadFile = File(...), _: bool = Depends(require_admin)):
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")
    data = await file.read()
    if len(data) > MAX_WELCOME_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 10MB).")

    import bot as bot_module

    path = f"trafficstars-bot/welcome_image/{secrets.token_hex(8)}_{file.filename or 'image'}"
    await bot_module.put_broadcast_file(path, data, content_type)
    await ensure_settings()
    await db.bot_settings.update_one(
        {"_id": "config"},
        {
            "$set": {
                "welcome_image_path": path,
                "welcome_image_content_type": content_type,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    logger.info("welcome image updated -> %s", path)
    return public_settings(await db.bot_settings.find_one({"_id": "config"}))


@api_router.delete("/admin/welcome-image")
async def remove_welcome_image(_: bool = Depends(require_admin)):
    await ensure_settings()
    await db.bot_settings.update_one(
        {"_id": "config"},
        {"$unset": {"welcome_image_path": "", "welcome_image_content_type": ""}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return public_settings(await db.bot_settings.find_one({"_id": "config"}))


@api_router.get("/welcome-image")
async def get_welcome_image():
    doc = await ensure_settings()
    path = doc.get("welcome_image_path")
    if not path:
        raise HTTPException(status_code=404, detail="No welcome image set.")

    import bot as bot_module

    try:
        data = await bot_module.get_broadcast_file(path)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load the welcome image right now.")
    return Response(content=data, media_type=doc.get("welcome_image_content_type") or "image/jpeg")


# ── ad bridge ───────────────────────────────────────────────────────────────
@api_router.get("/r")
async def redirect_to_bot(request: Request):
    """Ad landing bridge: stores the (long) TrafficStars click id and hands Telegram
    a short token, because /start payloads are capped at 64 chars."""
    params = dict(request.query_params)
    click_id = next((params[k] for k in CLICK_ID_KEYS if params.get(k)), None)
    if not click_id:
        return RedirectResponse(BOT_LINK, status_code=302)

    token = secrets.token_urlsafe(9)
    await db.bot_clicks.insert_one(
        {
            "token": token,
            "click_id": click_id,
            "params": {k: v for k, v in params.items() if k not in CLICK_ID_KEYS},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used_at": None,
        }
    )
    logger.info("bridge click_id len=%s -> token %s", len(click_id), token)
    return RedirectResponse(f"{BOT_LINK}?start={token}", status_code=302)


# ── telemetry ───────────────────────────────────────────────────────────────
@api_router.get("/")
async def root():
    return {"message": "Telegram redirect bot API"}


@api_router.get("/bot/status")
async def bot_status(request: Request):
    meta = await db.bot_meta.find_one({"_id": "runtime"}, {"_id": 0}) or {}
    settings = await ensure_settings()
    reset = settings.get("stats_reset") or {}
    conv_reset = reset.get("conversions")
    users_reset = reset.get("unique_users")
    daily_reset = reset.get("daily")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_start = f"{today}T00:00:00"
    # Lexicographic max works directly on ISO8601 strings — this also means a
    # conversions reset (or a same-day daily reset) correctly raises today's
    # own lower bound too, without any extra date-comparison logic.
    daily_lower = max(filter(None, [today_start, daily_reset, conv_reset]))

    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    own_base = f"{proto}://{host}"
    import bot as bot_module

    try:
        wh = await bot_module.webhook_info()
        webhook_url = wh["webhook_url"]
    except RuntimeError:
        webhook_url = ""
    is_live_here = bool(webhook_url) and webhook_url.startswith(own_base)

    start_filter = {"event": "start"}
    if conv_reset:
        start_filter["created_at"] = {"$gt": conv_reset}
    postback_filter = {"created_at": {"$gt": conv_reset}} if conv_reset else {}
    users_filter = {"first_seen_at": {"$gt": users_reset}} if users_reset else {}

    return {
        "online": bool(meta) and is_live_here,
        "is_live_here": is_live_here,
        "webhook_active": bool(webhook_url),
        "bot_username": meta.get("bot_username"),
        "official_url": settings["official_url"],
        "official_username": f"@{settings['official_username']}",
        "total_starts": await db.bot_events.count_documents(start_filter),
        "unique_users": await db.bot_users.count_documents(users_filter),
        "blocked_users": await db.bot_users.count_documents({**users_filter, "blocked": True}),
        "starts_today": await db.bot_events.count_documents(
            {"event": "start", "created_at": {"$gte": daily_lower}}
        ),
        "postback_enabled": bool(os.environ.get("TRAFFICSTARS_POSTBACK_URL", "").strip()),
        "postbacks_sent": await db.bot_postbacks.count_documents({**postback_filter, "ok": True}),
        "postbacks_failed": await db.bot_postbacks.count_documents({**postback_filter, "ok": False}),
        "starts_attributed": await db.bot_events.count_documents(
            {**start_filter, "source": {"$nin": [None, ""]}}
        ),
        "starts_unattributed": await db.bot_events.count_documents(
            {**start_filter, "source": {"$in": [None, ""]}}
        ),
        "last_broadcast": meta.get("last_broadcast"),
        "stats_reset": {"conversions": conv_reset, "unique_users": users_reset, "daily": daily_reset},
    }


VALID_RESET_METRICS = {"conversions", "unique_users", "daily"}


class StatsResetRequest(BaseModel):
    metric: str


@api_router.post("/admin/stats/reset")
async def reset_stats(body: StatsResetRequest, request: Request, _: bool = Depends(require_admin)):
    """Soft reset — records a cutoff timestamp per metric instead of deleting
    any bot_events/bot_users/bot_postbacks history, so postback dedup, click
    attribution and audit history all stay intact; only the displayed counts
    start counting from now."""
    if body.metric not in VALID_RESET_METRICS:
        raise HTTPException(status_code=400, detail="Unknown metric.")
    await ensure_settings()
    now = datetime.now(timezone.utc).isoformat()
    await db.bot_settings.update_one({"_id": "config"}, {"$set": {f"stats_reset.{body.metric}": now}})
    logger.info("stats reset -> %s", body.metric)
    return await bot_status(request)


@api_router.get("/bot/clicks")
async def bot_clicks(request: Request):
    """Diagnostics: recent /start payloads + their postback outcome."""
    events = (
        await db.bot_events.find({"event": "start"}, {"_id": 0}).sort("created_at", -1).to_list(20)
    )
    out = []
    for e in events:
        src = e.get("source")
        pb = await db.bot_postbacks.find_one({"click_id": src}, {"_id": 0}) if src else None
        out.append(
            {
                "created_at": e.get("created_at"),
                "user_id": e.get("user_id"),
                "click_id": src,
                "click_id_length": len(src) if src else 0,
                "postback_status": pb.get("status_code") if pb else None,
                "postback_response": pb.get("response") or pb.get("error") if pb else None,
                "postback_ok": pb.get("ok") if pb else None,
            }
        )
    return {
        "ad_url": f"{str(request.base_url).rstrip('/')}/api/r?click_id={{click_id}}",
        "direct_bot_link": BOT_LINK,
        "recent": out,
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await ensure_settings()
    await db.admin_attempts.create_index("last_at")
    logger.info("settings ready")
    # Initialize the bot client (no polling, no webhook receiver started yet) —
    # safe to do in every environment at once. An admin activates the webhook
    # for whichever environment should be the live one via
    # POST /api/admin/telegram/activate.
    import bot as bot_module

    try:
        await bot_module.init_bot()
        # Resume any broadcast that was still mid-send when this process last
        # stopped (redeploy, restart, crash) -- so a large 100k-recipient send
        # never silently loses its remaining recipients.
        await bot_module.resume_pending_broadcasts()
    except Exception:
        # A bad/revoked Telegram token or a transient Telegram API outage
        # must not take down the whole backend (bridge, admin panel,
        # settings API don't depend on the bot being initialized) — this
        # previously crashed the entire deployment's health check.
        logger.exception("bot init failed — bridge/admin/settings endpoints stay up, bot features are offline")


@app.on_event("shutdown")
async def shutdown_db_client():
    import bot as bot_module

    await bot_module.shutdown_bot()
    client.close()
