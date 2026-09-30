"""Persistent, deliberately approximate daily rhythm; all timestamps are UTC."""
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class DailyPresenceManager:
    def __init__(self, db, timezone_name: str = "Europe/Kyiv", sleep_start: int = 1, wake_hour: int = 7,
                 college_start_hour: int = 8, college_end_hour: int = 15,
                 college_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)):
        self.db, self.zone = db, ZoneInfo(timezone_name)
        self.timezone_name, self.sleep_start, self.wake_hour = timezone_name, sleep_start, wake_hour
        self.college_start_hour, self.college_end_hour = college_start_hour, college_end_hour
        self.college_weekdays = frozenset(college_weekdays)

    @staticmethod
    def _wake_jitter(chat_id: int, day: str) -> int:
        """Stable ±45 minute jitter, independent of Python hash randomization."""
        digest = hashlib.sha256(f"{chat_id}:{day}".encode("utf-8")).digest()
        return int.from_bytes(digest[:2], "big") % 91 - 45

    def _is_college_window(self, local) -> bool:
        if local.weekday() not in self.college_weekdays:
            return False
        hour = local.hour
        if self.college_start_hour == self.college_end_hour:
            return False
        if self.college_start_hour < self.college_end_hour:
            return self.college_start_hour <= hour < self.college_end_hour
        return hour >= self.college_start_hour or hour < self.college_end_hour

    async def state(self, chat_id: int, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self.zone); day = local.date().isoformat()
        row = await self.db.fetchone("SELECT * FROM daily_presence WHERE chat_id=?", (chat_id,))
        if not row or row["local_day"] != day:
            # Stable per day/chat jitter keeps a reboot from changing the night.
            jitter = self._wake_jitter(chat_id, day)
            crosses_midnight = self.sleep_start > self.wake_hour
            sleeping = (local.hour >= self.sleep_start or local.hour < self.wake_hour) if crosses_midnight else (self.sleep_start <= local.hour < self.wake_hour)
            wake = local.replace(hour=self.wake_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=jitter)
            if sleeping and crosses_midnight and local.hour >= self.sleep_start:
                wake += timedelta(days=1)
            until = wake.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if sleeping else None
            await self.db.execute("INSERT INTO daily_presence(chat_id,local_day,timezone,sleep_until,availability) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET local_day=excluded.local_day,timezone=excluded.timezone,sleep_until=excluded.sleep_until,availability=excluded.availability,updated_at=CURRENT_TIMESTAMP", (chat_id, day, self.timezone_name, until, "sleep" if sleeping else "available"))
        row = await self.db.fetchone("SELECT * FROM daily_presence WHERE chat_id=?", (chat_id,))
        event = await self.db.fetchone("SELECT * FROM daily_events WHERE chat_id=? AND julianday(starts_at)<=julianday('now') AND julianday(ends_at)>julianday('now') ORDER BY ends_at DESC LIMIT 1", (chat_id,))
        phase = "college" if row["availability"] != "sleep" and self._is_college_window(local) else "free"
        return {
            "availability": event["availability"] if event else row["availability"],
            "sleep_until": row["sleep_until"], "event": event, "phase": phase,
            "local_hour": local.hour,
        }

    async def is_sleeping(self, chat_id: int) -> bool:
        return (await self.state(chat_id))["availability"] == "sleep"

    @staticmethod
    def allows_delayed_reply(state: dict) -> bool:
        """Free afternoon/evening replies should not gain invented excuses."""
        event = state.get("event")
        return state.get("phase") == "college" or bool(event and event["availability"] in {"busy", "away"})
