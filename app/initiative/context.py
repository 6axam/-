"""Compact, character-aware context for initiative decisions."""

from app.conversation.tokens import component_size, estimate_tokens
from app.llm.prompts import read_prompt


class InitiativeContextBuilder:
    """Build only the information needed to decide whether to write first."""

    recent_max_messages = 6
    recent_history_token_budget = 300
    target_input_tokens = 3500

    def __init__(self, conversation_context, lifecycle):
        self.conversation_context, self.lifecycle = conversation_context, lifecycle

    @staticmethod
    def _truncate(text: str, budget: int) -> str:
        if estimate_tokens(text) <= budget:
            return text
        value = text[: budget * 3].rstrip()
        return value.rstrip() + "…" if value else ""

    @staticmethod
    def _render_recent(row) -> str:
        # Initiative only needs the conversational fact that media appeared,
        # not visual/sticker metadata or a second copy of image context.
        text = row["text"] or f"[{row['type']}]"
        return f"{row['sender']}: {text}"

    def _recent_history(self, rows) -> list[str]:
        selected: list[str] = []
        for row in reversed(rows[-self.recent_max_messages:]):
            rendered = self._render_recent(row)
            candidate = [rendered, *selected]
            if estimate_tokens("\n".join(candidate)) <= self.recent_history_token_budget:
                selected = candidate
                continue
            if not selected:
                bounded = self._truncate(rendered, self.recent_history_token_budget)
                if bounded:
                    selected = [bounded]
            break
        return selected

    async def build(self, user_id: int, chat_id: int):
        db = self.conversation_context.db
        lifecycle = await self.lifecycle.get(chat_id)
        event = await db.fetchone(
            "SELECT title,availability,ends_at,mentionable FROM daily_events "
            "WHERE chat_id=? AND julianday(starts_at)<=julianday('now') "
            "AND julianday(ends_at)>julianday('now') ORDER BY ends_at DESC LIMIT 1",
            (chat_id,),
        )
        idle = await db.fetchone(
            "SELECT (julianday('now') - julianday(MAX(timestamp))) * 1440 AS minutes "
            "FROM messages WHERE chat_id=? AND sender='user'",
            (chat_id,),
        )
        cooldown = await db.fetchone(
            "SELECT (julianday('now') - julianday(MAX(created_at))) * 24 AS hours, "
            "COUNT(CASE WHEN date(created_at)=date('now') THEN 1 END) AS today "
            "FROM initiative_history WHERE chat_id=?",
            (chat_id,),
        )
        rows = await db.recent_messages(
            chat_id,
            limit=self.recent_max_messages,
            recent_media_hours=self.conversation_context.recent_media_hours,
        )
        history_rows = self._recent_history(rows)
        history = "\n".join(history_rows) or "(none)"

        character = "CORE CHARACTER\n" + read_prompt("character.md")
        components = {
            "core_character": component_size(character),
            "personality_state": component_size(""),
            "emotional_state": component_size(""),
            "eligibility_lifecycle": component_size(""),
            "daily_event": component_size(""),
            "initiative_limits": component_size(""),
            "recent_conversation": component_size(history),
        }
        blocks = []

        personality = self.conversation_context.personality
        if personality:
            entries = await personality.relevant(history, limit=6)
            values = "\n".join(
                f"- {row['category']} / {row['subject']}: {row['value']} (strength {row['strength']:.2f})"
                for row in entries
            ) or "(none yet)"
            block = "DEVELOPED PERSONALITY\n" + values
            blocks.append(block)
            components["personality_state"] = component_size(block)

        emotional_state = self.conversation_context.emotional_state
        if emotional_state:
            state = await emotional_state.get()
            block = (
                "EMOTIONAL STATE\n"
                f"mood={state['mood']}; energy={state['energy']:.2f}; "
                f"social_energy={state['social_energy']:.2f}; offense={state['offense_level']:.2f}; "
                f"interest={state['conversation_interest']:.2f}; availability={state['availability']}"
            )
            blocks.append(block)
            components["emotional_state"] = component_size(block)

        eligibility_basis = "daily_event" if event else "semantic_followup"
        lifecycle_block = (
            "INITIATIVE ELIGIBILITY CONTEXT\n"
            f"basis={eligibility_basis}; status={lifecycle['conversation_status']}; "
            f"expects_reply={lifecycle['expects_reply']}; "
            f"followup_importance={lifecycle['followup_importance']:.2f}; "
            f"followup_reason={lifecycle['followup_reason'] or '(none)'}; "
            f"user_idle_minutes={int(idle['minutes'] or 0)}"
        )
        blocks.append(lifecycle_block)
        components["eligibility_lifecycle"] = component_size(lifecycle_block)

        event_block = (
            "CURRENT DAILY EVENT\n"
            f"title={event['title']}; availability={event['availability']}; "
            f"ends_at={event['ends_at']}; mentionable={event['mentionable']}"
            if event else "CURRENT DAILY EVENT\n(none)"
        )
        blocks.append(event_block)
        components["daily_event"] = component_size(event_block)

        limits_block = (
            "INITIATIVE LIMIT CONTEXT\n"
            f"hours_since_last={cooldown['hours'] if cooldown['hours'] is not None else 'never'}; "
            f"initiatives_today={cooldown['today']}"
        )
        blocks.append(limits_block)
        components["initiative_limits"] = component_size(limits_block)

        blocks.append("RECENT CONVERSATION\n" + history)
        breakdown = {
            "chat_id": chat_id,
            "history_messages": len(history_rows),
            "history_token_budget": self.recent_history_token_budget,
            "target_input_tokens": self.target_input_tokens,
            "components": components,
        }
        return character, "\n\n".join(blocks), breakdown
