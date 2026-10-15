from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.events.models import EventEnvelope


class EventProcessor:
    """Serialize event ingestion inside this runtime process.

    The journal is durable; this narrow lock prevents simultaneous chats from
    making conflicting global-state writes until the later full router exists.
    """
    def __init__(self, journal):
        self.journal, self._lock = journal, asyncio.Lock()

    async def ingest(self, event: EventEnvelope, apply: Callable[[dict], Awaitable[dict | None]] | None = None) -> tuple[dict, bool, dict | None]:
        async with self._lock:
            record, created = await self.journal.append(event)
            if not created or apply is None:
                return record, created, None
            try:
                result = await apply(record) or {}
                await self.journal.transition(record["event_id"], "processor_apply", result=result)
                return record, True, result
            except Exception:
                await self.journal.db.execute("UPDATE anya_events SET status='failed' WHERE event_id=?", (record["event_id"],))
                raise
