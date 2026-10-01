from types import SimpleNamespace

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.lifecycle import ConversationLifecycleManager
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.initiative.scheduler import InitiativeScheduler
from app.llm.schemas import ConversationMetadata, InitiativeDecision, LLMResponse


async def db_for(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'lifecycle.sqlite'}")
    await db.connect()
    return db


class NoopContext:
    async def build(self, *_args): return "system", "context"


class SilentProvider:
    async def generate(self, _request): return LLMResponse(actions=[Action(type=ActionType.silence)])


class Executor:
    def __init__(self): self.executed = []
    async def execute(self, item): self.executed.append(item.action.type)


async def test_silence_creates_no_action_queue_work():
    executor = Executor()
    manager = ConversationManager(SilentProvider(), NoopContext(), ActionQueue(executor))
    await manager.handle_turn(1, 10, "ага")
    assert executor.executed == []


async def test_reaction_only_and_sticker_only_are_valid_queue_turns():
    executor = Executor()
    queue = ActionQueue(executor)
    await queue.enqueue_many(10, "reaction", [Action(type=ActionType.reaction, emoji="👍", target_message_id=1)])
    await queue.enqueue_many(11, "sticker", [Action(type=ActionType.sticker, sticker_id=1)])
    import asyncio
    await asyncio.sleep(.02)
    assert executor.executed == [ActionType.reaction, ActionType.sticker]


async def test_lifecycle_persists_clamps_and_transitions(tmp_path):
    db = await db_for(tmp_path)
    lifecycle = ConversationLifecycleManager(db, cooling_minutes=1, ended_hours=2)
    await lifecycle.on_user_message(10)
    await lifecycle.apply(10, ConversationMetadata(expects_reply=True, followup_importance=.4, followup_reason="waiting for flash"))
    await db.execute("UPDATE conversation_lifecycle SET last_meaningful_interaction_at=datetime('now','-3 hours') WHERE chat_id=10")
    await lifecycle.transition_due()
    state = await lifecycle.get(10)
    assert state["conversation_status"] == "ended"
    assert state["expects_reply"] == 1 and state["followup_importance"] == .4
    await db.close()


class DecisionProvider:
    def __init__(self, decision): self.decision, self.calls = decision, 0
    async def decide_initiative(self, _request): self.calls += 1; return self.decision


class InitiativeManager:
    def __init__(self, provider): self.provider, self.sent, self.generations = provider, [], {}
    def has_active_generation(self, _chat): return False
    async def enqueue_initiative(self, chat, generation, actions): self.sent.append((chat, generation, actions)); return True


class ResponseScheduler:
    async def has_pending(self, _chat): return False


class PendingResponseScheduler:
    async def has_pending(self, _chat): return True


class InitiativeContext:
    async def build(self, _user, _chat): return "system", "context"


def settings(**overrides):
    values = dict(initiative_min_idle_minutes=45, initiative_spontaneous_min_idle_minutes=60, initiative_spontaneous_probability=.55, initiative_cooldown_hours=4, initiative_max_per_day=3, initiative_max_unanswered=1, initiative_enabled=True, initiative_check_interval_minutes=15)
    values.update(overrides)
    return SimpleNamespace(**values)


async def prepared_scheduler(tmp_path, decision):
    db = await db_for(tmp_path)
    await db.ensure_user(1, "owner")
    await db.record_message(chat_id=10, telegram_message_id=1, user_id=1, sender="user", kind="text", text="щас прошью и скажу")
    lifecycle = ConversationLifecycleManager(db, 30, 12)
    await lifecycle.on_user_message(10)
    await lifecycle.apply(10, ConversationMetadata(expects_reply=True, followup_importance=.4, followup_reason="flash result"))
    await db.execute("UPDATE conversation_lifecycle SET last_meaningful_interaction_at=datetime('now','-2 hours') WHERE chat_id=10")
    await db.mark_messages_read(10, [1])
    provider = DecisionProvider(decision)
    manager = InitiativeManager(provider)
    return db, InitiativeScheduler(db, manager, lifecycle, ResponseScheduler(), InitiativeContext(), settings()), provider, manager


async def test_initiative_eligibility_then_max_unanswered_prevents_spam(tmp_path):
    decision = InitiativeDecision(should_message=True, reason="waiting for flash result", actions=[Action(type=ActionType.text, text="ну шо")])
    db, scheduler, provider, manager = await prepared_scheduler(tmp_path, decision)
    assert await scheduler.check_chat(1, 10)
    assert len(manager.sent) == 1 and provider.calls == 1
    await db.execute("UPDATE messages SET timestamp=datetime('now','-6 hours') WHERE chat_id=10")
    await db.execute("UPDATE initiative_history SET created_at=datetime('now','-5 hours') WHERE chat_id=10")
    ok, reason, _ = await scheduler.eligibility(10)
    assert not ok and reason == "max_unanswered"
    await db.close()


