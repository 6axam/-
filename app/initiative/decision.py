from datetime import datetime, timedelta

class InitiativeDecision:
    def __init__(self, settings): self.settings = settings
    async def decide(self, db, user_id):
        last = await db.fetchone("SELECT created_at FROM initiative_history WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        today = await db.fetchone("SELECT count(*) n FROM initiative_history WHERE user_id=? AND date(created_at)=date('now')", (user_id,))
        state = await db.fetchone("SELECT last_interaction,conversation_interest FROM relationship_state WHERE user_id=?", (user_id,))
        if not state or today['n'] >= self.settings.initiative_daily_limit or not state['last_interaction']: return False, "limits or no relationship"
        # SQLite timestamps are UTC; only engage after a quiet period and for interested conversations.
        quiet = await db.fetchone("SELECT julianday('now') - julianday(?) AS days", (state['last_interaction'],))
        if quiet['days'] * 24 < self.settings.initiative_cooldown_hours or state['conversation_interest'] < .35: return False, "cooldown/low interest"
        return True, "long pause after an engaged conversation"
