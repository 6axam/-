from types import SimpleNamespace

from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.conversation.tokens import estimate_tokens
from app.database.db import Database
from app.emotions.affective import AffectiveEngine
from app.emotions.relationship import RelationshipBondManager
from app.llm.schemas import LLMResponse
from app.llm.openai_provider import OpenAICompatibleProvider


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'context.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "owner")
    return db


async def message(db, chat_id, message_id, text):
    await db.record_message(
        chat_id=chat_id, telegram_message_id=message_id, user_id=1,
        sender="user", kind="text", text=text,
    )


async def test_history_budget_keeps_newest_messages_in_chronological_order(tmp_path):
    db = await make_db(tmp_path)
    old, middle, newest = "OLD_ONLY " * 12, "MIDDLE_ONLY " * 12, "NEWEST_ONLY " * 12
    await message(db, 10, 1, old)
    await message(db, 10, 2, middle)
    await message(db, 10, 3, newest)
    budget = estimate_tokens(f"user: {middle}\nuser: {newest}")
    builder = ContextBuilder(db, recent_max_messages=10, recent_token_budget=budget)

    _, context, breakdown = await builder.build_with_breakdown(1, 10, "current")

    assert "OLD_ONLY" not in context
    assert "MIDDLE_ONLY" in context and "NEWEST_ONLY" in context
    assert context.index("MIDDLE_ONLY") < context.index("NEWEST_ONLY")
    assert breakdown["history_messages"] == 2
    assert breakdown["components"]["conversation_history"]["tokens"] <= budget
    await db.close()


async def test_history_respects_max_messages_even_when_budget_is_large(tmp_path):
    db = await make_db(tmp_path)
    for index in range(1, 5):
        await message(db, 10, index, f"MESSAGE_{index}")
    builder = ContextBuilder(db, recent_max_messages=2, recent_token_budget=10_000)

    _, context, breakdown = await builder.build_with_breakdown(1, 10, "current")

    assert "MESSAGE_1" not in context and "MESSAGE_2" not in context
    assert "MESSAGE_3" in context and "MESSAGE_4" in context
    assert breakdown["history_messages"] == 2
    await db.close()


async def test_single_oversized_recent_message_is_deterministically_bounded(tmp_path):
    db = await make_db(tmp_path)
    await message(db, 10, 1, "HUGE_MESSAGE " * 100)
    builder = ContextBuilder(db, recent_max_messages=24, recent_token_budget=30)

    _, context, breakdown = await builder.build_with_breakdown(1, 10, "current")

    assert "HUGE_MESSAGE" in context
    assert breakdown["history_messages"] == 1
    assert breakdown["components"]["conversation_history"]["tokens"] <= 30
    await db.close()


async def test_budgeted_history_remains_chat_scoped(tmp_path):
    db = await make_db(tmp_path)
    await message(db, 10, 1, "CHAT_A_ONLY")
    await message(db, 20, 1, "CHAT_B_SECRET")
    builder = ContextBuilder(db, recent_max_messages=24, recent_token_budget=1500)

    _, context, _ = await builder.build_with_breakdown(1, 10, "current")

    assert "CHAT_A_ONLY" in context
    assert "CHAT_B_SECRET" not in context
    await db.close()


async def test_primary_request_keeps_current_turn_and_attaches_numeric_breakdown(tmp_path):
    class Provider:
        def __init__(self): self.request = None
        async def generate(self, request): self.request = request; return LLMResponse()

    db = await make_db(tmp_path)
    await message(db, 10, 1, "OLD_HISTORY " * 100)
    provider = Provider()
    manager = ConversationManager(
        provider, ContextBuilder(db, recent_token_budget=30, target_input_tokens=100),
        ActionQueue(SimpleNamespace(execute=lambda _: None)),
    )

    await manager.handle_turn(1, 10, "CURRENT_TURN_MUST_STAY")

    telemetry = provider.request.telemetry
    assert "CURRENT_TURN_MUST_STAY" in provider.request.user_turn
    assert telemetry["history_messages"] == 1
    assert telemetry["components"]["current_user_turn"]["chars"] == len("CURRENT_TURN_MUST_STAY")
    assert telemetry["components"]["character_prompt"]["tokens"] > 0
    assert all(set(size) == {"chars", "tokens"} for size in telemetry["components"].values())
    assert "CURRENT_TURN_MUST_STAY" not in str(telemetry)
    await db.close()


async def test_current_logical_turn_is_present_once_and_identical_history_remains(tmp_path):
    class Provider:
        def __init__(self): self.request = None
        async def generate(self, request): self.request = request; return LLMResponse()

    db = await make_db(tmp_path)
    marker = "IDENTICAL_USER_TEXT"
    await message(db, 10, 1, marker)
    await db.create_turn(user_id=1, chat_id=10, merged_text=marker, telegram_message_ids=[1])
    await db.record_message(chat_id=10, telegram_message_id=2, sender="assistant", kind="text", text="previous answer")
    await message(db, 10, 3, marker)
    current_turn_id = await db.create_turn(user_id=1, chat_id=10, merged_text=marker, telegram_message_ids=[3])
    provider = Provider()
    manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(SimpleNamespace(execute=lambda _: None)))

    await manager.handle_turn(1, 10, marker, turn_id=current_turn_id)

    final_content = OpenAICompatibleProvider("key", "model")._user_content(provider.request)
    assert final_content.count(marker) == 2
    assert provider.request.context.count(marker) == 1
    assert provider.request.user_turn == marker
    assert provider.request.telemetry["history_messages"] == 2
    await db.close()


