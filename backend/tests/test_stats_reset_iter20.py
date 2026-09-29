"""Tests for POST /api/admin/stats/reset (soft-reset feature, iter 20)."""
import os
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # fallback: read from frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

ADMIN_PIN = "246810"
FAKE_USER_ID = 555111222


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": ADMIN_PIN}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_status():
    r = requests.get(f"{BASE_URL}/api/bot/status", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ── auth / validation ──
def test_reset_requires_auth():
    r = requests.post(f"{BASE_URL}/api/admin/stats/reset", json={"metric": "conversions"}, timeout=15)
    assert r.status_code == 401


def test_reset_invalid_token():
    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "conversions"},
        headers={"Authorization": "Bearer notavalidtoken"},
        timeout=15,
    )
    assert r.status_code == 401


def test_reset_unknown_metric_400(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "bogus"},
        headers=auth_headers,
        timeout=15,
    )
    assert r.status_code == 400


# ── db helper ──
def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def clear_all_resets():
    async def _c():
        db = _mongo()
        await db.bot_settings.update_one({"_id": "config"}, {"$unset": {"stats_reset": ""}})
    _run(_c())


def collection_counts():
    async def _c():
        db = _mongo()
        return {
            "bot_events": await db.bot_events.count_documents({}),
            "bot_users": await db.bot_users.count_documents({}),
            "bot_postbacks": await db.bot_postbacks.count_documents({}),
        }
    return _run(_c())


# ── independence tests ──
def test_reset_conversions_independence(auth_headers):
    clear_all_resets()
    before = get_status()
    raw_before = collection_counts()

    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "conversions"},
        headers=auth_headers,
        timeout=15,
    )
    assert r.status_code == 200, r.text
    after = r.json()

    # These should be zeroed
    assert after["total_starts"] == 0
    assert after["starts_attributed"] == 0
    assert after["starts_unattributed"] == 0
    assert after["postbacks_sent"] == 0
    assert after["postbacks_failed"] == 0
    # starts_today is also affected (uses conv_reset as lower bound)
    assert after["starts_today"] == 0

    # These MUST remain unchanged
    assert after["unique_users"] == before["unique_users"]
    assert after["blocked_users"] == before["blocked_users"]

    # historical docs untouched
    assert collection_counts() == raw_before
    assert after["stats_reset"]["conversions"] is not None


def test_reset_unique_users_independence(auth_headers):
    clear_all_resets()
    before = get_status()
    raw_before = collection_counts()

    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "unique_users"},
        headers=auth_headers,
        timeout=15,
    )
    assert r.status_code == 200
    after = r.json()

    assert after["unique_users"] == 0
    assert after["blocked_users"] == 0

    # Conversions/daily untouched
    assert after["total_starts"] == before["total_starts"]
    assert after["starts_today"] == before["starts_today"]
    assert after["postbacks_sent"] == before["postbacks_sent"]
    assert after["postbacks_failed"] == before["postbacks_failed"]
    assert after["starts_attributed"] == before["starts_attributed"]
    assert after["starts_unattributed"] == before["starts_unattributed"]

    assert collection_counts() == raw_before
    assert after["stats_reset"]["unique_users"] is not None


def test_reset_daily_independence(auth_headers):
    clear_all_resets()
    before = get_status()
    raw_before = collection_counts()

    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "daily"},
        headers=auth_headers,
        timeout=15,
    )
    assert r.status_code == 200
    after = r.json()

    assert after["starts_today"] == 0

    # Others untouched
    assert after["total_starts"] == before["total_starts"]
    assert after["unique_users"] == before["unique_users"]
    assert after["blocked_users"] == before["blocked_users"]
    assert after["postbacks_sent"] == before["postbacks_sent"]
    assert after["postbacks_failed"] == before["postbacks_failed"]
    assert after["starts_attributed"] == before["starts_attributed"]

    assert collection_counts() == raw_before
    assert after["stats_reset"]["daily"] is not None


# ── post-reset new activity ──
def test_post_reset_new_start_increments(auth_headers):
    """After resetting conversions, simulating a /start via bot.process_webhook_update
    should push total_starts to 1 and increase unique_users by 1."""
    # cleanup any stale fake user
    async def _cleanup():
        db = _mongo()
        await db.bot_events.delete_many({"user_id": FAKE_USER_ID})
        await db.bot_users.delete_many({"user_id": FAKE_USER_ID})
    _run(_cleanup())

    clear_all_resets()
    # Reset conversions
    r = requests.post(
        f"{BASE_URL}/api/admin/stats/reset",
        json={"metric": "conversions"},
        headers=auth_headers,
        timeout=15,
    )
    assert r.status_code == 200
    after_reset = r.json()
    assert after_reset["total_starts"] == 0
    users_before_start = after_reset["unique_users"]

    # Simulate /start via bot.process_webhook_update
    async def _simulate():
        import sys
        sys.path.insert(0, "/app/backend")
        import bot as bot_module
        await bot_module.init_bot()
        payload = {
            "update_id": 999999901,
            "message": {
                "message_id": 1,
                "date": 1735689600,
                "chat": {"id": FAKE_USER_ID, "type": "private"},
                "from": {"id": FAKE_USER_ID, "is_bot": False, "first_name": "TestFake", "language_code": "en"},
                "text": "/start",
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
        }
        try:
            await bot_module.process_webhook_update(payload)
        except Exception as e:
            # send_message will fail for a fake chat id — the DB track() runs first, that's fine
            print(f"expected send failure: {e}")
    _run(_simulate())

    # verify status
    st = get_status()
    assert st["total_starts"] >= 1, f"expected total_starts>=1, got {st}"
    assert st["unique_users"] == users_before_start + 1, f"users {users_before_start} -> {st['unique_users']}"

    # cleanup
    _run(_cleanup())


# ── final teardown: restore preview demo state ──
def test_zz_restore_demo_state(auth_headers):
    """Restore stats_reset to null so dashboard shows normal ~120/~59 numbers."""
    async def _c():
        db = _mongo()
        await db.bot_settings.update_one({"_id": "config"}, {"$unset": {"stats_reset": ""}})
        # also cleanup fake user
        await db.bot_events.delete_many({"user_id": FAKE_USER_ID})
        await db.bot_users.delete_many({"user_id": FAKE_USER_ID})
    _run(_c())
    st = get_status()
    assert st["stats_reset"] == {"conversions": None, "unique_users": None, "daily": None}
    # numbers should be back to normal
    assert st["total_starts"] > 50, f"expected preserved historical starts, got {st['total_starts']}"
    assert st["unique_users"] > 20, f"expected preserved historical users, got {st['unique_users']}"
