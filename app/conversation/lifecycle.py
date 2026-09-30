import logging

from app.llm.schemas import ConversationMetadata

log = logging.getLogger(__name__)


class ConversationLifecycleManager:
    def __init__(self, db, cooling_minutes: int, ended_hours: int):
        self.db, self.cooling_minutes, self.ended_hours = db, cooling_minutes, ended_hours

    async def get(self, chat_id: int):
        await self.db.execute("INSERT OR IGNORE INTO conversation_lifecycle(chat_id) VALUES(?)", (chat_id,))
        return await self.db.fetchone("SELECT * FROM conversation_lifecycle WHERE chat_id=?", (chat_id,))

    async def on_user_message(self, chat_id: int):
        await self.get(chat_id)
        await self.db.execute("UPDATE conversation_lifecycle SET conversation_status='active', last_user_message_at=CURRENT_TIMESTAMP, last_meaningful_interaction_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (chat_id,))

    async def on_bot_message(self, chat_id: int):
        await self.get(chat_id)
        await self.db.execute("UPDATE conversation_lifecycle SET last_bot_message_at=CURRENT_TIMESTAMP, last_meaningful_interaction_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (chat_id,))

    async def apply(self, chat_id: int, metadata: ConversationMetadata):
        await self.get(chat_id)
        importance = min(1.0, max(0.0, metadata.followup_importance))
        reason = metadata.followup_reason if metadata.expects_reply else None
        await self.db.execute("UPDATE conversation_lifecycle SET conversation_status=?, expects_reply=?, followup_importance=?, followup_reason=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (metadata.conversation_status, int(metadata.expects_reply), importance, reason, chat_id))
        log.info("expects_reply_changed chat_id=%s expects_reply=%s importance=%.2f", chat_id, metadata.expects_reply, importance)

    async def transition_due(self):
        cooling = await self.db.execute("UPDATE conversation_lifecycle SET conversation_status='cooling_down', updated_at=CURRENT_TIMESTAMP WHERE conversation_status='active' AND (julianday('now') - julianday(last_meaningful_interaction_at)) * 1440 >= ?", (self.cooling_minutes,))
        ended = await self.db.execute("UPDATE conversation_lifecycle SET conversation_status='ended', updated_at=CURRENT_TIMESTAMP WHERE conversation_status IN ('active','cooling_down') AND (julianday('now') - julianday(last_meaningful_interaction_at)) * 24 >= ?", (self.ended_hours,))
        if cooling.rowcount or ended.rowcount:
            log.info("conversation_state_changed cooling=%s ended=%s", cooling.rowcount, ended.rowcount)
        return cooling.rowcount, ended.rowcount
