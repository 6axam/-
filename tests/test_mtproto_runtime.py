from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.actions.models import Action, ActionType, ImageIntent, ImageKind, QueuedAction, VoiceIntent
from app.database.db import Database
from app.images import GeneratedImage
from app.telegram.mtproto.client import MTProtoSettings
from app.telegram.mtproto.runtime import (
    MTProtoActionExecutor,
    MTProtoInboundBridge,
    MTProtoStickerDocuments,
    logical_chat_id,
    peer_id_for_chat,
)
from app.telegram.transport import InboundKind, NormalizedInboundEvent


class _Buffer:
    def __init__(self):
        self.items = []

    async def add(self, chat_id, message, user_content=None):
        self.items.append((chat_id, message, user_content))


class _Manager:
    def __init__(self):
        self.interrupted = []

    async def interrupt(self, chat_id):
        self.interrupted.append(chat_id)


class _Lifecycle:
    def __init__(self):
        self.user_messages = []
        self.bot_messages = []

    async def on_user_message(self, chat_id):
        self.user_messages.append(chat_id)

    async def on_bot_message(self, chat_id):
        self.bot_messages.append(chat_id)


def _event(*, message_id=9, text="привет"):
    return NormalizedInboundEvent(
        transport="mtproto", account_id=99, peer_id=42, sender_id=42,
        message_id=message_id, kind=InboundKind.text,
        occurred_at=datetime.now(timezone.utc), text=text,
    )


async def test_mtproto_bridge_uses_separate_logical_chat_and_deduplicates(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'mtproto.sqlite'}")
    await db.connect()
    buffer, manager, lifecycle = _Buffer(), _Manager(), _Lifecycle()
    bridge = MTProtoInboundBridge(db, buffer, manager, lifecycle)

    assert await bridge.receive(_event(), "owner") is True
    assert await bridge.receive(_event(), "owner") is False

    assert manager.interrupted == [-42]
    assert lifecycle.user_messages == [-42]
    assert [(chat_id, item.text) for chat_id, item, _ in buffer.items] == [(-42, "привет")]
    rows = await db.fetchall("SELECT chat_id,user_id,text FROM messages")
    assert [tuple(row) for row in rows] == [(-42, 42, "привет")]
    await db.close()


def test_mtproto_private_chat_namespace_cannot_mix_with_bot_api_chats():
    assert logical_chat_id(42) == -42
    assert peer_id_for_chat(-42) == 42
    with pytest.raises(ValueError):
        logical_chat_id(0)
    with pytest.raises(ValueError):
        peer_id_for_chat(42)


class _Client:
    def __init__(self):
        self.sent = []
        self.files = []
        self.requests = []

    async def get_input_entity(self, peer_id):
        return peer_id

    async def __call__(self, _request):
        self.requests.append(_request)
        return None

    async def send_message(self, peer_id, text, reply_to=None):
        self.sent.append((peer_id, text, reply_to))
        return SimpleNamespace(id=91)

    async def send_file(self, peer_id, file, **kwargs):
        self.files.append((peer_id, file, kwargs))
        return SimpleNamespace(id=92 + len(self.files))


class _Transport:
    def __init__(self):
        self.client = _Client()

    async def connect(self):
        return self.client

    async def resolve_peer(self, peer_id):
        return peer_id

    async def account_id(self):
        return 99


async def test_mtproto_executor_sends_text_to_raw_peer_and_persists_under_namespace(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'mtproto.sqlite'}")
    await db.connect()
    transport, lifecycle = _Transport(), _Lifecycle()
    executor = MTProtoActionExecutor(transport, db, lifecycle=lifecycle)
    executor.timing.typing_seconds = lambda _text: 0
    item = QueuedAction(chat_id=-42, generation_id="g", action=Action(type=ActionType.text, text="привет"))

    await executor.execute(item)

    assert transport.client.sent == [(42, "привет", None)]
    assert lifecycle.bot_messages == [-42]
    row = await db.fetchone("SELECT chat_id,telegram_message_id,text FROM messages")
    assert tuple(row) == (-42, 91, "привет")
    state = await db.fetchone("SELECT status,direction,account_id,peer_id FROM telegram_message_states")
    assert tuple(state) == ("sent", "outgoing", 99, 42)
    await db.close()


