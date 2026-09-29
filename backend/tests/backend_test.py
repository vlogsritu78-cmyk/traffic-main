"""Backend and Bot verification tests for the Tamil redirect bot.

CRITICAL: Uses ONLY read-only Bot API methods (getMe, getMyCommands,
getMyDescription, getMyShortDescription, getWebhookInfo) plus a controlled
sendMessage to chat_id=1 to validate HTML parsing. Does NOT call
getUpdates/setWebhook (would break long-polling). Does NOT DM the owner.
"""

import os
import re
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

# --- config -----------------------------------------------------------------
frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")

BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or frontend_env.get("REACT_APP_BACKEND_URL")
).rstrip("/")

TOKEN = backend_env["TELEGRAM_BOT_TOKEN"]
OFFICIAL_URL = backend_env["OFFICIAL_TELEGRAM_URL"]
OFFICIAL_USERNAME = backend_env["OFFICIAL_TELEGRAM_USERNAME"]
BUTTON_TEXT = backend_env["OFFICIAL_BUTTON_TEXT"]
MONGO_URL = backend_env["MONGO_URL"]
DB_NAME = backend_env["DB_NAME"]

TG = f"https://api.telegram.org/bot{TOKEN}"

sys.path.insert(0, "/app/backend")


# --- Supervisor / process health -------------------------------------------
class TestBotProcess:
    def test_supervisor_running(self):
        import subprocess
        out = subprocess.check_output(["sudo", "supervisorctl", "status", "telegram_bot"]).decode()
        assert "RUNNING" in out, out

    def test_log_shows_application_started_no_tracebacks(self):
        log = Path("/var/log/supervisor/telegram_bot.err.log").read_text(errors="ignore")
        assert "Application started" in log
        assert "Traceback" not in log, "Traceback found in bot err log"

    def test_no_webhook_configured(self):
        r = requests.get(f"{TG}/getWebhookInfo", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["result"].get("url", "") == "", f"Webhook set: {data['result']}"


# --- Read-only Bot API checks ----------------------------------------------
class TestBotApi:
    def test_get_me_username(self):
        r = requests.get(f"{TG}/getMe", timeout=15)
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["username"] == "tamil_best_service_bot", res

    def test_no_commands_registered(self):
        r = requests.get(f"{TG}/getMyCommands", timeout=15).json()
        assert r["ok"] is True
        assert r["result"] == [], f"Expected no commands, got {r['result']}"

    def test_description_set(self):
        r = requests.get(f"{TG}/getMyDescription", timeout=15).json()
        assert r["ok"] is True
        desc = r["result"]["description"]
        assert desc.strip(), "empty description"
        assert "வண" in desc or "Welcome" in desc

    def test_short_description_set(self):
        r = requests.get(f"{TG}/getMyShortDescription", timeout=15).json()
        assert r["ok"] is True
        sd = r["result"]["short_description"]
        assert sd.strip(), "empty short description"

    def test_welcome_html_parses(self):
        from bot_content import welcome_text
        text = welcome_text(OFFICIAL_USERNAME)
        # sanity: tamil + english present
        assert re.search(r"[\u0B80-\u0BFF]", text), "no Tamil chars"
        assert "Welcome" in text
        # try sending to nonexistent chat_id=1 to force parse validation
        r = requests.post(
            f"{TG}/sendMessage",
            json={"chat_id": 1, "text": text, "parse_mode": "HTML"},
            timeout=15,
        ).json()
        assert r["ok"] is False
        desc = r.get("description", "").lower()
        assert "can't parse" not in desc and "can not parse" not in desc, (
            f"HTML parse error: {desc}"
        )
        assert "chat not found" in desc, f"Unexpected error: {desc}"


# --- Handler / keyboard shape ----------------------------------------------
class TestBotModule:
    def test_keyboard_built_in_reply_matches_live_settings(self):
        """Iteration 5: keyboard is now built inside reply() from live settings.
        Verify by mocking update.effective_message.reply_text and inspecting kwargs."""
        import asyncio, bot as botmod
        captured = {}

        class FakeMessage:
            async def reply_text(self, text, **kw):
                captured["text"] = text
                captured["kw"] = kw

        class FakeUpdate:
            effective_message = FakeMessage()

        asyncio.new_event_loop().run_until_complete(botmod.reply(FakeUpdate()))
        markup = captured["kw"]["reply_markup"]
        rows = markup.inline_keyboard
        assert len(rows) == 1 and len(rows[0]) == 1
        btn = rows[0][0]
        assert btn.text == BUTTON_TEXT
        # url must equal live official_url from settings doc, not env
        import pymongo
        c = pymongo.MongoClient(MONGO_URL)
        try:
            s = c[DB_NAME].bot_settings.find_one({"_id": "config"})
        finally:
            c.close()
        assert btn.url == s["official_url"]

    def test_handlers_registration_order(self):
        """CommandHandler('start') must be registered BEFORE MessageHandler(filters.ALL)."""
        src = Path("/app/backend/bot.py").read_text()
        i_cmd = src.find('CommandHandler("start"')
        i_msg = src.find("MessageHandler(filters.ALL")
        assert i_cmd != -1 and i_msg != -1
        assert i_cmd < i_msg, "start handler must come before catch-all"


# --- MongoDB seed + API status ---------------------------------------------
@pytest.fixture(scope="class")
def mongo_db():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


class TestApiStatus:
    def test_bot_meta_runtime_doc_exists(self, mongo_db):
        doc = mongo_db.bot_meta.find_one({"_id": "runtime"})
        assert doc is not None, "bot_meta runtime doc missing (post_init did not run?)"
        assert doc.get("bot_username") == "tamil_best_service_bot"

    def test_status_endpoint(self, mongo_db):
        r = requests.get(f"{BASE_URL}/api/bot/status", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["online"] is True
        assert data["bot_username"] == "tamil_best_service_bot"
        assert data["official_url"] == OFFICIAL_URL
        assert data["official_username"] == OFFICIAL_USERNAME
        # counts consistent with Mongo
        total_starts_db = mongo_db.bot_events.count_documents({"event": "start"})
        unique_users_db = mongo_db.bot_users.count_documents({})
        assert data["total_starts"] == total_starts_db
        assert data["unique_users"] == unique_users_db
        assert isinstance(data["starts_today"], int)
        assert data["starts_today"] >= 0

    def test_track_and_reply_flow_via_direct_call(self, mongo_db):
        """Simulate on_start by directly calling track() with a fake Update.
        Verifies a 'start' event lands in bot_events and user upserts to bot_users.
        """
        import asyncio
        import bot as botmod

        class FakeUser:
            id = 999000111
            username = "TEST_qa_user"
            first_name = "TEST"
            language_code = "en"

        class FakeUpdate:
            effective_user = FakeUser()

        # cleanup any prior residue
        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})

        try:
            asyncio.get_event_loop().run_until_complete(
                botmod.track(FakeUpdate(), "start", "trafficstars")
            )
        except RuntimeError:
            asyncio.new_event_loop().run_until_complete(
                botmod.track(FakeUpdate(), "start", "trafficstars")
            )

        ev = mongo_db.bot_events.find_one({"user_id": FakeUser.id, "event": "start"})
        assert ev is not None and ev["source"] == "trafficstars"
        u = mongo_db.bot_users.find_one({"user_id": FakeUser.id})
        assert u is not None and u["username"] == "TEST_qa_user"

        # cleanup
        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})


