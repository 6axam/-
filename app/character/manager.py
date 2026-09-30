import json
from datetime import datetime, timezone

from app.llm.schemas import EmotionalUpdate, SelfUpdateProposal


class PersonalityManager:
    max_updates_per_turn = 3

    def __init__(self, db):
        self.db = db

    async def apply(self, proposals: list[SelfUpdateProposal], source_turn_id: int | None = None):
        applied = []
        for proposal in proposals[: self.max_updates_per_turn]:
            row = await self.db.fetchone(
                "SELECT * FROM developed_personality WHERE category=? AND subject=? AND value=?",
                (proposal.category, proposal.subject, proposal.value),
            )
            if row:
                strength = max(-1.0, min(1.0, row["strength"] + proposal.strength_delta))
                await self.db.execute("UPDATE developed_personality SET strength=?, confidence=MIN(1.0, confidence + 0.03), source=?, source_turn_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (strength, proposal.reason, source_turn_id, row["id"]))
            else:
                # An opposing value slowly weakens existing opinions about the same subject.
                opposing = await self.db.fetchall("SELECT id,strength FROM developed_personality WHERE category=? AND subject=? AND value<>?", (proposal.category, proposal.subject, proposal.value))
                for old in opposing:
                    weakened = old["strength"] - (abs(proposal.strength_delta) * .5 if old["strength"] > 0 else -abs(proposal.strength_delta) * .5)
                    await self.db.execute("UPDATE developed_personality SET strength=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (weakened, old["id"]))
                await self.db.execute("INSERT INTO developed_personality(category,subject,value,strength,confidence,source,source_turn_id) VALUES(?,?,?,?,?,?,?)", (proposal.category, proposal.subject, proposal.value, proposal.strength_delta, .35, proposal.reason, source_turn_id))
            applied.append(proposal)
        return applied

    async def relevant(self, user_turn: str, limit: int = 8):
        rows = await self.db.fetchall("SELECT category,subject,value,strength,confidence FROM developed_personality ORDER BY ABS(strength) DESC, confidence DESC LIMIT 40")
        words = {word.lower() for word in user_turn.split() if len(word) > 3}
        ranked = sorted(rows, key=lambda row: (sum(word in (row["subject"] + " " + row["value"]).lower() for word in words), abs(row["strength"]), row["confidence"]), reverse=True)
        return ranked[:limit]


class EmotionalStateManager:
    def __init__(self, db): self.db = db

    async def get(self):
        await self.db.execute("INSERT OR IGNORE INTO emotional_state(id) VALUES(1)")
        state = await self.db.fetchone("SELECT * FROM emotional_state WHERE id=1")
        # Offense decays by 0.02/hour; nothing else jumps just because time passed.
        age = await self.db.fetchone("SELECT (julianday('now') - julianday(?)) * 24 AS hours", (state["last_updated"],))
        decay = min(state["offense_level"], max(0, age["hours"] or 0) * .02)
        if decay:
            await self.db.execute("UPDATE emotional_state SET offense_level=?, last_updated=CURRENT_TIMESTAMP WHERE id=1", (state["offense_level"] - decay,))
            state = await self.db.fetchone("SELECT * FROM emotional_state WHERE id=1")
        return state

    async def apply(self, update: EmotionalUpdate):
        state = await self.get()
        clamp = lambda value: max(0.0, min(1.0, value))
        energy = clamp(state["energy"] + update.energy_delta)
        social = clamp(state["social_energy"] + update.social_energy_delta)
        offense = clamp(state["offense_level"] + update.offense_delta)
        interest = clamp(state["conversation_interest"] + update.conversation_interest_delta)
        # Mood is derived from bounded state, rather than freely authored by the LLM.
        mood = "irritated" if offense >= .45 else "tired" if energy <= .3 else "warm" if interest >= .82 else "normal"
        await self.db.execute("UPDATE emotional_state SET mood=?, energy=?, social_energy=?, offense_level=?, conversation_interest=?, availability=?, last_updated=CURRENT_TIMESTAMP WHERE id=1", (
            mood, energy, social, offense, interest, update.availability or state["availability"],
        ))
        return await self.get()
