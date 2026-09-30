from pathlib import Path

from app.conversation.tokens import estimate_tokens


def user_profile() -> str:
    return Path("prompts/user_profile.md").read_text(encoding="utf-8")


def test_user_profile_keeps_core_identity_technical_context_and_preferences():
    profile = user_profile()
    for fact in (
        "18 лет",
        "Житомир",
        "второй курс колледжа",
        "EndeavourOS",
        "Arch-based Linux",
        "ESP32",
        "C, C++, Python",
        "OpenAI, Codex и OpenRouter",
        "мат, опечатки, сокращения",
        "«Покороче»",
        "прежде всего собеседница",
    ):
        assert fact in profile


def test_user_profile_stays_within_compact_token_budget():
    assert estimate_tokens(user_profile()) <= 700