async def test_all_messages_in_current_burst_are_excluded_from_history(tmp_path):
    db = await make_db(tmp_path)
    await message(db, 10, 1, "OLDER_HISTORY")
    await db.create_turn(user_id=1, chat_id=10, merged_text="OLDER_HISTORY", telegram_message_ids=[1])
    await message(db, 10, 2, "BURST_FIRST")
    await message(db, 10, 3, "BURST_SECOND")
    turn_id = await db.create_turn(
        user_id=1, chat_id=10, merged_text="BURST_FIRST\nBURST_SECOND", telegram_message_ids=[2, 3],
    )

    _system, context, breakdown = await ContextBuilder(db).build_with_breakdown(
        1, 10, "BURST_FIRST\nBURST_SECOND", current_turn_id=turn_id,
    )

    assert "OLDER_HISTORY" in context
    assert "BURST_FIRST" not in context and "BURST_SECOND" not in context
    assert breakdown["history_messages"] == 1
    await db.close()


async def test_delayed_response_excludes_its_durable_turn_from_history(tmp_path):
    class Provider:
        supports_vision = False
        def __init__(self): self.request = None
        async def generate(self, request): self.request = request; return LLMResponse()

    class Scheduler:
        def __init__(self): self.completed = []
        def bind(self, _manager): pass
        async def is_current(self, _record): return True
        async def complete(self, record_id): self.completed.append(record_id)

    db = await make_db(tmp_path)
    await message(db, 10, 1, "DELAYED_HISTORY")
    await db.create_turn(user_id=1, chat_id=10, merged_text="DELAYED_HISTORY", telegram_message_ids=[1])
    await db.record_message(chat_id=10, telegram_message_id=2, sender="assistant", kind="text", text="old response")
    await message(db, 10, 3, "DELAYED_FIRST")
    await message(db, 10, 4, "DELAYED_SECOND")
    await db.create_turn(
        user_id=1, chat_id=10, merged_text="DELAYED_FIRST\nDELAYED_SECOND", telegram_message_ids=[3, 4],
    )
    provider, scheduler = Provider(), Scheduler()
    manager = ConversationManager(
        provider, ContextBuilder(db), ActionQueue(SimpleNamespace(execute=lambda _: None)), scheduler=scheduler,
    )

    await manager.handle_scheduled({"id": 7, "chat_id": 10, "generation_id": "delayed", "delay_event_id": None})

    assert "DELAYED_HISTORY" in provider.request.context
    assert "DELAYED_FIRST" not in provider.request.context
    assert "DELAYED_SECOND" not in provider.request.context
    assert provider.request.user_turn == "DELAYED_FIRST\nDELAYED_SECOND"
    assert scheduler.completed == [7]
    await db.close()


async def test_canonical_life_background_is_in_system_prompt_and_telemetry(tmp_path):
    db = await make_db(tmp_path)
    system, _, breakdown = await ContextBuilder(db).build_with_breakdown(1, 10, "привет")

    assert "LIFE BACKGROUND" in system
    for fact in ("Житомире", "18 лет", "2 курсе", "дизайна"):
        assert fact in system
    size = breakdown["components"]["life_background"]
    assert size["chars"] >= 600 and size["chars"] <= 1200
    assert size["tokens"] <= 400
    # The compact life block may add context, but must not turn a normal
    # empty-history request into an oversized prompt by itself.
    assert breakdown["estimated_input_tokens"] < 5600
    await db.close()


async def test_runtime_relationship_state_drives_expression_without_fixed_love(tmp_path):
    db = await make_db(tmp_path)
    builder = ContextBuilder(db)
    builder.affective_engine = AffectiveEngine(db)
    builder.relationship_manager = RelationshipBondManager(db)
    system, context, breakdown = await builder.build_with_breakdown(1, 10, "привет")
    assert "ты его любишь" not in system.lower()
    assert "RELATIONSHIP STATE" in context
    assert "love_strength=" in context and "regulation_capacity=" in context
    assert breakdown["components"]["relationship_state"]["tokens"] > 0
    await db.close()


async def test_action_tendencies_reach_conversation_system_prompt(tmp_path):
    db = await make_db(tmp_path)
    system, _, breakdown = await ContextBuilder(
        db, sticker_tendency=.85, reaction_tendency=.70
    ).build_with_breakdown(1, 10, "привет")

    assert "ACTION TENDENCIES" in system
    assert "sticker_tendency=0.85" in system
    assert "reaction_tendency=0.70" in system
    assert breakdown["components"]["action_tendencies"]["tokens"] > 0
    await db.close()


async def test_bedtime_state_is_compact_optional_context(tmp_path):
    db = await make_db(tmp_path)
    _system, context, breakdown = await ContextBuilder(db).build_with_breakdown(
        1, 10, "привет", bedtime_state={"bedtime_window": True, "local_time": "2026-10-01 00:45", "minutes_until_sleep": 15, "already_said_goodnight": False}
    )
    assert "BEDTIME STATE" in context and "minutes_until_sleep=15" in context
    assert breakdown["components"]["bedtime_state"]["tokens"] > 0
    await db.close()