async def test_ordinary_absence_can_take_spontaneous_path(tmp_path):
    db = await db_for(tmp_path)
    lifecycle = ConversationLifecycleManager(db, 30, 12)
    await lifecycle.on_user_message(10)
    await db.execute("UPDATE conversation_lifecycle SET last_meaningful_interaction_at=datetime('now','-2 days') WHERE chat_id=10")
    provider = DecisionProvider(InitiativeDecision(should_message=True, reason="should not be called"))
    scheduler = InitiativeScheduler(db, InitiativeManager(provider), lifecycle, ResponseScheduler(), InitiativeContext(), settings(), rng=lambda: 0)
    assert await scheduler.check_chat(1, 10)
    assert provider.calls == 1
    await db.close()


async def test_initiative_skips_pending_response_and_active_generation(tmp_path):
    db, scheduler, provider, manager = await prepared_scheduler(tmp_path, InitiativeDecision(should_message=True, reason="x"))
    scheduler.response_scheduler = PendingResponseScheduler()
    assert not await scheduler.check_chat(1, 10)
    assert provider.calls == 0
    scheduler.response_scheduler = ResponseScheduler()
    manager.has_active_generation = lambda _chat: True
    assert not await scheduler.check_chat(1, 10)
    assert provider.calls == 0
    await db.close()


async def test_restart_keeps_lifecycle_and_initiative_cooldown(tmp_path):
    db, scheduler, _provider, _manager = await prepared_scheduler(tmp_path, InitiativeDecision(should_message=False, reason="no"))
    await db.execute("INSERT INTO initiative_history(chat_id,reason) VALUES(?,?)", (10, "test"))
    await db.close()
    restarted = Database(f"sqlite:///{tmp_path / 'lifecycle.sqlite'}")
    await restarted.connect()
    lifecycle = ConversationLifecycleManager(restarted, 30, 12)
    state = await lifecycle.get(10)
    assert state["expects_reply"] == 1
    await restarted.close()


async def test_unread_message_blocks_initiative_before_llm(tmp_path):
    db, scheduler, provider, _manager = await prepared_scheduler(
        tmp_path, InitiativeDecision(should_message=True, reason="x")
    )
    await db.record_message(chat_id=10, telegram_message_id=2, user_id=1, sender="user", kind="text", text="UNREAD")
    assert not await scheduler.check_chat(1, 10)
    assert provider.calls == 0
    await db.close()


async def test_spontaneous_gate_is_injectable(tmp_path):
    db = await db_for(tmp_path)
    lifecycle = ConversationLifecycleManager(db, 30, 12)
    await lifecycle.on_user_message(10)
    await db.execute("UPDATE conversation_lifecycle SET last_meaningful_interaction_at=datetime('now','-2 days') WHERE chat_id=10")
    provider = DecisionProvider(InitiativeDecision(should_message=True, reason="x"))
    scheduler = InitiativeScheduler(db, InitiativeManager(provider), lifecycle, ResponseScheduler(), InitiativeContext(), settings(initiative_spontaneous_probability=.5), rng=lambda: .9)
    ok, reason, _state = await scheduler.eligibility(10)
    assert not ok and reason == "spontaneous_gate_closed"
    await db.close()


async def test_new_message_during_initiative_llm_call_cancels_delivery(tmp_path):
    db = await db_for(tmp_path)
    await db.ensure_user(1, "owner")
    lifecycle = ConversationLifecycleManager(db, 30, 12)
    await lifecycle.on_user_message(10)
    await db.execute("UPDATE conversation_lifecycle SET last_meaningful_interaction_at=datetime('now','-2 hours') WHERE chat_id=10")

    class Provider:
        async def decide_initiative(self, _request):
            await db.record_message(chat_id=10, telegram_message_id=99, user_id=1, sender="user", kind="text", text="новое сообщение")
            return InitiativeDecision(should_message=True, reason="old", actions=[Action(type=ActionType.text, text="старое")])

    manager = InitiativeManager(Provider())
    scheduler = InitiativeScheduler(db, manager, lifecycle, ResponseScheduler(), InitiativeContext(), settings(), rng=lambda: 0)
    assert not await scheduler._decide(1, 10, "spontaneous")
    assert manager.sent == []
    await db.close()
