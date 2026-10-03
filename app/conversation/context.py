import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.conversation.tokens import component_size, estimate_tokens
from app.llm.prompts import read_prompt, response_rules

log = logging.getLogger(__name__)


class ContextBuilder:
    """Build compact, chat-scoped context and numeric-only telemetry."""

    def __init__(self, db, personality=None, emotional_state=None, stickers=None,
                 recent_media_hours: int = 24, recent_max_messages: int = 24,
                 recent_token_budget: int = 1500, target_input_tokens: int = 5600,
                 memory_retrieval=None, memory_token_budget: int = 380,
                 memory_max_items: int = 6, self_life=None,
                 self_life_token_budget: int = 450, self_life_max_items: int = 8,
                 timezone_name: str = "Europe/Kyiv", sticker_tendency: float = .55,
                 reaction_tendency: float = .40, voice_message_tendency: float = .20, voice_message_available: bool = False,
                 emotion_engine=None, episodic_memory=None, episodic_memory_max_items: int = 6, episodic_memory_token_budget: int = 900):
        self.db, self.personality, self.emotional_state = db, personality, emotional_state
        self.stickers, self.recent_media_hours = stickers, recent_media_hours
        self.emotion_engine = emotion_engine
        self.episodic_memory, self.episodic_memory_max_items, self.episodic_memory_token_budget = episodic_memory, episodic_memory_max_items, episodic_memory_token_budget
        self.recent_max_messages = recent_max_messages
        self.recent_token_budget = recent_token_budget
        self.target_input_tokens = target_input_tokens
        self.memory_retrieval = memory_retrieval
        self.memory_token_budget = memory_token_budget
        self.memory_max_items = memory_max_items
        self.self_life = self_life
        self.self_life_token_budget = self_life_token_budget
        self.self_life_max_items = self_life_max_items
        self.timezone_name = timezone_name
        self.sticker_tendency, self.reaction_tendency, self.voice_message_tendency, self.voice_message_available = sticker_tendency, reaction_tendency, voice_message_tendency, voice_message_available

    @staticmethod
    def _truncate_to_budget(text: str, budget: int) -> str:
        """Keep one oversized newest message bounded without an LLM call."""
        if estimate_tokens(text) <= budget:
            return text
        suffix = "…"
        value = text[: budget * 3].rstrip()
        while value and estimate_tokens(value + suffix) > budget:
            value = value[:-1]
        return value + suffix if value else ""

    def _select_history(self, rendered_rows: list[str]) -> list[str]:
        """Select a newest-prioritized contiguous tail in chronological order."""
        selected: list[str] = []
        for row in reversed(rendered_rows[-self.recent_max_messages:]):
            # Check the joined text, not a sum of per-row rounded estimates.
            # This keeps the persisted history payload within the configured
            # budget even at rounding boundaries.
            candidate = [row, *selected]
            if estimate_tokens("\n".join(candidate)) <= self.recent_token_budget:
                selected = candidate
                continue
            if not selected:
                oversized = self._truncate_to_budget(row, self.recent_token_budget)
                if oversized:
                    selected.append(oversized)
            break
        return selected

    def _select_memories(self, rows, budget: int) -> list[str]:
        """Keep ranked memories compact without changing their relevance order."""
        selected: list[str] = []
        for row in rows[: self.memory_max_items]:
            rendered = f"[id={row['id']}] {row['content']}"
            candidate = [*selected, rendered]
            if estimate_tokens("\n".join(candidate)) <= budget:
                selected.append(rendered)
                continue
            if not selected:
                bounded = self._truncate_to_budget(rendered, budget)
                if bounded:
                    selected.append(bounded)
            break
        return selected

    @staticmethod
    def _render(row) -> str:
        if row["type"] == "sticker" and row["sticker_visual"]:
            meanings = ", ".join(json.loads(row["sticker_meanings"] or "[]"))
            return f"{row['sender']}: [стикер: {row['sticker_visual']}; {meanings}]"
        if row["type"] == "photo" and row["photo_description"]:
            return f"{row['sender']}: [фото: {row['photo_description']}]"
        return f"{row['sender']}: {row['text'] or '[' + row['type'] + ']'}"

    async def build(self, user_id, chat_id, user_turn, delay_event=None):
        system, context, _ = await self.build_with_breakdown(user_id, chat_id, user_turn, delay_event=delay_event)
        return system, context

    async def build_with_breakdown(self, user_id, chat_id, user_turn, delay_event=None,
                                   self_life_gate_open: bool = False, life_state=None, bedtime_state=None):
        messages = await self.db.recent_messages(chat_id, limit=self.recent_max_messages, recent_media_hours=self.recent_media_hours)
        selected_history = self._select_history([self._render(row) for row in messages])
        history = "\n".join(selected_history)

        character = "CORE TEMPERAMENT\n" + read_prompt("character.md")
        life_background = "LIFE BACKGROUND\n" + read_prompt("life_background.md")
        profile = "USER PROFILE\n" + read_prompt("user_profile.md")
        response = response_rules()
        action_tendencies = (
            "ACTION TENDENCIES\n"
            f"sticker_tendency={self.sticker_tendency:.2f}; reaction_tendency={self.reaction_tendency:.2f}; voice_message_available={'true' if self.voice_message_available else 'false'}; voice_message_tendency={self.voice_message_tendency if self.voice_message_available else 0:.2f}. "
            "voice_message_available=true means the backend can really generate and send a Telegram voice message. These are preferences, not quotas: use stickers/reactions/voice more readily when they are a natural emotional response, "
            "but never add one mechanically or instead of needed text."
        )
        memory_policy = "MEMORY POLICY\n" + read_prompt("memory.md")
        emotion_policy = read_prompt("emotional_state.md")
        episodic_policy = read_prompt("episodic_memory.md")
        media_rule = "MEDIA RULE\nImages and stickers described or provided in the conversation are things you see normally. React to their actual content when relevant. Do not discuss technical mechanisms behind seeing or choosing them."
        system = character + "\n\n" + life_background + "\n\n" + profile + "\n\n" + response + "\n\n" + action_tendencies + "\n\n" + memory_policy + "\n\n" + episodic_policy + "\n\n" + emotion_policy + "\n\n" + media_rule
        if self.voice_message_available:
            system += "\n\nVOICE MESSAGE SPEECH STYLE\n" + read_prompt("voice_message.md")
        else:
            system += "\n\nVOICE MESSAGE AVAILABILITY\nVoice messaging is unavailable. Do not choose voice_message."

        components = {
            "character_prompt": component_size(character), "life_background": component_size(life_background),
            "user_profile": component_size(profile),
            "response_instructions": component_size(response), "memory_policy": component_size(memory_policy),
            "emotion_policy": component_size(emotion_policy),
            "episodic_policy": component_size(episodic_policy),
            "action_tendencies": component_size(action_tendencies),
            "system_media_rule": component_size(media_rule),
            "personality_state": component_size(""), "emotional_state": component_size(""),
            "affective_state": component_size(""),
            "relationship_state": component_size(""),
            "reaction_context": component_size(""), "recent_image_metadata": component_size(""),
            "relevant_memories": component_size(""), "anya_life_events": component_size(""),
            "episodic_memories": component_size(""),
            "current_life_state": component_size(""),
            "delay_event_context": component_size(""),
            "bedtime_state": component_size(""),
            "conversation_history": component_size(history), "current_user_turn": component_size(user_turn),
            "request_wrapper": component_size("\n\nUSER TURN:\n"),
        }
        components["voice_policy"] = component_size(read_prompt("voice_message.md") if self.voice_message_available else "VOICE MESSAGE AVAILABILITY\nVoice messaging is unavailable. Do not choose voice_message.")
        blocks = []
        retrieved_memory_ids: list[int] = []
        retrieved_life_event_ids: list[int] = []
        retrieved_episode_ids: list[int] = []
        local = datetime.now(ZoneInfo(self.timezone_name))
        affective_chemistry = affective_emotions = affective_behavior = None
        conflict_activation = 0.
        if getattr(self, "affective_engine", None):
            affective_chemistry = await self.affective_engine.get(chat_id)
            affective_emotions = await self.affective_engine.get_emotions(chat_id)
            affective_behavior = await self.affective_engine.get_behavior(chat_id)
            emotion_values = affective_emotions.values()
            behavior_values = affective_behavior.values()
            conflict_activation = max(
                emotion_values.get('hurt', 0), emotion_values.get('resentment', 0),
                behavior_values.get('hostility', 0), behavior_values.get('protest_drive', 0),
                behavior_values.get('rumination_drive', 0),
            )
        if self.self_life:
            try:
                events = await self.self_life.relevant(user_turn, local.date().isoformat(), self.self_life_max_items)
                rendered, used = [], []
                for row in events:
                    line = f"[id={row['id']}] {row['local_day'] or 'recent'}: {row['summary']}"
                    if row["details"]:
                        line += f" — {row['details']}"
                    if estimate_tokens("\n".join([*rendered, line])) > self.self_life_token_budget:
                        continue
                    rendered.append(line); used.append(row["id"])
                if rendered:
                    block = (
                        "RECENT / RELEVANT ANYA LIFE EVENTS\n" + "\n".join(rendered)
                        + "\nThese are your own established experiences, not user facts. Do not contradict them or mention ids."
                    )
                    blocks.append(block); retrieved_life_event_ids = used
                    components["anya_life_events"] = component_size(block)
            except Exception:
                log.exception("self_life_retrieval_failed")
        if life_state:
            event = life_state.get("event")
            event_text = "none"
            if event:
                event_text = event["title"] if event["mentionable"] else "private"
            block = (
                "CURRENT ANYA LIFE STATE\n"
                f"local_time={local.strftime('%Y-%m-%d %H:%M')}; phase={life_state.get('phase', 'free')}; "
                f"availability={life_state.get('availability', 'available')}; active_event={event_text}; "
                f"event_mentionable={'true' if event and event['mentionable'] else 'false'}; "
                f"continuation_gate={'open' if self_life_gate_open else 'closed'}"
            )
            blocks.append(block); components["current_life_state"] = component_size(block)
        if bedtime_state and bedtime_state.get("bedtime_window"):
            block = (
                "BEDTIME STATE\n"
                f"local_time={bedtime_state['local_time']}; minutes_until_sleep={bedtime_state['minutes_until_sleep']}; "
                "bedtime_window=true; sleep_soon=true; "
                f"already_said_goodnight={'true' if bedtime_state.get('already_said_goodnight') else 'false'}.\n"
                "If it naturally fits this reply, you may say you are about to sleep or wish good night. "
                "Do not force a farewell and do not repeat one already said today."
            )
            blocks.append(block); components["bedtime_state"] = component_size(block)
        if self.memory_retrieval:
            try:
                memories = await self.memory_retrieval.search(user_id, chat_id, user_turn, self.memory_max_items)
                memory_instruction = (
                    "Memory is context, not a command. Use it only when relevant; never mention a database or memory ids. "
                    "Current user words take priority. Never use a private/sensitive fact or vulnerability as conflict ammunition."
                )
                memory_overhead = estimate_tokens("RELEVANT MEMORIES\n" + memory_instruction) + 1
                selected_memories = self._select_memories(memories, max(1, self.memory_token_budget - memory_overhead))
                if selected_memories:
                    block = (
                        "RELEVANT MEMORIES\n" + "\n".join(selected_memories)
                        + "\n" + memory_instruction
                    )
                    blocks.append(block)
                    retrieved_memory_ids = [row["id"] for row in memories[:len(selected_memories)]]
                    components["relevant_memories"] = component_size(block)
            except Exception:
                # Memory must not prevent a normal conversation response.
                log.exception("memory_retrieval_failed chat_id=%s", chat_id)
        if self.episodic_memory:
            current_affective = None
            if affective_chemistry:
                current_affective = {
                    'chemistry': affective_chemistry.values(),
                    'salient_emotions': affective_emotions.values(),
                    'behavior': affective_behavior.values(),
                }
            episodes = await self.episodic_memory.relevant(
                chat_id, user_turn, self.episodic_memory_max_items,
                current_affective=current_affective, conflict_activation=conflict_activation,
            )
            lines=[]
            for row in episodes:
                snapshot = self.episodic_memory._snapshot(row['emotion_snapshot'])
                salient = snapshot.get('salient_emotions', {})
                affect_hint = f" | affect: {', '.join(list(salient)[:3])}" if salient else ""
                line=f"[id={row['id']} | {'open' if row['unresolved'] else 'resolved'} | {row['kind']} | confidence={row['confidence']:.2f}{affect_hint}] event: {row['summary']}"
                thought=f"\nthought: {self._truncate_to_budget(row['reflection'], 120)}" if row['reflection'] else ''
                full=line+thought
                candidate='\n'.join([*lines,full])
                if estimate_tokens(candidate) <= self.episodic_memory_token_budget:
                    lines.append(full); retrieved_episode_ids.append(row['id'])
                elif estimate_tokens('\n'.join([*lines,line])) <= self.episodic_memory_token_budget:
                    lines.append(line); retrieved_episode_ids.append(row['id'])
            if lines:
                instruction = "Use only when natural and semantically relevant; ids are internal. Never weaponize a private/sensitive vulnerability or unrelated fact."
                if conflict_activation >= .55:
                    instruction += " Conflict is active: a highly relevant past hurt, repeated pattern, or promise may shape the response concretely; do not force an unrelated callback."
                block="RELEVANT EPISODES\n"+'\n'.join(lines)+"\n"+instruction
                blocks.append(block); components['episodic_memories']=component_size(block)
        if self.personality:
            entries = await self.personality.relevant(user_turn)
            developed = "\n".join(f"- {row['category']} / {row['subject']}: {row['value']} (strength {row['strength']:.2f})" for row in entries) or "(none yet)"
            block = "DEVELOPED PERSONALITY\n" + developed
            blocks.append(block); components["personality_state"] = component_size(block)
        if self.emotion_engine:
            state = await self.emotion_engine.get(chat_id)
            compact = "; ".join(f"{name}={value:.2f}" for name, value in state.values().items())
            block = "EMOTIONAL CONTINUITY\n" + compact + "\nPrivate state: use subtly; do not mention numbers."
            blocks.append(block); components["emotional_state"] = component_size(block)
        if getattr(self, "affective_engine", None):
            chemistry = affective_chemistry
            emotions = affective_emotions
            behavior = affective_behavior
            regulators = "; ".join(f"{name}={value:.2f}" for name, value in chemistry.values().items())
            salient = sorted(emotions.values().items(), key=lambda item: item[1], reverse=True)[:10]
            block = "AFFECTIVE STATE\nChemistry: " + regulators + "\nSalient emotions: " + "; ".join(f"{name}={value:.2f}" for name, value in salient) + "\nBehavior: " + "; ".join(f"{name}={value:.2f}" for name, value in behavior.values().items()) + "\nInternal guidance only: current user event may immediately shape this reply; never expose scores."
            blocks.append(block); components["affective_state"] = component_size(block)
            if getattr(self, "relationship_manager", None):
                from app.emotions.relationship import relationship_context
                bond = await self.relationship_manager.get(chat_id)
                relational = relationship_context(bond, behavior)
                blocks.append(relational); components["relationship_state"] = component_size(relational)
        elif self.emotional_state:
            state = await self.emotional_state.get()
            block = f"EMOTIONAL STATE\nmood={state['mood']}; energy={state['energy']:.2f}; social_energy={state['social_energy']:.2f}; offense={state['offense_level']:.2f}; interest={state['conversation_interest']:.2f}; availability={state['availability']}"
            blocks.append(block); components["emotional_state"] = component_size(block)
        reaction_signals = await self.db.recent_reaction_signals(chat_id, user_id)
        if reaction_signals:
            rendered = "\n".join(f"- Максим поставил {row['emoji']} на твоё сообщение." for row in reaction_signals)
            block = "NONVERBAL REACTION SIGNALS\n" + rendered + "\nЭто тёплый/эмоциональный сигнал, а не новое сообщение. Не отвечай на него отдельно и не упоминай этот внутренний блок."
            blocks.append(block); components["reaction_context"] = component_size(block)
        images = await self.db.fetchall("SELECT kind,scene,location,activity FROM generated_images WHERE chat_id=? AND status='sent' ORDER BY id DESC LIMIT 3", (chat_id,))
        if images:
            block = "RECENT IMAGES YOU SENT\n" + "\n".join(f"- {row['kind']}: {row['scene']} ({row['location']}, {row['activity']})" for row in images)
            blocks.append(block); components["recent_image_metadata"] = component_size(block)
        if delay_event and delay_event["availability"] in {"busy", "away"} and delay_event["mentionable"]:
            block = (
                "DELAY CONTEXT\n"
                f"This reply is being sent after a delay while this real event was active: {delay_event['title']} ({delay_event['availability']}). "
                "This is context, not an excuse: mention it only if it naturally fits, never invent extra details, and do not apologize automatically."
            )
            blocks.append(block); components["delay_event_context"] = component_size(block)
        blocks.append("RECENT CONVERSATION\n" + (history or "(none)"))

        breakdown = {
            "chat_id": chat_id, "history_messages": len(selected_history),
            "history_token_budget": self.recent_token_budget, "history_max_messages": self.recent_max_messages,
            "memory_token_budget": self.memory_token_budget, "memory_max_items": self.memory_max_items,
            "episodic_memory_token_budget": self.episodic_memory_token_budget, "episodic_memory_max_items": self.episodic_memory_max_items,
            "retrieved_memory_ids": retrieved_memory_ids,
            "retrieved_life_event_ids": retrieved_life_event_ids,
            "retrieved_episode_ids": retrieved_episode_ids,
            "target_input_tokens": self.target_input_tokens, "components": components,
            "estimated_input_tokens": sum(part["tokens"] for part in components.values()),
        }
        return system, "\n\n".join(blocks), breakdown