def test_live_mtproto_runtime_requires_allowlist_and_safe_switches():
    settings = MTProtoSettings(
        _env_file=None,
        telegram_api_id=1, telegram_api_hash="a" * 32,
        telegram_mtproto_enabled=True, telegram_mtproto_dry_run=False,
        telegram_allowlist="42",
    )
    settings.require_live_runtime()

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWLIST"):
        MTProtoSettings(
            _env_file=None,
            telegram_api_id=1, telegram_api_hash="a" * 32,
            telegram_mtproto_enabled=True, telegram_mtproto_dry_run=False,
        ).require_live_runtime()


class _Stickers:
    def __init__(self, db):
        self.db = db
        self.usage = []

    async def file_id(self, sticker_id):
        return f"mtproto:{sticker_id}"

    async def resolve_intent(self, _intent, _chat_id):
        return None

    async def record_usage(self, sticker_id, chat_id, direction):
        self.usage.append((sticker_id, chat_id, direction))


async def test_mtproto_executor_reaction_sticker_photo_and_voice_use_existing_queue_actions(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'actions.sqlite'}")
    await db.connect()
    transport = _Transport()
    stickers = _Stickers(db)
    documents = MTProtoStickerDocuments(db)
    await db.execute("INSERT INTO stickers(id,file_id,file_unique_id,emoji,sticker_type) VALUES(?,?,?,?,?)", (7, "mtproto:7", "mtproto:7", "🙂", "static"))
    await documents.save(7, SimpleNamespace(id=700, access_hash=701, file_reference=b"ref", mime_type="image/webp"))

    class Prompts:
        async def allowed(self, *_args): return True
        async def build(self, _chat, _intent): return "prompt", {"location": "home", "activity": "rest", "clothing": "tee"}, []

    class Images:
        name = "fake"; model = "fake"; supports_references = True
        async def generate(self, _prompt, _refs): return GeneratedImage(b"jpg", "image/jpeg", .01)

    class Voice:
        enabled = True
        async def generate(self, *_args, **_kwargs): return SimpleNamespace(data=b"ogg", mime_type="audio/ogg")

    executor = MTProtoActionExecutor(transport, db, stickers=stickers, image_provider=Images(), image_prompts=Prompts(), voice_provider=Voice(), sticker_documents=documents)
    executor.timing.typing_seconds = lambda _text: 0
    executor.timing.pause = lambda _duration: 0
    await executor.execute(QueuedAction(chat_id=-42, generation_id="r", action=Action(type=ActionType.reaction, emoji="❤️", target_message_id=9)))
    await executor.execute(QueuedAction(chat_id=-42, generation_id="s", action=Action(type=ActionType.sticker, sticker_id=7)))
    await executor.execute(QueuedAction(chat_id=-42, generation_id="i", action=Action(type=ActionType.image, image_intent=ImageIntent(kind=ImageKind.casual_photo, scene="home"))))
    await executor.execute(QueuedAction(chat_id=-42, generation_id="v", action=Action(type=ActionType.voice_message, voice_intent=VoiceIntent(text="привет"))))

    assert transport.client.requests  # reaction and typing use Telethon requests
    assert len(transport.client.files) == 3
    assert transport.client.files[2][2]["voice_note"] is True
    assert stickers.usage == [(7, -42, "outgoing")]
    assert [row["type"] for row in await db.fetchall("SELECT type FROM messages ORDER BY id")] == ["sticker", "photo", "voice"]
    await db.close()


async def test_mtproto_sticker_without_a_transport_document_is_safely_not_sent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'sticker.sqlite'}")
    await db.connect()
    transport = _Transport()
    executor = MTProtoActionExecutor(transport, db, stickers=_Stickers(db), sticker_documents=MTProtoStickerDocuments(db))

    await executor.execute(QueuedAction(chat_id=-42, generation_id="g", action=Action(type=ActionType.sticker, sticker_id=404)))

    assert transport.client.files == []
    assert await db.fetchall("SELECT * FROM messages") == []
    await db.close()
