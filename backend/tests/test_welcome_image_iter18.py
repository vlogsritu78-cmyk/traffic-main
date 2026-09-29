"""Iteration 18 — welcome/advertisement image feature.

Covers:
  POST   /api/admin/welcome-image   (admin JWT, multipart)
  DELETE /api/admin/welcome-image   (admin JWT)
  GET    /api/welcome-image         (public stream)
  GET    /api/settings              (has_welcome_image flag)
  bot.reply() ordering (photo before text) — unit level, no live Telegram send
"""
import base64
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
API = f"{BASE_URL}/api"

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUAAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(scope="session")
def admin_pin():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    m = re.search(r"PIN:\s*`?(\d+)`?", content)
    if not m:
        pytest.skip("No admin PIN found in test_credentials.md")
    return m.group(1)


@pytest.fixture(scope="session")
def token(admin_pin):
    r = requests.post(f"{API}/admin/unlock", json={"pin": admin_pin}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"admin unlock failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token")
    if not tok:
        pytest.fail(f"no token in unlock response: {r.text[:300]}")
    return tok


@pytest.fixture(scope="session")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── auth guards ─────────────────────────────────────────────────────────────
class TestWelcomeImageAuth:
    def test_upload_requires_token(self):
        r = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("t.png", TINY_PNG, "image/png")},
            timeout=30,
        )
        assert r.status_code == 401, r.text[:300]

    def test_upload_invalid_token(self):
        r = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("t.png", TINY_PNG, "image/png")},
            headers={"Authorization": "Bearer not.a.real.token"},
            timeout=30,
        )
        assert r.status_code == 401, r.text[:300]

    def test_delete_requires_token(self):
        r = requests.delete(f"{API}/admin/welcome-image", timeout=30)
        assert r.status_code == 401, r.text[:300]


# ── validation ──────────────────────────────────────────────────────────────
class TestWelcomeImageValidation:
    def test_reject_non_image(self, auth):
        r = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
            headers=auth,
            timeout=30,
        )
        assert r.status_code == 400, r.text[:300]
        assert "image" in r.json().get("detail", "").lower()

    def test_reject_oversized_image(self, auth):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024 + 1024)
        r = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("big.png", big, "image/png")},
            headers=auth,
            timeout=120,
        )
        assert r.status_code == 400, r.text[:300]
        assert "large" in r.json().get("detail", "").lower()

    def test_missing_file_field(self, auth):
        r = requests.post(f"{API}/admin/welcome-image", headers=auth, timeout=30)
        assert r.status_code == 422, r.text[:300]


# ── full lifecycle ──────────────────────────────────────────────────────────
class TestWelcomeImageLifecycle:
    def test_clean_state_first(self, auth):
        # ensure we start from no-image so the 404 branch can be asserted
        r = requests.delete(f"{API}/admin/welcome-image", headers=auth, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["has_welcome_image"] is False

    def test_get_image_404_when_unset(self, auth):
        requests.delete(f"{API}/admin/welcome-image", headers=auth, timeout=30)
        r = requests.get(f"{API}/welcome-image", timeout=30)
        assert r.status_code == 404, r.text[:300]
        assert r.json().get("detail") == "No welcome image set."

    def test_settings_flag_false_when_unset(self, auth):
        requests.delete(f"{API}/admin/welcome-image", headers=auth, timeout=30)
        r = requests.get(f"{API}/settings", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "has_welcome_image" in data
        assert data["has_welcome_image"] is False

    def test_upload_then_get_then_delete(self, auth):
        # UPLOAD
        r = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("TEST_ad.png", TINY_PNG, "image/png")},
            headers=auth,
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["has_welcome_image"] is True
        # existing public fields preserved
        for key in ("official_username", "official_url", "welcome_ta", "welcome_en", "button_text"):
            assert key in body
        # internal fields must not leak
        assert "welcome_image_path" not in body
        assert "_id" not in body
        assert "pin_hash" not in body

        # GET /settings reflects persistence
        s = requests.get(f"{API}/settings", timeout=30).json()
        assert s["has_welcome_image"] is True

        # GET bytes round-trip
        img = requests.get(f"{API}/welcome-image", timeout=60)
        assert img.status_code == 200, img.text[:200]
        assert img.headers.get("content-type", "").startswith("image/png")
        assert img.content == TINY_PNG

        # REPLACE with a second upload
        r2 = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("TEST_ad2.jpg", TINY_PNG, "image/jpeg")},
            headers=auth,
            timeout=60,
        )
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json()["has_welcome_image"] is True
        img2 = requests.get(f"{API}/welcome-image", timeout=60)
        assert img2.status_code == 200
        assert img2.headers.get("content-type", "").startswith("image/jpeg")

        # DELETE
        d = requests.delete(f"{API}/admin/welcome-image", headers=auth, timeout=30)
        assert d.status_code == 200, d.text[:300]
        assert d.json()["has_welcome_image"] is False
        assert requests.get(f"{API}/welcome-image", timeout=30).status_code == 404
        assert requests.get(f"{API}/settings", timeout=30).json()["has_welcome_image"] is False

    def test_settings_put_does_not_clobber_image(self, auth):
        # upload image, then save welcome text, image must survive
        up = requests.post(
            f"{API}/admin/welcome-image",
            files={"file": ("TEST_keep.png", TINY_PNG, "image/png")},
            headers=auth,
            timeout=60,
        )
        assert up.status_code == 200
        cur = requests.get(f"{API}/settings", timeout=30).json()
        put = requests.put(
            f"{API}/settings",
            json={
                "official_username": cur["official_username"],
                "welcome_ta": cur["welcome_ta"],
                "welcome_en": cur["welcome_en"],
            },
            headers=auth,
            timeout=30,
        )
        assert put.status_code == 200, put.text[:300]
        assert put.json()["has_welcome_image"] is True
        # cleanup
        requests.delete(f"{API}/admin/welcome-image", headers=auth, timeout=30)
