import json

from app.conversation.tokens import component_size, estimate_tokens
from app.llm.prompts import read_prompt, response_rules


class ContextBuilder:
    """Build compact, chat-scoped context and numeric-only telemetry."""

    def __init__(self, db, personality=None, emotional_state=None, stickers=None,
                 recent_media_hours: int = 24, recent_max_messages: int = 24,
                 recent_token_budget: int = 1500, target_input_tokens: int = 5000):
        self.db, self.personality, self.emotional_state = db, personality, emotional_state
        self.stickers, self.recent_media_hours = stickers, recent_media_hours
        self.recent_max_messages = recent_max_messages
        self.recent_token_budget = recent_token_budget
        self.target_input_tokens = target_input_tokens

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

    @staticmethod
    def _render(row) -> str:
        if row["type"] == "sticker" and row["sticker_visual"]:
            meanings = ", ".join(json.loads(row["sticker_meanings"] or "[]"))
            return f"{row['sender']}: [стикер: {row['sticker_visual']}; {meanings}]"
        if row["type"] == "photo" and row["photo_description"]:
            return f"{row['sender']}: [фото: {row['photo_description']}]"
        return f"{row['sender']}: {row['text'] or '[' + row['type'] + ']'}"

    async def build(self, user_id, chat_id, user_turn):
        system, context, _ = await self.build_with_breakdown(user_id, chat_id, user_turn)
        return system, context

    async def build_with_breakdown(self, user_id, chat_id, user_turn):
        messages = await self.db.recent_messages(chat_id, limit=self.recent_max_messages, recent_media_hours=self.recent_media_hours)
        selected_history = self._select_history([self._render(row) for row in messages])
        history = "\n".join(selected_history)

        character = "IMMUTABLE CORE PERSONALITY\n" + read_prompt("character.md")
        profile = "USER PROFILE\n" + read_prompt("user_profile.md")
        response = response_rules()
        media_rule = "MEDIA RULE\nImages and stickers described or provided in the conversation are things you see normally. React to their actual content when relevant. Do not discuss technical mechanisms behind seeing or choosing them."
        system = character + "\n\n" + profile + "\n\n" + response + "\n\n" + media_rule

        components = {
            "character_prompt": component_size(character), "user_profile": component_size(profile),
            "response_instructions": component_size(response), "system_media_rule": component_size(media_rule),
            "personality_state": component_size(""), "emotional_state": component_size(""),
            "reaction_context": component_size(""), "recent_image_metadata": component_size(""),
            "conversation_history": component_size(history), "current_user_turn": component_size(user_turn),
            "request_wrapper": component_size("\n\nUSER TURN:\n"),
        }
        blocks = []
        if self.personality:
            entries = await self.personality.relevant(user_turn)
            developed = "\n".join(f"- {row['category']} / {row['subject']}: {row['value']} (strength {row['strength']:.2f})" for row in entries) or "(none yet)"
            block = "DEVELOPED PERSONALITY\n" + developed
            blocks.append(block); components["personality_state"] = component_size(block)
        if self.emotional_state:
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
        blocks.append("RECENT CONVERSATION\n" + (history or "(none)"))

        breakdown = {
            "chat_id": chat_id, "history_messages": len(selected_history),
            "history_token_budget": self.recent_token_budget, "history_max_messages": self.recent_max_messages,
            "target_input_tokens": self.target_input_tokens, "components": components,
            "estimated_input_tokens": sum(part["tokens"] for part in components.values()),
        }
        return system, "\n\n".join(blocks), breakdown
