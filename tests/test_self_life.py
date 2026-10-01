import asyncio
from types import SimpleNamespace

from app.actions.models import Action, ActionType
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.llm.schemas import AutobiographicalEventCandidate, LLMResponse, SpontaneousContinuation
from app.self_life import SelfLifeManager


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'self-life.sqlite'}")
    await db.connect()
    return db


def event(decision="SAVE", summary="препод попросил переделать макет", **kwargs):
    return AutobiographicalEventCandidate(decision=decision, summary=summary, **kwargs)


def test_self_life_gate_is_injectable():
    assert SelfLifeManager(SimpleNamespace(), probability=.60, rng=lambda: .59).continuation_gate()
    assert not SelfLifeManager(SimpleNamespace(), probability=.60, rng=lambda: .60).continuation_gate()


async def test_global_autobiography_saves_updates_and_deduplicates(tmp_path):
    db = await make_db(tmp_path)
    life = SelfLifeManager(db, rng=lambda: 0)
    assert await life.apply(event(), source_chat_id=10, source_turn_id=None, allowed_target_ids=set(), local_day="2026-10-01") == "saved"
    row = await db.fetchone("SELECT * FROM anya_life_events")
    # Same character event from a different chat is a global duplicate, not a
    # second biography for another user.
    assert await life.apply(event(summary="препод попросил переделать макет"), source_chat_id=20, source_turn_id=None, allowed_target_ids=set(), local_day="2026-10-01") == "deduplicated"
    assert await life.apply(event("UPDATE_EXISTING", "Аня переделала макет", target_event_id=row["id"]), source_chat_id=20, source_turn_id=None, allowed_target_ids={row["id"]}, local_day="2026-10-01") == "updated"
    assert (await db.fetchone("SELECT summary,source_chat_id FROM anya_life_events WHERE id=?", (row["id"],)))["source_chat_id"] == 20
    assert len(await life.relevant("макет", "2026-10-01")) == 1
    await db.close()


async def test_context_includes_global_events_and_hides_private_event_title(tmp_path):
    db = await make_db(tmp_path)
    life = SelfLifeManager(db)
    await life.apply(event(summary="Аня переделывала макет по типографике"), source_chat_id=10, source_turn_id=None, allowed_target_ids=set(), local_day=life.local_day("Europe/Kyiv"))
    builder = ContextBuilder(db, self_life=life, self_life_token_budget=120, timezone_name="Europe/Kyiv")
    private_event = {"title": "личный врач", "availability": "busy", "mentionable": 0}
    _system, context, breakdown = await builder.build_with_breakdown(
        1, 20, "что там с макетом", self_life_gate_open=True,
        life_state={"phase": "college", "availability": "busy", "event": private_event},
    )
    assert "RECENT / RELEVANT ANYA LIFE EVENTS" in context
    assert "типографике" in context and "личный врач" not in context
    assert "phase=college" in context and "continuation_gate=open" in context
    assert breakdown["retrieved_life_event_ids"] and breakdown["components"]["anya_life_events"]["tokens"] > 0
    await db.close()


class Queue:
    def __init__(self):
        self.executor = SimpleNamespace()
        self.items = []

    async def enqueue_many(self, _chat_id, _generation, actions):
        self.items = actions

    def cancel_generation(self, _generation):
        pass


class Context:
    async def build_with_breakdown(self, *_args, **_kwargs):
        return "system", "context", {"retrieved_life_event_ids": []}


class Presence:
    def __init__(self, availability="available"):
        self.availability = availability

    async def state(self, _chat_id):
        return {"availability": self.availability, "phase": "free", "event": None, "sleep_until": "2099-01-01 07:00:00"}


async def test_open_gate_appends_one_separate_continuation_and_saves_event(tmp_path):
    db = await make_db(tmp_path)
    life = SelfLifeManager(db, rng=lambda: 0)

    class Provider:
        async def generate(self, _request):
            return LLMResponse(
                actions=[Action(type=ActionType.text, text="угу")],
                spontaneous_continuation=SpontaneousContinuation(
                    send=True, text="у нас сегодня шрифт всем не понравился",
                    event_candidate=event(summary="На паре спорили из-за шрифта", participants=["препод"]),
                ),
            )

    queue = Queue()
    manager = ConversationManager(Provider(), Context(), queue, presence=Presence(), self_life=life)
    await manager.handle_turn(1, 10, "ладно")
    assert [action.text for action in queue.items] == ["угу", "у нас сегодня шрифт всем не понравился"]
    assert (await db.fetchone("SELECT summary FROM anya_life_events"))["summary"] == "На паре спорили из-за шрифта"
    await db.close()


async def test_closed_gate_never_adds_or_persists_continuation(tmp_path):
    db = await make_db(tmp_path)
    life = SelfLifeManager(db, rng=lambda: 1)

    class Provider:
        async def generate(self, _request):
            return LLMResponse(actions=[Action(type=ActionType.text, text="угу")], spontaneous_continuation=SpontaneousContinuation(send=True, text="лишнее", event_candidate=event()))

    queue = Queue()
    await ConversationManager(Provider(), Context(), queue, presence=Presence(), self_life=life).handle_turn(1, 10, "ладно")
    assert [action.text for action in queue.items] == ["угу"]
    assert await db.fetchall("SELECT * FROM anya_life_events") == []
    await db.close()


async def test_stale_generation_does_not_persist_self_life_event(tmp_path):
    db = await make_db(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class Provider:
        async def generate(self, _request):
            entered.set(); await release.wait()
            return LLMResponse(actions=[Action(type=ActionType.text, text="угу")], spontaneous_continuation=SpontaneousContinuation(send=True, text="история", event_candidate=event()))

    manager = ConversationManager(Provider(), Context(), Queue(), presence=Presence(), self_life=SelfLifeManager(db, rng=lambda: 0))
    task = asyncio.create_task(manager.handle_turn(1, 10, "первое"))
    await entered.wait()
    await manager.interrupt(10)
    release.set(); await task
    assert await db.fetchall("SELECT * FROM anya_life_events") == []
    await db.close()
