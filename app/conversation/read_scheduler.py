"""Durable internal read simulation, deliberately independent from replies."""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


class ReadTimingEngine:
    def __init__(self, settings, rng=None):
        self.settings = settings
        self.rng = rng or random.uniform

    def _range(self, low_name: str, high_name: str) -> float:
        low, high = getattr(self.settings, low_name), getattr(self.settings, high_name)
        return self.rng(min(low, high), max(low, high))

    def delay(self, daily_state: dict, *, active_conversation: bool) -> float:
        if daily_state["availability"] == "sleep":
            return self._range("read_delay_after_wake_min_seconds", "read_delay_after_wake_max_seconds")
        event = daily_state.get("event")
        event_availability = event["availability"] if event else None
        if event_availability == "away":
            return self._range("read_delay_away_min_seconds", "read_delay_away_max_seconds")
        if event_availability == "busy":
            return self._range("read_delay_busy_min_seconds", "read_delay_busy_max_seconds")
        if daily_state.get("phase") == "college":
            return self._range("read_delay_college_min_seconds", "read_delay_college_max_seconds")
        if active_conversation:
            return self._range("read_delay_active_free_min_seconds", "read_delay_active_free_max_seconds")
        return self._range("read_delay_free_min_seconds", "read_delay_free_max_seconds")


class ReadScheduler:
    """One durable read claim per batch; a later unread tail gets a new job."""
    def __init__(self, db, presence, timing: ReadTimingEngine, poll_seconds: float = 1.0, now=None):
        self.db, self.presence, self.timing, self.poll_seconds = db, presence, timing, poll_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.on_messages_read = None

    def bind(self, callback):
        self.on_messages_read = callback

    async def schedule(self, user_id: int, chat_id: int, boundary_message_id: int):
        state = await self.presence.state(chat_id)
        existing = await self.db.fetchone(
            "SELECT * FROM scheduled_reads WHERE chat_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (chat_id,)
        )
        if existing:
            boundary = max(existing["boundary_message_id"], boundary_message_id)
            if boundary != existing["boundary_message_id"]:
                await self.db.execute(
                    "UPDATE scheduled_reads SET boundary_message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                    (boundary, existing["id"]),
                )
            log.info("read_coalesced chat_id=%s boundary_message_id=%s read_after=%s", chat_id, boundary, existing["read_after"])
            return existing["id"]

        signals = await self.db.chat_read_signals(chat_id)
        active = signals["seconds_since_bot"] is not None and signals["seconds_since_bot"] < 75
        now = self.now()
        delay = self.timing.delay(state, active_conversation=active)
        if state["availability"] == "sleep" and state["sleep_until"]:
            wake = datetime.strptime(state["sleep_until"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            read_at = wake + timedelta(seconds=delay)
        else:
            read_at = now + timedelta(seconds=delay)
        read_after = read_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = await self.db.execute(
            "INSERT INTO scheduled_reads(chat_id,user_id,boundary_message_id,read_after,status) VALUES(?,?,?,?, 'pending')",
            (chat_id, user_id, boundary_message_id, read_after),
        )
        event = state.get("event")
        log.info(
            "read_scheduled chat_id=%s phase=%s availability=%s event_availability=%s delay_seconds=%.2f boundary_message_id=%s",
            chat_id, state.get("phase"), state.get("availability"), event["availability"] if event else None,
            delay, boundary_message_id,
        )
        return result.lastrowid

    async def recover_after_restart(self):
        result = await self.db.execute("UPDATE scheduled_reads SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE status='processing'")
        if result.rowcount:
            log.info("read_recovered_after_restart count=%s", result.rowcount)

    async def is_current(self, record) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM scheduled_reads WHERE id=? AND chat_id=? AND status='processing'",
            (record["id"], record["chat_id"]),
        )
        return bool(row)

    async def process_due(self):
        if not self.on_messages_read:
            return
        records = await self.db.fetchall(
            "SELECT * FROM scheduled_reads WHERE status='pending' AND julianday(read_after)<=julianday('now') ORDER BY read_after,id"
        )
        for record in records:
            claimed = await self.db.execute(
                "UPDATE scheduled_reads SET status='processing',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (record["id"],)
            )
            if not claimed.rowcount or not await self.is_current(record):
                continue
            log.info("read_due chat_id=%s boundary_message_id=%s", record["chat_id"], record["boundary_message_id"])
            try:
                await self.on_messages_read(record)
            except Exception:
                log.exception("read_processing_failed chat_id=%s boundary_message_id=%s", record["chat_id"], record["boundary_message_id"])
                continue
            await self.db.execute("UPDATE scheduled_reads SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='processing'", (record["id"],))
            log.info("read_completed chat_id=%s boundary_message_id=%s", record["chat_id"], record["boundary_message_id"])

    async def run(self):
        await self.recover_after_restart()
        while True:
            try:
                await self.process_due()
            except Exception:
                log.exception("read_scheduler_cycle_failed")
            await asyncio.sleep(self.poll_seconds)
