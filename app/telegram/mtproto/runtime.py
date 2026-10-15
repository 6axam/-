"""The deliberately narrow first live MTProto bridge: private allowlisted text."""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from types import SimpleNamespace

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


class MTProtoStickerDocuments:
    """Map reusable Telegram documents to existing semantic sticker IDs."""

    def __init__(self, db):
        self.db = db

    async def save(self, sticker_id: int, document) -> None:
        document_id = getattr(document, "id", None)
        access_hash = getattr(document, "access_hash", None)
        file_reference = getattr(document, "file_reference", None)
        if document_id is None or access_hash is None or file_reference is None:
            raise ValueError("MTProto sticker document is missing reusable file metadata")
        await self.db.execute(
            "INSERT INTO mtproto_sticker_documents(sticker_id,document_id,access_hash,file_reference,mime_type) VALUES(?,?,?,?,?) "
            "ON CONFLICT(sticker_id) DO UPDATE SET document_id=excluded.document_id,access_hash=excluded.access_hash,"
            "file_reference=excluded.file_reference,mime_type=excluded.mime_type,updated_at=CURRENT_TIMESTAMP",
            (sticker_id, int(document_id), int(access_hash), bytes(file_reference), getattr(document, "mime_type", None)),
        )

    async def input_document(self, sticker_id: int):
        row = await self.db.fetchone(
            "SELECT document_id,access_hash,file_reference FROM mtproto_sticker_documents WHERE sticker_id=?", (sticker_id,)
        )
        if not row:
            return None
        from telethon.tl.types import InputDocument
        return InputDocument(row["document_id"], row["access_hash"], row["file_reference"])


class MTProtoInboundBridge:
    """Persist a normalized event, then hand it to the existing debounce flow."""

    def __init__(self, db, buffer, manager, lifecycle=None, stickers=None, sticker_documents=None):
        self.db = db
        self.buffer = buffer
        self.manager = manager
        self.lifecycle = lifecycle
        self.stickers = stickers
        self.sticker_documents = sticker_documents

    async def receive(self, event: NormalizedInboundEvent, username: str | None = None) -> bool:
        if event.transport != "mtproto" or event.kind not in {InboundKind.text, InboundKind.sticker}:
            return False
        if not event.sender_id or not event.message_id or not event.peer_id or (event.kind == InboundKind.text and not event.text):
            return False
        chat_id = logical_chat_id(event.peer_id)
        await self.db.ensure_user(event.sender_id, username)
        sticker_file_id = None
        if event.kind == InboundKind.sticker:
            document = event.media.get("document")
            if not document or not self.stickers or not self.sticker_documents:
                return False
            document_id = getattr(document, "id", None)
            access_hash = getattr(document, "access_hash", None)
            if document_id is None or access_hash is None:
                return False
            sticker_file_id = f"mtproto:{document_id}"
            stub = SimpleNamespace(
                file_id=sticker_file_id,
                file_unique_id=f"mtproto:{document_id}:{access_hash}",
                emoji=event.media.get("emoji") or "",
                set_name=None,
                is_animated=False,
                is_video=False,
            )
            sticker_id = await self.stickers.register_incoming(stub, queue_analysis=False)
            await self.sticker_documents.save(sticker_id, document)
            await self.stickers.seen(stub, chat_id)
        inserted = await self.db.record_message(
            chat_id=chat_id,
            telegram_message_id=event.message_id,
            user_id=event.sender_id,
            sender="user",
            kind=event.kind.value,
            text=event.text or None,
            sticker_file_id=sticker_file_id,
            reply_to=event.reply_to_message_id,
        )
        if inserted is None:
            return False
        await self.db.record_telegram_message_state(
            transport="mtproto", account_id=event.account_id, peer_type="user",
            peer_id=event.peer_id, message_id=event.message_id,
            direction="incoming", local_chat_id=chat_id, status="received",
            source="mtproto_inbound",
        )
        await self.manager.interrupt(chat_id)
        if self.lifecycle:
            await self.lifecycle.on_user_message(chat_id)
        text = event.text or "[стикер]"
        await self.buffer.add(chat_id, MTProtoBufferedMessage(event.message_id, text, event.sender_id, username))
        return True


