from app.llm.prompts import read_prompt


class InitiativeContextBuilder:
    """Small, extensible context. Future memory/events slots remain explicit."""
    def __init__(self, conversation_context, lifecycle):
        self.conversation_context, self.lifecycle = conversation_context, lifecycle

    async def build(self, user_id: int, chat_id: int):
        system, recent = await self.conversation_context.build(user_id, chat_id, "")
        lifecycle = await self.lifecycle.get(chat_id)
        event = await self.conversation_context.db.fetchone("SELECT title,availability,ends_at,mentionable FROM daily_events WHERE chat_id=? AND julianday(starts_at)<=julianday('now') AND julianday(ends_at)>julianday('now') ORDER BY ends_at DESC LIMIT 1", (chat_id,))
        state = "\n".join(f"{key}={lifecycle[key]}" for key in ("conversation_status", "last_user_message_at", "last_bot_message_at", "expects_reply", "followup_importance", "followup_reason"))
        event_block = f"CURRENT DAILY EVENT\ntitle={event['title']}; availability={event['availability']}; ends_at={event['ends_at']}; mentionable={event['mentionable']}" if event else "CURRENT DAILY EVENT\n(none)"
        return system, "INITIATIVE MODE\nDecide whether there is a concrete, human reason to write first. A current daily event may be mentioned only if it is marked mentionable. Do not message merely because time passed.\n\nCONVERSATION LIFECYCLE\n" + state + "\n\n" + event_block + "\n\n" + recent
