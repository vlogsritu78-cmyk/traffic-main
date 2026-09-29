"""
Iteration 13: Broadcast timeout fix (Cloudflare 524 bug)
Tests POST /api/admin/broadcast is now async (BackgroundTasks) and returns
quickly with {queued, total}; GET /api/bot/status exposes last_broadcast.
Also light regression pass on auth boundaries, /api/r bridge, /api/settings.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_PIN = "246810"


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    return s


@pytest.fixture(scope="module")
def auth_token(api_client):
    r = api_client.post(f"{BASE_URL}/api/admin/unlock", json={"pin": ADMIN_PIN})
    if r.status_code != 200:
        pytest.skip(f"unlock failed: {r.status_code} {r.text}")
    return r.json().get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


class TestBroadcastAsync:
    def test_broadcast_no_auth_401(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/broadcast", data={"message": "hi"})
        assert r.status_code == 401

    def test_broadcast_empty_400(self, api_client, auth_headers):
        r = api_client.post(f"{BASE_URL}/api/admin/broadcast", data={"message": ""}, headers=auth_headers)
        assert r.status_code == 400
        assert "Write a message or attach a file." in r.json().get("detail", "")

    def test_broadcast_returns_fast_and_queued(self, api_client, auth_headers):
        start = time.monotonic()
        r = api_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            data={"message": "TEST_iter13 broadcast async fix check"},
            headers=auth_headers,
        )
        elapsed = time.monotonic() - start
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("queued") is True
        assert isinstance(data.get("total"), int)
        assert elapsed < 5, f"broadcast took {elapsed}s, should return almost immediately"
        # stash total for next test
        TestBroadcastAsync.total = data["total"]

    def test_status_last_broadcast_populated_after_wait(self, api_client):
        time.sleep(15)
        r = api_client.get(f"{BASE_URL}/api/bot/status")
        assert r.status_code == 200
        data = r.json()
        assert "last_broadcast" in data
        lb = data["last_broadcast"]
        assert lb is not None, "last_broadcast should be populated after background task completes"
        assert "sent" in lb and "failed" in lb and "total" in lb and "completed_at" in lb
        assert lb["total"] == getattr(TestBroadcastAsync, "total", lb["total"])


class TestRegression:
    def test_bridge_redirect_302(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/r", allow_redirects=False)
        assert r.status_code in (302, 307)

    def test_settings_get_public(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/settings")
        assert r.status_code == 200
        assert "official_url" in r.json() or "official_username" in r.json()

    def test_settings_put_requires_auth(self, api_client):
        r = api_client.put(f"{BASE_URL}/api/settings", json={"official_username": "test"})
        assert r.status_code == 401
