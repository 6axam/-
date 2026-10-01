import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


class ResponseScheduler:
    """Persists delayed work but asks the LLM only when the response is due."""
    def __init__(self, db, timing, poll_seconds: float = 1.0, active_conversation_window_seconds: float = 120.0):
        self.db, self.timing, self.poll_seconds = db, timing, poll_seconds
        self.active_conversation_window_seconds = active_conversation_window_seconds
        self.manager = None
        self._task = None

    def bind(self, manager): self.manager = manager

    async def is_active_conversation(self, chat_id: int) -> bool:
        return await self.db.chat_is_active(chat_id, self.active_conversation_window_seconds)

    async def schedule(self, user_id: int, chat_id: int, generation_id: str, urgency: str, *, daily_state: dict | None = None):
        state = await self.manager.emotional_state.get() if self.manager and self.manager.emotional_state else None
        signals = await self.db.chat_response_signals(chat_id)
        event = (daily_state or {}).get("event")
        event_availability = event["availability"] if event and event["availability"] in {"busy", "away"} else None
        delay = self.timing.delay(
            urgency, state, active_conversation=await self.is_active_conversation(chat_id),
            pending_messages=signals["user_messages"], daily_phase=(daily_state or {}).get("phase", "free"),
            event_availability=event_availability,
        )
        respond_after = (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        delay_event_id = event["id"] if event_availability else None
        existing = await self.db.fetchone("SELECT id FROM scheduled_responses WHERE chat_id=? AND status='pending'", (chat_id,))
        if existing:
            await self.db.execute("UPDATE scheduled_responses SET respond_after=?, generation_id=?, delay_event_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (respond_after, generation_id, delay_event_id, existing["id"]))
            return existing["id"]
        result = await self.db.execute("INSERT INTO scheduled_responses(chat_id,user_id,respond_after,generation_id,status,delay_event_id) VALUES(?,?,?,?, 'pending',?)", (chat_id, user_id, respond_after, generation_id, delay_event_id))
        return result.lastrowid

    async def schedule_at(self, user_id: int, chat_id: int, generation_id: str, respond_after: str, urgency: str = "normal"):
        existing = await self.db.fetchone("SELECT id FROM scheduled_responses WHERE chat_id=? AND status='pending'", (chat_id,))
        if existing:
            await self.db.execute("UPDATE scheduled_responses SET respond_after=?,generation_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (respond_after, generation_id, existing["id"]))
            return existing["id"]
        result = await self.db.execute("INSERT INTO scheduled_responses(chat_id,user_id,respond_after,generation_id,status) VALUES(?,?,?,?, 'pending')", (chat_id, user_id, respond_after, generation_id))
        return result.lastrowid

    async def recover_after_restart(self):
        await self.db.execute("UPDATE scheduled_responses SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE status='processing'")

    async def cancel_chat(self, chat_id: int) -> int:
        """Persistently invalidate delayed work superseded by a new user turn."""
        result = await self.db.execute(
            "UPDATE scheduled_responses SET status='cancelled',updated_at=CURRENT_TIMESTAMP "
            "WHERE chat_id=? AND status IN ('pending','processing')",
            (chat_id,),
        )
        if result.rowcount:
            log.info("scheduled_responses_cancelled chat_id=%s count=%s", chat_id, result.rowcount)
        return result.rowcount

    async def is_current(self, record) -> bool:
        """Final durable fence against a claim/cancel race."""
        row = await self.db.fetchone(
            "SELECT 1 FROM scheduled_responses WHERE id=? AND chat_id=? AND generation_id=? AND status='processing'",
            (record["id"], record["chat_id"], record["generation_id"]),
        )
        return bool(row)

    async def has_pending(self, chat_id: int) -> bool:
        return bool(await self.db.fetchone("SELECT 1 FROM scheduled_responses WHERE chat_id=? AND status='pending'", (chat_id,)))

    async def due(self):
        return await self.db.fetchall("SELECT * FROM scheduled_responses WHERE status='pending' AND julianday(respond_after) <= julianday('now') ORDER BY respond_after")

    async def process_due(self):
        if not self.manager:
            return
        for record in await self.due():
            # Claim first: restart/repeated loop invocations cannot generate twice.
            claimed = await self.db.execute("UPDATE scheduled_responses SET status='processing', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (record["id"],))
            if claimed.rowcount and await self.is_current(record):
                await self.manager.handle_scheduled(record)

    async def run(self):
        await self.recover_after_restart()
        while True:
            try:
                await self.process_due()
            except Exception:
                log.exception("response_scheduler_cycle_failed")
            await asyncio.sleep(self.poll_seconds)

    def start(self):
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self.run())
        return self._task

    async def complete(self, schedule_id: int):
        # Never overwrite cancellation that raced with a claimed response.
        await self.db.execute("UPDATE scheduled_responses SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='processing'", (schedule_id,))
