from types import SimpleNamespace

from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.conversation.tokens import estimate_tokens
from app.database.db import Database
from app.llm.schemas import LLMResponse


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
