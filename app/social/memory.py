"""Deterministic early memory routing for consented social experience."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.social.models import SocialEvent


@dataclass(frozen=True)
class MemoryRoutingResult:
    working_created: bool = False
    episodic_created: bool = False
    social_created: bool = False
    skipped: bool = False


class SocialMemoryRouter:
    """Create bounded temporary experience memories without belief formation.

    This is deliberately deterministic and source-policy gated. A later LLM
    appraisal may enrich selected records, but it cannot bypass consent or
    promote an external claim to semantic knowledge on its own.
    """

    def __init__(self, db, *, now=None):
        self.db = db
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _importance(event: SocialEvent) -> float:
        value = .25
        if event.actor_id:
            value += .10
        if event.reply_to_event_id or event.reference_event_ids:
            value += .15
        if event.event_kind in {"direct_message", "reply", "mention"}:
            value += .20
        if event.media_metadata:
            value += .05
        return min(.75, value)

    @staticmethod
    def _emotional_significance(event: SocialEvent) -> float:
        return .35 if event.event_kind in {"direct_message", "reply", "mention"} else .10

    async def route(self, source_row_id: int, event: SocialEvent) -> MemoryRoutingResult:
        permission = event.source.permission
        if not permission.memory_allowed or event.event_kind == "delete":
            return MemoryRoutingResult(skipped=True)
        event_id = await self.db.social_event_id(source_row_id, event.event_id)
        if event_id is None:
            return MemoryRoutingResult(skipped=True)
        now = self.now()
        importance = self._importance(event)
        emotional = self._emotional_significance(event)
        summary = event.content[:512] if permission.retention_allowed and event.content else None
        working = await self.db.record_social_memory(
            social_event_id=event_id, tier="working", actor_id=event.actor_id, summary=summary,
            importance=importance, emotional_significance=emotional, confidence=.50,
            expires_at=(now + timedelta(hours=6 + importance * 18)).isoformat(),
            provenance={"router": "social_memory_v1", "source_event_id": event.event_id},
        )
        episodic = False
        # Only direct, referential or otherwise high-salience experience is
        # promoted. Generic feed items expire as working context.
        if importance >= .50:
            episodic = await self.db.record_social_memory(
                social_event_id=event_id, tier="episodic", actor_id=event.actor_id, summary=summary,
                importance=importance, emotional_significance=emotional, confidence=.50,
                expires_at=None, provenance={"router": "social_memory_v1", "source_event_id": event.event_id},
            )
        social = False
        if event.actor_id:
            social = await self.db.record_social_memory(
                social_event_id=event_id, tier="social", actor_id=event.actor_id, summary=summary,
                importance=importance, emotional_significance=emotional, confidence=.50,
                expires_at=(now + timedelta(days=30)).isoformat(),
                provenance={"router": "social_memory_v1", "source_event_id": event.event_id},
            )
        return MemoryRoutingResult(working, episodic, social)
