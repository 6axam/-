import asyncio
import pytest

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.character.manager import EmotionalStateManager, PersonalityManager
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.conversation.response_scheduler import ResponseScheduler
from app.database.db import Database
from app.llm.prompts import read_prompt
from app.llm.schemas import LLMResponse, ResponseTiming, SelfUpdateProposal


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'character.sqlite'}")
    await db.connect()
    return db


def test_immutable_character_and_user_profile_load():
    character = read_prompt("character.md")
    profile = read_prompt("user_profile.md")
    assert "Тебя зовут Аня" in character
    assert "Максим" in profile


async def test_dynamic_personality_persists_and_strengthens(tmp_path):
    db = await make_db(tmp_path)
    manager = PersonalityManager(db)
    proposal = SelfUpdateProposal(category="music", subject="artist:X", value="likes", strength_delta=.1, reason="repeated discussion")
    await manager.apply([proposal])
    await manager.apply([proposal])
    row = await db.fetchone("SELECT strength,confidence FROM developed_personality WHERE subject='artist:X'")
    assert row["strength"] == .2
    assert row["confidence"] > .35
    await db.close()


async def test_contradictory_preference_weakens_existing_one(tmp_path):
    db = await make_db(tmp_path)
    manager = PersonalityManager(db)
    like = SelfUpdateProposal(category="games", subject="game:X", value="likes", strength_delta=.1, reason="x")
    await manager.apply([like])
    await manager.apply([like])
    await manager.apply([like])
    await manager.apply([SelfUpdateProposal(category="games", subject="game:X", value="dislikes", strength_delta=.1, reason="changed mind")])
    old = await db.fetchone("SELECT strength FROM developed_personality WHERE subject='game:X' AND value='likes'")
    assert old["strength"] == pytest.approx(.25)
    await db.close()


async def test_emotional_state_clamps_and_offense_decays(tmp_path):
    db = await make_db(tmp_path)
    state = EmotionalStateManager(db)
    from app.llm.schemas import EmotionalUpdate
    await state.apply(EmotionalUpdate(energy_delta=.08, offense_delta=.06))
    await state.apply(EmotionalUpdate(energy_delta=.08, offense_delta=.06))
    current = await state.get()
    assert current["energy"] <= 1 and current["offense_level"] <= 1
    await db.execute("UPDATE emotional_state SET offense_level=.5, last_updated=datetime('now','-5 hours') WHERE id=1")
    decayed = await state.get()
    assert decayed["offense_level"] < .5
    await db.close()


class FastTiming:
    def delay(self, _urgency, _state, **_kwargs): return .01


class DelayedProvider:
    def __init__(self): self.requests = []
    async def decide_timing(self, _request): return ResponseTiming(mode="delayed", urgency="low")
    async def generate(self, request):
        self.requests.append(request)
        return LLMResponse(actions=[Action(type=ActionType.text, text="fresh")])


class RecordingExecutor:
    def __init__(self): self.actions = []
    async def execute(self, item): self.actions.append(item.action.text)


async def test_delayed_response_persists_coalesces_and_rebuilds_context(tmp_path):
    db = await make_db(tmp_path)
    await db.ensure_user(1, "owner")
    await db.record_message(chat_id=10, telegram_message_id=1, user_id=1, sender="user", kind="text", text="старое")
    personality, emotions = PersonalityManager(db), EmotionalStateManager(db)
    scheduler = ResponseScheduler(db, FastTiming())
    provider, executor = DelayedProvider(), RecordingExecutor()
    manager = ConversationManager(provider, ContextBuilder(db, personality, emotions), ActionQueue(executor), personality, emotions, scheduler)
    await manager.handle_turn(1, 10, "старое")
    first = await db.fetchone("SELECT id FROM scheduled_responses WHERE status='pending'")
    # A second message joins the same pending response rather than creating another one.
    await db.record_message(chat_id=10, telegram_message_id=2, user_id=1, sender="user", kind="text", text="новое")
    await manager.handle_turn(1, 10, "новое")
    rows = await db.fetchall("SELECT * FROM scheduled_responses WHERE status='pending'")
    assert len(rows) == 1 and rows[0]["id"] == first["id"]
    # No executor/typing action exists during the waiting period.
    assert executor.actions == []
    await asyncio.sleep(.02)
    await scheduler.process_due()
    await asyncio.sleep(.02)
    assert executor.actions == ["fresh"]
    assert "новое" in provider.requests[-1].user_turn
    assert "новое" in provider.requests[-1].context
    assert (await db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (first["id"],)))["status"] == "completed"
    await db.close()


async def test_restart_recovers_due_scheduled_response(tmp_path):
    db = await make_db(tmp_path)
    await db.ensure_user(1, "owner")
    await db.record_message(chat_id=10, telegram_message_id=1, user_id=1, sender="user", kind="text", text="привет")
    provider, executor = DelayedProvider(), RecordingExecutor()
    scheduler = ResponseScheduler(db, FastTiming())
    manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(executor), scheduler=scheduler)
    await manager.handle_turn(1, 10, "привет")
    # Simulate process restart: a new scheduler/manager reuses the persisted row.
    restarted = ResponseScheduler(db, FastTiming())
    restarted_manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(executor), scheduler=restarted)
    await db.execute("UPDATE scheduled_responses SET respond_after=datetime('now','-1 second') WHERE chat_id=10")
    await restarted.process_due()
    await asyncio.sleep(.02)
    assert executor.actions == ["fresh"]
    await db.close()
