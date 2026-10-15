from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.actions.models import Action, ActionType, QueuedAction
from app.database.db import Database
from app.telegram.mtproto.client import MTProtoSettings
from app.telegram.mtproto.runtime import (
    MTProtoActionExecutor,
    MTProtoInboundBridge,
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

    async def get_input_entity(self, peer_id):
        return peer_id

    async def __call__(self, _request):
        return None

    async def send_message(self, peer_id, text, reply_to=None):
        self.sent.append((peer_id, text, reply_to))
        return SimpleNamespace(id=91)


class _Transport:
    def __init__(self):
        self.client = _Client()

    async def connect(self):
        return self.client

    async def resolve_peer(self, peer_id):
        return peer_id


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
