"""Transport-neutral, privacy-bounded social source contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _required(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(frozen=True)
class SourcePermission:
    """A source's explicit, narrow right to be processed by Anya.

    This is intentionally more specific than an adapter connection: a working
    API token never grants permission to send data to an LLM or retain it.
    """

    usage_mode: str
    ai_processing_allowed: bool = False
    memory_allowed: bool = False
    retention_allowed: bool = False
    explicit_consent: bool = False
    consent_scope: str | None = None
    expires_at: datetime | None = None

    def valid_now(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.expires_at is None or self.expires_at.astimezone(timezone.utc) >= now


@dataclass(frozen=True)
class SourceDescriptor:
    platform: str
    platform_account_id: str
    source_id: str
    source_type: str
    permission: SourcePermission
    display_name: str | None = None

    def __post_init__(self):
        for name in ("platform", "platform_account_id", "source_id", "source_type"):
            object.__setattr__(self, name, _required(getattr(self, name), name))


@dataclass(frozen=True)
class SocialEvent:
    """One source observation before appraisal or memory formation.

    `content` is transient adapter input. The persistence layer stores a hash
    and, only when retention is allowed, a small bounded excerpt.
    """

    source: SourceDescriptor
    event_id: str
    actor_id: str | None
    event_kind: str
    occurred_at: datetime
    content_ref: str | None = None
    content: str | None = None
    thread_id: str | None = None
    media_metadata: dict[str, Any] = field(default_factory=dict)
    reply_to_event_id: str | None = None
    reference_event_ids: tuple[str, ...] = ()
    visibility: str = "private"
    provenance: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        object.__setattr__(self, "event_kind", _required(self.event_kind, "event_kind"))
        if self.content is not None:
            object.__setattr__(self, "content", self.content.strip() or None)
        if self.event_kind not in {"delete", "restore"} and not (self.content or self.content_ref):
            raise ValueError("non-deletion social event requires content or content_ref")
        if self.occurred_at.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("social event timestamps must be timezone-aware")
        object.__setattr__(self, "reference_event_ids", tuple(_required(value, "reference_event_id") for value in self.reference_event_ids))

    @property
    def idempotency_key(self) -> str:
        source = self.source
        return f"{source.platform}:{source.platform_account_id}:{source.source_id}:{self.event_id}"
