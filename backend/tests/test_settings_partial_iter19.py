"""Iteration 19 — PUT /api/settings TRUE PARTIAL UPDATE regression tests.

Bug under test (reported twice): editing welcome text raised the username
format error, and editing the username raised the welcome-text error.
"""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
PIN = "246810"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(client):
    r = client.post(f"{BASE_URL}/api/admin/unlock", json={"pin": PIN}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"unlock failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token")
    assert isinstance(tok, str) and tok
    return tok


@pytest.fixture(scope="module")
def auth(client, token):
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture(scope="module")
def original(client):
    r = client.get(f"{BASE_URL}/api/settings", timeout=30)
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def restore(client, token, original):
    """Restore the demo state after the module finishes."""
    yield
    client.headers.update({"Authorization": f"Bearer {token}"})
    r = client.put(
        f"{BASE_URL}/api/settings",
        json={
            "official_username": original["official_username"],
            "welcome_ta": original["welcome_ta"],
            "welcome_en": original["welcome_en"],
        },
        timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    now = client.get(f"{BASE_URL}/api/settings", timeout=30).json()
    assert now["official_username"] == original["official_username"]
    assert now["welcome_ta"] == original["welcome_ta"]
    assert now["welcome_en"] == original["welcome_en"]
    assert now["official_url"] == original["official_url"]


def get_settings(client):
    r = client.get(f"{BASE_URL}/api/settings", timeout=30)
    assert r.status_code == 200
    return r.json()


# (a) welcome-text-only PUT must not touch/validate username
def test_welcome_only_patch_keeps_username(auth, original):
    ta = original["welcome_ta"] + "\nTEST_TA_EDIT"
    en = original["welcome_en"] + "\nTEST_EN_EDIT"
    r = auth.put(f"{BASE_URL}/api/settings", json={"welcome_ta": ta, "welcome_en": en}, timeout=30)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["welcome_ta"] == ta
    assert body["welcome_en"] == en
    assert body["official_username"] == original["official_username"]
    assert body["official_url"] == original["official_url"]

    after = get_settings(auth)
    assert after["welcome_ta"] == ta
    assert after["official_username"] == original["official_username"]
    assert after["official_url"] == original["official_url"]


# (b) username-only PUT must not touch/validate welcome text
def test_username_only_patch_keeps_welcome(auth):
    before = get_settings(auth)
    new_user = "TEST_iter19_user"[:32].replace("-", "_")
    r = auth.put(f"{BASE_URL}/api/settings", json={"official_username": new_user}, timeout=30)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["official_username"] == new_user
    assert body["official_url"] == f"https://t.me/{new_user}?text=hi"
    assert body["welcome_ta"] == before["welcome_ta"]
    assert body["welcome_en"] == before["welcome_en"]

    after = get_settings(auth)
    assert after["official_username"] == new_user
    assert after["welcome_ta"] == before["welcome_ta"]
    assert after["welcome_en"] == before["welcome_en"]


@pytest.mark.parametrize("bad", ["ab", "has spaces", "bad!name", "a" * 33])
def test_invalid_username_only_does_not_touch_welcome(auth, bad):
    before = get_settings(auth)
    r = auth.put(f"{BASE_URL}/api/settings", json={"official_username": bad}, timeout=30)
    assert r.status_code == 400, f"expected 400 for {bad!r}, got {r.status_code}"
    assert "username" in r.json()["detail"].lower()
    after = get_settings(auth)
    assert after["welcome_ta"] == before["welcome_ta"]
    assert after["welcome_en"] == before["welcome_en"]
    assert after["official_username"] == before["official_username"]


@pytest.mark.parametrize("payload_key", ["welcome_ta", "welcome_en"])
def test_empty_welcome_block_does_not_touch_username(auth, payload_key):
    before = get_settings(auth)
    r = auth.put(f"{BASE_URL}/api/settings", json={payload_key: "   "}, timeout=30)
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"] == "Both the Tamil and English blocks are required."
    after = get_settings(auth)
    assert after["official_username"] == before["official_username"]
    assert after["official_url"] == before["official_url"]
    assert after["welcome_ta"] == before["welcome_ta"]
    assert after["welcome_en"] == before["welcome_en"]


# (e) backward compatible full payload
def test_full_payload_still_works(auth, original):
    payload = {
        "official_username": "TEST_full_iter19",
        "welcome_ta": "TEST full TA",
        "welcome_en": "TEST full EN",
    }
    r = auth.put(f"{BASE_URL}/api/settings", json=payload, timeout=30)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["official_username"] == payload["official_username"]
    assert body["welcome_ta"] == "TEST full TA"
    assert body["welcome_en"] == "TEST full EN"
    after = get_settings(auth)
    assert after["official_username"] == payload["official_username"]
    assert after["welcome_en"] == "TEST full EN"


def test_empty_body_rejected(auth):
    before = get_settings(auth)
    r = auth.put(f"{BASE_URL}/api/settings", json={}, timeout=30)
    assert r.status_code == 400
    assert r.json()["detail"] == "Nothing to update."
    after = get_settings(auth)
    assert after == {**before, "updated_at": after["updated_at"]}


def test_username_accepts_at_and_tme_link(auth):
    r = auth.put(f"{BASE_URL}/api/settings", json={"official_username": "https://t.me/TEST_link19?x=1"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["official_username"] == "TEST_link19"
    r = auth.put(f"{BASE_URL}/api/settings", json={"official_username": "@TEST_at19"}, timeout=30)
    assert r.status_code == 200
    assert r.json()["official_username"] == "TEST_at19"


def test_put_requires_auth(client):
    s = requests.Session()
    r = s.put(f"{BASE_URL}/api/settings", json={"welcome_ta": "x", "welcome_en": "y"}, timeout=30)
    assert r.status_code == 401
    assert "PIN" in r.json()["detail"]


# ── regression: other admin endpoints unaffected ────────────────────────────
def test_bot_status_ok(client):
    r = client.get(f"{BASE_URL}/api/bot/status", timeout=30)
    assert r.status_code == 200
    d = r.json()
    for k in ("online", "bot_username", "official_url", "total_starts", "unique_users"):
        assert k in d


def test_broadcast_history_and_latest(auth):
    r = auth.get(f"{BASE_URL}/api/admin/broadcast/history", timeout=30)
    assert r.status_code == 200
    jobs = r.json()
    assert isinstance(jobs, list)
    for j in jobs:
        assert "_id" not in j
        assert "id" in j and "status" in j
    r2 = auth.get(f"{BASE_URL}/api/admin/broadcast/latest", timeout=30)
    assert r2.status_code == 200


def test_broadcast_validation_no_live_send(auth):
    """Empty broadcast must 400 (never triggers a real send)."""
    mp = requests.Session()
    mp.headers.update({"Authorization": auth.headers["Authorization"]})
    r = mp.post(f"{BASE_URL}/api/admin/broadcast", data={"message": "   "}, timeout=30)
    assert r.status_code in (400, 503), r.text[:300]


def test_change_pin_roundtrip(auth, client):
    r = auth.post(f"{BASE_URL}/api/admin/pin", json={"new_pin": "999111"}, timeout=30)
    assert r.status_code == 200 and r.json() == {"updated": True}
    fresh = requests.Session()
    r2 = fresh.post(f"{BASE_URL}/api/admin/unlock", json={"pin": "999111"}, timeout=30)
    assert r2.status_code == 200, r2.text[:300]
    new_tok = r2.json()["token"]
    # restore original PIN
    r3 = fresh.post(
        f"{BASE_URL}/api/admin/pin",
        json={"new_pin": PIN},
        headers={"Authorization": f"Bearer {new_tok}"},
        timeout=30,
    )
    assert r3.status_code == 200
    r4 = requests.post(f"{BASE_URL}/api/admin/unlock", json={"pin": PIN}, timeout=30)
    assert r4.status_code == 200, "original PIN not restored!"


def test_welcome_image_endpoints(auth):
    """Upload -> flag set -> remove; must not disturb settings text/username."""
    before = get_settings(auth)
    if before.get("has_welcome_image"):
        pytest.skip("a welcome image is already set in this env — not touching demo state")
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    mp = requests.Session()  # separate session: no JSON Content-Type header
    mp.headers.update({"Authorization": auth.headers["Authorization"]})
    up = mp.post(
        f"{BASE_URL}/api/admin/welcome-image",
        files={"file": ("TEST_iter19.png", png, "image/png")},
        timeout=60,
    )
    if up.status_code != 200:
        pytest.fail(f"welcome-image upload failed {up.status_code}: {up.text[:300]}")
    assert up.json()["has_welcome_image"] is True
    assert up.json()["official_username"] == before["official_username"]
    assert up.json()["welcome_ta"] == before["welcome_ta"]

    img = requests.get(f"{BASE_URL}/api/welcome-image", timeout=60)
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")

    rm = auth.delete(f"{BASE_URL}/api/admin/welcome-image", timeout=60)
    assert rm.status_code == 200
    assert rm.json()["has_welcome_image"] is False
    after = get_settings(auth)
    assert after["welcome_ta"] == before["welcome_ta"]
    assert after["official_username"] == before["official_username"]


def test_bad_image_type_rejected(auth):
    mp = requests.Session()
    mp.headers.update({"Authorization": auth.headers["Authorization"]})
    r = mp.post(
        f"{BASE_URL}/api/admin/welcome-image",
        files={"file": ("t.txt", b"hello", "text/plain")},
        timeout=30,
    )
    assert r.status_code == 400
    assert "image" in r.json()["detail"].lower()