# --- TrafficStars postback -------------------------------------------------
import asyncio
import importlib


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class TestPostback:
    def test_bot_starts_with_postback_enabled_log(self):
        log = Path("/var/log/supervisor/telegram_bot.err.log").read_text(errors="ignore")
        # last occurrence must show ENABLED
        assert "trafficstars postback: ENABLED" in log
        assert "trafficstars postback: disabled" not in log.split("trafficstars postback: ENABLED")[-1]

    def test_unique_index_on_click_id(self, mongo_db):
        idx = mongo_db.bot_postbacks.index_information()
        found = any(
            spec.get("unique") and spec.get("key") == [("click_id", 1)]
            for spec in idx.values()
        )
        assert found, f"Missing unique index on bot_postbacks.click_id — got {idx}"

    def test_postback_url_env_and_module(self):
        import bot as botmod
        importlib.reload(botmod)
        assert botmod.POSTBACK_URL.startswith("https://tsyndicate.com/api/v1/cpa/action")
        assert "{click_id}" in botmod.POSTBACK_URL or "{clickid}" in botmod.POSTBACK_URL
        assert "key=" in botmod.POSTBACK_URL and "goalid=0" in botmod.POSTBACK_URL

    def test_placeholder_substitution_and_live_call_persists(self, mongo_db):
        """Fire against real tsyndicate endpoint with fake click id.
        Expect HTTP call to succeed with 400 'clickid is short' or similar — proves reachability
        and substitution. Doc must persist with status_code/response/ok fields."""
        import bot as botmod
        importlib.reload(botmod)
        cid = "TEST_qa_short_9x"
        mongo_db.bot_postbacks.delete_many({"click_id": cid})
        _run(botmod.fire_postback(cid, 999000222))
        doc = mongo_db.bot_postbacks.find_one({"click_id": cid})
        assert doc is not None
        # URL was substituted (no placeholders remaining) and key/goalid intact
        assert "{clickid}" not in doc["url"] and "{click_id}" not in doc["url"]
        assert cid in doc["url"]
        assert "key=7n5d2Rz1ikahFrTCVzoOAyVOu7JPVaE3Zfkc" in doc["url"]
        assert "goalid=0" in doc["url"]
        # result fields present
        assert "status_code" in doc or "error" in doc
        assert "ok" in doc
        # cleanup
        mongo_db.bot_postbacks.delete_many({"click_id": cid})

    def test_dedupe_only_one_postback(self, mongo_db):
        import bot as botmod
        importlib.reload(botmod)
        cid = "TEST_qa_dedupe_777"
        mongo_db.bot_postbacks.delete_many({"click_id": cid})
        # Patch httpx to count outbound calls
        import httpx
        calls = {"n": 0}
        real_client = httpx.AsyncClient

        class CountingClient(real_client):
            async def get(self, url, *a, **kw):
                calls["n"] += 1
                return await super().get(url, *a, **kw)

        httpx.AsyncClient = CountingClient
        try:
            _run(botmod.fire_postback(cid, 999000333))
            _run(botmod.fire_postback(cid, 999000333))
        finally:
            httpx.AsyncClient = real_client
        docs = list(mongo_db.bot_postbacks.find({"click_id": cid}))
        assert len(docs) == 1, f"expected 1 postback doc, got {len(docs)}"
        assert calls["n"] == 1, f"expected exactly 1 outbound HTTP call, got {calls['n']}"
        mongo_db.bot_postbacks.delete_many({"click_id": cid})

    def test_network_failure_does_not_raise_and_records_ok_false(self, mongo_db):
        import bot as botmod
        importlib.reload(botmod)
        # Point at unreachable host by monkeypatching module-level POSTBACK_URL
        original = botmod.POSTBACK_URL
        botmod.POSTBACK_URL = "http://127.0.0.1:1/api?clickid={click_id}"
        cid = "TEST_qa_unreach_555"
        mongo_db.bot_postbacks.delete_many({"click_id": cid})
        try:
            _run(botmod.fire_postback(cid, 999000444))  # must not raise
        finally:
            botmod.POSTBACK_URL = original
        doc = mongo_db.bot_postbacks.find_one({"click_id": cid})
        assert doc is not None
        assert doc.get("ok") is False
        assert "error" in doc
        mongo_db.bot_postbacks.delete_many({"click_id": cid})

    def test_postback_disabled_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("TRAFFICSTARS_POSTBACK_URL", "")
        import bot as botmod
        importlib.reload(botmod)
        assert botmod.POSTBACK_URL == ""
        # reload back to normal for downstream tests
        monkeypatch.undo()
        importlib.reload(botmod)
        assert botmod.POSTBACK_URL != ""

    def test_on_start_without_payload_does_not_create_postback(self, mongo_db):
        """Empty context.args → track event but no bot_postbacks doc."""
        import bot as botmod
        importlib.reload(botmod)

        class FakeUser:
            id = 999000555
            username = "TEST_no_payload"
            first_name = "TEST"
            language_code = "en"

        class FakeMessage:
            async def reply_text(self, *a, **kw):
                return None

        class FakeUpdate:
            effective_user = FakeUser()
            effective_message = FakeMessage()

        class FakeContext:
            args = []

        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})
        pb_before = mongo_db.bot_postbacks.count_documents({"user_id": FakeUser.id})

        _run(botmod.on_start(FakeUpdate(), FakeContext()))

        ev = mongo_db.bot_events.find_one({"user_id": FakeUser.id, "event": "start"})
        assert ev is not None and ev.get("source") in (None, "")
        pb_after = mongo_db.bot_postbacks.count_documents({"user_id": FakeUser.id})
        assert pb_after == pb_before, "No postback should be created for empty payload"

        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})


