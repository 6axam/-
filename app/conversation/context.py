import json
from app.llm.prompts import read_prompt, response_rules

class ContextBuilder:
    """Core context deliberately includes only character and persisted dialogue."""
    def __init__(self, db, personality=None, emotional_state=None, stickers=None, recent_media_hours: int = 24):
        self.db, self.personality, self.emotional_state, self.stickers, self.recent_media_hours = db, personality, emotional_state, stickers, recent_media_hours
    async def build(self, user_id, chat_id, user_turn):
        messages = await self.db.recent_messages(chat_id, limit=24, recent_media_hours=self.recent_media_hours)
        def render(row):
            if row["type"] == "sticker" and row["sticker_visual"]:
                meanings = ", ".join(json.loads(row["sticker_meanings"] or "[]"))
                return f"{row['sender']}: [стикер: {row['sticker_visual']}; {meanings}]"
            if row["type"] == "photo" and row["photo_description"]:
                return f"{row['sender']}: [фото: {row['photo_description']}]"
            return f"{row['sender']}: {row['text'] or '[' + row['type'] + ']'}"
        history = "\n".join(render(row) for row in messages)
        system = "IMMUTABLE CORE PERSONALITY\n" + read_prompt("character.md") + "\n\nUSER PROFILE\n" + read_prompt("user_profile.md") + "\n\n" + response_rules() + "\n\nMEDIA RULE\nImages and stickers described or provided in the conversation are things you see normally. React to their actual content when relevant. Do not discuss technical mechanisms behind seeing or choosing them."
        blocks = []
        if self.personality:
            entries = await self.personality.relevant(user_turn)
            developed = "\n".join(f"- {row['category']} / {row['subject']}: {row['value']} (strength {row['strength']:.2f})" for row in entries) or "(none yet)"
            blocks.append("DEVELOPED PERSONALITY\n" + developed)
        if self.emotional_state:
            state = await self.emotional_state.get()
            blocks.append(f"EMOTIONAL STATE\nmood={state['mood']}; energy={state['energy']:.2f}; social_energy={state['social_energy']:.2f}; offense={state['offense_level']:.2f}; interest={state['conversation_interest']:.2f}; availability={state['availability']}")
        reaction_signals = await self.db.recent_reaction_signals(chat_id, user_id)
        if reaction_signals:
            rendered = "\n".join(f"- Максим поставил {row['emoji']} на твоё сообщение." for row in reaction_signals)
            blocks.append("NONVERBAL REACTION SIGNALS\n" + rendered + "\nЭто тёплый/эмоциональный сигнал, а не новое сообщение. Не отвечай на него отдельно и не упоминай этот внутренний блок.")
        images = await self.db.fetchall("SELECT kind,scene,location,activity FROM generated_images WHERE chat_id=? AND status='sent' ORDER BY id DESC LIMIT 3", (chat_id,))
        if images:
            blocks.append("RECENT IMAGES YOU SENT\n" + "\n".join(f"- {row['kind']}: {row['scene']} ({row['location']}, {row['activity']})" for row in images))
        blocks.append("RECENT CONVERSATION\n" + (history or "(none)"))
        return system, "\n\n".join(blocks)
