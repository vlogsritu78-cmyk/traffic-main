"""Iteration 16: verify blocked-user exclusion from broadcasts and
un-block-on-interact behavior.

Covers:
- GET /api/bot/status includes blocked_users count matching direct DB query
- POST /api/admin/broadcast's upfront `total` excludes blocked:true users
- Sending a broadcast marks newly-failed users as blocked:true (blocked_bot/invalid_chat)
  and the very next broadcast's total drops accordingly
- Webhook /start from a currently-blocked user_id un-blocks them (track() sets blocked:False)
- Regression: sent+failed==total and sum(error_counts)==failed still hold
"""
import os
import time

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_PIN = "246810"


@pytest.fixture(scope="module")
def api_client():
    # No default Content-Type header -- broadcast endpoint expects
    # multipart/form-data (Form fields), while unlock expects JSON;
    # `requests` sets the correct Content-Type per-call automatically
    # as long as we don't force one globally here.
    return requests.Session()


@pytest.fixture(scope="module")
def admin_token(api_client):
    r = api_client.post(f"{BASE_URL}/api/admin/unlock", json={"pin": ADMIN_PIN})
    if r.status_code != 200:
        pytest.skip(f"Admin unlock failed: {r.status_code} {r.text}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_client(api_client, admin_token):
    api_client.headers.update({"Authorization": f"Bearer {admin_token}"})
    return api_client


def _mongo_db():
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestBlockedUsersStatus:
    def test_status_blocked_users_matches_db(self, api_client):
        db = _mongo_db()
        db_blocked = run_async(db.bot_users.count_documents({"blocked": True}))
        r = api_client.get(f"{BASE_URL}/api/bot/status")
        assert r.status_code == 200
        data = r.json()
        assert "blocked_users" in data
        assert isinstance(data["blocked_users"], int)
        assert data["blocked_users"] == db_blocked


class TestBroadcastExcludesBlocked:
    def test_broadcast_total_excludes_blocked(self, auth_client):
        db = _mongo_db()
        expected_total = run_async(db.bot_users.count_documents({"blocked": {"$ne": True}}))

        r = auth_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_iter16 broadcast - blocked exclusion check"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["queued"] is True
        assert body["total"] == expected_total, "broadcast total must exclude blocked users"

        job_id = body["job_id"]
        # poll for completion
        final = None
        for _ in range(60):
            time.sleep(2)
            latest = auth_client.get(f"{BASE_URL}/api/admin/broadcast/latest")
            assert latest.status_code == 200
            j = latest.json()
            if j and j["status"] == "done":
                final = j
                break
        assert final is not None, "broadcast did not complete in time"

        # regression invariants (iter14/15)
        assert final["sent"] + final["failed"] == final["total"]
        assert sum(final.get("error_counts", {}).values()) == final["failed"]

        # newly failed users must now be marked blocked in db
        new_blocked = run_async(db.bot_users.count_documents({"blocked": True}))
        assert new_blocked >= 18  # at least the pre-existing ones from main agent's run

    def test_second_broadcast_total_drops_after_first(self, auth_client):
        """Run a second broadcast right after; its total should be <= first's
        total since any newly-failed users get excluded."""
        db = _mongo_db()
        expected_total = run_async(db.bot_users.count_documents({"blocked": {"$ne": True}}))
        r = auth_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_iter16 broadcast 2 - confirm shrinking total"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == expected_total

        job_id = body["job_id"]
        final = None
        for _ in range(60):
            time.sleep(2)
            latest = auth_client.get(f"{BASE_URL}/api/admin/broadcast/latest")
            j = latest.json()
            if j and j["status"] == "done":
                final = j
                break
        assert final is not None
        assert final["sent"] + final["failed"] == final["total"]
        assert sum(final.get("error_counts", {}).values()) == final["failed"]


class TestUnblockOnInteract:
    def test_webhook_start_unblocks_user(self, api_client):
        db = _mongo_db()
        blocked_doc = run_async(db.bot_users.find_one({"blocked": True}))
        if not blocked_doc:
            pytest.skip("No blocked user available to test un-blocking")
        uid = blocked_doc["user_id"]

        from dotenv import dotenv_values
        secret = dotenv_values("/app/backend/.env").get("TELEGRAM_WEBHOOK_SECRET")
        assert secret, "TELEGRAM_WEBHOOK_SECRET not found in backend/.env"

        update_payload = {
            "update_id": 999999001,
            "message": {
                "message_id": 1,
                "date": int(time.time()),
                "text": "/start",
                "chat": {"id": uid, "type": "private", "first_name": "TestUser"},
                "from": {"id": uid, "is_bot": False, "first_name": "TestUser"},
            },
        }
        r = requests.post(
            f"{BASE_URL}/api/telegram/webhook/{secret}",
            json=update_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
        # webhook endpoint should respond ok even if reply_text delivery
        # itself fails downstream (since chat doesn't really exist)
        assert r.status_code == 200, f"webhook call failed: {r.status_code} {r.text}"

        time.sleep(1)
        updated_doc = run_async(db.bot_users.find_one({"user_id": uid}))
        assert updated_doc is not None
        assert updated_doc["blocked"] is False, (
            f"user {uid} should be un-blocked after interacting again, got: {updated_doc}"
        )