class TestStatusAndClicksApi:
    def test_status_has_new_postback_fields(self, mongo_db):
        r = requests.get(f"{BASE_URL}/api/bot/status", timeout=15)
        assert r.status_code == 200
        data = r.json()
        for k in ("postback_enabled", "postbacks_sent", "postbacks_failed",
                  "starts_attributed", "starts_unattributed", "total_starts"):
            assert k in data, f"missing field {k}"
        assert data["postback_enabled"] is True
        assert data["starts_attributed"] + data["starts_unattributed"] == data["total_starts"]

    def test_clicks_endpoint(self, mongo_db):
        r = requests.get(f"{BASE_URL}/api/bot/clicks", timeout=15)
        assert r.status_code == 200
        data = r.json()
        # Iteration 3: ad_url is now the bridge URL, not the direct t.me deep link
        assert data["ad_url"].endswith("/api/r?click_id={click_id}"), data["ad_url"]
        assert data["ad_url"].startswith("http")
        assert data.get("direct_bot_link") == "https://t.me/tamil_best_service_bot"
        assert isinstance(data["recent"], list)
        assert len(data["recent"]) <= 20
        for e in data["recent"]:
            for k in ("created_at", "user_id", "click_id", "click_id_length",
                      "postback_status", "postback_response", "postback_ok"):
                assert k in e, f"missing key {k} in recent entry"
            if e["click_id"] is None:
                assert e["click_id_length"] == 0
            else:
                assert e["click_id_length"] == len(e["click_id"])



