"""Unit-level check of bot.reply(): photo must be sent BEFORE the welcome text
when a welcome image is configured, and skipped entirely when it is not.
No live Telegram calls — the Update/Message objects are fakes.
"""
import asyncio
import sys
import types

sys.path.insert(0, "/app/backend")

import bot as bot_module  # noqa: E402

CALLS = []


class FakeMessage:
    async def reply_photo(self, data, *a, **kw):
        CALLS.append(("photo", len(data) if data else 0))

    async def reply_text(self, text, *a, **kw):
        CALLS.append(("text", text[:40]))


class FakeUpdate:
    effective_message = FakeMessage()


async def run(has_image, storage_ok=True):
    CALLS.clear()
    base = {
        "official_url": "https://t.me/x",
        "official_username": "x",
        "welcome_ta": "TA",
        "welcome_en": "EN",
    }
    if has_image:
        base["welcome_image_path"] = "trafficstars-bot/welcome_image/fake.png"

    async def fake_settings():
        return base

    async def fake_get_file(path):
        if not storage_ok:
            raise RuntimeError("storage down")
        return b"\x89PNG" * 10

    orig_s, orig_f = bot_module.get_settings, bot_module.get_broadcast_file
    bot_module.get_settings = fake_settings
    bot_module.get_broadcast_file = fake_get_file
    try:
        await bot_module.reply(FakeUpdate())
    finally:
        bot_module.get_settings = orig_s
        bot_module.get_broadcast_file = orig_f
    return list(CALLS)


def test_photo_sent_before_text_when_image_set():
    calls = asyncio.run(run(True))
    kinds = [c[0] for c in calls]
    assert kinds == ["photo", "text"], calls


def test_text_only_when_no_image():
    calls = asyncio.run(run(False))
    assert [c[0] for c in calls] == ["text"], calls


def test_text_still_sent_when_image_fetch_fails():
    calls = asyncio.run(run(True, storage_ok=False))
    assert [c[0] for c in calls] == ["text"], calls
