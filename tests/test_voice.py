from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.actions.models import Action, ActionType, QueuedAction, VoiceIntent
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.database.db import Database
from app.telegram.executor import TelegramActionExecutor
from app.voice import DisabledVoiceProvider, VoiceGenerationResult, VoiceProvider
from app.voice.byteplus_seed import BytePlusSeedAudioProvider, VoiceProviderError
import base64
import httpx
import json


def test_voice_action_requires_voice_intent():
    with pytest.raises(ValidationError):
        Action(type=ActionType.voice_message)
    action = Action(type=ActionType.voice_message, voice_intent=VoiceIntent(text="блин ну короче да"))
    assert action.voice_intent.text


async def test_voice_tendency_reaches_system_prompt(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'voice.sqlite'}"); await db.connect()
    system, _, breakdown = await ContextBuilder(db, voice_message_tendency=.20, voice_message_available=True).build_with_breakdown(1, 10, "привет")
    assert "voice_message_tendency=0.20" in system
    assert breakdown["components"]["action_tendencies"]["tokens"] > 0
    disabled, _, _ = await ContextBuilder(db, voice_message_tendency=.20, voice_message_available=False).build_with_breakdown(1, 10, "привет")
    assert "voice_message_available=false" in disabled and "voice_message_tendency=0.00" in disabled
    await db.close()


class FakeVoiceProvider(VoiceProvider):
    name = "fake"
    def __init__(self): self.calls = []
    async def generate(self, text, *, mood=None, pace=None, energy=.5):
        self.calls.append((text, mood, pace, energy))
        return VoiceGenerationResult(b"ogg-bytes", "audio/ogg")


class VoiceBot:
    def __init__(self): self.sent = []
    async def send_voice(self, chat_id, *, voice):
        self.sent.append((chat_id, voice.data, voice.filename))
        return SimpleNamespace(message_id=77)
    async def send_chat_action(self, *_args): pass


class Lifecycle:
    def __init__(self): self.calls = []
    async def on_bot_message(self, chat_id): self.calls.append(chat_id)


async def test_voice_executor_sends_persists_and_updates_lifecycle(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'voice.sqlite'}"); await db.connect()
    bot, provider, lifecycle = VoiceBot(), FakeVoiceProvider(), Lifecycle()
    executor = TelegramActionExecutor(bot, db, voice_provider=provider, lifecycle=lifecycle)
    action = Action(type=ActionType.voice_message, voice_intent=VoiceIntent(text="ну я почти сплю уже", mood="sleepy", pace="slow", energy=.2))
    await executor.execute(QueuedAction(chat_id=10, generation_id="g", action=action))
    assert provider.calls == [("ну я почти сплю уже", "sleepy", "slow", .2)]
    assert bot.sent == [(10, b"ogg-bytes", "anya.ogg")]
    row = await db.fetchone("SELECT sender,type,text FROM messages WHERE chat_id=10")
    assert dict(row) == {"sender": "assistant", "type": "voice", "text": "ну я почти сплю уже"}
    assert lifecycle.calls == [10]
    await db.close()


async def test_disabled_or_failed_voice_safely_skips(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'voice.sqlite'}"); await db.connect()
    bot = VoiceBot(); action = Action(type=ActionType.voice_message, voice_intent=VoiceIntent(text="голос"))
    await TelegramActionExecutor(bot, db, voice_provider=DisabledVoiceProvider()).execute(QueuedAction(chat_id=10, generation_id="g", action=action))
    class Failing(VoiceProvider):
        async def generate(self, *_args, **_kwargs): raise RuntimeError("offline")
    await TelegramActionExecutor(bot, db, voice_provider=Failing()).execute(QueuedAction(chat_id=10, generation_id="g", action=action))
    assert bot.sent == []
    assert await db.fetchall("SELECT * FROM messages") == []
    await db.close()


async def test_queue_keeps_voice_as_one_action_without_text_cadence_split():
    class Executor:
        def __init__(self): self.actions = []
        async def execute(self, item): self.actions.append(item.action.type)
    executor = Executor(); queue = ActionQueue(executor)
    await queue.enqueue_many(10, "g", [Action(type=ActionType.voice_message, voice_intent=VoiceIntent(text="ну да"))])
    import asyncio; await asyncio.sleep(.02)
    assert executor.actions == [ActionType.voice_message]


async def test_byteplus_provider_builds_request_and_decodes_audio(tmp_path):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    seen = {}
    async def handler(request):
        seen["headers"], seen["json"] = dict(request.headers), json.loads(request.content)
        return httpx.Response(200, json={"data": {"audio": base64.b64encode(b"ogg").decode(), "duration": 1.5}})
    provider = BytePlusSeedAudioProvider("secret-key", "seed-audio-1.0", "https://example.test/create", reference, transport=httpx.MockTransport(handler))
    result = await provider.generate("ну да", mood="sleepy", pace="slow", energy=.2)
    assert result.data == b"ogg" and result.mime_type == "audio/ogg" and result.duration_seconds == 1.5
    assert seen["headers"]["x-api-key"] == "secret-key" and seen["headers"]["x-api-request-id"]
    assert seen["json"]["model"] == "seed-audio-1.0" and "@Audio1" in seen["json"]["text_prompt"]
    assert seen["json"]["references"][0]["audio_data"] == base64.b64encode(b"reference").decode()
    assert seen["json"]["audio_config"]["format"] == "ogg_opus" and seen["json"]["audio_config"]["sample_rate"] == 48000


async def test_byteplus_malformed_response_is_controlled(tmp_path):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    provider = BytePlusSeedAudioProvider("key", "seed-audio-1.0", "https://example.test/create", reference, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {}})))
    with pytest.raises(VoiceProviderError): await provider.generate("ну да")
