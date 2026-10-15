"""Presentation-only Russian labels; storage and engine keys stay unchanged."""

LABELS = {
    "warmth": "теплота", "trust": "доверие", "joy": "радость", "sadness": "грусть",
    "irritation": "раздражение", "hurt": "обида", "anxiety": "тревога", "fatigue": "усталость",
    "curiosity": "любопытство", "social_need": "потребность в общении",
    "valence": "валентность", "arousal": "возбуждение", "energy": "энергия",
    "reward_sensitivity": "чувствительность к награде", "stress": "стресс",
    "social_safety": "социальная безопасность", "attachment": "привязанность",
    "novelty_drive": "тяга к новому", "threat": "угроза", "frustration": "фрустрация",
    "self_worth": "самооценка", "grief_load": "груз грусти", "resentment": "негодование",
    "vulnerability": "уязвимость", "inhibition": "торможение",
    "bond_strength": "сила связи", "relational_trust": "доверие в отношениях",
    "familiarity": "близость", "care_investment": "вовлечённость", "rupture_load": "напряжение отношений",
    "relationship_security": "чувство надёжности", "love_strength": "сила привязанности",
    "hostility": "враждебность", "protest_drive": "протест", "profanity_drive": "склонность к мату",
    "caps_drive": "склонность к CAPS", "expression_intensity": "интенсивность выражения",
    "repair_drive": "стремление к примирению", "regulation_capacity": "саморегуляция",
    "contentment": "удовлетворённость", "excitement": "воодушевление", "amusement": "веселье",
    "relief": "облегчение", "hope": "надежда", "gratitude": "благодарность", "pride": "гордость",
    "affection": "нежность", "tenderness": "мягкость", "closeness": "ощущение близости",
    "longing": "тоска", "loneliness": "одиночество", "interest": "интерес", "anticipation": "ожидание",
    "surprise": "удивление", "boredom": "скука", "melancholy": "меланхолия", "grief": "горе",
    "disappointment": "разочарование", "apathy": "апатия", "depressive_tone": "подавленность",
    "anger": "злость", "envy": "зависть", "disgust": "отвращение", "fear": "страх",
    "shame": "стыд", "guilt": "вина", "embarrassment": "неловкость", "nostalgia": "ностальгия",
    "overwhelm": "перегруз", "defensiveness": "защитность", "regret": "сожаление",
    "response_energy": "энергия ответа", "verbal_fluency": "связность речи", "warmth_expression": "выражение теплоты",
    "openness": "открытость", "playfulness": "игривость", "patience": "терпение", "initiative_drive": "тяга к инициативе",
    "social_seeking": "поиск общения", "avoidance": "избегание", "irritability": "раздражительность",
    "fear_of_loss": "страх потери", "rumination_drive": "зацикливание", "contact_drive": "тяга к контакту",
    "message_burst_drive": "порыв написать", "affective_volatility": "эмоциональная изменчивость",
    "expects_reply": "ожидает ответа", "followup_importance": "важность продолжения",
}

LIFECYCLE = {"active": "активный", "cooling_down": "затихает", "ended": "завершён"}
AVAILABILITY = {"available": "доступна", "busy": "занята", "away": "отошла", "sleep": "спит"}
REQUEST_KINDS = {"conversation": "чат", "initiative": "инициатива", "timing": "решение времени", "daily-life": "жизнь Ани", "daily_life": "жизнь Ани", "sticker_vision": "анализ стикера", "photo_vision": "анализ изображения"}

COMPONENT_LABELS = {
    "character_prompt": "ядро характера", "life_background": "фон жизни", "user_profile": "профиль пользователя",
    "response_instructions": "правила ответа", "memory_policy": "правила памяти", "episodic_policy": "эпизодическая память",
    "emotion_policy": "эмоциональная политика", "affective_state": "аффективное состояние",
    "relationship_state": "отношения", "conversation_history": "история", "current_user_turn": "текущий turn",
    "json_schema_instructions": "response schema", "initiative_policy": "правила инициативы",
}


def label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " "))