# --- Iteration 3: /api/r bridge + resolve_click_id -------------------------
import string

BRIDGE_TOKEN_CHARS = set(string.ascii_letters + string.digits + "-_")


def _long_click_id(n=140):
    # base64-url-ish, well over Telegram's 64-char /start payload cap
    import secrets as _s
    return _s.token_urlsafe(n)[:n]


class TestBridgeRedirect:
    def _get(self, path):
        return requests.get(f"{BASE_URL}{path}", allow_redirects=False, timeout=15)

    def test_happy_path_long_click_id_redirects_to_short_token(self, mongo_db):
        cid = _long_click_id(140)
        assert len(cid) >= 127
        r = self._get(f"/api/r?click_id={cid}")
        assert r.status_code == 302, r.text
        loc = r.headers.get("Location") or r.headers.get("location")
        assert loc and loc.startswith("https://t.me/tamil_best_service_bot?start="), loc
        token = loc.rsplit("?start=", 1)[1]
        assert 1 <= len(token) <= 64
        assert set(token).issubset(BRIDGE_TOKEN_CHARS), f"token has bad chars: {token!r}"
        doc = mongo_db.bot_clicks.find_one({"token": token})
        assert doc is not None, "bridge did not persist mapping"
        assert doc["click_id"] == cid
        assert len(doc["click_id"]) == len(cid)  # no truncation
        assert doc.get("used_at") is None
        assert "created_at" in doc
        mongo_db.bot_clicks.delete_one({"token": token})

    def test_alternate_param_names(self, mongo_db):
        for key in ("click_id", "clickid", "cid"):
            cid = f"TEST_alt_{key}_" + _long_click_id(120)
            r = self._get(f"/api/r?{key}={cid}")
            assert r.status_code == 302
            loc = r.headers["location"]
            assert "?start=" in loc, f"no start payload for {key}"
            token = loc.rsplit("?start=", 1)[1]
            doc = mongo_db.bot_clicks.find_one({"token": token})
            assert doc is not None, f"no mapping stored for {key}"
            assert doc["click_id"] == cid
            mongo_db.bot_clicks.delete_one({"token": token})

    def test_extra_params_go_into_params_subdoc(self, mongo_db):
        cid = "TEST_bridge_extraparams_" + _long_click_id(100)
        r = self._get(f"/api/r?click_id={cid}&campaign=abc&spot=xyz")
        assert r.status_code == 302
        token = r.headers["location"].rsplit("?start=", 1)[1]
        doc = mongo_db.bot_clicks.find_one({"token": token})
        assert doc is not None
        assert doc["click_id"] == cid  # extras not merged into click_id
        params = doc.get("params") or {}
        assert params.get("campaign") == "abc"
        assert params.get("spot") == "xyz"
        assert "click_id" not in params and "clickid" not in params and "cid" not in params
        mongo_db.bot_clicks.delete_one({"token": token})

    def test_no_click_id_redirects_to_plain_bot_link_and_no_doc(self, mongo_db):
        before = mongo_db.bot_clicks.count_documents({})
        r = self._get("/api/r")
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc == "https://t.me/tamil_best_service_bot", loc
        assert "?start=" not in loc
        after = mongo_db.bot_clicks.count_documents({})
        assert after == before, "bot_clicks doc created for empty click_id"

    def test_unique_index_on_bot_clicks_token(self, mongo_db):
        idx = mongo_db.bot_clicks.index_information()
        found = any(
            spec.get("unique") and spec.get("key") == [("token", 1)]
            for spec in idx.values()
        )
        assert found, f"Missing unique index on bot_clicks.token — got {idx}"


