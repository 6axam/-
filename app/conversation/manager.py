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
        if scheduler: scheduler.bind(self)

    def interrupt(self, chat_id: int) -> None:
        generation = self.generations.pop(chat_id, None)
        if generation:
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
        self.interrupt(chat_id)
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
            try:
                timing = await decider(request) if decider else ResponseTiming()
            except Exception:
                log.warning("Timing decision failed; responding immediately", exc_info=True)
                timing = ResponseTiming()
            if self.generations.get(chat_id) != generation:
                log.debug("Dropping stale timing decision for generation %s", generation)
                return generation
            if timing.mode == "delayed":
                await self.scheduler.schedule(user_id, chat_id, generation, timing.urgency)
                return generation
        return await self._generate(user_id, chat_id, text, generation, turn_id, target_message_id, user_content)

    async def _generate(self, user_id, chat_id, text, generation, turn_id=None, target_message_id=None, user_content=None):
        system, context = await self.context.build(user_id, chat_id, text)
        target_hint = f"\n\nCURRENT USER MESSAGE ID FOR OPTIONAL REACTION: {target_message_id}" if target_message_id else ""
        task = asyncio.create_task(self.provider.generate(LLMRequest(system=system, context=context, user_turn=text + target_hint, user_content=user_content or [])))
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
        user_id, text, message_ids = await self.context.db.pending_user_message_ids(record["chat_id"])
        if not user_id or not text:
            await self.scheduler.complete(record["id"])
            return
        generation = record["generation_id"]
        self.generations[record["chat_id"]] = generation
        images = await self.media.inputs_for_messages(record["chat_id"], message_ids) if self.media and getattr(self.provider, "supports_vision", False) else []
        await self._generate(user_id, record["chat_id"], text, generation, user_content=images)
        await self.scheduler.complete(record["id"])
