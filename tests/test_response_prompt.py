from pathlib import Path

from app.actions.models import ImageKind
from app.conversation.tokens import estimate_tokens


def response_prompt() -> str:
    return Path("prompts/response.md").read_text(encoding="utf-8")


def test_response_prompt_keeps_critical_action_behavior_contract():
    prompt = response_prompt()
    for concept in (
        "минимальный естественный ответ",
        "отдельное Telegram-сообщение",
        "одна законченная мысль — один `text` action",
        "`silence`",
        "`sticker_intent`",
        "`target_message_id`",
        "`reply_to_message_id`",
        "`image` выбирай редко",
        "`self_updates`",
        "`conversation`",
    ):
        assert concept in prompt


def test_response_prompt_delegates_schema_details_to_native_structured_output():
    prompt = response_prompt()
    assert '"$defs"' not in prompt
    assert '"properties"' not in prompt
    assert "creative_image" not in prompt
    assert "photo_of_something" not in prompt
    for kind in ImageKind:
        assert kind.value not in prompt


def test_response_prompt_stays_within_compact_token_budget():
    assert estimate_tokens(response_prompt()) <= 700


def test_response_prompt_keeps_bursts_optional_not_a_length_heuristic():
    prompt = response_prompt().lower()
    assert "не дроби абзац" in prompt
    assert "не требует" in prompt
    assert prompt.count("text action") <= 6