class TestResolveClickId:
    def test_resolves_token_to_full_click_id_and_stamps_used_at(self, mongo_db):
        import bot as botmod
        importlib.reload(botmod)
        cid = "TEST_resolve_" + _long_click_id(120)
        # Create mapping via bridge
        r = requests.get(f"{BASE_URL}/api/r?click_id={cid}", allow_redirects=False, timeout=15)
        token = r.headers["location"].rsplit("?start=", 1)[1]
        resolved = _run(botmod.resolve_click_id(token, 999000901))
        assert resolved == cid  # exact string equality, full length
        assert len(resolved) == len(cid)
        doc = mongo_db.bot_clicks.find_one({"token": token})
        assert doc is not None
        assert doc.get("used_at") is not None
        assert doc.get("user_id") == 999000901
        mongo_db.bot_clicks.delete_one({"token": token})

    def test_unknown_payload_returns_payload_unchanged(self, mongo_db):
        import bot as botmod
        importlib.reload(botmod)
        raw = "raw_short_click_id_abc123"
        # Ensure no accidental doc exists
        mongo_db.bot_clicks.delete_many({"token": raw})
        resolved = _run(botmod.resolve_click_id(raw, 999000902))
        assert resolved == raw


class TestOnStartAttribution:
    def test_on_start_resolves_before_track_and_postback(self, mongo_db):
        """The value stored in bot_events.source AND passed to fire_postback must be the
        FULL resolved click_id, not the short token."""
        import bot as botmod
        importlib.reload(botmod)

        cid = "TEST_e2e_" + _long_click_id(130)
        r = requests.get(f"{BASE_URL}/api/r?click_id={cid}", allow_redirects=False, timeout=15)
        token = r.headers["location"].rsplit("?start=", 1)[1]

        class FakeUser:
            id = 999000903
            username = "TEST_e2e_user"
            first_name = "TEST"
            language_code = "en"

        class FakeMessage:
            async def reply_text(self, *a, **kw):
                return None

        class FakeUpdate:
            effective_user = FakeUser()
            effective_message = FakeMessage()

        class FakeContext:
            args = [token]

        # Capture fire_postback calls
        captured = {"args": None, "n": 0}

        async def fake_fire(click_id, user_id):
            captured["args"] = (click_id, user_id)
            captured["n"] += 1

        orig = botmod.fire_postback
        botmod.fire_postback = fake_fire

        # cleanup
        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})
        try:
            _run(botmod.on_start(FakeUpdate(), FakeContext()))
        finally:
            botmod.fire_postback = orig

        # bot_events.source == FULL click id
        ev = mongo_db.bot_events.find_one({"user_id": FakeUser.id, "event": "start"})
        assert ev is not None
        assert ev["source"] == cid, "bot_events.source should be resolved full click id"
        assert ev["source"] != token

        # fire_postback called with FULL click id
        assert captured["n"] == 1
        assert captured["args"][0] == cid
        assert captured["args"][0] != token

        # cleanup
        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_clicks.delete_one({"token": token})

    def test_dedupe_on_resolved_click_id(self, mongo_db):
        """Firing postback twice for the same resolved click id creates exactly 1 doc + 1 HTTP call."""
        import bot as botmod
        importlib.reload(botmod)
        cid = "TEST_dedupe_resolved_" + _long_click_id(60)
        mongo_db.bot_postbacks.delete_many({"click_id": cid})

        import httpx
        calls = {"n": 0}
        real_client = httpx.AsyncClient

        class CountingClient(real_client):
            async def get(self, url, *a, **kw):
                calls["n"] += 1
                return await super().get(url, *a, **kw)

        httpx.AsyncClient = CountingClient
        try:
            _run(botmod.fire_postback(cid, 999000904))
            _run(botmod.fire_postback(cid, 999000904))
        finally:
            httpx.AsyncClient = real_client
        docs = list(mongo_db.bot_postbacks.find({"click_id": cid}))
        assert len(docs) == 1
        assert calls["n"] == 1
        mongo_db.bot_postbacks.delete_many({"click_id": cid})


