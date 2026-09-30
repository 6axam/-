"""Persistent, deliberately approximate daily rhythm; all timestamps are UTC."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class DailyPresenceManager:
    def __init__(self, db, timezone_name: str = "Europe/Kyiv", sleep_start: int = 1, wake_hour: int = 9):
        self.db, self.zone = db, ZoneInfo(timezone_name)
        self.timezone_name, self.sleep_start, self.wake_hour = timezone_name, sleep_start, wake_hour

    async def state(self, chat_id: int, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self.zone); day = local.date().isoformat()
        row = await self.db.fetchone("SELECT * FROM daily_presence WHERE chat_id=?", (chat_id,))
        if not row or row["local_day"] != day:
            # Stable per day/chat jitter keeps a reboot from changing the night.
            jitter = (hash(f"{chat_id}:{day}") % 91) - 45
            crosses_midnight = self.sleep_start > self.wake_hour
            sleeping = (local.hour >= self.sleep_start or local.hour < self.wake_hour) if crosses_midnight else (self.sleep_start <= local.hour < self.wake_hour)
            wake = local.replace(hour=self.wake_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=jitter)
            if sleeping and crosses_midnight and local.hour >= self.sleep_start:
                wake += timedelta(days=1)
            until = wake.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if sleeping else None
            await self.db.execute("INSERT INTO daily_presence(chat_id,local_day,timezone,sleep_until,availability) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET local_day=excluded.local_day,timezone=excluded.timezone,sleep_until=excluded.sleep_until,availability=excluded.availability,updated_at=CURRENT_TIMESTAMP", (chat_id, day, self.timezone_name, until, "sleep" if sleeping else "available"))
        row = await self.db.fetchone("SELECT * FROM daily_presence WHERE chat_id=?", (chat_id,))
        event = await self.db.fetchone("SELECT * FROM daily_events WHERE chat_id=? AND julianday(starts_at)<=julianday('now') AND julianday(ends_at)>julianday('now') ORDER BY ends_at DESC LIMIT 1", (chat_id,))
        return {"availability": event["availability"] if event else row["availability"], "sleep_until": row["sleep_until"], "event": event}

    async def is_sleeping(self, chat_id: int) -> bool:
        return (await self.state(chat_id))["availability"] == "sleep"
