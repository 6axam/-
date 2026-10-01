"""Global, compact autobiography for Anya; separate from user memories."""

import json
import logging
import random
from datetime import datetime

from app.memory.manager import _matched_tokens, _words

log = logging.getLogger(__name__)


class SelfLifeManager:
    def __init__(self, db, probability: float = .60, rng=None):
        self.db, self.probability, self.rng = db, probability, rng or random.random

    def continuation_gate(self) -> bool:
        return self.rng() < self.probability

    async def relevant(self, query: str, local_day: str, limit: int = 8):
        rows = await self.db.fetchall(
            "SELECT *, (julianday('now')-julianday(COALESCE(occurred_at,created_at)))*24 AS age_hours "
            "FROM anya_life_events ORDER BY COALESCE(occurred_at,created_at) DESC,id DESC LIMIT 40"
        )
        query_words = _words(query)
        ranked = []
        for row in rows:
            event_words = _words(" ".join(filter(None, [row["summary"], row["details"], row["kind"]])))
            exact, forms = _matched_tokens(query_words, event_words)
            day_bonus = 2.0 if row["local_day"] == local_day else (1.0 if row["local_day"] else 0.0)
            recency = max(0.0, 1.0 - min(float(row["age_hours"] or 720), 720) / 720)
            ranked.append((exact * 4 + forms * 3 + day_bonus + recency, row))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
        return [row for _, row in ranked[:limit]]

    async def apply(self, candidate, *, source_chat_id: int, source_turn_id: int | None,
                    allowed_target_ids: set[int], local_day: str) -> str | None:
        if not candidate or candidate.decision == "IGNORE":
            return None
        if candidate.decision == "UPDATE_EXISTING":
            if not candidate.target_event_id or candidate.target_event_id not in allowed_target_ids:
                log.warning("self_life_invalid_update_target target_event_id=%s", candidate.target_event_id)
                return None
            result = await self.db.execute(
                "UPDATE anya_life_events SET summary=?,details=?,kind=?,participants_json=?,source_chat_id=?,source_turn_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (candidate.summary.strip(), candidate.details, candidate.kind, json.dumps(candidate.participants, ensure_ascii=False),
                 source_chat_id, source_turn_id, candidate.target_event_id),
            )
            if result.rowcount:
                log.info("self_life_event_updated event_id=%s", candidate.target_event_id)
                return "updated"
            return None

        # Same-day lexical duplicates are ignored conservatively. A later
        # follow-up remains possible through UPDATE_EXISTING or a distinct
        # sufficiently different event.
        rows = await self.db.fetchall(
            "SELECT id,summary,details FROM anya_life_events WHERE local_day=? ORDER BY id DESC LIMIT 24", (local_day,)
        )
        incoming = _words(" ".join(filter(None, [candidate.summary, candidate.details])))
        for row in rows:
            existing = _words(" ".join(filter(None, [row["summary"], row["details"]])))
            exact, forms = _matched_tokens(incoming, existing)
            overlap = (exact + forms) / max(1, len(incoming | existing))
            if overlap >= .55:
                log.info("self_life_event_deduplicated existing_event_id=%s", row["id"])
                return "deduplicated"
        occurred_at = candidate.occurred_at_hint or None
        result = await self.db.execute(
            "INSERT INTO anya_life_events(occurred_at,local_day,kind,summary,details,participants_json,source_chat_id,source_turn_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (occurred_at, local_day, candidate.kind, candidate.summary.strip(), candidate.details,
             json.dumps(candidate.participants, ensure_ascii=False), source_chat_id, source_turn_id),
        )
        log.info("self_life_event_saved event_id=%s", result.lastrowid)
        return "saved"

    @staticmethod
    def local_day(timezone_name: str) -> str:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_name)).date().isoformat()
