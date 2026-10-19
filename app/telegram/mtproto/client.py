from __future__ import annotations

import os
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.telegram.transport import TelegramTransport, TransportCapabilities

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadAcknowledgement:
    """Result of a requested receipt, distinct from a Telegram confirmation."""

    requested: bool
    confirmed: bool
    server_watermark: int | None
    rpc_returned_true: bool


@dataclass(frozen=True)
class MessageReadUpdate:
    account_id: int
    peer_id: int
    max_message_id: int
    inbox: bool


class MTProtoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_session_path: str = "data/mtproto/anya.session"
    telegram_mtproto_enabled: bool = False
    telegram_mtproto_dry_run: bool = True
    telegram_allowlist: str = ""
    telegram_auto_reply_unknown: bool = False
    telegram_groups_enabled: bool = False
    telegram_autonomous_outbound: bool = False

    def require_credentials(self) -> tuple[int, str]:
        value = self.telegram_api_hash.get_secret_value() if self.telegram_api_hash else ""
        if not self.telegram_api_id or not value:
            raise RuntimeError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the local .env before MTProto login.")
        return self.telegram_api_id, value

    def require_live_runtime(self) -> None:
        """Fail closed before the account can receive or send live traffic."""
        self.require_credentials()
        if not self.telegram_mtproto_enabled:
            raise RuntimeError("Set TELEGRAM_MTPROTO_ENABLED=true before starting the MTProto runtime.")
        if self.telegram_mtproto_dry_run:
            raise RuntimeError("Set TELEGRAM_MTPROTO_DRY_RUN=false before starting the MTProto runtime.")
        if not self.allowlist:
            raise RuntimeError("Set TELEGRAM_ALLOWLIST to the Telegram user ID allowed to test Anya.")
        if self.telegram_auto_reply_unknown or self.telegram_groups_enabled or self.telegram_autonomous_outbound:
            raise RuntimeError("MTProto test runtime requires unknown replies, groups, and autonomous outbound to remain disabled.")

    @property
    def session_path(self) -> Path:
        path = Path(self.telegram_session_path).expanduser()
        if not path.is_absolute(): path = Path.cwd() / path
        return path

    @property
    def allowlist(self) -> frozenset[int]:
        return frozenset(int(v.strip()) for v in self.telegram_allowlist.split(",") if v.strip())


