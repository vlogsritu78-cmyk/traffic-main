"""Iteration 5 — admin PIN + editable settings tests.

CRITICAL invariants preserved:
  * Original official_username restored to Heheheehejvjsjsjsjdjjdjdbdh
  * welcome_ta/welcome_en restored to bot_content defaults
  * PIN restored to 246810
  * db.admin_attempts wiped after brute-force test
"""

import asyncio
import importlib
import os
import sys
import time
from pathlib import Path

import bcrypt
import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

frontend_env = dotenv_values("/app/frontend/.env")
backend_env = dotenv_values("/app/backend/.env")
BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or frontend_env.get("REACT_APP_BACKEND_URL")
).rstrip("/")
MONGO_URL = backend_env["MONGO_URL"]
DB_NAME = backend_env["DB_NAME"]
ADMIN_PIN = backend_env["ADMIN_PIN"]
TOKEN_TG = backend_env["TELEGRAM_BOT_TOKEN"]
ORIGINAL_USERNAME = "Heheheehejvjsjsjsjdjjdjdbdh"

sys.path.insert(0, "/app/backend")
from bot_content import DEFAULT_TA, DEFAULT_EN  # noqa: E402


@pytest.fixture(scope="module")
def mongo_db():
    c = MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(scope="module", autouse=True)
def _restore_after_module(mongo_db):
    """Restore critical settings + wipe lockouts after all iteration-5 tests."""
    yield
    # Best-effort: unlock with any of the PINs used and restore config
    for pin in (ADMIN_PIN, "999111", "246810"):
        r = requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": pin}, timeout=10)
        if r.status_code == 200:
            tk = r.json()["token"]
            # restore username/text
            requests.put(
                f"{BASE_URL}/api/settings",
                json={
                    "official_username": ORIGINAL_USERNAME,
                    "welcome_ta": DEFAULT_TA,
                    "welcome_en": DEFAULT_EN,
                },
                headers={"Authorization": f"Bearer {tk}"},
                timeout=10,
            )
            # restore PIN
            requests.post(
                f"{BASE_URL}/api/admin/pin",
                json={"new_pin": ADMIN_PIN},
                headers={"Authorization": f"Bearer {tk}"},
                timeout=10,
            )
            break
    mongo_db.admin_attempts.delete_many({})


def _unlock(pin=ADMIN_PIN):
    return requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": pin}, timeout=15)


