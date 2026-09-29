"""Tests for NEW POST /api/admin/broadcast endpoint (iteration 11).
Covers: auth boundary (401), validation (400 empty), and 503 (bot not running
in this preview environment - BOT_POLLING_ENABLED=false is intentional)."""
import os
import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get('REACT_APP_BACKEND_URL') or frontend_env.get('REACT_APP_BACKEND_URL')).rstrip('/')
ADMIN_PIN = "246810"


@pytest.fixture(scope="module")
def admin_token():
    resp = requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": ADMIN_PIN})
    if resp.status_code != 200:
        pytest.skip(f"Could not unlock admin panel: {resp.status_code} {resp.text}")
    return resp.json()["token"]


class TestBroadcastAuth:
    def test_broadcast_without_auth_401(self):
        resp = requests.post(f"{BASE_URL}/api/admin/broadcast", data={"message": "hello"})
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    def test_broadcast_invalid_token_401(self):
        resp = requests.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "hello"},
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestBroadcastValidation:
    def test_broadcast_empty_message_and_no_file_400(self, admin_token):
        resp = requests.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": ""},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"] == "Write a message or attach a file."

    def test_broadcast_whitespace_only_message_400(self, admin_token):
        resp = requests.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "   "},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400


class TestBroadcastSend:
    def test_broadcast_with_message_returns_503_bot_not_running(self, admin_token):
        """Preview env has BOT_POLLING_ENABLED=false (_bot_app is None) by design.
        This 503 is the CORRECT expected behavior here, not a bug."""
        resp = requests.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_broadcast message"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"] == "The bot isn't running in this environment, so it can't send messages here."

    def test_broadcast_with_file_only_returns_503(self, admin_token):
        files = {"file": ("test.txt", b"hello world", "text/plain")}
        resp = requests.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": ""},
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 503
