"""Read-only state acquisition and pure presentation models for the TUI."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.presence import DailyPresenceManager


@dataclass(frozen=True)
class PresenceView:
    phase: str = "unknown"
    availability: str = "unknown"
    local_time: str = "--:--"
    sleeping: bool = False
    event_title: str | None = None
    event_availability: str | None = None
    sleep_until: str | None = None


@dataclass(frozen=True)
class MemoryView:
    active_count: int = 0
    unresolved_count: int = 0
    recent_summaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnyaSnapshot:
    captured_at: datetime
    chat_id: int
    presence: PresenceView = field(default_factory=PresenceView)
    lifecycle: str = "unknown"
    processing: str | None = None
    regulators: Mapping[str, float] = field(default_factory=dict)
    emotions: Mapping[str, float] = field(default_factory=dict)
    relationship: Mapping[str, float] = field(default_factory=dict)
    memories: MemoryView = field(default_factory=MemoryView)
    source_available: bool = True


def database_path(database_url: str, cwd: Path | None = None) -> Path:
    """Resolve the project's SQLite URL without creating directories or files."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Anya TUI supports the project's SQLite database only")
    raw = database_url[len(prefix) :]
    path = Path(raw)
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path.resolve()


def _json_map(raw: Any) -> dict[str, float]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            result[str(key)] = max(0.0, min(1.0, float(item)))
        except (TypeError, ValueError):
            continue
    return result


