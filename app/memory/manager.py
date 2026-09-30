import json
from app.llm.schemas import MemoryCandidate

class MemoryManager:
    def __init__(self, db): self.db = db
    async def apply(self, user_id: int, candidates: list[MemoryCandidate]):
        for item in candidates:
            if item.decision == "IGNORE": continue
            if item.decision == "UPDATE_EXISTING":
                await self.db.execute("DELETE FROM memories WHERE user_id=? AND content=?", (user_id, item.content))
            await self.db.execute("INSERT INTO memories(user_id,content,importance,confidence,tags,last_used) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)", (user_id,item.content,item.importance,item.confidence,json.dumps(item.tags)))
    async def relevant(self, user_id: int, query: str, limit=6):
        words = [w.lower() for w in query.split() if len(w) > 3]
        rows = await self.db.fetchall("SELECT * FROM memories WHERE user_id=? ORDER BY importance DESC, last_used DESC LIMIT 30", (user_id,))
        ranked = sorted(rows, key=lambda r: sum(w in r['content'].lower() for w in words) + r['importance'], reverse=True)[:limit]
        for row in ranked: await self.db.execute("UPDATE memories SET last_used=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
        return ranked
