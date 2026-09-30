from types import SimpleNamespace

from app.character.manager import EmotionalStateManager, PersonalityManager
from app.conversation.context import ContextBuilder
from app.conversation.lifecycle import ConversationLifecycleManager
from app.conversation.tokens import estimate_tokens
from app.database.db import Database
from app.initiative.context import InitiativeContextBuilder
from app.initiative.scheduler import InitiativeScheduler
from app.llm.openai_provider import INITIATIVE_STRUCTURED_OUTPUT_INSTRUCTION
from app.llm.schemas import InitiativeDecision


async def make_runtime(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'initiative-context.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "owner")
    personality, emotions = PersonalityManager(db), EmotionalStateManager(db)
    conversation = ContextBuilder(db, personality, emotions)
    lifecycle = ConversationLifecycleManager(db, cooling_minutes=30, ended_hours=12)
    return db, personality, emotions, lifecycle, InitiativeContextBuilder(conversation, lifecycle)


async def add_message(db, chat_id, message_id, text):
    await db.record_message(
        chat_id=chat_id, telegram_message_id=message_id, user_id=1,
        sender="user", kind="text", text=text,
    )


async def test_initiative_context_includes_character_emotion_and_compact_recent_history(tmp_path):
    db, _personality, emotions, lifecycle, builder = await make_runtime(tmp_path)
    await add_message(db, 10, 1, "жду результат прошивки")
    await lifecycle.on_user_message(10)
    await emotions.get()

    system, context, breakdown = await builder.build(1, 10)

    assert "Тебя зовут Аня" in system
    assert "EMOTIONAL STATE" in context
    assert "жду результат прошивки" in context
    assert "USER PROFILE" not in system + context
    assert "RECENT IMAGES YOU SENT" not in context
    assert estimate_tokens(system + "\n" + INITIATIVE_STRUCTURED_OUTPUT_INSTRUCTION + context) <= 3500
    assert breakdown["history_messages"] == 1
    await db.close()


async def test_initiative_recent_history_is_chat_scoped(tmp_path):
    db, _personality, _emotions, lifecycle, builder = await make_runtime(tmp_path)
    await add_message(db, 10, 1, "CHAT_A_FOLLOWUP")
    await add_message(db, 20, 1, "CHAT_B_PRIVATE_SECRET")
    await lifecycle.on_user_message(10)

    _system, context, _breakdown = await builder.build(1, 10)

    assert "CHAT_A_FOLLOWUP" in context
    assert "CHAT_B_PRIVATE_SECRET" not in context
    await db.close()


async def test_scheduler_passes_compact_character_context_to_initiative_provider(tmp_path):
    class Provider:
        def __init__(self): self.request = None
        async def decide_initiative(self, request):
            self.request = request
            return InitiativeDecision(should_message=False, reason="not now")

    class Manager:
        def __init__(self, provider): self.provider, self.generations = provider, {}
        def has_active_generation(self, _chat_id): return False
        async def enqueue_initiative(self, *_args): return False

    class Responses:
        async def has_pending(self, _chat_id): return False

    db, _personality, _emotions, lifecycle, context = await make_runtime(tmp_path)
    await add_message(db, 10, 1, "потом скину результат")
    await lifecycle.on_user_message(10)
    provider = Provider()
    scheduler = InitiativeScheduler(
        db, Manager(provider), lifecycle, Responses(), context,
        SimpleNamespace(),
    )

    await scheduler._decide(1, 10)

    assert "Тебя зовут Аня" in provider.request.system
    assert "EMOTIONAL STATE" in provider.request.context
    assert provider.request.telemetry["kind"] == "initiative"
    assert provider.request.telemetry["target_input_tokens"] == 3500
    await db.close()