class MTProtoActionExecutor:
    """Execute supported existing ActionQueue actions through Telethon."""

    typing_refresh_seconds = 4.0

    def __init__(self, transport, db, stickers=None, lifecycle=None, image_provider=None, image_prompts=None,
                 image_daily_limit=2, image_cooldown_hours=12, voice_provider=None, sticker_documents=None):
        self.transport = transport
        self.db = db
        self.stickers = stickers
        self.lifecycle = lifecycle
        self.image_provider = image_provider
        self.image_prompts = image_prompts
        self.image_daily_limit = image_daily_limit
        self.image_cooldown_hours = image_cooldown_hours
        self.voice_provider = voice_provider
        self.sticker_documents = sticker_documents
        self.timing = TimingEngine()
        self._image_generations = {}

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

    async def _send_text(self, item, text: str):
        client = await self.transport.connect()
        sent = await client.send_message(
            await self.transport.resolve_peer(peer_id_for_chat(item.chat_id)), text,
            reply_to=item.action.reply_to_message_id,
        )
        await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.id, sender="assistant", kind="text", text=text)
        await self._record_outgoing_state(item.chat_id, sent.id)
        if self.lifecycle:
            await self.lifecycle.on_bot_message(item.chat_id)
        return sent

    async def _record_outgoing_state(self, chat_id: int, message_id: int) -> None:
        """Record only a successful Telegram send; never infer recipient read."""
        account_id = getattr(self.transport, "account_id", None)
        if not account_id:
            return
        try:
            await self.db.record_telegram_message_state(
                transport="mtproto", account_id=await account_id(), peer_type="user",
                peer_id=peer_id_for_chat(chat_id), message_id=message_id,
                direction="outgoing", local_chat_id=chat_id, status="sent",
                source="mtproto_send",
            )
        except Exception:
            # Delivery itself succeeded. State telemetry must never cause a
            # duplicate external send during a reconnect/retry path.
            log.exception("mtproto_outgoing_state_record_failed chat_id=%s message_id=%s", chat_id, message_id)

    @staticmethod
    def _upload(data: bytes, filename: str):
        stream = io.BytesIO(data)
        stream.name = filename
        return stream

    def register_generation(self, generation_id, actions):
        image_count = sum(action.type == ActionType.image for action in actions)
        if image_count:
            self._image_generations[generation_id] = {"remaining": image_count, "has_text": any(action.type == ActionType.text and action.text for action in actions), "notified": False}

    def _finish_image(self, generation_id):
        state = self._image_generations.get(generation_id)
        if state:
            state["remaining"] -= 1
            if state["remaining"] <= 0:
                self._image_generations.pop(generation_id, None)

    async def _notify_image_failure(self, item):
        state = self._image_generations.setdefault(item.generation_id, {"remaining": 1, "has_text": False, "notified": False})
        if state["has_text"] or state["notified"]:
            return
        state["notified"] = True
        await self._send_text(item, "блин, фотка не получилась")

    async def execute(self, item) -> None:
        action = item.action
        if action.type == ActionType.pause:
            delay = self.timing.between_messages(action.text or "") if action.automatic else self.timing.pause(action.duration)
            await asyncio.sleep(delay)
            return
        if action.type == ActionType.silence:
            return
        if action.type == ActionType.text and action.text:
            text = strip_emoji(action.text)
            if not text.strip():
                return
            await self._typing(item.chat_id)
            await asyncio.sleep(self.timing.typing_seconds(text))
            await self._send_text(item, text)
        elif action.type == ActionType.reaction:
            await self._reaction(item)
        elif action.type == ActionType.sticker:
            await self._sticker(item)
        elif action.type == ActionType.image:
            await self._image(item)
        elif action.type == ActionType.voice_message:
            await self._voice(item)
        else:
            log.warning("mtproto_action_skipped action_type=%s reason=unsupported", action.type.value)

    async def _reaction(self, item):
        action = item.action
        if not action.emoji or not action.target_message_id:
            log.debug("mtproto_reaction_skipped reason=missing_target_or_emoji")
            return
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji
        client = await self.transport.connect()
        peer = await self.transport.resolve_peer(peer_id_for_chat(item.chat_id))
        await client(SendReactionRequest(peer=peer, msg_id=action.target_message_id, reaction=[ReactionEmoji(action.emoji)]))
        await self.db.record_reaction(chat_id=item.chat_id, telegram_message_id=action.target_message_id, actor="assistant", emoji=action.emoji)

    async def _sticker(self, item):
        if not self.stickers or not self.sticker_documents:
            log.info("mtproto_sticker_skipped reason=sticker_catalog_unavailable")
            return
        action = item.action
        sticker_id = action.sticker_id or (await self.stickers.resolve_intent(action.sticker_intent, item.chat_id) if action.sticker_intent else None)
        if not sticker_id:
            return
        document = await self.sticker_documents.input_document(sticker_id)
        if not document:
            log.info("mtproto_sticker_skipped sticker_id=%s reason=no_mtproto_document", sticker_id)
            return
        client = await self.transport.connect()
        sent = await client.send_file(await self.transport.resolve_peer(peer_id_for_chat(item.chat_id)), document)
        file_id = await self.stickers.file_id(sticker_id)
        await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.id, sender="assistant", kind="sticker", sticker_file_id=file_id)
        await self._record_outgoing_state(item.chat_id, sent.id)
        await self.stickers.db.execute("UPDATE stickers SET times_sent=times_sent+1 WHERE id=?", (sticker_id,))
        await self.stickers.record_usage(sticker_id, item.chat_id, "outgoing")
        if self.lifecycle:
            await self.lifecycle.on_bot_message(item.chat_id)

    async def _image(self, item):
        action = item.action
        try:
            if not action.image_intent or not self.image_provider or not self.image_prompts:
                raise RuntimeError("image generation is unavailable")
            if not await self.image_prompts.allowed(item.chat_id, self.image_daily_limit, self.image_cooldown_hours):
                raise RuntimeError("image generation limit or cooldown is active")
            prompt, visual, references = await self.image_prompts.build(item.chat_id, action.image_intent)
            if references and not getattr(self.image_provider, "supports_references", False):
                raise RuntimeError("configured image provider does not support identity references")
            await self.db.execute("INSERT INTO image_generation_usage(chat_id,provider,prompt,status) VALUES(?,?,?,'processing')", (item.chat_id, self.image_provider.name, prompt))
            generated = await self.image_provider.generate(prompt, references)
            client = await self.transport.connect()
            sent = await client.send_file(await self.transport.resolve_peer(peer_id_for_chat(item.chat_id)), self._upload(generated.data, "anya.jpg"), caption=action.image_intent.caption)
            await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.id, sender="assistant", kind="photo", text=action.image_intent.caption or "[generated image]")
            await self._record_outgoing_state(item.chat_id, sent.id)
            await self.db.execute("UPDATE image_generation_usage SET status='sent',cost=? WHERE id=(SELECT max(id) FROM image_generation_usage WHERE chat_id=? AND status='processing')", (generated.cost, item.chat_id))
            await self.db.execute("INSERT INTO generated_images(chat_id,telegram_message_id,kind,scene,location,activity,clothing_context,provider,model,status) VALUES(?,?,?,?,?,?,?,?,?,?)", (item.chat_id, sent.id, action.image_intent.kind.value, action.image_intent.scene, visual["location"], visual["activity"], visual["clothing"], self.image_provider.name, getattr(self.image_provider, "model", ""), "sent"))
            if self.lifecycle:
                await self.lifecycle.on_bot_message(item.chat_id)
        except Exception:
            log.warning("mtproto_image_generation_failed chat_id=%s", item.chat_id, exc_info=True)
            await self.db.execute("UPDATE image_generation_usage SET status='failed' WHERE id=(SELECT max(id) FROM image_generation_usage WHERE chat_id=? AND status='processing')", (item.chat_id,))
            await self._notify_image_failure(item)
        finally:
            self._finish_image(item.generation_id)

    async def _voice(self, item):
        action = item.action
        if not action.voice_intent or not self.voice_provider or not getattr(self.voice_provider, "enabled", True):
            log.info("mtproto_voice_skipped reason=voice_provider_unavailable")
            return
        session = await self.start_generation_typing(item.chat_id)
        try:
            generated = await self.voice_provider.generate(action.voice_intent.text, mood=action.voice_intent.mood, pace=action.voice_intent.pace, energy=action.voice_intent.energy)
        finally:
            await self.stop_generation_typing(session)
        if not generated or not generated.data:
            return
        extension = ".ogg" if generated.mime_type in {"audio/ogg", "audio/opus"} else ".mp3" if generated.mime_type == "audio/mpeg" else ".bin"
        client = await self.transport.connect()
        sent = await client.send_file(await self.transport.resolve_peer(peer_id_for_chat(item.chat_id)), self._upload(generated.data, "anya" + extension), voice_note=True)
        await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.id, sender="assistant", kind="voice", text=action.voice_intent.text)
        await self._record_outgoing_state(item.chat_id, sent.id)
        if self.lifecycle:
            await self.lifecycle.on_bot_message(item.chat_id)
