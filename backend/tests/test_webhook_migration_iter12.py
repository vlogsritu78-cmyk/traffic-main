"""Iteration 12: Telegram webhook migration tests.
Covers: GET /api/bot/status new fields, POST /api/admin/telegram/activate
(auth + success + url correctness), POST /api/telegram/webhook/{secret}
(auth boundary via secret path + header), and regressions (/api/r bridge,
/api/settings, /api/admin/broadcast).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_PIN = "246810"
WEBHOOK_SECRET = "2I_9d9cDO0x_ecsvXBUk7Tnm3hUJE-I-"


@pytest.fixture(scope="module")
def admin_token():
    resp = requests.post(f"{API}/admin/unlock", json={"pin": ADMIN_PIN})
    if resp.status_code != 200:
        pytest.skip(f"unlock failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


class TestBotStatus:
    def test_status_fields_present_and_typed(self):
        resp = requests.get(f"{API}/bot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_live_here" in data
        assert "webhook_active" in data
        assert "polling_enabled" not in data
        assert isinstance(data["is_live_here"], bool)
        assert isinstance(data["webhook_active"], bool)
        assert isinstance(data["online"], bool)


class TestActivateAuth:
    def test_activate_without_auth_401(self):
        resp = requests.post(f"{API}/admin/telegram/activate")
        assert resp.status_code == 401

    def test_activate_with_bad_token_401(self):
        resp = requests.post(
            f"{API}/admin/telegram/activate",
            headers={"Authorization": "Bearer garbage.invalid.token"},
        )
        assert resp.status_code == 401


class TestActivateFlow:
    def test_activate_success_and_status_reflects(self, admin_token):
        resp = requests.post(
            f"{API}/admin/telegram/activate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "webhook_url" in data
        assert "pending_update_count" in data
        assert BASE_URL in data["webhook_url"]
        assert data["webhook_url"].endswith(f"/api/telegram/webhook/{WEBHOOK_SECRET}")
        assert isinstance(data["pending_update_count"], int)

        time.sleep(1)
        status = requests.get(f"{API}/bot/status").json()
        assert status["is_live_here"] is True
        assert status["webhook_active"] is True
        assert status["online"] is True


class TestWebhookEndpointAuth:
    def test_wrong_secret_in_path_404(self):
        resp = requests.post(
            f"{API}/telegram/webhook/WRONG-SECRET",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )
        assert resp.status_code == 404

    def test_missing_secret_header_404(self):
        resp = requests.post(
            f"{API}/telegram/webhook/{WEBHOOK_SECRET}",
            json={"update_id": 1},
        )
        assert resp.status_code == 404

    def test_wrong_secret_header_404(self):
        resp = requests.post(
            f"{API}/telegram/webhook/{WEBHOOK_SECRET}",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
        )
        assert resp.status_code == 404

    def test_correct_secret_both_places_ok(self):
        resp = requests.post(
            f"{API}/telegram/webhook/{WEBHOOK_SECRET}",
            json={"update_id": 999999, "message": {"message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"}, "text": "hi"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestRegressions:
    def test_bridge_redirect(self):
        resp = requests.get(f"{API}/r?click_id=TEST_click_iter12", allow_redirects=False)
        assert resp.status_code == 302
        assert "start=" in resp.headers["location"]

    def test_bridge_redirect_no_click_id(self):
        resp = requests.get(f"{API}/r", allow_redirects=False)
        assert resp.status_code == 302

    def test_settings_public_read(self):
        resp = requests.get(f"{API}/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "official_username" in data
        assert "welcome_ta" in data

    def test_settings_write_requires_auth(self):
        resp = requests.put(f"{API}/settings", json={"official_username": "x", "welcome_ta": "a", "welcome_en": "b"})
        assert resp.status_code == 401

    def test_broadcast_after_migration(self, admin_token):
        resp = requests.post(
            f"{API}/admin/broadcast",
            data={"message": "testing broadcast still works after webhook migration"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sent" in data and "failed" in data and "total" in data
        assert data["sent"] > 0
