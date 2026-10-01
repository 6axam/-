"""Compact, character-aware context for initiative decisions."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.conversation.tokens import component_size, estimate_tokens
from app.llm.prompts import read_prompt


class InitiativeContextBuilder:
    recent_max_messages = 6
    recent_history_token_budget = 300
    memory_token_budget = 300
    life_token_budget = 350
    target_input_tokens = 3500

    def __init__(self, conversation_context, lifecycle, presence=None):
        self.conversation_context, self.lifecycle, self.presence = conversation_context, lifecycle, presence

    @staticmethod
    def _bounded(lines, budget):
        kept = []
        for line in lines:
            if estimate_tokens("\n".join([*kept, line])) <= budget:
                kept.append(line)
        return kept

    async def build(self, user_id: int, chat_id: int, *, basis: str = "spontaneous", daily_state=None):
        db, cc = self.conversation_context.db, self.conversation_context
        lifecycle = await self.lifecycle.get(chat_id)
        daily_state = daily_state or (await self.presence.state(chat_id) if self.presence else {"phase": "free", "availability": "available", "event": None})
        event = daily_state.get("event")
        local = datetime.now(ZoneInfo(getattr(cc, "timezone_name", "Europe/Kyiv")))
        idle = await db.fetchone("SELECT (julianday('now')-julianday(MAX(timestamp)))*1440 AS minutes FROM messages WHERE chat_id=? AND sender='user'", (chat_id,))
        cooldown = await db.fetchone("SELECT (julianday('now')-julianday(MAX(created_at)))*24 AS hours, COUNT(CASE WHEN date(created_at)=date('now') THEN 1 END) AS today FROM initiative_history WHERE chat_id=?", (chat_id,))
        rows = await db.recent_messages(chat_id, limit=self.recent_max_messages, recent_media_hours=cc.recent_media_hours)
        rendered = [f"{row['sender']}: {row['text'] or '[' + row['type'] + ']'}" for row in rows]
        history_lines = self._bounded(list(reversed(rendered)), self.recent_history_token_budget)
        history = "\n".join(reversed(history_lines)) or "(none)"
        character = "CORE CHARACTER\n" + read_prompt("character.md") + "\n\nLIFE BACKGROUND\n" + read_prompt("life_background.md") + "\n\n" + read_prompt("initiative.md")
        components = {"core_character": component_size(character), "personality_state": component_size(""), "emotional_state": component_size(""), "current_life_state": component_size(""), "eligibility_lifecycle": component_size(""), "recent_conversation": component_size(history), "relevant_memories": component_size(""), "anya_life_events": component_size(""), "recent_initiatives": component_size("")}
        blocks = []
        if cc.personality:
            values = "\n".join(f"- {r['category']} / {r['subject']}: {r['value']}" for r in await cc.personality.relevant(history, limit=6)) or "(none yet)"
            block = "DEVELOPED PERSONALITY\n" + values; blocks.append(block); components["personality_state"] = component_size(block)
        if cc.emotional_state:
            state = await cc.emotional_state.get()
            block = f"EMOTIONAL STATE\nmood={state['mood']}; energy={state['energy']:.2f}; social_energy={state['social_energy']:.2f}; offense={state['offense_level']:.2f}; interest={state['conversation_interest']:.2f}"
            blocks.append(block); components["emotional_state"] = component_size(block)
        event_text = "active_event=false"
        if event:
            event_text = f"active_event=true; title={event['title']}; availability={event['availability']}; mentionable=true" if event["mentionable"] else f"active_event=true; availability={event['availability']}; mentionable=false"
        block = f"CURRENT ANYA LIFE STATE\nlocal_time={local.strftime('%Y-%m-%d %H:%M')}; local_day={local.date()}; phase={daily_state.get('phase', 'free')}; availability={daily_state.get('availability', 'available')}; {event_text}"
        blocks.append(block); components["current_life_state"] = component_size(block)
        block = f"INITIATIVE CONTEXT\nbasis={basis}; status={lifecycle['conversation_status']}; expects_reply={lifecycle['expects_reply']}; followup_importance={lifecycle['followup_importance']:.2f}; followup_reason={lifecycle['followup_reason'] or '(none)'}; user_idle_minutes={int(idle['minutes'] or 0)}; hours_since_last={cooldown['hours'] if cooldown['hours'] is not None else 'never'}; initiatives_today={cooldown['today']}"
        blocks.append(block); components["eligibility_lifecycle"] = component_size(block)
        if getattr(cc, "memory_retrieval", None):
            memories = await cc.memory_retrieval.search(user_id, chat_id, f"{lifecycle['followup_reason'] or ''} {history}", 4)
            lines = self._bounded([f"[id={r['id']}] {r['content']}" for r in memories[:4]], self.memory_token_budget)
            if lines:
                block = "RELEVANT USER MEMORIES\n" + "\n".join(lines) + "\nUse only when naturally relevant; do not mention ids."
                blocks.append(block); components["relevant_memories"] = component_size(block)
        retrieved_life_event_ids = []
        if getattr(cc, "self_life", None):
            events = await cc.self_life.relevant(f"{lifecycle['followup_reason'] or ''} {history}", local.date().isoformat(), 6)
            lines = self._bounded([f"[id={r['id']}] {r['local_day'] or 'recent'}: {r['summary']}" for r in events[:6]], self.life_token_budget)
            if lines:
                block = "RECENT / RELEVANT ANYA LIFE EVENTS\n" + "\n".join(lines) + "\nThese are established experiences; do not mention ids."
                blocks.append(block); components["anya_life_events"] = component_size(block)
                retrieved_life_event_ids = [int(line.split("]", 1)[0][4:]) for line in lines]
        recent = await db.fetchall("SELECT basis,kind,reason FROM initiative_history WHERE chat_id=? ORDER BY created_at DESC,id DESC LIMIT 5", (chat_id,))
        if recent:
            block = "RECENT INITIATIVES — vary form and openings\n" + "\n".join(f"- {r['basis'] or 'legacy'}/{r['kind'] or 'unknown'}: {r['reason']}" for r in recent)
            blocks.append(block); components["recent_initiatives"] = component_size(block)
        blocks.append("RECENT CONVERSATION\n" + history)
        return character, "\n\n".join(blocks), {"chat_id": chat_id, "history_messages": len(history_lines), "target_input_tokens": self.target_input_tokens, "components": components, "retrieved_life_event_ids": retrieved_life_event_ids}