class TestClicksEndpointReflectsResolvedLength:
    def test_click_id_length_matches_resolved_full_id(self, mongo_db):
        """After on_start with a bridge token, /api/bot/clicks recent[] should reflect the
        FULL resolved click id length, not the 12-char token length."""
        import bot as botmod
        importlib.reload(botmod)

        cid = "TEST_len_" + _long_click_id(120)
        assert len(cid) > 64
        r = requests.get(f"{BASE_URL}/api/r?click_id={cid}", allow_redirects=False, timeout=15)
        token = r.headers["location"].rsplit("?start=", 1)[1]

        class FakeUser:
            id = 999000905
            username = "TEST_len_user"
            first_name = "TEST"
            language_code = "en"

        class FakeMessage:
            async def reply_text(self, *a, **kw):
                return None

        class FakeUpdate:
            effective_user = FakeUser()
            effective_message = FakeMessage()

        class FakeContext:
            args = [token]

        async def noop(*a, **kw):
            return None
        orig = botmod.fire_postback
        botmod.fire_postback = noop
        try:
            _run(botmod.on_start(FakeUpdate(), FakeContext()))
        finally:
            botmod.fire_postback = orig

        data = requests.get(f"{BASE_URL}/api/bot/clicks", timeout=15).json()
        mine = [e for e in data["recent"] if e.get("user_id") == FakeUser.id]
        assert mine, "recent[] did not contain our test event"
        entry = mine[0]
        assert entry["click_id"] == cid
        assert entry["click_id_length"] == len(cid)
        assert entry["click_id_length"] > 64  # explicitly beyond Telegram cap

        mongo_db.bot_events.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_users.delete_many({"user_id": FakeUser.id})
        mongo_db.bot_clicks.delete_one({"token": token})
