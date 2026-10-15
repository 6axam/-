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
    capabilities = TransportCapabilities(reactions=True, edit=True, delete=True, read_receipts=True, voice=True, stickers=True, albums=True, typing=True, contact_block=True)

    def __init__(self, settings: MTProtoSettings, *, client_factory=None):
        self.settings, self._client_factory, self.client = settings, client_factory, None
        # An update already contains the exact InputPeer required for receipts,
        # typing and replies. Retaining it avoids a fragile second lookup by ID.
        self._private_peers: dict[int, Any] = {}

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
        if self.client is None: self.client = self._make_client()
        await self.client.connect()
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

    async def run_private_inbound(self, on_event) -> None:
        """Run only allowlisted incoming private text events until disconnected."""
        self.settings.require_live_runtime()
        client = await self.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Run MTProto login or login-qr first.")
        try:
            from telethon import events
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed. Run: ./.venv/bin/pip install -r requirements.txt") from exc
        me = await client.get_me()
        account_id = int(me.id)

        @client.on(events.NewMessage(incoming=True))
        async def receive(raw_event):
            # Groups, channels, outgoing echo events, service events and media
            # all remain outside the first live text-only slice.
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
            if not text:
                return
            reply_to = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
            from app.telegram.transport import InboundKind, NormalizedInboundEvent
            event = NormalizedInboundEvent(
                transport=self.name,
                account_id=account_id,
                peer_id=int(peer_id),
                sender_id=int(sender_id),
                message_id=int(raw_event.id),
                kind=InboundKind.text,
                occurred_at=getattr(message, "date", None) or datetime.now(timezone.utc),
                text=text,
                reply_to_message_id=reply_to,
            )
            await on_event(event, getattr(sender, "username", None))

        await client.run_until_disconnected()

    async def resolve_peer(self, peer_id: int):
        if peer_id in self._private_peers:
            return self._private_peers[peer_id]
        client = await self.connect()
        return await client.get_input_entity(peer_id)

    async def mark_read(self, peer_id: int, max_message_id: int) -> None:
        client = await self.connect()
        peer = await self.resolve_peer(peer_id)
        last_error = None
        for attempt in range(2):
            try:
                acknowledged = await client.send_read_acknowledge(peer, message=max_message_id)
                if acknowledged:
                    log.info("mtproto_read_acknowledged peer_id=%s max_message_id=%s", peer_id, max_message_id)
                    return
                last_error = RuntimeError("Telegram did not acknowledge the read receipt")
            except Exception as exc:
                last_error = exc
            if attempt == 0:
                await asyncio.sleep(.2)
        raise RuntimeError("MTProto read receipt was not acknowledged") from last_error

    async def close(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except asyncio.CancelledError:
                # Interactive Ctrl+C may cancel cleanup too; the process is
                # already exiting and no session/auth data is lost here.
                pass
