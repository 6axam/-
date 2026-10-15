"""Transport-neutral contracts; core code must not depend on aiogram events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class TransportCapabilities:
    reactions: bool = False; edit: bool = False; delete: bool = False
    read_receipts: bool = False; voice: bool = False; stickers: bool = False
    albums: bool = False; typing: bool = False; contact_block: bool = False


class InboundKind(str, Enum):
    text = "text"; photo = "photo"; voice = "voice"; audio = "audio"; video = "video"
    animation = "animation"; document = "document"; sticker = "sticker"; edit = "edit"; delete = "delete"; read = "read"


@dataclass(frozen=True)
class NormalizedInboundEvent:
    transport: str; account_id: int; peer_id: int; sender_id: int | None; message_id: int | None
    kind: InboundKind; occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    text: str = ""; reply_to_message_id: int | None = None; media: dict[str, Any] = field(default_factory=dict)
    outgoing: bool = False; manual_outgoing: bool = False; grouped_id: int | None = None

    @property
    def idempotency_key(self) -> str:
        return f"{self.transport}:{self.account_id}:{self.peer_id}:{self.kind.value}:{self.message_id or 0}"


class TelegramTransport:
    name = "abstract"
    capabilities = TransportCapabilities()
    async def status(self) -> dict[str, Any]: raise NotImplementedError
    async def close(self) -> None: raise NotImplementedError
