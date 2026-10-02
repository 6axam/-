import asyncio
import logging
import random
import uuid

from app.llm.schemas import LLMRequest

log = logging.getLogger(__name__)


class InitiativeScheduler:
    def __init__(self, db, manager, lifecycle, response_scheduler, context, settings, buffer=None, presence=None, rng=None):
        self.db, self.manager, self.lifecycle, self.response_scheduler, self.context = db, manager, lifecycle, response_scheduler, context
        self.settings, self.buffer, self.presence, self.rng = settings, buffer, presence, rng or random.random
        self.tasks: dict[int, asyncio.Task] = {}

    def cancel_chat(self, chat_id: int):
        task = self.tasks.pop(chat_id, None)
        if task and not task.done(): task.cancel(); log.info("initiative_cancelled_stale chat_id=%s", chat_id)

    async def _spontaneous_probability(self, chat_id: int) -> float:
        """A bounded preference only; all eligibility safety rails stay absolute."""
        base = getattr(self.settings, "initiative_spontaneous_probability", .55)
        conversation_context = getattr(self.context, "conversation_context", None)
        engine = getattr(conversation_context, "emotion_engine", None)
        if not engine:
            return base
        state = (await engine.get(chat_id)).values()
        pull = (state["social_need"] - .45) + (state["curiosity"] - .55) + (state["warmth"] - .65)
        resistance = (state["fatigue"] - .25) + (state["hurt"] - .02) + (state["irritation"] - .05) + (state["anxiety"] - .10)
        return max(.15, min(.85, base * (1 + pull * .35 - resistance * .30)))

    async def _safety_block(self, chat_id: int, daily_state=None):
        if self.manager.has_active_generation(chat_id): return "active_generation"
        if await self.response_scheduler.has_pending(chat_id): return "pending_delayed_response"
        if self.buffer and (chat_id in self.buffer.items or chat_id in self.buffer.tasks): return "debouncing_user_message"
        if await self.db.has_unread_user_messages(chat_id): return "unread_user_message"
        if await self.db.has_active_scheduled_read(chat_id): return "scheduled_read"
        state = daily_state or (await self.presence.state(chat_id) if self.presence else None)
        if state and state.get("availability") == "sleep": return "sleep"
        return None

    async def eligibility(self, chat_id: int):
        state = dict(await self.lifecycle.get(chat_id))
        daily_state = await self.presence.state(chat_id) if self.presence else {"availability": "available", "event": None}
        blocked = await self._safety_block(chat_id, daily_state)
        if blocked: return False, blocked, state
        if self.presence and getattr(self.settings, "bedtime_ritual_enabled", False):
            state_for = getattr(self.presence, "bedtime_state_for", None)
            bedtime = await state_for(chat_id, window_minutes=getattr(self.settings, "bedtime_window_minutes", 20)) if state_for else self.presence.bedtime_state(window_minutes=getattr(self.settings, "bedtime_window_minutes", 20))
        else:
            bedtime = None
        if bedtime and bedtime["bedtime_window"]:
            if await self.db.bedtime_done(chat_id, bedtime["local_day"]): return False, "bedtime_already_done", state
            if self.rng() >= getattr(self.settings, "bedtime_initiative_probability", .65): return False, "bedtime_gate_closed", state
            state["initiative_basis"], state["daily_state"], state["bedtime_state"] = "bedtime", daily_state, bedtime
            return True, "bedtime", state
        event = daily_state.get("event")
        semantic = bool(state["expects_reply"] and state["followup_importance"] > 0) or bool(event)
        idle = await self.db.fetchone("SELECT (julianday('now')-julianday(?))*1440 AS minutes", (state["last_meaningful_interaction_at"],))
        idle_minutes = idle["minutes"] or 0
        if idle_minutes < self.settings.initiative_min_idle_minutes: return False, "min_idle", state
        if not semantic:
            if idle_minutes < getattr(self.settings, "initiative_spontaneous_min_idle_minutes", 60): return False, "spontaneous_min_idle", state
            basis = "spontaneous"
        else:
            basis = "semantic"
        cooldown = await self.db.fetchone("SELECT (julianday('now')-julianday(MAX(created_at)))*24 AS hours FROM initiative_history WHERE chat_id=?", (chat_id,))
        if cooldown["hours"] is not None and cooldown["hours"] < self.settings.initiative_cooldown_hours: return False, "cooldown", state
        daily = await self.db.fetchone("SELECT COUNT(*) AS count FROM initiative_history WHERE chat_id=? AND date(created_at)=date('now')", (chat_id,))
        if daily["count"] >= self.settings.initiative_max_per_day: return False, "daily_limit", state
        unanswered = await self.db.fetchone("SELECT COUNT(*) AS count FROM initiative_history h WHERE h.chat_id=? AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.chat_id=h.chat_id AND m.sender='user' AND julianday(m.timestamp)>julianday(h.created_at))", (chat_id,))
        if unanswered["count"] >= self.settings.initiative_max_unanswered: return False, "max_unanswered", state
        if basis == "spontaneous" and self.rng() >= await self._spontaneous_probability(chat_id):
            return False, "spontaneous_gate_closed", state
        state["initiative_basis"], state["daily_state"] = basis, daily_state
        return True, basis, state

    async def check_chat(self, user_id: int, chat_id: int):
        ok, reason, state = await self.eligibility(chat_id)
        if not ok:
            level = logging.INFO if reason in {"unread_user_message", "scheduled_read"} else logging.DEBUG
            log.log(level, "initiative_blocked chat_id=%s reason=%s", chat_id, reason)
            return False
        basis = state["initiative_basis"]
        log.info("initiative_candidate chat_id=%s basis=%s", chat_id, basis)
        task = asyncio.create_task(self._decide(user_id, chat_id, basis, state["daily_state"], state.get("bedtime_state")))
        self.tasks[chat_id] = task
        try: return await task
        except asyncio.CancelledError: return False
        finally:
            if self.tasks.get(chat_id) is task: self.tasks.pop(chat_id, None)

    async def _decide(self, user_id: int, chat_id: int, basis="spontaneous", daily_state=None, bedtime_state=None):
        try:
            built = await self.context.build(user_id, chat_id, basis=basis, daily_state=daily_state, bedtime_state=bedtime_state)
        except TypeError:  # narrow legacy/dummy test contexts
            built = await self.context.build(user_id, chat_id)
        system, context, telemetry = (*built, {})[:3] if len(built) == 2 else built
        decision = await self.manager.provider.decide_initiative(LLMRequest(system=system, context=context, user_turn="", telemetry={**telemetry, "kind": "initiative", "initiative_basis": basis}))
        log.info("initiative_llm_decision chat_id=%s should_message=%s kind=%s", chat_id, decision.should_message, decision.kind)
        if not decision.should_message: return False
        # No second probability draw: this is strictly a stale/safety fence.
        if await self._safety_block(chat_id):
            log.info("initiative_cancelled_stale chat_id=%s basis=%s", chat_id, basis)
            return False
        generation = str(uuid.uuid4()); self.manager.generations[chat_id] = generation
        sent = await self.manager.enqueue_initiative(chat_id, generation, decision.actions)
        if sent:
            await self.db.execute("INSERT INTO initiative_history(chat_id,reason,basis,kind) VALUES(?,?,?,?)", (chat_id, decision.reason, basis, decision.kind))
            if basis == "bedtime":
                await self.db.record_bedtime(chat_id, bedtime_state["local_day"], "initiative")
            candidate = decision.self_life_event_candidate
            if candidate and getattr(self.context.conversation_context, "self_life", None):
                ids = set(telemetry.get("retrieved_life_event_ids", []))
                await self.context.conversation_context.self_life.apply(candidate, source_chat_id=chat_id, source_turn_id=None, allowed_target_ids=ids, local_day=self.context.conversation_context.self_life.local_day(getattr(self.context.conversation_context, "timezone_name", "Europe/Kyiv")))
            log.info("initiative_sent chat_id=%s basis=%s kind=%s", chat_id, basis, decision.kind)
        return sent

    async def run_once(self):
        await self.lifecycle.transition_due()
        for row in await self.db.fetchall("SELECT chat_id,MAX(user_id) AS user_id FROM messages WHERE sender='user' GROUP BY chat_id"):
            await self.check_chat(row["user_id"], row["chat_id"])

    async def run(self):
        while True:
            try:
                if self.settings.initiative_enabled: await self.run_once()
            except Exception: log.exception("initiative_scheduler_cycle_failed")
            await asyncio.sleep(self.settings.initiative_check_interval_minutes * 60)