class MTProtoTransport(TelegramTransport):
    name = "mtproto"
    # Capabilities are a contract for the live adapter, not a list of every
    # feature the Telegram protocol could theoretically support.
    capabilities = TransportCapabilities(
        reactions=True, edit=True, delete=True, read_receipts=True,
        voice=True, stickers=True, albums=False, typing=True, contact_block=False,
    )

    def __init__(self, settings: MTProtoSettings, *, client_factory=None):
        self.settings, self._client_factory, self.client = settings, client_factory, None
        self._connect_lock = asyncio.Lock()
        self._session_lock_handle = None
        # An update already contains the exact InputPeer required for receipts,
        # typing and replies. Retaining it avoids a fragile second lookup by ID.
        self._private_peers: dict[int, Any] = {}
        self._account_id: int | None = None

    def _acquire_session_lock(self) -> None:
        """Prevent two local runtimes from opening the same Telethon session."""
        if self._session_lock_handle is not None:
            return
        # This runtime is Linux-first. Keep the import local so offline tools
        # can still import the transport on platforms without fcntl.
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows has its own locking
            return
        lock_path = self._secure_session_path().with_suffix(self._secure_session_path().suffix + ".lock")
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("The MTProto session is already in use by another local Anya process.") from exc
        self._session_lock_handle = handle

    def _release_session_lock(self) -> None:
        handle, self._session_lock_handle = self._session_lock_handle, None
        if handle is None:
            return
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()

    @staticmethod
    def _is_connected(client) -> bool:
        value = getattr(client, "is_connected", None)
        if callable(value):
            value = value()
        if value is not None:
            return bool(value)
        return bool(getattr(client, "connected", False))

    def _secure_session_path(self) -> Path:
        path = self.settings.session_path
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists(): os.chmod(path, 0o600)
        return path

    def _make_client(self):
        api_id, api_hash = self.settings.require_credentials()
        path = self._secure_session_path()
        if self._client_factory: return self._client_factory(str(path), api_id, api_hash)
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed. Run: ./.venv/bin/pip install -r requirements.txt") from exc
        return TelegramClient(str(path), api_id, api_hash)

    async def connect(self):
        # Telethon's connect is not safe to race with another connect on the
        # same client/session. Re-use a live client and serialize creation and
        # reconnect attempts within this process.
        async with self._connect_lock:
            if self.client is None:
                self._acquire_session_lock()
                try:
                    self.client = self._make_client()
                except Exception:
                    self._release_session_lock()
                    raise
            if not self._is_connected(self.client):
                try:
                    await self.client.connect()
                except Exception:
                    log.warning("mtproto_connect_failed", exc_info=True)
                    raise
            return self.client

    async def status(self) -> dict[str, Any]:
        client = await self.connect()
        authorized = await client.is_user_authorized()
        result = {"transport": self.name, "connected": True, "authorized": bool(authorized), "dry_run": self.settings.telegram_mtproto_dry_run}
        if authorized:
            me = await client.get_me()
            result["account_id_suffix"] = str(getattr(me, "id", ""))[-4:]
            result["display_name"] = " ".join(filter(None, [getattr(me, "first_name", None), getattr(me, "last_name", None)])) or None
        return result

    async def account_id(self) -> int:
        if self._account_id is None:
            client = await self.connect()
            self._account_id = int((await client.get_me()).id)
        return self._account_id

    @staticmethod
    def _inbound_kind(message):
        """Classify only Telegram metadata; do not fetch untrusted binaries."""
        from app.telegram.transport import InboundKind
        document = getattr(message, "document", None)
        if getattr(message, "sticker", False) and document:
            return InboundKind.sticker
        if getattr(message, "photo", None):
            return InboundKind.photo
        if getattr(message, "voice", None):
            return InboundKind.voice
        if getattr(message, "audio", None):
            return InboundKind.audio
        if getattr(message, "video", None):
            return InboundKind.video
        if getattr(message, "gif", None):
            return InboundKind.animation
        if document:
            return InboundKind.document
        return InboundKind.text

    async def run_private_inbound(self, on_event, on_message_read=None) -> None:
        """Run allowlisted private inbound metadata until disconnected."""
        self.settings.require_live_runtime()
        client = await self.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Run MTProto login or login-qr first.")
        try:
            from telethon import events
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed. Run: ./.venv/bin/pip install -r requirements.txt") from exc
        account_id = await self.account_id()

        async def receive_message_read(read_event, *, inbox: bool) -> None:
            if not on_message_read:
                return
            peer_id = getattr(read_event, "chat_id", None)
            max_message_id = getattr(read_event, "max_id", None)
            # This live runtime deliberately knows only allowlisted personal
            # chats. Do not create a receipt trail for a group or a stranger.
            if not peer_id or not max_message_id or int(peer_id) not in self.settings.allowlist:
                return
            log.info(
                "mtproto_message_read_update direction=%s peer_id=%s max_message_id=%s",
                "inbox" if inbox else "outbox", peer_id, max_message_id,
            )
            await on_message_read(MessageReadUpdate(
                account_id=account_id, peer_id=int(peer_id),
                max_message_id=int(max_message_id), inbox=inbox,
            ))

        @client.on(events.NewMessage(incoming=True))
        async def receive(raw_event):
            # Groups, channels and outgoing echoes are intentionally outside
            # this account's allowlisted private-conversation runtime.
            if not getattr(raw_event, "is_private", False) or getattr(raw_event, "out", False):
                return
            sender = await raw_event.get_sender()
            sender_id = getattr(sender, "id", None)
            peer_id = getattr(raw_event, "chat_id", None)
            if not sender_id or not peer_id or sender_id not in self.settings.allowlist:
                return
            input_peer = getattr(raw_event, "input_chat", None)
            if input_peer is not None:
                self._private_peers[int(peer_id)] = input_peer
            message = raw_event.message
            text = (getattr(raw_event, "raw_text", "") or "").strip()
            document = getattr(message, "document", None)
            kind = self._inbound_kind(message)
            if not text and kind.value == "text":
                return
            reply_to = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
            from app.telegram.transport import InboundKind, NormalizedInboundEvent
            emoji = next((getattr(attr, "alt", None) for attr in getattr(document, "attributes", []) if getattr(attr, "alt", None)), None) if kind == InboundKind.sticker else None
            event = NormalizedInboundEvent(
                transport=self.name,
                account_id=account_id,
                peer_id=int(peer_id),
                sender_id=int(sender_id),
                message_id=int(raw_event.id),
                kind=kind,
                occurred_at=getattr(message, "date", None) or datetime.now(timezone.utc),
                text=text,
                reply_to_message_id=reply_to,
                # Store only the small protocol reference required for a
                # sticker re-send. Other inbound media stays metadata-only
                # until a bounded, transport-neutral analyzer is introduced.
                media={"document": document, "emoji": emoji} if kind == InboundKind.sticker else {
                    "grouped_id": getattr(message, "grouped_id", None),
                    "mime_type": getattr(getattr(message, "file", None), "mime_type", None),
                },
                grouped_id=getattr(message, "grouped_id", None),
            )
            await on_event(event, getattr(sender, "username", None))

        @client.on(events.MessageRead(inbox=True))
        async def inbox_read(read_event):
            await receive_message_read(read_event, inbox=True)

        @client.on(events.MessageRead(inbox=False))
        async def outbox_read(read_event):
            await receive_message_read(read_event, inbox=False)

        await client.run_until_disconnected()

    async def resolve_peer(self, peer_id: int):
        if peer_id in self._private_peers:
            return self._private_peers[peer_id]
        client = await self.connect()
        return await client.get_input_entity(peer_id)

    async def inbox_read_watermark(self, peer) -> int | None:
        """Ask Telegram for the authoritative inbox watermark for one peer."""
        client = await self.connect()
        try:
            from telethon.tl.functions.messages import GetPeerDialogsRequest
            from telethon.tl.types import InputDialogPeer
            result = await client(GetPeerDialogsRequest([InputDialogPeer(peer=peer)]))
            watermarks = [
                int(dialog.read_inbox_max_id or 0)
                for dialog in getattr(result, "dialogs", [])
                if getattr(dialog, "read_inbox_max_id", None) is not None
            ]
            return max(watermarks, default=None)
        except Exception:
            # The acknowledgement may still be applied and arrive as a
            # MessageRead update. This is diagnostic, not proof of failure.
            log.warning("mtproto_read_watermark_query_failed", exc_info=True)
            return None

    async def mark_read(self, peer_id: int, max_message_id: int) -> ReadAcknowledgement:
        client = await self.connect()
        peer = await self.resolve_peer(peer_id)
        rpc_result = await client.send_read_acknowledge(peer, max_id=max_message_id)
        watermark = await self.inbox_read_watermark(peer)
        confirmed = watermark is not None and watermark >= max_message_id
        log.info(
            "mtproto_read_ack_result peer_id=%s max_message_id=%s rpc_returned_true=%s server_watermark=%s confirmed=%s",
            peer_id, max_message_id, bool(rpc_result), watermark, confirmed,
        )
        return ReadAcknowledgement(
            requested=True, confirmed=confirmed, server_watermark=watermark,
            rpc_returned_true=bool(rpc_result),
        )

    async def close(self) -> None:
        async with self._connect_lock:
            if self.client:
                try:
                    await self.client.disconnect()
                except asyncio.CancelledError:
                    # Interactive Ctrl+C may cancel cleanup too; the process is
                    # already exiting and no session/auth data is lost here.
                    pass
                finally:
                    self._release_session_lock()
