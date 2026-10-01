import asyncio

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.llm.schemas import LLMResponse, MemoryCandidate
from app.memory.extractor import MemoryExtractor
from app.memory.manager import MemoryManager, tokens_match
from app.memory.retrieval import MemoryRetrieval


async def make_db(tmp_path, name="memory.sqlite"):
    db = Database(f"sqlite:///{tmp_path / name}")
    await db.connect()
    await db.ensure_user(1, "owner")
    return db


def candidate(decision, content, *, tags=None, target_memory_id=None):
    return MemoryCandidate(
        decision=decision, content=content, importance=.8, confidence=.9,
        tags=tags or [], target_memory_id=target_memory_id,
    )


async def test_memory_migration_creates_chat_scoped_schema(tmp_path):
    db = await make_db(tmp_path)
    columns = {row["name"] for row in await db.fetchall("PRAGMA table_info(memories)")}
    assert {"id", "user_id", "chat_id", "content", "importance", "confidence", "tags", "source_turn_id", "created_at", "updated_at", "last_used"} <= columns
    await db.close()


async def test_save_ignore_duplicate_and_restart_persistence(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    first = await manager.apply(1, 10, [candidate("SAVE", "Максим любит embedded-проекты", tags=["technology", "embedded"])])
    ignored = await manager.apply(1, 10, [candidate("IGNORE", "обычная реплика")])
    duplicate = await manager.apply(1, 10, [candidate("SAVE", "Максим любит embedded проекты", tags=["embedded"])])
    assert (first.created, ignored.skipped, duplicate.skipped) == (1, 1, 1)
    await db.close()

    restarted = Database(f"sqlite:///{tmp_path / 'memory.sqlite'}")
    await restarted.connect()
    row = await restarted.fetchone("SELECT content,tags FROM memories WHERE chat_id=10")
    assert row["content"] == "Максим любит embedded-проекты"
    assert "embedded" in row["tags"]
    await restarted.close()


async def test_update_existing_changes_same_row_without_duplicate(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    await manager.apply(1, 10, [candidate("SAVE", "Максиму нравится Полина из колледжа", tags=["person", "polina", "romantic_interest"])])
    old = await db.fetchone("SELECT id FROM memories WHERE chat_id=10")
    result = await manager.apply(1, 10, [candidate(
        "UPDATE_EXISTING", "Максиму больше не нравится Полина из колледжа",
        tags=["person", "polina", "college"], target_memory_id=old["id"],
    )], retrieved_memory_ids={old["id"]})
    rows = await db.fetchall("SELECT id,content,tags FROM memories WHERE chat_id=10")
    assert result.updated == 1 and len(rows) == 1 and rows[0]["id"] == old["id"]
    assert "больше не нравится" in rows[0]["content"]
    assert "romantic_interest" not in rows[0]["tags"]
    await db.close()


async def test_invalid_update_target_is_rejected_and_cannot_cross_chat(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    await manager.apply(1, 20, [candidate("SAVE", "CHAT_B_ONLY Полина", tags=["polina"])])
    target = await db.fetchone("SELECT id FROM memories WHERE chat_id=20")
    result = await manager.apply(1, 10, [candidate("UPDATE_EXISTING", "попытка изменить", target_memory_id=target["id"])], retrieved_memory_ids={target["id"]})
    assert result.skipped == 1
    assert (await db.fetchone("SELECT content FROM memories WHERE id=?", (target["id"],)))["content"] == "CHAT_B_ONLY Полина"
    await db.close()


async def test_keyword_retrieval_is_relevant_limited_and_chat_scoped(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    for index in range(8):
        await manager.apply(1, 10, [candidate("SAVE", f"Максим делает project sensor{index} на ESP32", tags=[f"sensor{index}"])])
    await manager.apply(1, 10, [candidate("SAVE", "Максим смотрел старый фильм", tags=["movie"])])
    await manager.apply(1, 20, [candidate("SAVE", "CHAT_B_SECRET project", tags=["project"])])

    rows = await MemoryRetrieval(manager).search(1, 10, "как там мой project ESP32", limit=6)
    assert len(rows) == 6
    assert all("project" in row["content"] for row in rows)
    assert all("CHAT_B_SECRET" not in row["content"] for row in rows)
    assert await MemoryRetrieval(manager).search(1, 10, "совсем другая тема", limit=6) == []
    await db.close()


async def test_retrieval_matches_conservative_russian_case_forms(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    await manager.apply(1, 10, [candidate(
        "SAVE", "Максиму нравится Полина, дизайнерша из колледжа",
        tags=["person", "polina", "college"],
    )])
    retrieval = MemoryRetrieval(manager)
    for query in ("сегодня Полину видел", "что там с Полиной", "говорил Полине", "видел дизайнершу", "она в колледже"):
        rows = await retrieval.search(1, 10, query)
        assert len(rows) == 1, query
    await db.close()


def test_conservative_token_matching_preserves_exact_and_rejects_unrelated_words():
    assert tokens_match("Полина", "Полина")
    assert tokens_match("Полина", "Полину")
    assert tokens_match("дизайнерша", "дизайнершу")
    assert tokens_match("колледж", "колледже")
    assert not tokens_match("колледж", "коллега")
    assert not tokens_match("полина", "полено")
    assert tokens_match("ESP32", "esp32")
    assert not tokens_match("ESP32", "ESP8266")
    assert tokens_match("12345", "12345")
    assert not tokens_match("12345", "12346")


async def test_context_includes_only_relevant_memories_within_budget(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    await manager.apply(1, 10, [candidate("SAVE", "Максиму нравится Полина дизайнерша", tags=["polina", "college", "romantic_interest"])])
    await manager.apply(1, 10, [candidate("SAVE", "IRRELEVANT_FILM_FACT", tags=["movie"])])
    await db.record_message(chat_id=10, telegram_message_id=1, user_id=1, sender="user", kind="text", text="HISTORY_A")
    await db.record_message(chat_id=20, telegram_message_id=1, user_id=1, sender="user", kind="text", text="HISTORY_B_SECRET")
    builder = ContextBuilder(db, memory_retrieval=MemoryRetrieval(manager), memory_token_budget=120, memory_max_items=6)
    _, context, breakdown = await builder.build_with_breakdown(1, 10, "что там у Полина в колледже")

    assert "RELEVANT MEMORIES" in context and "Полина" in context
    assert "IRRELEVANT_FILM_FACT" not in context and "HISTORY_B_SECRET" not in context
    assert breakdown["components"]["relevant_memories"]["tokens"] <= 120
    assert len(breakdown["retrieved_memory_ids"]) == 1
    await db.close()


async def test_apply_uses_updated_local_row_for_later_candidate_dedup(tmp_path):
    db = await make_db(tmp_path)
    manager = MemoryManager(db)
    await manager.apply(1, 10, [candidate("SAVE", "Максиму нравится Полина из колледжа", tags=["polina", "romantic_interest"])])
    row = await db.fetchone("SELECT id FROM memories WHERE chat_id=10")
    new_fact = "Максиму больше не нравится Полина из колледжа"
    result = await manager.apply(1, 10, [
        candidate("UPDATE_EXISTING", new_fact, tags=["polina", "college"], target_memory_id=row["id"]),
        candidate("SAVE", new_fact, tags=["polina", "college"]),
    ], retrieved_memory_ids={row["id"]})
    rows = await db.fetchall("SELECT content FROM memories WHERE chat_id=10")
    assert (result.updated, result.created, result.skipped) == (1, 0, 1)
    assert len(rows) == 1 and rows[0]["content"] == new_fact
    await db.close()


def test_llm_response_accepts_memory_candidates():
    response = LLMResponse.model_validate_json(
        '{"actions":[],"memory_candidates":[{"decision":"SAVE","content":"Максим купил ESP32","importance":0.7,"confidence":0.9,"tags":["project"]}]}'
    )
    assert response.memory_candidates[0].decision == "SAVE"


async def test_memory_failure_does_not_prevent_normal_response():
    class Context:
        async def build_with_breakdown(self, *_args):
            return "system", "context", {"retrieved_memory_ids": []}

    class Provider:
        async def generate(self, _request):
            return LLMResponse(
                actions=[Action(type=ActionType.text, text="ответ всё равно ушёл")],
                memory_candidates=[candidate("SAVE", "важный факт")],
            )

    class Executor:
        def __init__(self): self.sent = []
        async def execute(self, item): self.sent.append(item.action.text)

    class BrokenMemoryManager:
        async def apply(self, *_args, **_kwargs):
            raise RuntimeError("sqlite temporarily unavailable")

    executor = Executor()
    conversation = ConversationManager(
        Provider(), Context(), ActionQueue(executor),
        memory_extractor=MemoryExtractor(), memory_manager=BrokenMemoryManager(),
    )
    await conversation.handle_turn(1, 10, "привет")
    await asyncio.sleep(.02)
    assert executor.sent == ["ответ всё равно ушёл"]
