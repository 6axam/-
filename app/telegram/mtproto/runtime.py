"""The deliberately narrow first live MTProto bridge: private allowlisted text."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.actions.models import ActionType
from app.actions.timing import TimingEngine
from app.telegram.text import strip_emoji
from app.telegram.transport import InboundKind, NormalizedInboundEvent

log = logging.getLogger(__name__)


def logical_chat_id(peer_id: int) -> int:
    """Keep MTProto private histories disjoint from Bot API's positive chat IDs."""
    if peer_id <= 0:
        raise ValueError("MTProto private peer_id must be positive")
    return -peer_id


def peer_id_for_chat(chat_id: int) -> int:
    if chat_id >= 0:
        raise ValueError("MTProto executor only accepts its own negative logical chat IDs")
    return -chat_id


@dataclass(frozen=True)
class MTProtoBufferedMessage:
    message_id: int
    text: str
    user_id: int
    username: str | None = None
    caption: str | None = None

    @property
    def from_user(self):
        return type("MTProtoUser", (), {"id": self.user_id, "username": self.username})()


class MTProtoInboundBridge:
    """Persist a normalized event, then hand it to the existing debounce flow."""

    def __init__(self, db, buffer, manager, lifecycle=None):
        self.db = db
        self.buffer = buffer
        self.manager = manager
        self.lifecycle = lifecycle

    async def receive(self, event: NormalizedInboundEvent, username: str | None = None) -> bool:
        if event.transport != "mtproto" or event.kind != InboundKind.text:
            return False
        if not event.sender_id or not event.message_id or not event.peer_id or not event.text:
            return False
        chat_id = logical_chat_id(event.peer_id)
        await self.db.ensure_user(event.sender_id, username)
        inserted = await self.db.record_message(
            chat_id=chat_id,
            telegram_message_id=event.message_id,
            user_id=event.sender_id,
            sender="user",
            kind="text",
            text=event.text,
            reply_to=event.reply_to_message_id,
        )
        if inserted is None:
            return False
        await self.manager.interrupt(chat_id)
        if self.lifecycle:
            await self.lifecycle.on_user_message(chat_id)
        await self.buffer.add(chat_id, MTProtoBufferedMessage(event.message_id, event.text, event.sender_id, username))
        return True


class MTProtoActionExecutor:
    """Execute only the tested text subset of Anya's actions through Telethon."""

    typing_refresh_seconds = 4.0

    def __init__(self, transport, db, lifecycle=None):
        self.transport = transport
        self.db = db
        self.lifecycle = lifecycle
        self.timing = TimingEngine()

    async def _typing(self, chat_id: int) -> None:
        client = await self.transport.connect()
        peer_id = peer_id_for_chat(chat_id)
        try:
            from telethon.tl.functions.messages import SetTypingRequest
            from telethon.tl.types import SendMessageTypingAction
            peer = await self.transport.resolve_peer(peer_id)
            await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
        except Exception:
            log.debug("mtproto_typing_failed peer_id=%s", peer_id, exc_info=True)

    async def start_generation_typing(self, chat_id: int):
        await self._typing(chat_id)

        async def refresh():
            while True:
                await asyncio.sleep(self.typing_refresh_seconds)
                await self._typing(chat_id)

        return asyncio.create_task(refresh())

    async def stop_generation_typing(self, session) -> None:
        session.cancel()
        try:
            await session
        except asyncio.CancelledError:
            pass

    async def execute(self, item) -> None:
        action = item.action
        if action.type == ActionType.pause:
            delay = self.timing.between_messages(action.text or "") if action.automatic else self.timing.pause(action.duration)
            await asyncio.sleep(delay)
            return
        if action.type in {ActionType.silence, ActionType.reaction, ActionType.sticker, ActionType.image, ActionType.voice_message}:
            log.info("mtproto_action_skipped action_type=%s reason=text_only_test_runtime", action.type.value)
            return
        if action.type != ActionType.text or not action.text:
            return
        text = strip_emoji(action.text)
        if not text.strip():
            return
        await self._typing(item.chat_id)
        await asyncio.sleep(self.timing.typing_seconds(text))
        client = await self.transport.connect()
        sent = await client.send_message(
            await self.transport.resolve_peer(peer_id_for_chat(item.chat_id)), text,
            reply_to=action.reply_to_message_id,
        )
        await self.db.record_message(
            chat_id=item.chat_id,
            telegram_message_id=sent.id,
            sender="assistant",
            kind="text",
            text=text,
        )
        if self.lifecycle:
            await self.lifecycle.on_bot_message(item.chat_id)
