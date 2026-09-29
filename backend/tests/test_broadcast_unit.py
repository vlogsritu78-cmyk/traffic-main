"""Isolated unit test for bot.broadcast_message() logic (iteration 11).
Mocks bot._bot_app with a fake Telegram Application so we can verify:
- correct send_photo/send_video/send_document method selection by content_type
- file is uploaded only once, file_id reused for subsequent recipients
- per-recipient exceptions don't abort the whole broadcast
- returns accurate {sent, failed, total} counts
Does NOT touch the real Telegram API or affect production's poller.
"""
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import dotenv_values

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

frontend_env = dotenv_values("/app/frontend/.env")

import bot as bot_module  # noqa: E402


class FakeMsg:
    def __init__(self, kind, file_id):
        if kind == "photo":
            self.photo = [types.SimpleNamespace(file_id=file_id)]
        elif kind == "video":
            self.video = types.SimpleNamespace(file_id=file_id)
        elif kind == "document":
            self.document = types.SimpleNamespace(file_id=file_id)


@pytest.fixture
def fake_bot_app(monkeypatch):
    fake_app = MagicMock()
    fake_app.bot.send_message = AsyncMock(return_value=None)
    fake_app.bot.send_photo = AsyncMock(return_value=FakeMsg("photo", "FILEID_PHOTO"))
    fake_app.bot.send_video = AsyncMock(return_value=FakeMsg("video", "FILEID_VIDEO"))
    fake_app.bot.send_document = AsyncMock(return_value=FakeMsg("document", "FILEID_DOC"))
    monkeypatch.setattr(bot_module, "_bot_app", fake_app)
    return fake_app


class _FakeCursor:
    """Mimics motor's find(...) async-iterable cursor without touching real Mongo,
    avoiding event-loop-per-test issues with the module-level Motor client."""

    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for d in self._docs:
            yield d


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *a, **k):
        return _FakeCursor(self._docs)


@pytest.fixture
def seeded_users(monkeypatch):
    """Fake 3 recipients so broadcast_message() (which queries db.bot_users) has
    users to iterate, without touching the real MongoDB event loop across tests."""
    uids = [900000001, 900000002, 900000003]
    docs = [{"user_id": uid} for uid in uids]
    monkeypatch.setattr(bot_module, "db", types.SimpleNamespace(bot_users=_FakeCollection(docs)))
    return uids


class TestBroadcastMessageUnit:
    @pytest.mark.asyncio
    async def test_no_bot_app_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(bot_module, "_bot_app", None)
        with pytest.raises(RuntimeError):
            await bot_module.broadcast_message("hello", None)

    @pytest.mark.asyncio
    async def test_text_only_calls_send_message_for_each_user(self, fake_bot_app, seeded_users):
        result = await bot_module.broadcast_message("hello everyone", None)
        assert result["total"] >= len(seeded_users)
        assert result["sent"] >= len(seeded_users) - result["failed"]
        assert fake_bot_app.bot.send_message.call_count >= len(seeded_users)

    @pytest.mark.asyncio
    async def test_image_media_uses_send_photo_and_reuses_file_id(self, fake_bot_app, seeded_users):
        media = (b"fakebytes", "image/png", "pic.png")
        result = await bot_module.broadcast_message("caption", media)
        assert fake_bot_app.bot.send_photo.call_count >= len(seeded_users)
        # first call uploads raw bytes (BytesIO), subsequent calls reuse file_id string
        calls = fake_bot_app.bot.send_photo.call_args_list
        first_payload = calls[0].kwargs.get("photo")
        assert not isinstance(first_payload, str)
        if len(calls) > 1:
            second_payload = calls[1].kwargs.get("photo")
            assert second_payload == "FILEID_PHOTO"
        assert result["sent"] + result["failed"] == result["total"]

    @pytest.mark.asyncio
    async def test_video_media_uses_send_video(self, fake_bot_app, seeded_users):
        media = (b"fakebytes", "video/mp4", "clip.mp4")
        await bot_module.broadcast_message("", media)
        assert fake_bot_app.bot.send_video.call_count >= len(seeded_users)
        assert fake_bot_app.bot.send_photo.call_count == 0
        assert fake_bot_app.bot.send_document.call_count == 0

    @pytest.mark.asyncio
    async def test_other_media_falls_back_to_send_document(self, fake_bot_app, seeded_users):
        media = (b"fakebytes", "application/pdf", "doc.pdf")
        await bot_module.broadcast_message("", media)
        assert fake_bot_app.bot.send_document.call_count >= len(seeded_users)

    @pytest.mark.asyncio
    async def test_per_recipient_exception_does_not_abort_broadcast(self, fake_bot_app, seeded_users):
        # Make the 2nd call fail, others succeed
        call_count = {"n": 0}

        async def flaky_send(uid, text):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise Exception("blocked by user")
            return None

        fake_bot_app.bot.send_message = AsyncMock(side_effect=flaky_send)
        result = await bot_module.broadcast_message("hi", None)
        assert result["failed"] >= 1
        assert result["sent"] + result["failed"] == result["total"]
        # all recipients attempted despite one failure
        assert call_count["n"] == result["total"]
