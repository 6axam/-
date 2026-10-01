from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.actions.models import Action, ActionType, QueuedAction, VoiceIntent
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.config import Settings
from app.database.db import Database
from app.main import make_voice_provider
from app.telegram.executor import TelegramActionExecutor
from app.voice import DisabledVoiceProvider, OpenRouterSeedAudioProvider, VoiceGenerationResult, VoiceProvider, VoiceProviderError
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
    assert "backend can really generate and send a Telegram voice message" in system
    assert "direct voice request" in system or "скинь голосовуху" in system
    assert breakdown["components"]["action_tendencies"]["tokens"] > 0
    disabled, _, _ = await ContextBuilder(db, voice_message_tendency=.20, voice_message_available=False).build_with_breakdown(1, 10, "привет")
    assert "voice_message_available=false" in disabled and "voice_message_tendency=0.00" in disabled
    assert "Voice messaging is unavailable. Do not choose voice_message." in disabled
    await db.close()


class FakeVoiceProvider(VoiceProvider):
    name = "fake"
    def __init__(self): self.calls = []
    async def generate(self, text, *, mood=None, pace=None, energy=.5):
        self.calls.append((text, mood, pace, energy))
        return VoiceGenerationResult(b"mp3-bytes", "audio/mpeg")


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
    assert bot.sent == [(10, b"mp3-bytes", "anya.mp3")]
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


async def test_openrouter_seed_provider_uses_exact_voice_clone_contract(tmp_path, caplog):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    seen = {}
    async def handler(request):
        seen["headers"], seen["json"] = dict(request.headers), json.loads(request.content)
        return httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg", "x-generation-id": "gen-safe"})
    provider = OpenRouterSeedAudioProvider("secret-key", "bytedance-seed/seed-audio-1-0", "https://example.test/audio/speech", reference, transport=httpx.MockTransport(handler))
    result = await provider.generate("ну да", mood="sleepy", pace="slow", energy=.2)
    assert result == VoiceGenerationResult(b"mp3", "audio/mpeg", None)
    assert seen["headers"]["authorization"] == "Bearer secret-key"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["json"] == {
        "model": "bytedance-seed/seed-audio-1-0",
        "input": "ну да",
        "response_format": "mp3",
        "reference_audio": base64.b64encode(b"reference").decode(),
    }
    assert "references" not in seen["json"] and "audio_config" not in seen["json"]
    assert "voice" not in seen["json"] and "data:" not in seen["json"]["reference_audio"]
    assert "secret-key" not in caplog.text
    assert seen["json"]["reference_audio"] not in caplog.text
    assert "ну да" not in caplog.text


async def test_openrouter_seed_reference_is_cached(tmp_path):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    provider = OpenRouterSeedAudioProvider("key", "model", "https://example.test/audio/speech", reference, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg"})))
    await provider.generate("one")
    reference.unlink()
    await provider.generate("two")


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": {"type": "invalid_request", "message": "bad"}}),
    httpx.Response(502, text="upstream unavailable"),
])
async def test_openrouter_seed_errors_are_controlled(tmp_path, response):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    provider = OpenRouterSeedAudioProvider("key", "model", "https://example.test/audio/speech", reference, transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(VoiceProviderError):
        await provider.generate("ну да")


def test_voice_config_prefers_explicit_key_and_falls_back_to_openrouter_llm_key(tmp_path):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    shared = dict(_env_file=None, telegram_bot_token="token", owner_telegram_id=1, llm_provider="openrouter", llm_api_key="llm-key", voice_generation_enabled=True, voice_provider="openrouter_seed", anya_voice_reference=str(reference))
    fallback = make_voice_provider(Settings(**shared))
    assert isinstance(fallback, OpenRouterSeedAudioProvider) and fallback.api_key == "llm-key"
    explicit = make_voice_provider(Settings(**shared, voice_api_key="voice-key"))
    assert explicit.api_key == "voice-key"


def test_voice_config_fails_without_openrouter_key_or_with_wrong_provider(tmp_path):
    reference = tmp_path / "ref.wav"; reference.write_bytes(b"reference")
    common = dict(_env_file=None, telegram_bot_token="token", owner_telegram_id=1, llm_api_key="", voice_generation_enabled=True, anya_voice_reference=str(reference))
    with pytest.raises(ValidationError):
        Settings(**common, llm_provider="openrouter", voice_provider="openrouter_seed")
    with pytest.raises(ValidationError):
        Settings(**common, llm_provider="openai", voice_provider="byteplus_seed", voice_api_key="voice-key")
