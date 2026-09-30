import asyncio
import logging
import uuid

from app.llm.schemas import LLMRequest

log = logging.getLogger(__name__)


class InitiativeScheduler:
    def __init__(self, db, manager, lifecycle, response_scheduler, context, settings, buffer=None):
        self.db, self.manager, self.lifecycle, self.response_scheduler, self.context, self.settings, self.buffer = db, manager, lifecycle, response_scheduler, context, settings, buffer
        self.tasks: dict[int, asyncio.Task] = {}

    def cancel_chat(self, chat_id: int):
        task = self.tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            log.info("initiative_cancelled chat_id=%s", chat_id)

    async def eligibility(self, chat_id: int):
        state = await self.lifecycle.get(chat_id)
        if self.manager.has_active_generation(chat_id): return False, "active_generation", state
        if await self.response_scheduler.has_pending(chat_id): return False, "pending_delayed_response", state
        if self.buffer and (chat_id in self.buffer.items or chat_id in self.buffer.tasks): return False, "debouncing_user_message", state
        event = await self.db.fetchone("SELECT title FROM daily_events WHERE chat_id=? AND julianday(ends_at)>julianday('now') AND julianday(starts_at)<=julianday('now') LIMIT 1", (chat_id,))
        if (not state["expects_reply"] or state["followup_importance"] <= 0) and not event: return False, "no_semantic_followup_or_event", state
        idle = await self.db.fetchone("SELECT (julianday('now') - julianday(?)) * 1440 AS minutes", (state["last_meaningful_interaction_at"],))
        if (idle["minutes"] or 0) < self.settings.initiative_min_idle_minutes: return False, "min_idle", state
        cooldown = await self.db.fetchone("SELECT (julianday('now') - julianday(MAX(created_at))) * 24 AS hours FROM initiative_history WHERE chat_id=?", (chat_id,))
        if cooldown["hours"] is not None and cooldown["hours"] < self.settings.initiative_cooldown_hours: return False, "cooldown", state
        daily = await self.db.fetchone("SELECT COUNT(*) AS count FROM initiative_history WHERE chat_id=? AND date(created_at)=date('now')", (chat_id,))
        if daily["count"] >= self.settings.initiative_max_per_day: return False, "daily_limit", state
        unanswered = await self.db.fetchone("SELECT COUNT(*) AS count FROM initiative_history h WHERE h.chat_id=? AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.chat_id=h.chat_id AND m.sender='user' AND julianday(m.timestamp) > julianday(h.created_at))", (chat_id,))
        if unanswered["count"] >= self.settings.initiative_max_unanswered: return False, "max_unanswered", state
        return True, "eligible", state

    async def check_chat(self, user_id: int, chat_id: int):
        ok, reason, _state = await self.eligibility(chat_id)
        log.info("initiative_eligibility chat_id=%s eligible=%s reason=%s", chat_id, ok, reason)
        if not ok: return False
        task = asyncio.create_task(self._decide(user_id, chat_id))
        self.tasks[chat_id] = task
        try: return await task
        except asyncio.CancelledError: return False
        finally:
            if self.tasks.get(chat_id) is task: self.tasks.pop(chat_id, None)

    async def _decide(self, user_id: int, chat_id: int):
        system, context = await self.context.build(user_id, chat_id)
        decision = await self.manager.provider.decide_initiative(LLMRequest(system=system, context=context, user_turn=""))
        log.info("initiative_llm_decision chat_id=%s should_message=%s reason=%s", chat_id, decision.should_message, decision.reason)
        if not decision.should_message:
            log.info("initiative_skipped chat_id=%s reason=%s", chat_id, decision.reason)
            return False
        generation = str(uuid.uuid4())
        self.manager.generations[chat_id] = generation
        sent = await self.manager.enqueue_initiative(chat_id, generation, decision.actions)
        if sent:
            await self.db.execute("INSERT INTO initiative_history(chat_id,reason) VALUES(?,?)", (chat_id, decision.reason))
            log.info("initiative_sent chat_id=%s reason=%s", chat_id, decision.reason)
        return sent

    async def run_once(self):
        await self.lifecycle.transition_due()
        rows = await self.db.fetchall("SELECT chat_id, MAX(user_id) AS user_id FROM messages WHERE sender='user' GROUP BY chat_id")
        for row in rows:
            await self.check_chat(row["user_id"], row["chat_id"])

    async def run(self):
        while True:
            try:
                if self.settings.initiative_enabled:
                    await self.run_once()
            except Exception:
                log.exception("initiative_scheduler_cycle_failed")
            await asyncio.sleep(self.settings.initiative_check_interval_minutes * 60)
