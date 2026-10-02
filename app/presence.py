"""Persistent, deliberately approximate daily rhythm; all timestamps are UTC."""
import hashlib
from datetime import datetime, time, timedelta, timezone
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

    def _sleep_episode(self, chat_id: int, local: datetime, delay_minutes: int = 0) -> tuple[datetime, datetime] | None:
        """Return the active/surrounding episode's start and jittered wake.

        The jitter is keyed to the wake date.  That keeps a 23:00→07:00
        episode stable before and after midnight, and lets the actual wake
        minute—not just its hour—decide whether Anya is awake.
        """
        if self.sleep_start == self.wake_hour:
            return None
        start_time, wake_time = time(self.sleep_start), time(self.wake_hour)
        crosses_midnight = self.sleep_start > self.wake_hour
        if crosses_midnight:
            start_date = local.date() if local.timetz().replace(tzinfo=None) >= start_time else local.date() - timedelta(days=1)
            wake_date = start_date + timedelta(days=1)
        else:
            start_date = local.date()
            wake_date = start_date
        start = datetime.combine(start_date, start_time, tzinfo=self.zone) + timedelta(minutes=delay_minutes)
        wake = datetime.combine(wake_date, wake_time, tzinfo=self.zone)
        wake += timedelta(minutes=self._wake_jitter(chat_id, wake_date.isoformat()))
        return start, wake

    async def state(self, chat_id: int, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self.zone)
        base_episode = self._sleep_episode(chat_id, local)
        delay = await self.db.bedtime_delay_minutes(chat_id, base_episode[0].date().isoformat()) if base_episode else 0
        episode = self._sleep_episode(chat_id, local, delay)
        sleeping = bool(episode and episode[0] <= local < episode[1])
        wake = episode[1] if sleeping and episode else None
        day = local.date().isoformat()
        desired_availability = "sleep" if sleeping else "available"
        desired_until = wake.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if wake else None
        row = await self.db.fetchone("SELECT * FROM daily_presence WHERE chat_id=?", (chat_id,))
        if not row:
            await self.db.execute(
                "INSERT INTO daily_presence(chat_id,local_day,timezone,sleep_until,availability) VALUES(?,?,?,?,?)",
                (chat_id, day, self.timezone_name, desired_until, desired_availability),
            )
        elif (row["local_day"], row["timezone"], row["sleep_until"], row["availability"]) != (day, self.timezone_name, desired_until, desired_availability):
            await self.db.execute(
                "UPDATE daily_presence SET local_day=?,timezone=?,sleep_until=?,availability=?,updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
                (day, self.timezone_name, desired_until, desired_availability, chat_id),
            )
        now_utc = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        event = await self.db.fetchone(
            "SELECT * FROM daily_events WHERE chat_id=? AND julianday(starts_at)<=julianday(?) AND julianday(ends_at)>julianday(?) ORDER BY ends_at DESC LIMIT 1",
            (chat_id, now_utc, now_utc),
        )
        phase = "college" if not sleeping and self._is_college_window(local) else "free"
        return {
            "availability": "sleep" if sleeping else (event["availability"] if event else "available"),
            "sleep_until": desired_until, "event": event, "phase": phase,
            "local_hour": local.hour,
        }

    async def is_sleeping(self, chat_id: int) -> bool:
        return (await self.state(chat_id))["availability"] == "sleep"

    def bedtime_state(self, now: datetime | None = None, window_minutes: int = 20) -> dict:
        """Compact, deterministic state for the short window before sleep."""
        now = (now or datetime.now(timezone.utc)).astimezone(self.zone)
        start = datetime.combine(now.date(), time(self.sleep_start), tzinfo=self.zone)
        if now >= start:
            start += timedelta(days=1)
        return self._bedtime_state(now, start, window_minutes)

    async def bedtime_state_for(self, chat_id: int, now: datetime | None = None, window_minutes: int = 20) -> dict:
        """Bedtime state with the direct one-night delay, if one was saved."""
        now = (now or datetime.now(timezone.utc)).astimezone(self.zone)
        start = datetime.combine(now.date(), time(self.sleep_start), tzinfo=self.zone)
        if now >= start:
            start += timedelta(days=1)
        delay = await self.db.bedtime_delay_minutes(chat_id, start.date().isoformat())
        return self._bedtime_state(now, start + timedelta(minutes=delay), window_minutes)

    def _bedtime_state(self, now: datetime, start: datetime, window_minutes: int) -> dict:
        minutes = max(0, int((start - now).total_seconds() // 60))
        return {"bedtime_window": 0 < (start - now).total_seconds() <= window_minutes * 60,
                "sleep_soon": 0 < (start - now).total_seconds() <= window_minutes * 60,
                "minutes_until_sleep": minutes, "local_time": now.strftime("%Y-%m-%d %H:%M"),
                "local_day": now.date().isoformat()}

    async def delay_next_bedtime(self, chat_id: int, delay_minutes: int, now: datetime | None = None) -> dict:
        """Replace the next episode's delay; it never changes the default schedule."""
        now = (now or datetime.now(timezone.utc)).astimezone(self.zone)
        start = datetime.combine(now.date(), time(self.sleep_start), tzinfo=self.zone)
        if now >= start:
            start += timedelta(days=1)
        await self.db.set_bedtime_delay(chat_id, start.date().isoformat(), delay_minutes)
        delayed = start + timedelta(minutes=delay_minutes)
        return {"sleep_start_date": start.date().isoformat(), "sleep_at": delayed.strftime("%Y-%m-%d %H:%M")}

    @staticmethod
    def allows_delayed_reply(state: dict) -> bool:
        """Free afternoon/evening replies should not gain invented excuses."""
        event = state.get("event")
        return state.get("phase") == "college" or bool(event and event["availability"] in {"busy", "away"})