def _valid_token():
    r = _unlock()
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── AUTH ─────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_get_settings_public_no_pin_hash(self):
        r = requests.get(f"{BASE_URL}/api/settings", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "pin" not in data and "pin_hash" not in data
        for k in ("official_username", "official_url", "welcome_ta", "welcome_en", "button_text"):
            assert k in data

    def test_wrong_pin_returns_401(self, mongo_db):
        mongo_db.admin_attempts.delete_many({})
        r = _unlock("000000")
        assert r.status_code == 401
        assert r.json()["detail"] == "Wrong PIN."
        mongo_db.admin_attempts.delete_many({})

    def test_correct_pin_returns_token(self, mongo_db):
        mongo_db.admin_attempts.delete_many({})
        r = _unlock(ADMIN_PIN)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["token"], str) and len(body["token"]) > 20
        assert body["expires_in_hours"] == 8

    def test_put_settings_without_auth_401(self):
        r = requests.put(
            f"{BASE_URL}/api/settings",
            json={"official_username": "abcdef", "welcome_ta": "x", "welcome_en": "y"},
            timeout=10,
        )
        assert r.status_code == 401
        assert isinstance(r.json()["detail"], str)

    def test_put_settings_garbage_bearer_401(self):
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": "Bearer not.a.real.jwt"},
            json={"official_username": "abcdef", "welcome_ta": "x", "welcome_en": "y"},
            timeout=10,
        )
        assert r.status_code == 401
        assert isinstance(r.json()["detail"], str)

    def test_put_settings_with_valid_token_200(self, mongo_db):
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={
                "official_username": ORIGINAL_USERNAME,
                "welcome_ta": DEFAULT_TA,
                "welcome_en": DEFAULT_EN,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["official_username"] == ORIGINAL_USERNAME

    def test_pin_hash_stored_bcrypt(self, mongo_db):
        doc = mongo_db.bot_settings.find_one({"_id": "config"})
        assert doc is not None
        assert doc["pin_hash"].startswith("$2b$")
        # Verify plaintext PIN validates
        assert bcrypt.checkpw(ADMIN_PIN.encode(), doc["pin_hash"].encode())


# ── BRUTE FORCE (Iteration 6 — X-Forwarded-For based tracking) ──────────────
class TestBruteForce:
    """After the iter6 fix, client_ip() trusts left-most XFF, so we can pin
    the caller identity by sending a stable XFF header. The 6th wrong attempt
    from the same XFF must return 429."""

    def _unlock_with_xff(self, ip, pin="111111"):
        return requests.post(
            f"{BASE_URL}/api/admin/unlock",
            json={"pin": pin},
            headers={"X-Forwarded-For": ip},
            timeout=15,
        )

    def test_lockout_after_5_wrong_same_xff(self, mongo_db):
        mongo_db.admin_attempts.delete_many({})
        ip = "203.0.113.55"
        try:
            for i in range(5):
                r = self._unlock_with_xff(ip)
                assert r.status_code == 401, f"attempt {i}: {r.status_code} {r.text}"
            r6 = self._unlock_with_xff(ip)
            assert r6.status_code == 429, r6.text
            detail = r6.json()["detail"]
            assert "Try again in" in detail and "min" in detail
            # doc exists for the per-IP bucket
            assert mongo_db.admin_attempts.find_one({"_id": ip}) is not None
            assert mongo_db.admin_attempts.find_one({"_id": "__global__"}) is not None
        finally:
            mongo_db.admin_attempts.delete_many({})

    @pytest.mark.xdist_group("bruteforce_serial")
    def test_global_ceiling_trips_with_rotating_xff(self, mongo_db):
        """Rotate XFF per request; the per-IP bucket never crosses limit, but
        the __global__ counter must trip at 20. NOTE: this test is inherently
        non-parallelizable because a successful unlock anywhere in the suite
        clears __global__. It is retried once if the ceiling never trips."""
        for attempt in range(2):
            mongo_db.admin_attempts.delete_many({})
            got_429_at = None
            for i in range(25):
                r = self._unlock_with_xff(f"198.51.100.{i+1}")
                if r.status_code == 429:
                    got_429_at = i
                    detail = r.json()["detail"]
                    assert "Try again in" in detail
                    break
                assert r.status_code == 401, f"unexpected {r.status_code} at {i}: {r.text}"
            mongo_db.admin_attempts.delete_many({})
            if got_429_at is not None:
                assert got_429_at >= 20, f"tripped too early at {got_429_at}"
                return
            time.sleep(1)
        pytest.fail("Global 20-attempt ceiling never tripped — rotating XFF still bypasses")

    def test_successful_unlock_clears_both_buckets(self, mongo_db):
        mongo_db.admin_attempts.delete_many({})
        ip = "203.0.113.99"
        # 3 wrong attempts (below per-IP lockout)
        for _ in range(3):
            r = self._unlock_with_xff(ip)
            assert r.status_code == 401
        assert mongo_db.admin_attempts.find_one({"_id": ip}) is not None
        assert mongo_db.admin_attempts.find_one({"_id": "__global__"}) is not None
        # Now correct PIN with same XFF
        r_ok = requests.post(
            f"{BASE_URL}/api/admin/unlock",
            json={"pin": ADMIN_PIN},
            headers={"X-Forwarded-For": ip},
            timeout=15,
        )
        assert r_ok.status_code == 200, r_ok.text
        # BOTH buckets should be cleared
        assert mongo_db.admin_attempts.find_one({"_id": ip}) is None
        assert mongo_db.admin_attempts.find_one({"_id": "__global__"}) is None


# ── PIN CHANGE + RESTORE ────────────────────────────────────────────────────
class TestPinChange:
    def test_change_pin_and_restore(self, mongo_db):
        mongo_db.admin_attempts.delete_many({})
        tk = _valid_token()
        # Change to 999111
        r = requests.post(
            f"{BASE_URL}/api/admin/pin",
            json={"new_pin": "999111"},
            headers={"Authorization": f"Bearer {tk}"},
            timeout=10,
        )
        assert r.status_code == 200

        # Old PIN fails
        assert _unlock("246810").status_code == 401
        mongo_db.admin_attempts.delete_many({})
        # New PIN succeeds
        r2 = _unlock("999111")
        assert r2.status_code == 200
        tk2 = r2.json()["token"]

        # Restore
        r3 = requests.post(
            f"{BASE_URL}/api/admin/pin",
            json={"new_pin": ADMIN_PIN},
            headers={"Authorization": f"Bearer {tk2}"},
            timeout=10,
        )
        assert r3.status_code == 200
        mongo_db.admin_attempts.delete_many({})
        assert _unlock(ADMIN_PIN).status_code == 200


# ── USERNAME NORMALISATION + VALIDATION ─────────────────────────────────────
class TestUsername:
    @pytest.mark.parametrize(
        "raw",
        ["testaccount", "@testaccount", "https://t.me/testaccount", "t.me/testaccount?text=hi"],
    )
    def test_normalisation_accepts(self, raw):
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": raw, "welcome_ta": DEFAULT_TA, "welcome_en": DEFAULT_EN},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["official_username"] == "testaccount"
        # server-rebuilt URL, client cannot inject
        assert d["official_url"] == "https://t.me/testaccount?text=hi"

    @pytest.mark.parametrize(
        "bad",
        ["abc", "a" * 40, "bad name!!", ""],
    )
    def test_invalid_rejected_400(self, bad):
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": bad, "welcome_ta": "x", "welcome_en": "y"},
            timeout=10,
        )
        # empty string is caught by pydantic min_length=1 -> 422; others by clean_username -> 400
        assert r.status_code in (400, 422), r.text
        detail = r.json()["detail"]
        # Must be string OR (for pydantic 422) list; iteration requires 400 with string.
        # But empty "" hits Pydantic min_length=1 first → 422 with list.
        if r.status_code == 400:
            assert isinstance(detail, str), f"detail must be string, got {type(detail)}"

    def test_empty_username_is_400_string(self):
        """Iteration 6 fix: SettingsUpdate no longer has min_length=1, so empty
        username must be rejected by clean_username with 400 + string detail."""
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": "", "welcome_ta": "x", "welcome_en": "y"},
            timeout=10,
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, str), f"detail must be string, got {type(detail)}: {detail}"
        assert "username" in detail.lower()


