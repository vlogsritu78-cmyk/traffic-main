"""Iteration 15: verify categorized broadcast failure reasons (error_counts).

Covers:
- GET /api/admin/broadcast/latest includes new `error_counts` field
- Invariant: sent + failed == total, and sum(error_counts.values()) == failed
- Same invariant holds with a media file attached (first-upload path)
- Regression: status transitions running -> done, processed count == total
"""
import os
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_PIN = "246810"


@pytest.fixture(scope="module")
def api_client():
    session = requests.Session()
    return session


@pytest.fixture(scope="module")
def auth_token(api_client):
    resp = api_client.post(f"{BASE_URL}/api/admin/unlock", json={"pin": ADMIN_PIN})
    if resp.status_code != 200:
        pytest.skip(f"Unlock failed: {resp.status_code} {resp.text}")
    return resp.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


def wait_for_done(api_client, headers, timeout=60):
    start = time.time()
    last = None
    while time.time() - start < timeout:
        r = api_client.get(f"{BASE_URL}/api/admin/broadcast/latest", headers=headers)
        assert r.status_code == 200
        last = r.json()
        if last and last.get("status") == "done":
            return last
        time.sleep(2)
    pytest.fail(f"Broadcast job did not complete in {timeout}s, last={last}")


class TestBroadcastErrorCounts:
    def test_text_only_broadcast_error_counts_invariant(self, api_client, auth_headers):
        resp = api_client.post(
            f"{BASE_URL}/api/admin/broadcast",
            headers=auth_headers,
            data={"message": "TEST_iter15 diagnostic broadcast (text only)"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["queued"] is True
        assert "job_id" in body
        total = body["total"]

        job = wait_for_done(api_client, auth_headers)
        assert job["status"] == "done"
        assert job["total"] == total
        assert "error_counts" in job
        assert isinstance(job["error_counts"], dict)

        sent, failed = job["sent"], job["failed"]
        assert sent + failed == total, f"sent({sent})+failed({failed}) != total({total})"

        err_sum = sum(job["error_counts"].values())
        assert err_sum == failed, f"sum(error_counts)={err_sum} != failed={failed}"

        # only known labels expected
        known = {"blocked_bot", "invalid_chat", "rate_limited", "network_timeout", "other"}
        assert set(job["error_counts"].keys()) <= known

    def test_broadcast_with_media_error_counts_invariant(self, api_client, auth_headers, tmp_path):
        img_path = tmp_path / "test.png"
        # minimal 1x1 png
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a4944415478"
            "9c6360000002000155005738f8ee0000000049454e44ae426082"
        )
        img_path.write_bytes(png_bytes)

        with open(img_path, "rb") as f:
            resp = api_client.post(
                f"{BASE_URL}/api/admin/broadcast",
                headers=auth_headers,
                data={"message": "TEST_iter15 diagnostic broadcast (with image)"},
                files={"file": ("test.png", f, "image/png")},
            )
        assert resp.status_code == 200
        body = resp.json()
        total = body["total"]

        job = wait_for_done(api_client, auth_headers, timeout=90)
        assert job["status"] == "done"

        sent, failed = job["sent"], job["failed"]
        assert sent + failed == total
        err_sum = sum(job.get("error_counts", {}).values())
        assert err_sum == failed, f"sum(error_counts)={err_sum} != failed={failed} (media path)"

    def test_latest_endpoint_requires_auth(self, api_client):
        resp = api_client.get(f"{BASE_URL}/api/admin/broadcast/latest")
        assert resp.status_code == 401

    def test_broadcast_endpoint_requires_auth(self, api_client):
        resp = api_client.post(f"{BASE_URL}/api/admin/broadcast", data={"message": "should fail"})
        assert resp.status_code == 401
