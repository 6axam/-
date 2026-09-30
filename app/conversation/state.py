import json
from app.llm.schemas import StateUpdate

class StateManager:
    def __init__(self, db): self.db = db
    async def get(self, user_id):
        await self.db.execute("INSERT OR IGNORE INTO relationship_state(user_id,last_interaction) VALUES(?,CURRENT_TIMESTAMP)", (user_id,))
        return await self.db.fetchone("SELECT * FROM relationship_state WHERE user_id=?", (user_id,))
    async def apply(self, user_id, update: StateUpdate):
        state = await self.get(user_id)
        def cap(value): return min(1, max(0, value))
        mood = update.mood or state['mood']; topics = json.dumps(update.current_topics if update.current_topics is not None else json.loads(state['current_topics']))
        await self.db.execute("UPDATE relationship_state SET mood=?,energy=?,relationship_level=?,conversation_interest=?,current_topics=?,last_interaction=CURRENT_TIMESTAMP WHERE user_id=?", (mood,cap(state['energy']+update.energy_delta),cap(state['relationship_level']+update.relationship_delta),cap(state['conversation_interest']+update.interest_delta),topics,user_id))
