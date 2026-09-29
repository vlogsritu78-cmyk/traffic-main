"""
Iteration 8 follow-up: re-verify admin PIN unlock + settings write flow after
main agent reset bot_settings.pin_hash to bcrypt('246810') and cleared
db.admin_attempts. Scope: unlock, PUT /settings auth, wrong-PIN behavior,
bot status sanity. Bridge/bot.py stability already confirmed in iteration_7.
"""
import os
import requests
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
_base = os.environ.get("REACT_APP_BACKEND_URL") or _env.get("REACT_APP_BACKEND_URL")
if not _base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
BASE_URL = _base.rstrip("/")
API = f"{BASE_URL}/api"
CORRECT_PIN = "246810"


def test_unlock_with_correct_pin_returns_token():
    resp = requests.post(f"{API}/admin/unlock", json={"pin": CORRECT_PIN})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 20
    assert data.get("expires_in_hours") == 8 or "expires_in_hours" in data


def test_settings_put_without_auth_returns_401():
    resp = requests.put(
        f"{API}/settings",
        json={"official_username": "should_not_work", "welcome_ta": "x", "welcome_en": "y"},
    )
    assert resp.status_code == 401, resp.text


def test_wrong_pin_returns_401_then_correct_pin_still_works():
    resp = requests.post(f"{API}/admin/unlock", json={"pin": "000000"})
    assert resp.status_code == 401, resp.text
    assert "Wrong PIN" in resp.json().get("detail", "")

    resp2 = requests.post(f"{API}/admin/unlock", json={"pin": CORRECT_PIN})
    assert resp2.status_code == 200, resp2.text
    assert "token" in resp2.json()


def test_settings_put_with_auth_persists_and_is_restored():
    # get token
    unlock_resp = requests.post(f"{API}/admin/unlock", json={"pin": CORRECT_PIN})
    assert unlock_resp.status_code == 200
    token = unlock_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # fetch current settings to restore later
    get_resp = requests.get(f"{API}/settings")
    assert get_resp.status_code == 200
    original = get_resp.json()
    original_username = original["official_username"]
    original_ta = original["welcome_ta"]
    original_en = original["welcome_en"]

    try:
        test_username = "verifytest_iter8"
        put_resp = requests.put(
            f"{API}/settings",
            json={
                "official_username": test_username,
                "welcome_ta": original_ta,
                "welcome_en": original_en,
            },
            headers=headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        put_data = put_resp.json()
        assert put_data["official_username"] == test_username

        # confirm persistence via GET
        verify_resp = requests.get(f"{API}/settings")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["official_username"] == test_username
    finally:
        # restore original value
        restore_resp = requests.put(
            f"{API}/settings",
            json={
                "official_username": original_username,
                "welcome_ta": original_ta,
                "welcome_en": original_en,
            },
            headers=headers,
        )
        assert restore_resp.status_code == 200, restore_resp.text
        final_check = requests.get(f"{API}/settings")
        assert final_check.json()["official_username"] == original_username


def test_bot_status_still_online_no_regression():
    resp = requests.get(f"{API}/bot/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("online") is True