# ── Iteration 6 — validation contract + non-mutation ─────────────────────────
class TestIter6Validation:
    def test_blank_welcome_ta_returns_400_string(self):
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": ORIGINAL_USERNAME, "welcome_ta": "   ", "welcome_en": "English"},
            timeout=10,
        )
        assert r.status_code == 400, r.text
        assert isinstance(r.json()["detail"], str)
        assert "Tamil" in r.json()["detail"] or "English" in r.json()["detail"]

    def test_blank_welcome_en_returns_400_string(self):
        tk = _valid_token()
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": ORIGINAL_USERNAME, "welcome_ta": "தமிழ்", "welcome_en": ""},
            timeout=10,
        )
        assert r.status_code == 400, r.text
        assert isinstance(r.json()["detail"], str)

    def test_rejected_write_does_not_partially_mutate(self, mongo_db):
        """A 400 (bad username OR blank welcome) must NOT overwrite any field
        of bot_settings — snapshot before, verify identical after."""
        # First set a known-good state
        tk = _valid_token()
        r0 = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={
                "official_username": ORIGINAL_USERNAME,
                "welcome_ta": DEFAULT_TA,
                "welcome_en": DEFAULT_EN,
            },
            timeout=10,
        )
        assert r0.status_code == 200
        before = mongo_db.bot_settings.find_one({"_id": "config"})

        # Attempt bad update — invalid username
        r_bad = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": "!!bad!!", "welcome_ta": "NEW_TA", "welcome_en": "NEW_EN"},
            timeout=10,
        )
        assert r_bad.status_code == 400

        # Attempt bad update — blank welcome
        r_bad2 = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": "somenewuser", "welcome_ta": "", "welcome_en": ""},
            timeout=10,
        )
        assert r_bad2.status_code == 400

        after = mongo_db.bot_settings.find_one({"_id": "config"})
        # updated_at may differ only if a write happened — but nothing should have
        assert after["official_username"] == before["official_username"]
        assert after["official_url"] == before["official_url"]
        assert after["welcome_ta"] == before["welcome_ta"]
        assert after["welcome_en"] == before["welcome_en"]

    def test_non_admin_token_type_rejected(self):
        """A JWT signed with the same secret but type!='admin' must be 401."""
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        secret = backend_env["JWT_SECRET"]
        bad = pyjwt.encode(
            {"sub": "admin", "type": "guest",
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            secret, algorithm="HS256",
        )
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {bad}"},
            json={"official_username": "someuser", "welcome_ta": "a", "welcome_en": "b"},
            timeout=10,
        )
        assert r.status_code == 401
        assert isinstance(r.json()["detail"], str)