class ReadOnlyStateReader:
    """Take short SQLite snapshots without running migrations or writing state."""

    def __init__(
        self,
        database_url: str,
        chat_id: int,
        *,
        timezone_name: str = "Europe/Kyiv",
        sleep_start: int = 1,
        wake_hour: int = 7,
        college_start_hour: int = 8,
        college_end_hour: int = 15,
        college_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
        cwd: Path | None = None,
    ) -> None:
        self.path = database_path(database_url, cwd)
        self.chat_id = chat_id
        self.presence = DailyPresenceManager(
            None,
            timezone_name=timezone_name,
            sleep_start=sleep_start,
            wake_hour=wake_hour,
            college_start_hour=college_start_hour,
            college_end_hour=college_end_hour,
            college_weekdays=college_weekdays,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro",
            uri=True,
            timeout=0.25,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=250")
        return connection

    @staticmethod
    def _one(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        try:
            return connection.execute(sql, params).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower() or "no such column" in str(exc).lower():
                return None
            raise

    @staticmethod
    def _all(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        try:
            return connection.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower() or "no such column" in str(exc).lower():
                return []
            raise

    def poll(self, now: datetime | None = None) -> AnyaSnapshot:
        now = now or datetime.now(timezone.utc)
        if not self.path.is_file():
            return AnyaSnapshot(now, self.chat_id, source_available=False)
        try:
            with self._connect() as connection:
                return self._read(connection, now)
        except (sqlite3.Error, OSError):
            return AnyaSnapshot(now, self.chat_id, source_available=False)

    def _read(self, connection: sqlite3.Connection, now: datetime) -> AnyaSnapshot:
        local = now.astimezone(self.presence.zone)
        base_episode = self.presence._sleep_episode(self.chat_id, local)
        delay = 0
        if base_episode:
            row = self._one(
                connection,
                "SELECT delay_minutes FROM bedtime_overrides WHERE chat_id=? AND sleep_start_date=?",
                (self.chat_id, base_episode[0].date().isoformat()),
            )
            delay = int(row["delay_minutes"]) if row else 0
        episode = self.presence._sleep_episode(self.chat_id, local, delay)
        sleeping = bool(episode and episode[0] <= local < episode[1])
        now_utc = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        event = self._one(
            connection,
            """SELECT title,availability,ends_at FROM daily_events
               WHERE chat_id=? AND julianday(starts_at)<=julianday(?)
                 AND julianday(ends_at)>julianday(?)
               ORDER BY ends_at DESC LIMIT 1""",
            (self.chat_id, now_utc, now_utc),
        )
        phase = "college" if not sleeping and self.presence._is_college_window(local) else "free"
        availability = "sleep" if sleeping else (str(event["availability"]) if event else "available")
        presence = PresenceView(
            phase=phase,
            availability=availability,
            local_time=local.strftime("%H:%M"),
            sleeping=sleeping,
            event_title=str(event["title"]) if event else None,
            event_availability=str(event["availability"]) if event else None,
            sleep_until=episode[1].strftime("%H:%M") if sleeping and episode else None,
        )

        lifecycle_row = self._one(
            connection,
            "SELECT conversation_status FROM conversation_lifecycle WHERE chat_id=?",
            (self.chat_id,),
        )
        response = self._one(
            connection,
            "SELECT status FROM scheduled_responses WHERE chat_id=? AND status='processing' ORDER BY id DESC LIMIT 1",
            (self.chat_id,),
        )
        read = self._one(
            connection,
            "SELECT status FROM scheduled_reads WHERE chat_id=? AND status='processing' ORDER BY id DESC LIMIT 1",
            (self.chat_id,),
        )
        processing = "responding" if response else ("reading" if read else None)

        regulators_row = self._one(connection, "SELECT regulators_json FROM affective_states WHERE chat_id=?", (self.chat_id,))
        emotions_row = self._one(connection, "SELECT emotions_json FROM affective_profiles WHERE chat_id=?", (self.chat_id,))
        relationship_row = self._one(connection, "SELECT * FROM relationship_bonds WHERE chat_id=?", (self.chat_id,))
        relationship_keys = (
            "bond_strength", "relational_trust", "familiarity", "care_investment",
            "rupture_load", "relationship_security", "love_strength",
        )
        relationship = {
            key: float(relationship_row[key])
            for key in relationship_keys
            if relationship_row is not None and key in relationship_row.keys()
        }

        counts = self._one(
            connection,
            """SELECT COUNT(*) AS active_count,
                      COALESCE(SUM(CASE WHEN unresolved=1 THEN 1 ELSE 0 END),0) AS unresolved_count
               FROM episodic_memories WHERE chat_id=? AND status='active'""",
            (self.chat_id,),
        )
        summaries = self._all(
            connection,
            """SELECT summary FROM episodic_memories
               WHERE chat_id=? AND status='active'
               ORDER BY updated_at DESC,id DESC LIMIT 3""",
            (self.chat_id,),
        )
        memories = MemoryView(
            active_count=int(counts["active_count"]) if counts else 0,
            unresolved_count=int(counts["unresolved_count"]) if counts else 0,
            recent_summaries=tuple(str(row["summary"]) for row in summaries),
        )
        return AnyaSnapshot(
            captured_at=now,
            chat_id=self.chat_id,
            presence=presence,
            lifecycle=str(lifecycle_row["conversation_status"]) if lifecycle_row else "unknown",
            processing=processing,
            regulators=_json_map(regulators_row["regulators_json"] if regulators_row else None),
            emotions=_json_map(emotions_row["emotions_json"] if emotions_row else None),
            relationship=relationship,
            memories=memories,
        )


EMOTION_GROUPS: dict[str, tuple[str, ...]] = {
    "warm": ("affection", "tenderness", "closeness", "trust", "gratitude", "warmth"),
    "anger": ("anger", "irritation", "frustration", "rage"),
    "sadness": ("sadness", "melancholy", "grief", "depressive_tone", "hurt"),
    "anxiety": ("anxiety", "fear", "overwhelm", "defensiveness", "threat"),
    "happy": ("joy", "amusement", "excitement", "contentment", "relief"),
    "jealousy": ("jealousy", "resentment", "envy"),
}


def aggregate_emotions(emotions: Mapping[str, float]) -> dict[str, float]:
    """Preserve peaks while reducing the wide affect vector to visual channels."""
    return {
        group: max((float(emotions.get(name, 0.0)) for name in members), default=0.0)
        for group, members in EMOTION_GROUPS.items()
    }


def top_emotions(emotions: Mapping[str, float], limit: int = 5) -> list[tuple[str, float]]:
    return sorted(
        ((name, float(value)) for name, value in emotions.items() if float(value) > 0.01),
        key=lambda item: (-item[1], item[0]),
    )[:limit]


def particle_budget(intensity: float) -> int:
    value = max(0.0, min(1.0, float(intensity)))
    if value < 0.18:
        return 0
    return min(8, 1 + int(value * 7))


def meter(value: float | None, width: int = 10) -> str:
    """Stable plain-text meter that remains readable without terminal color."""
    if value is None:
        return "·" * width + "  n/a"
    bounded = max(0.0, min(1.0, float(value)))
    filled = round(bounded * width)
    return "█" * filled + "░" * (width - filled) + f" {bounded:4.0%}"


def relationship_lines(relationship: Mapping[str, float]) -> tuple[str, ...]:
    labels = (
        ("bond_strength", "bond"),
        ("relational_trust", "trust"),
        ("relationship_security", "security"),
        ("love_strength", "love"),
        ("rupture_load", "rupture"),
    )
    return tuple(f"{label:<8} {meter(relationship.get(key))}" for key, label in labels)


def transition_events(previous: AnyaSnapshot | None, current: AnyaSnapshot) -> list[str]:
    if previous is None:
        return [f"наблюдение подключено · {current.presence.local_time}"]
    events: list[str] = []
    if previous.presence.sleeping != current.presence.sleeping:
        events.append("Аня уснула" if current.presence.sleeping else "Аня проснулась")
    if previous.presence.phase != current.presence.phase:
        events.append(f"режим: {current.presence.phase}")
    if previous.presence.event_title != current.presence.event_title and current.presence.event_title:
        events.append(f"событие: {current.presence.event_title}")
    if previous.processing != current.processing and current.processing:
        events.append("читает сообщения" if current.processing == "reading" else "готовит ответ")
    old_anger = aggregate_emotions(previous.emotions)["anger"]
    new_anger = aggregate_emotions(current.emotions)["anger"]
    if old_anger < 0.65 <= new_anger:
        events.append("злость стала заметно сильнее")
    return events
