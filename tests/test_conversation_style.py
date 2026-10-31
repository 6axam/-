from pathlib import Path

from app.actions.models import Action, ActionType
from app.conversation.message_splitter import MessageSplitter
from app.conversation.tokens import estimate_tokens


def _prompt(name: str) -> str:
    return Path("prompts", name).read_text(encoding="utf-8")


def test_character_style_has_room_for_unremarkable_and_imperfect_replies():
    prompt = _prompt("character.md")
    speech = prompt.split("## Речь", 1)[1].split("## Разговор", 1)[0]
    awkward = prompt.split("## Социальная неловкость и несовершенство", 1)[1].split("## Близость", 1)[0]

    assert estimate_tokens(prompt) <= 3000
    assert len(speech.split()) >= 70
    assert all(marker in speech.lower() for marker in ("не каждая реплика", "естественная пауза", "не создавай ошибки"))
    assert all(marker in awkward.lower() for marker in ("не выкупить", "не согласиться", "не изображай"))
    assert "не описывай себя списком" in prompt.lower()


def test_plain_long_thought_is_not_transformed_into_performative_burst():
    action = Action(
        type=ActionType.text,
        text="Я понимаю, почему эта идея тебе нравится, но тут есть один неприятный крайний случай, который лучше сначала проверить на реальном сообщении.",
    )
    splitter = MessageSplitter(target_chars=40, min_chars=10)

    assert splitter.split_actions([action]) == [action]


def test_initiative_and_social_style_forbid_service_checkins_without_forbidding_small_impulses():
    initiative = _prompt("initiative.md").lower()
    character = _prompt("character.md").lower()

    assert "do not manufacture" in initiative
    assert "do not default" in initiative
    assert "само знакомство" in character
    assert "без морали и второго абзаца" in character
