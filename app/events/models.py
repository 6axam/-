from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class EventEnvelope:
    """A durable fact, deliberately separate from an action or a decision."""
    event_type: str
    source: str
    actor_kind: str
    scope: str
    idempotency_key: str
    actor_id: str | None = None
    target_kind: str | None = "anya"
    target_id: str | None = "global"
    payload: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = 1

    def __post_init__(self):
        if not self.event_type or not self.source or not self.actor_kind or not self.scope or not self.idempotency_key:
            raise ValueError("event type, source, actor, scope and idempotency key are required")
        if self.occurred_at.tzinfo is None:
            object.__setattr__(self, "occurred_at", self.occurred_at.replace(tzinfo=timezone.utc))
