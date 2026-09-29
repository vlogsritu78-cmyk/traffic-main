"""Iteration 17 - GET /api/admin/broadcast/history (new endpoint)"""
import os
import re
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
HISTORY = f"{BASE_URL}/api/admin/broadcast/history"


@pytest.fixture(scope="module")
def pin():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    m = re.search(r"PIN:\s*`?(\d+)", content)
    if not m:
        pytest.skip("no PIN in test_credentials.md")
    return m.group(1)


@pytest.fixture(scope="module")
def token(pin):
    r = requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": pin}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"unlock failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token")
    assert isinstance(tok, str) and tok
    return tok


# --- auth guard ---
class TestHistoryAuth:
    def test_no_token_401(self):
        r = requests.get(HISTORY, timeout=30)
        assert r.status_code in (401, 403), r.text[:200]

    def test_bad_token_401(self):
        r = requests.get(HISTORY, headers={"Authorization": "Bearer garbage"}, timeout=30)
        assert r.status_code in (401, 403), r.text[:200]


# --- payload shape ---
class TestHistoryPayload:
    def test_shape_and_sort(self, token):
        r = requests.get(HISTORY, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert isinstance(data, list)
        assert len(data) <= 20
        assert len(data) > 0, "expected pre-existing broadcast jobs in preview DB"
        keys = {
            "id", "text_preview", "media_filename", "status", "sent",
            "failed", "total", "error_counts", "created_at", "completed_at",
        }
        created = []
        for job in data:
            assert keys.issubset(job.keys()), f"missing keys: {keys - set(job.keys())}"
            assert "_id" not in job
            assert isinstance(job["id"], str)
            assert isinstance(job["sent"], int)
            assert isinstance(job["failed"], int)
            assert isinstance(job["total"], int)
            assert isinstance(job["error_counts"], dict)
            assert len(job["text_preview"]) <= 80
            created.append(job["created_at"])
        assert created == sorted(created, reverse=True), "not sorted newest first"

    def test_new_job_doc_appears_first(self, token):
        """Inject a synthetic bot_broadcasts doc (no real Telegram DMs sent to the
        real users in this preview DB) and verify the endpoint surfaces it first
        with the failure breakdown fields intact. Cleaned up afterwards."""
        from datetime import datetime, timezone

        from dotenv import dotenv_values as dv
        from pymongo import MongoClient

        be = dv("/app/backend/.env")
        client = MongoClient(be["MONGO_URL"])
        col = client[be["DB_NAME"]].bot_broadcasts
        doc_id = "TEST_hist_iter17"
        col.delete_one({"_id": doc_id})
        now = datetime.now(timezone.utc)
        col.insert_one(
            {
                "_id": doc_id,
                "text": "TEST_history_iter17 " + ("x" * 120),
                "media_filename": None,
                "status": "done",
                "sent": 7,
                "failed": 2,
                "total": 9,
                "error_counts": {"blocked the bot": 1, "chat not found": 1},
                "created_at": now.isoformat(),
                "completed_at": now.isoformat(),
            }
        )
        try:
            data = requests.get(
                HISTORY, headers={"Authorization": f"Bearer {token}"}, timeout=30
            ).json()
            top = data[0]
            assert top["id"] == doc_id, f"newest job not first: {top['id']}"
            assert len(top["text_preview"]) == 80
            assert top["sent"] == 7 and top["failed"] == 2 and top["total"] == 9
            assert top["error_counts"]["blocked the bot"] == 1
            assert top["completed_at"] is not None
        finally:
            col.delete_one({"_id": doc_id})
            client.close()
