"""Persistent, deterministic emotional continuity with no model calls."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.emotions.model import EMOTION_BASELINE, EMOTION_DIMENSIONS, EmotionDelta, EmotionalState, clamp


class EmotionalEngine:
    """Keeps one bounded emotional state per Telegram chat.

    All elapsed-time changes are calculated from the stored timestamp, making
    restart behaviour and tests deterministic.  LLM output only supplies a
    sparse delta; it cannot choose timestamps, baselines, or bypass caps.
    """

    # Hours required to close half the distance to each neutral baseline.
    HALF_LIFE_HOURS = {
        "warmth": 72.0, "trust": 168.0, "joy": 24.0, "sadness": 18.0,
        "irritation": 10.0, "hurt": 42.0, "anxiety": 12.0, "fatigue": 8.0,
        "curiosity": 20.0, "social_need": 30.0,
    }
    DELTA_CAP = .12

    def __init__(self, db, timezone_name: str = "Europe/Kyiv"):
        self.db = db
        self.zone = ZoneInfo(timezone_name)

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stamp(value: datetime) -> str:
        # Preserve the in-process clock exactly, so initialisation followed by
        # an immediate read is a true no-op rather than a tiny phantom decay.
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    async def _row(self, chat_id: int, now: datetime):
        row = await self.db.fetchone("SELECT * FROM emotional_states WHERE chat_id=?", (chat_id,))
        if row:
            return row
        state = EmotionalState.baseline()
        stamp = self._stamp(now)
        values = state.values()
        await self.db.execute(
            "INSERT INTO emotional_states(chat_id,warmth,trust,joy,sadness,irritation,hurt,anxiety,fatigue,curiosity,social_need,last_interaction_at,last_advanced_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, *(values[name] for name in EMOTION_DIMENSIONS), stamp, stamp, stamp),
        )
        return await self.db.fetchone("SELECT * FROM emotional_states WHERE chat_id=?", (chat_id,))

    def _evolve(self, state: EmotionalState, elapsed_hours: float, local_hour: int, sleeping: bool) -> EmotionalState:
        if elapsed_hours <= 0:
            return state
        values = state.values()
        for name in EMOTION_DIMENSIONS:
            half_life = self.HALF_LIFE_HOURS[name]
            factor = math.exp(-math.log(2) * elapsed_hours / half_life)
            values[name] = EMOTION_BASELINE[name] + (values[name] - EMOTION_BASELINE[name]) * factor
        # Absence creates only a modest wish for contact, capped and reversible.
        absence_gain = min(.18, elapsed_hours * .006)
        values["social_need"] = clamp(values["social_need"] + absence_gain)
        # Fatigue is a deterministic rhythm layered over decay, never a free LLM label.
        night_target = .78 if local_hour >= 23 or local_hour < 7 or sleeping else .25
        fatigue_factor = 1 - math.exp(-elapsed_hours / 3.0)
        values["fatigue"] = clamp(values["fatigue"] + (night_target - values["fatigue"]) * fatigue_factor)
        return state.with_values(values)

    async def advance(self, chat_id: int, *, now: datetime | None = None, sleeping: bool = False) -> EmotionalState:
        now_utc = self._now(now)
        row = await self._row(chat_id, now_utc)
        previous = self._parse(row["last_advanced_at"])
        elapsed_hours = max(0.0, (now_utc - previous).total_seconds() / 3600)
        state = self._evolve(EmotionalState.from_row(row), elapsed_hours, now_utc.astimezone(self.zone).hour, sleeping)
        if elapsed_hours > 0:
            values = state.values()
            await self.db.execute(
                "UPDATE emotional_states SET warmth=?,trust=?,joy=?,sadness=?,irritation=?,hurt=?,anxiety=?,fatigue=?,curiosity=?,social_need=?,last_advanced_at=?,updated_at=? WHERE chat_id=?",
                (*(values[name] for name in EMOTION_DIMENSIONS), self._stamp(now_utc), self._stamp(now_utc), chat_id),
            )
        return state

    async def get(self, chat_id: int, *, now: datetime | None = None, sleeping: bool = False) -> EmotionalState:
        return await self.advance(chat_id, now=now, sleeping=sleeping)

    async def apply_delta(self, chat_id: int, delta: EmotionDelta | dict[str, float], *, now: datetime | None = None, sleeping: bool = False) -> EmotionalState:
        now_utc = self._now(now)
        state = await self.advance(chat_id, now=now_utc, sleeping=sleeping)
        values = state.values()
        changes = delta.bounded(self.DELTA_CAP) if isinstance(delta, EmotionDelta) else EmotionDelta(delta).bounded(self.DELTA_CAP)
        for name, value in changes.items():
            values[name] = clamp(values[name] + value)
        updated = state.with_values(values)
        stamp = self._stamp(now_utc)
        await self.db.execute(
            "UPDATE emotional_states SET warmth=?,trust=?,joy=?,sadness=?,irritation=?,hurt=?,anxiety=?,fatigue=?,curiosity=?,social_need=?,last_interaction_at=?,last_advanced_at=?,updated_at=? WHERE chat_id=?",
            (*(updated.values()[name] for name in EMOTION_DIMENSIONS), stamp, stamp, stamp, chat_id),
        )
        return updated
