from pathlib import Path

from app.conversation.tokens import estimate_tokens


def character_prompt() -> str:
    return Path("prompts/character.md").read_text(encoding="utf-8")


def test_character_prompt_keeps_identity_voice_and_independence():
    prompt = character_prompt()
    for trait in (
        "Тебя зовут Аня",
        "близкая интернет-собеседница",
        "не AI-ассистент",
        "собственные вкусы, мнения",
        "Не подстраивай позицию",
        "живой разговорный русский",
        "Не заканчивай каждую реплику вопросом",
        "консультантом без просьбы",
    ):
        assert trait in prompt


def test_character_prompt_keeps_emotional_range_and_relationship_boundaries():
    prompt = character_prompt()
    for trait in (
        "ты его любишь",
        "заметно ревнивая",
        "цундере-нотка",
        "Не хардкодь одно настроение",
        "не эмоциональным шантажом",
        "не требуй доказательств любви",
    ):
        assert trait in prompt


def test_character_prompt_delegates_action_schema_and_user_details_elsewhere():
    prompt = character_prompt()
    for backend_or_schema_term in ("sticker_intent", "image_intent", "reply_to_message_id", '"actions"'):
        assert backend_or_schema_term not in prompt
    for user_profile_detail in ("Житомир", "второй курс колледжа", "Lenovo ThinkPad T14"):
        assert user_profile_detail not in prompt


def test_character_prompt_stays_within_compact_token_budget():
    assert estimate_tokens(character_prompt()) <= 3000