# ── WELCOME TEXT + HTML SAFETY ──────────────────────────────────────────────
class TestWelcome:
    def test_put_and_get_welcome_persists(self):
        tk = _valid_token()
        ta = "தமிழ் body TEST_iter5"
        en = "English body TEST_iter5"
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={"official_username": ORIGINAL_USERNAME, "welcome_ta": ta, "welcome_en": en},
            timeout=10,
        )
        assert r.status_code == 200
        g = requests.get(f"{BASE_URL}/api/settings", timeout=10).json()
        assert g["welcome_ta"] == ta
        assert g["welcome_en"] == en

    def test_html_injection_is_escaped(self):
        from bot_content import welcome_text
        malicious = '<b>x</b> & "quotes" <script>alert(1)</script>'
        text = welcome_text("@user", "ok", malicious)
        # sendMessage to chat_id=1 with HTML — must fail with 'chat not found', not parse error
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage",
            json={"chat_id": 1, "text": text, "parse_mode": "HTML"},
            timeout=15,
        ).json()
        assert r["ok"] is False
        desc = r.get("description", "").lower()
        assert "can't parse" not in desc and "can not parse" not in desc, f"HTML parse err: {desc}"
        assert "chat not found" in desc, desc


# ── BOT PROPAGATION (5s TTL) ────────────────────────────────────────────────
class TestBotPropagation:
    def test_bot_get_settings_reflects_put_after_ttl(self, mongo_db):
        import bot as botmod
        importlib.reload(botmod)

        tk = _valid_token()
        new_ta = "TEST_prop_ta_%d" % int(time.time())
        new_en = "TEST_prop_en_%d" % int(time.time())
        r = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={
                "official_username": "propuser",
                "welcome_ta": new_ta,
                "welcome_en": new_en,
            },
            timeout=10,
        )
        assert r.status_code == 200

        # Force cache expiry
        botmod._settings_cache.update(at=0.0, data=None)
        loop = asyncio.new_event_loop()
        s = loop.run_until_complete(botmod.get_settings())
        assert s["official_username"] == "propuser"
        assert s["official_url"] == "https://t.me/propuser?text=hi"
        assert s["welcome_ta"] == new_ta
        assert s["welcome_en"] == new_en

        # reply() builds one-button keyboard with new url
        captured = {}

        class FakeMessage:
            async def reply_text(self, text, **kw):
                captured["text"] = text
                captured["kw"] = kw

        class FakeUpdate:
            effective_message = FakeMessage()

        loop.run_until_complete(botmod.reply(FakeUpdate()))
        markup = captured["kw"]["reply_markup"]
        rows = markup.inline_keyboard
        assert len(rows) == 1 and len(rows[0]) == 1
        assert rows[0][0].url == "https://t.me/propuser?text=hi"
        # Welcome body includes escaped new text
        assert new_ta in captured["text"]
        assert new_en in captured["text"]

        # Restore
        r2 = requests.put(
            f"{BASE_URL}/api/settings",
            headers={"Authorization": f"Bearer {tk}"},
            json={
                "official_username": ORIGINAL_USERNAME,
                "welcome_ta": DEFAULT_TA,
                "welcome_en": DEFAULT_EN,
            },
            timeout=10,
        )
        assert r2.status_code == 200


# ── REGRESSION ──────────────────────────────────────────────────────────────
class TestRegression:
    def test_status_has_all_fields(self):
        r = requests.get(f"{BASE_URL}/api/bot/status", timeout=10)
        assert r.status_code == 200
        d = r.json()
        for k in (
            "online", "bot_username", "official_url", "official_username",
            "total_starts", "unique_users", "starts_today",
            "postback_enabled", "postbacks_sent", "postbacks_failed",
            "starts_attributed", "starts_unattributed",
        ):
            assert k in d, f"missing {k}"
        # settings-backed
        gs = requests.get(f"{BASE_URL}/api/settings", timeout=10).json()
        assert d["official_url"] == gs["official_url"]
        assert d["official_username"] == f"@{gs['official_username']}"

    def test_bridge_redirect(self):
        cid = "TEST_iter5_bridge_" + "x" * 120
        r = requests.get(
            f"{BASE_URL}/api/r?click_id={cid}", allow_redirects=False, timeout=10
        )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://t.me/tamil_best_service_bot?start=")
        token = loc.rsplit("?start=", 1)[1]
        assert len(token) <= 64

    def test_bot_has_zero_commands(self):
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN_TG}/getMyCommands", timeout=15
        ).json()
        assert r["ok"] and r["result"] == []
