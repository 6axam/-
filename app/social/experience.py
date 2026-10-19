"""Persist consent-gated social observations without forming beliefs yet."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.social.permissions import SourcePermissionPolicy


@dataclass(frozen=True)
class IngestionResult:
    received: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    deleted: int = 0


class SocialExperienceIngestor:
    """Phase 1 boundary: normalize, policy-check, deduplicate, persist cursor.

    No LLM, memory writer, relationship update, or belief update is called in
    this layer. Later stages consume only `accepted` event rows.
    """

    max_excerpt_chars = 512

    def __init__(self, db, permission_policy: SourcePermissionPolicy | None = None, memory_router=None):
        self.db = db
        self.permission_policy = permission_policy or SourcePermissionPolicy()
        self.memory_router = memory_router

    async def ingest(self, adapter) -> IngestionResult:
        await adapter.connect()
        result = IngestionResult()
        try:
            source = adapter.source
            source_row_id, stored_cursor = await self.db.upsert_social_source(source, capabilities=adapter.capabilities())
            cursor = stored_cursor if stored_cursor is not None else await adapter.get_cursor()
            raw_events = await adapter.fetch_events(cursor)
            for raw in raw_events:
                event = adapter.normalize(raw)
                decision = self.permission_policy.evaluate(event)
                status = "accepted" if decision.allowed else "rejected"
                inserted = await self.db.record_social_event(
                    source_row_id=source_row_id, event=event, status=status,
                    rejection_reason=decision.reason,
                    content_hash=self._content_hash(event.content) if decision.allowed else None,
                    content_excerpt=self._excerpt(event.content, event.source.permission.retention_allowed and decision.allowed),
                )
                result = IngestionResult(
                    received=result.received + 1,
                    accepted=result.accepted + int(inserted and decision.allowed),
                    rejected=result.rejected + int(inserted and not decision.allowed),
                    duplicates=result.duplicates + int(not inserted),
                    deleted=result.deleted,
                )
                if inserted and event.event_kind == "delete":
                    deleted = await self.db.delete_social_event_content(source_row_id, event.reference_event_ids)
                    result = IngestionResult(**{**result.__dict__, "deleted": result.deleted + deleted})
                elif inserted and decision.allowed and event.event_kind in {"edit", "restore"}:
                    await self.db.update_social_event_content(
                        source_row_id, event.reference_event_ids,
                        content_ref=event.content_ref,
                        content_hash=self._content_hash(event.content),
                        content_excerpt=self._excerpt(event.content, event.source.permission.retention_allowed),
                        restoring=event.event_kind == "restore",
                    )
                    if self.memory_router:
                        await self.memory_router.route(source_row_id, event)
                elif inserted and decision.allowed and self.memory_router:
                    await self.memory_router.route(source_row_id, event)
                await adapter.acknowledge(event)
                next_cursor = raw.get("cursor") if isinstance(raw, dict) else None
                await self.db.update_social_source_cursor(source_row_id, next_cursor or await adapter.get_cursor())
            return result
        finally:
            await adapter.close()

    @staticmethod
    def _content_hash(content: str | None) -> str | None:
        return hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None

    def _excerpt(self, content: str | None, allowed: bool) -> str | None:
        if not allowed or not content:
            return None
        return content[:self.max_excerpt_chars]
