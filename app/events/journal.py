from __future__ import annotations

import json
from typing import Any, Mapping

from app.events.models import EventEnvelope


class EventJournal:
    """Append-only event storage with idempotency-key recovery."""
    def __init__(self, db):
        self.db = db

    async def append(self, event: EventEnvelope) -> tuple[dict[str, Any], bool]:
        result = await self.db.execute(
            "INSERT INTO anya_events(event_id,event_type,occurred_at,source,actor_kind,actor_id,target_kind,target_id,scope,payload_json,correlation_id,causation_id,idempotency_key,schema_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
            (event.event_id, event.event_type, event.occurred_at.isoformat(timespec="microseconds"), event.source,
             event.actor_kind, event.actor_id, event.target_kind, event.target_id, event.scope,
             json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
             event.correlation_id, event.causation_id, event.idempotency_key, event.schema_version),
        )
        if result.rowcount:
            row = await self.db.fetchone("SELECT * FROM anya_events WHERE event_id=?", (event.event_id,))
            return dict(row), True
        row = await self.db.fetchone("SELECT * FROM anya_events WHERE idempotency_key=?", (event.idempotency_key,))
        if not row:
            raise RuntimeError("event idempotency conflict without journal row")
        return dict(row), False

    async def transition(self, event_id: str, transition_kind: str, *, before: Mapping[str, Any] | None = None,
                         after: Mapping[str, Any] | None = None, result: Mapping[str, Any] | None = None) -> bool:
        inserted = await self.db.execute(
            "INSERT INTO anya_event_transitions(event_id,transition_kind,before_json,after_json,result_json) VALUES(?,?,?,?,?) ON CONFLICT(event_id,transition_kind) DO NOTHING",
            (event_id, transition_kind, json.dumps(dict(before or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
             json.dumps(dict(after or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
             json.dumps(dict(result or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
        if inserted.rowcount:
            await self.db.execute("UPDATE anya_events SET status='applied' WHERE event_id=? AND status='recorded'", (event_id,))
        return bool(inserted.rowcount)

    async def recent(self, *, limit: int = 100):
        return await self.db.fetchall("SELECT * FROM anya_events ORDER BY occurred_at DESC,event_id DESC LIMIT ?", (limit,))
