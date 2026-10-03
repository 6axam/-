import asyncio, logging, uuid
from app.actions.models import Action, ActionType
from app.llm.schemas import EmotionalUpdate, LLMRequest, LLMResponse, ResponseTiming

log = logging.getLogger(__name__)

class ConversationManager:
    def __init__(self, provider, context, queue, personality=None, emotional_state=None, scheduler=None, splitter=None, lifecycle=None, media=None, presence=None, memory_extractor=None, memory_manager=None, self_life=None, timezone_name="Europe/Kyiv", bedtime_ritual_enabled=False, bedtime_window_minutes=20, emotion_engine=None, episodic_memory=None):
        self.provider,self.context,self.queue = provider,context,queue
        self.personality, self.emotional_state, self.scheduler, self.splitter, self.lifecycle = personality, emotional_state, scheduler, splitter, lifecycle
        self.media = media
        self.presence = presence
        self.memory_extractor = memory_extractor
        self.memory_manager = memory_manager
        self.emotion_engine = emotion_engine
        self.episodic_memory = episodic_memory
        self.self_life, self.timezone_name = self_life, timezone_name
        self.bedtime_ritual_enabled, self.bedtime_window_minutes = bedtime_ritual_enabled, bedtime_window_minutes
        self.initiative_scheduler = None
        self.generations: dict[int, str] = {}
        # Incremented at arrival time, before any await. It closes the small
        # read-claim → generation race where a newer message arrives while an
        # older batch is still entering `handle_turn`.
        self.interrupt_versions: dict[int, int] = {}
        self.requests: dict[int, asyncio.Task] = {}
        # One lightweight Telegram typing session per active generation.  It is
        # deliberately separate from ActionQueue typing, which happens just
        # before each individual outgoing message.
        self.typing_sessions: dict[int, tuple[str, object]] = {}
        if scheduler: scheduler.bind(self)

    async def _start_generation_typing(self, chat_id: int, generation: str) -> None:
        current = self.typing_sessions.get(chat_id)
        if current and current[0] == generation:
            return
        if current:
            await self._stop_generation_typing(chat_id, current[0])
        start = getattr(self.queue.executor, "start_generation_typing", None)
        if not start:
            return
        try:
            session = await start(chat_id)
        except Exception:
            # Presence is cosmetic: a Telegram issue must not block a reply.
            log.debug("generation_typing_start_failed chat_id=%s", chat_id, exc_info=True)
            return
        self.typing_sessions[chat_id] = (generation, session)

    async def _stop_generation_typing(self, chat_id: int, generation: str) -> None:
        current = self.typing_sessions.get(chat_id)
        if not current or current[0] != generation:
            return
        self.typing_sessions.pop(chat_id, None)
        stop = getattr(self.queue.executor, "stop_generation_typing", None)
        if not stop:
            return
        try:
            await stop(current[1])
        except Exception:
            log.debug("generation_typing_stop_failed chat_id=%s", chat_id, exc_info=True)

    async def interrupt(self, chat_id: int) -> None:
        # Do this first and durably: a scheduler may otherwise claim the row
        # while a new incoming Telegram message is still in debounce.
        self.interrupt_versions[chat_id] = self.interrupt_versions.get(chat_id, 0) + 1
        if self.scheduler:
            await self.scheduler.cancel_chat(chat_id)
        generation = self.generations.pop(chat_id, None)
        if generation:
            await self._stop_generation_typing(chat_id, generation)
            self.queue.cancel_generation(generation)
        task = self.requests.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
        if self.initiative_scheduler:
            self.initiative_scheduler.cancel_chat(chat_id)

    def has_active_generation(self, chat_id: int) -> bool:
        task = self.requests.get(chat_id)
        return bool(task and not task.done()) or self.queue.is_busy(chat_id)

    async def observe_reaction(self, chat_id: int, emoji: str) -> None:
        """A reaction is a lightweight, nonverbal signal, never a new turn."""
        if emoji in {"❤️", "❤", "🔥", "🥰", "😍"} and self.emotion_engine:
            await self.emotion_engine.apply_delta(chat_id, {"warmth": .01, "curiosity": .01})
        elif emoji in {"❤️", "❤", "🔥", "🥰", "😍"} and self.emotional_state:
            await self.emotional_state.apply(
                EmotionalUpdate(social_energy_delta=.01, conversation_interest_delta=.025)
            )
        log.debug("nonverbal_reaction_received chat_id=%s emoji=%s", chat_id, emoji)

    async def handle_turn(self, user_id, chat_id, text, turn_id=None, target_message_id=None, user_content=None):
        expected_interrupt_version = self.interrupt_versions.get(chat_id, 0) + 1
        await self.interrupt(chat_id)
        generation = str(uuid.uuid4())
        if self.interrupt_versions.get(chat_id) != expected_interrupt_version:
            log.debug("Dropping superseded turn before generation chat_id=%s", chat_id)
            return generation
        self.generations[chat_id] = generation
        daily_state = await self.presence.state(chat_id) if self.presence else None
        if daily_state and daily_state["availability"] == "sleep":
            await self.scheduler.schedule_at(user_id, chat_id, generation, daily_state["sleep_until"], "normal")
            log.info("presence_sleep_defers_turn chat_id=%s until=%s", chat_id, daily_state["sleep_until"])
            return generation
        if self.scheduler and await self.scheduler.has_pending(chat_id):
            await self.scheduler.schedule(user_id, chat_id, generation, "normal")
            return generation
        if self.scheduler:
            active_check = getattr(self.scheduler, "is_active_conversation", None)
            active = await active_check(chat_id) if active_check else False
            if active:
                # A recent reply means Anya is already looking at this chat.
                # This deterministic path avoids an unnecessary timing LLM
                # request; scheduler still applies busy/away caps.
                await self.scheduler.schedule(user_id, chat_id, generation, "normal", daily_state=daily_state)
                log.info("timing_decision_skipped_active_conversation chat_id=%s", chat_id)
                return generation
            # A free period without a persisted busy/away event always goes
            # straight to generation, so do not spend a second LLM call on a
            # delay that the backend would reject anyway.
            if daily_state and not self.presence.allows_delayed_reply(daily_state):
                log.info("timing_decision_skipped_free_period chat_id=%s phase=%s", chat_id, daily_state["phase"])
                return await self._generate(user_id, chat_id, text, generation, turn_id, target_message_id, user_content)
            system, context = await self.context.build(user_id, chat_id, text)
            event = daily_state.get("event") if daily_state else None
            timing_signal = (
                "TIMING DAILY STATE\n"
                f"phase={(daily_state or {}).get('phase', 'unknown')}; "
                f"active_event={'true' if event else 'false'}; "
                f"event_availability={event['availability'] if event else 'none'}"
            )
            # This compact state is deliberately only attached to the timing
            # decider. It never enters the primary conversation prompt.
            request = LLMRequest(system=system, context=context + "\n\n" + timing_signal, user_turn=text, user_content=user_content or [])
            decider = getattr(self.provider, "decide_timing", None)
            # Begin presence before the first network round-trip, including the
            # timing decision call.  The same session continues into generate.
            await self._start_generation_typing(chat_id, generation)
            try:
                timing = await decider(request) if decider else ResponseTiming()
            except Exception:
                log.warning("Timing decision failed; responding immediately", exc_info=True)
                timing = ResponseTiming()
            if self.generations.get(chat_id) != generation:
                log.debug("Dropping stale timing decision for generation %s", generation)
                await self._stop_generation_typing(chat_id, generation)
                return generation
            if timing.mode == "delayed":
                await self._stop_generation_typing(chat_id, generation)
                await self.scheduler.schedule(user_id, chat_id, generation, timing.urgency, daily_state=daily_state)
                return generation
        return await self._generate(user_id, chat_id, text, generation, turn_id, target_message_id, user_content)

    async def _generate(self, user_id, chat_id, text, generation, turn_id=None, target_message_id=None, user_content=None, delay_event=None, bedtime_state=None):
        if self.generations.get(chat_id) != generation:
            log.debug("Dropping stale generation before context build generation=%s", generation)
            return generation
        life_state = await self.presence.state(chat_id) if self.presence else None
        if bedtime_state is None and self.presence and self.bedtime_ritual_enabled:
            state_for = getattr(self.presence, "bedtime_state_for", None)
            bedtime_state = await state_for(chat_id, window_minutes=self.bedtime_window_minutes) if state_for else self.presence.bedtime_state(window_minutes=self.bedtime_window_minutes)
            if bedtime_state["bedtime_window"]:
                bedtime_state["already_said_goodnight"] = await self.context.db.bedtime_done(chat_id, bedtime_state["local_day"])
        self_life_gate_open = bool(self.self_life and (not life_state or life_state.get("availability") != "sleep") and self.self_life.continuation_gate())
        log.info("self_life_gate_%s chat_id=%s", "open" if self_life_gate_open else "closed", chat_id)
        build_with_breakdown = getattr(self.context, "build_with_breakdown", None)
        if build_with_breakdown:
            context_kwargs = {"delay_event": delay_event} if delay_event is not None else {}
            if self.self_life:
                context_kwargs.update(self_life_gate_open=self_life_gate_open, life_state=life_state)
            if bedtime_state:
                context_kwargs["bedtime_state"] = bedtime_state
            if delay_event is not None:
                system, context, breakdown = await build_with_breakdown(user_id, chat_id, text, **context_kwargs)
            else:
                system, context, breakdown = await build_with_breakdown(user_id, chat_id, text, **context_kwargs)
        else:
            # Compatibility for narrow test/dummy contexts; production uses
            # ContextBuilder and always emits numeric breakdown telemetry.
            system, context = await self.context.build(user_id, chat_id, text)
            breakdown = {}
        if self.generations.get(chat_id) != generation:
            log.debug("Dropping stale generation before LLM request generation=%s", generation)
            return generation
        await self._start_generation_typing(chat_id, generation)
        target_hint = f"\n\nCURRENT USER MESSAGE ID FOR OPTIONAL REACTION: {target_message_id}" if target_message_id else ""
        task = asyncio.create_task(self.provider.generate(LLMRequest(
            system=system, context=context, user_turn=text + target_hint,
            user_content=user_content or [], telemetry={**breakdown, "generation_id": generation},
        )))
        self.requests[chat_id] = task
        provider_failed = False
        try: response = await task
        except asyncio.CancelledError:
            return generation
        except Exception:
            log.exception("LLM generation failed")
            response = LLMResponse(actions=[Action(type=ActionType.text, text="бля, у меня что-то подвисло. попробуй ещё раз через минуту")])
            provider_failed = True
        finally:
            if self.requests.get(chat_id) is task:
                self.requests.pop(chat_id, None)
            await self._stop_generation_typing(chat_id, generation)
        if self.generations.get(chat_id) != generation:
            log.debug("Dropping stale LLM response for generation %s", generation)
            return generation
        adjustment = response.bedtime_adjustment
        if adjustment.mode == "delay_once" and self.presence and not provider_failed:
            try:
                plan = await self.presence.delay_next_bedtime(chat_id, adjustment.delay_minutes)
                log.info("bedtime_delayed_by_owner chat_id=%s delay_minutes=%s sleep_at=%s", chat_id, adjustment.delay_minutes, plan["sleep_at"])
            except Exception:
                log.exception("bedtime_delay_persistence_failed chat_id=%s", chat_id)
        response.actions = await self._validate_action_targets(chat_id, response.actions, target_message_id)
        if self.splitter:
            response.actions = self.splitter.split_actions(response.actions)
        continuation = response.spontaneous_continuation
        has_normal_reply = bool(response.actions) and not all(action.type == ActionType.silence for action in response.actions)
        if self_life_gate_open and not provider_failed and has_normal_reply and continuation.send and continuation.text.strip():
            response.actions.append(Action(type=ActionType.text, text=continuation.text.strip()))
            log.info("self_life_continuation_sent chat_id=%s", chat_id)
            if continuation.event_candidate and self.generations.get(chat_id) == generation:
                try:
                    await self.self_life.apply(
                        continuation.event_candidate, source_chat_id=chat_id, source_turn_id=turn_id,
                        allowed_target_ids=set(breakdown.get("retrieved_life_event_ids", [])),
                        local_day=self.self_life.local_day(self.timezone_name),
                    )
                except Exception:
                    log.exception("self_life_persistence_failed chat_id=%s generation=%s", chat_id, generation)
        if self.personality:
            await self.personality.apply(response.self_updates, turn_id)
        if self.emotion_engine and not provider_failed:
            affective = getattr(self, "affective_engine", None)
            if affective:
                _before, _delta, current_emotions, _profile, _behavior = await affective.apply_appraisal(chat_id, response.affective_appraisal)
            else:
                current_emotions = await self.emotion_engine.apply_delta(chat_id, response.emotion_delta.values())
            if self.episodic_memory:
                await self.episodic_memory.apply(chat_id, response.memory_episode, current_emotions, resolve_episode_ids=response.resolve_episode_ids, generation_id=generation, message_id=target_message_id, exposed_ids=set(breakdown.get('retrieved_episode_ids', [])))
        elif self.emotional_state:
            await self.emotional_state.apply(response.emotional_update)
        if self.lifecycle:
            await self.lifecycle.apply(chat_id, response.conversation)
        if self.memory_extractor and self.memory_manager:
            try:
                candidates = self.memory_extractor.extract(response.memory_candidates)
                if candidates:
                    await self.memory_manager.apply(
                        user_id, chat_id, candidates, source_turn_id=turn_id,
                        retrieved_memory_ids=set(breakdown.get("retrieved_memory_ids", [])),
                    )
            except Exception:
                # A reply that was already generated remains deliverable even
                # if durable memory is temporarily unavailable.
                log.exception("memory_persistence_failed chat_id=%s generation=%s", chat_id, generation)
        if not response.actions or all(action.type == ActionType.silence for action in response.actions):
            log.info("silence_selected chat_id=%s generation=%s", chat_id, generation)
            return generation
        await self.queue.enqueue_many(chat_id,generation,response.actions)
        # Queue acceptance is the delivery-pipeline boundary.  Do not consume
        # today's ritual before enqueue succeeds.
        if bedtime_state and bedtime_state.get("bedtime_window") and not bedtime_state.get("already_said_goodnight"):
            farewell_words = ("спокойной ночи", "спать", "выруба", "отруба", "бб")
            if any(action.text and any(word in action.text.lower() for word in farewell_words) for action in response.actions if action.type == ActionType.text):
                await self.context.db.record_bedtime(chat_id, bedtime_state["local_day"], "conversation")
        return generation

    async def _validate_action_targets(self, chat_id, actions, current_message_id):
        safe = []
        for action in actions:
            if action.type == ActionType.reaction and not action.target_message_id:
                action.target_message_id = current_message_id
            if action.reply_to_message_id and not await self.context.db.known_user_message(chat_id, action.reply_to_message_id):
                log.warning("dropping_invalid_reply_target chat_id=%s target=%s", chat_id, action.reply_to_message_id)
                action.reply_to_message_id = None
            if action.type == ActionType.reaction and not action.target_message_id:
                log.debug("dropping_reaction_without_target chat_id=%s", chat_id); continue
            safe.append(action)
        return safe

    async def enqueue_initiative(self, chat_id: int, generation: str, actions):
        if self.generations.get(chat_id) != generation:
            log.info("initiative_cancelled chat_id=%s generation=%s", chat_id, generation)
            return False
        if self.splitter:
            actions = self.splitter.split_actions(actions)
        if not actions or all(action.type == ActionType.silence for action in actions):
            return False
        await self.queue.enqueue_many(chat_id, generation, actions)
        return True

    async def handle_scheduled(self, record):
        if not await self.scheduler.is_current(record):
            log.info("scheduled_response_stale_before_load id=%s", record["id"])
            return
        user_id, text, message_ids = await self.context.db.pending_user_message_ids(record["chat_id"])
        if not user_id or not text:
            await self.scheduler.complete(record["id"])
            return
        if not await self.scheduler.is_current(record):
            log.info("scheduled_response_stale_before_generation id=%s", record["id"])
            return
        generation = record["generation_id"]
        self.generations[record["chat_id"]] = generation
        images = await self.media.inputs_for_messages(record["chat_id"], message_ids) if self.media and getattr(self.provider, "supports_vision", False) else []
        if not await self.scheduler.is_current(record):
            log.info("scheduled_response_stale_before_dispatch id=%s", record["id"])
            return
        delay_event_id = record["delay_event_id"] if "delay_event_id" in record.keys() else None
        delay_event = None
        if delay_event_id:
            delay_event = await self.context.db.fetchone(
                "SELECT id,title,availability,mentionable FROM daily_events WHERE id=? AND chat_id=?",
                (delay_event_id, record["chat_id"]),
            )
        await self._generate(user_id, record["chat_id"], text, generation, user_content=images, delay_event=delay_event)
        await self.scheduler.complete(record["id"])
