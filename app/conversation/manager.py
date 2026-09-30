import asyncio, logging, uuid
from app.actions.models import Action, ActionType
from app.llm.schemas import EmotionalUpdate, LLMRequest, LLMResponse, ResponseTiming

log = logging.getLogger(__name__)

class ConversationManager:
    def __init__(self, provider, context, queue, personality=None, emotional_state=None, scheduler=None, splitter=None, lifecycle=None, media=None, presence=None):
        self.provider,self.context,self.queue = provider,context,queue
        self.personality, self.emotional_state, self.scheduler, self.splitter, self.lifecycle = personality, emotional_state, scheduler, splitter, lifecycle
        self.media = media
        self.presence = presence
        self.initiative_scheduler = None
        self.generations: dict[int, str] = {}
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
        if emoji in {"❤️", "❤", "🔥", "🥰", "😍"} and self.emotional_state:
            await self.emotional_state.apply(
                EmotionalUpdate(social_energy_delta=.01, conversation_interest_delta=.025)
            )
        log.debug("nonverbal_reaction_received chat_id=%s emoji=%s", chat_id, emoji)

    async def handle_turn(self, user_id, chat_id, text, turn_id=None, target_message_id=None, user_content=None):
        await self.interrupt(chat_id)
        generation = str(uuid.uuid4()); self.generations[chat_id] = generation
        if self.presence and await self.presence.is_sleeping(chat_id):
            state = await self.presence.state(chat_id)
            await self.scheduler.schedule_at(user_id, chat_id, generation, state["sleep_until"], "normal")
            log.info("presence_sleep_defers_turn chat_id=%s until=%s", chat_id, state["sleep_until"])
            return generation
        if self.scheduler and await self.scheduler.has_pending(chat_id):
            await self.scheduler.schedule(user_id, chat_id, generation, "normal")
            return generation
        if self.scheduler:
            system, context = await self.context.build(user_id, chat_id, text)
            request = LLMRequest(system=system, context=context, user_turn=text, user_content=user_content or [])
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
                await self.scheduler.schedule(user_id, chat_id, generation, timing.urgency)
                return generation
        return await self._generate(user_id, chat_id, text, generation, turn_id, target_message_id, user_content)

    async def _generate(self, user_id, chat_id, text, generation, turn_id=None, target_message_id=None, user_content=None):
        if self.generations.get(chat_id) != generation:
            log.debug("Dropping stale generation before context build generation=%s", generation)
            return generation
        build_with_breakdown = getattr(self.context, "build_with_breakdown", None)
        if build_with_breakdown:
            system, context, breakdown = await build_with_breakdown(user_id, chat_id, text)
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
        try: response = await task
        except asyncio.CancelledError:
            return generation
        except Exception:
            log.exception("LLM generation failed")
            response = LLMResponse(actions=[Action(type=ActionType.text, text="бля, у меня что-то подвисло. попробуй ещё раз через минуту")])
        finally:
            if self.requests.get(chat_id) is task:
                self.requests.pop(chat_id, None)
            await self._stop_generation_typing(chat_id, generation)
        if self.generations.get(chat_id) != generation:
            log.debug("Dropping stale LLM response for generation %s", generation)
            return generation
        response.actions = await self._validate_action_targets(chat_id, response.actions, target_message_id)
        if self.splitter:
            response.actions = self.splitter.split_actions(response.actions)
        if self.personality:
            await self.personality.apply(response.self_updates, turn_id)
        if self.emotional_state:
            await self.emotional_state.apply(response.emotional_update)
        if self.lifecycle:
            await self.lifecycle.apply(chat_id, response.conversation)
        if not response.actions or all(action.type == ActionType.silence for action in response.actions):
            log.info("silence_selected chat_id=%s generation=%s", chat_id, generation)
            return generation
        await self.queue.enqueue_many(chat_id,generation,response.actions)
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
        await self._generate(user_id, record["chat_id"], text, generation, user_content=images)
        await self.scheduler.complete(record["id"])
