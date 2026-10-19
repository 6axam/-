"""Privacy-scoped retrieval of permitted external experience."""
from __future__ import annotations

from datetime import datetime, timezone

from app.memory.manager import _lexical_overlap


class SocialMemoryRetrieval:
    """Retrieve only social memory that is expressly allowed in one chat.

    A source's permission scope is an opaque routing key, not a person lookup.
    No cross-platform identity inference happens here.
    """

    def __init__(self, db, *, now=None):
        self.db = db
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def search(self, consent_scope: str, query: str, limit: int = 4):
        rows = await self.db.active_social_memories_for_scope(
            consent_scope, self.now().astimezone(timezone.utc).isoformat(),
        )
        ranked = []
        for row in rows:
            score = _lexical_overlap(query, row["summary"])
            if score <= 0:
                continue
            # Salience orders equally relevant observations, but popularity,
            # source identity and repetition never become truth indicators.
            ranked.append((score, row["importance"], row["emotional_significance"], row))
        ranked.sort(key=lambda item: item[:3], reverse=True)
        selected, seen_events = [], set()
        for _score, _importance, _emotional, row in ranked:
            # The same observation can inhabit several tiers. A context block
            # gets one representative, never duplicated copies of it.
            if row["social_event_id"] in seen_events:
                continue
            selected.append(row)
            seen_events.add(row["social_event_id"])
            if len(selected) >= max(0, limit):
                break
        return selected
