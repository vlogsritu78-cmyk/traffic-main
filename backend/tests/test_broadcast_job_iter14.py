"""Iteration 14: persistent/resumable broadcast job system.
Covers: POST /api/admin/broadcast (queued job creation), GET /api/admin/broadcast/latest,
50MB file-size validation, and a quick regression pass on bridge/settings/webhook endpoints
that predate this iteration.
"""
import os
import time
import requests
import pytest
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PIN = "246810"


@pytest.fixture(scope="module")
def api_client():
    session = requests.Session()
    return session


@pytest.fixture(scope="module")
def admin_token(api_client):
    resp = api_client.post(f"{BASE_URL}/api/admin/unlock", json={"pin": PIN})
    if resp.status_code != 200:
        pytest.skip("Admin unlock failed - skipping authenticated tests")
    return resp.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


class TestBroadcastAuth:
    def test_broadcast_latest_requires_auth(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/admin/broadcast/latest")
        assert resp.status_code == 401

    def test_broadcast_post_requires_auth(self, api_client):
        resp = api_client.post(f"{BASE_URL}/api/admin/broadcast", data={"message": "no auth test"})
        assert resp.status_code == 401


class TestBroadcastQueueing:
    def test_broadcast_text_only_returns_queued_shape(self, api_client, auth_headers):
        resp = api_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_ITER14 job-queue shape check"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["queued"] is True
        assert "job_id" in data and isinstance(data["job_id"], str) and len(data["job_id"]) > 0
        assert isinstance(data["total"], int) and data["total"] >= 0

    def test_broadcast_empty_message_and_no_file_400(self, api_client, auth_headers):
        resp = api_client.post(f"{BASE_URL}/api/admin/broadcast", data={"message": ""}, headers=auth_headers)
        assert resp.status_code == 400

    def test_broadcast_latest_reflects_job_after_queue(self, api_client, auth_headers):
        resp = api_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_ITER14 latest-shape check"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        job_total = resp.json()["total"]

        time.sleep(2)
        latest = api_client.get(f"{BASE_URL}/api/admin/broadcast/latest", headers=auth_headers)
        assert latest.status_code == 200
        data = latest.json()
        for key in ("status", "sent", "failed", "total", "created_at", "completed_at"):
            assert key in data
        assert data["status"] in ("running", "done")
        assert data["total"] == job_total

        # Poll briefly for completion (small preview user base finishes fast).
        for _ in range(10):
            if data["status"] == "done":
                break
            time.sleep(2)
            data = api_client.get(f"{BASE_URL}/api/admin/broadcast/latest", headers=auth_headers).json()
        assert data["status"] == "done"
        assert data["sent"] + data["failed"] == data["total"]


class TestFileSizeValidation:
    def test_file_over_50mb_rejected(self, api_client, auth_headers, tmp_path):
        big_file = tmp_path / "big.bin"
        with open(big_file, "wb") as f:
            f.seek(51 * 1024 * 1024 - 1)
            f.write(b"\0")
        with open(big_file, "rb") as f:
            resp = api_client.post(
                f"{BASE_URL}/api/admin/broadcast",
                data={"message": "TEST_ITER14 oversized file"},
                files={"file": ("big.bin", f, "application/octet-stream")},
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "50MB" in resp.json()["detail"]


class TestRegression:
    """Quick sanity pass on endpoints predating iteration 14."""

    def test_bridge_redirect_302(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/r", allow_redirects=False)
        assert resp.status_code == 302

    def test_get_settings_public(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "official_username" in data
        assert "welcome_ta" in data and "welcome_en" in data

    def test_put_settings_requires_auth(self, api_client):
        resp = api_client.put(
            f"{BASE_URL}/api/settings",
            json={"official_username": "someuser", "welcome_ta": "x", "welcome_en": "y"},
        )
        assert resp.status_code == 401

    def test_bot_status(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/bot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["unique_users"], int)
        assert "webhook_active" in data

    def test_activate_telegram_requires_auth(self, api_client):
        resp = api_client.post(f"{BASE_URL}/api/admin/telegram/activate")
        assert resp.status_code == 401

    def test_activate_telegram_with_auth(self, api_client, auth_headers):
        resp = api_client.post(f"{BASE_URL}/api/admin/telegram/activate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "webhook_url" in data
