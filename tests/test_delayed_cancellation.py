import asyncio
from types import SimpleNamespace

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.manager import ConversationManager
from app.conversation.response_scheduler import ResponseScheduler
from app.database.db import Database
from app.llm.schemas import LLMResponse, ResponseTiming


class FastTiming:
    def delay(self, _urgency, _state, **_kwargs):
        return 0


class Context:
    def __init__(self, db):
        self.db = db

    async def build(self, _user_id, _chat_id, _text):
        return "system", "context"


class DelayedProvider:
    def __init__(self):
        self.generations = 0

    async def decide_timing(self, _request):
        return ResponseTiming(mode="delayed", urgency="normal")

    async def generate(self, _request):
        self.generations += 1
        return LLMResponse(actions=[Action(type=ActionType.text, text="scheduled")])


class Executor:
    def __init__(self):
        self.actions = []

    async def execute(self, item):
        self.actions.append(item.action.text)


async def make_runtime(tmp_path, *, context=None):
    db = Database(f"sqlite:///{tmp_path / 'delayed.sqlite'}")
    await db.connect()
    provider, executor = DelayedProvider(), Executor()
    scheduler = ResponseScheduler(db, FastTiming())
    manager = ConversationManager(provider, context or Context(db), ActionQueue(executor), scheduler=scheduler)
    return db, scheduler, manager, provider, executor


async def insert_delayed(db, chat_id, generation="old"):
    await db.ensure_user(1, "owner")
    await db.record_message(chat_id=chat_id, telegram_message_id=1, user_id=1, sender="user", kind="text", text="message A")
    result = await db.execute(
        "INSERT INTO scheduled_responses(chat_id,user_id,respond_after,generation_id,status) VALUES(?,?,datetime('now','-1 second'),?,'pending')",
        (chat_id, 1, generation),
    )
    return result.lastrowid


async def test_new_user_message_persistently_cancels_pending_delayed_response(tmp_path):
    db, scheduler, manager, provider, executor = await make_runtime(tmp_path)
    schedule_id = await insert_delayed(db, 10)

    await db.record_message(chat_id=10, telegram_message_id=2, user_id=1, sender="user", kind="text", text="message B")
    await manager.interrupt(10)
    await scheduler.process_due()

    assert (await db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (schedule_id,)))["status"] == "cancelled"
    assert provider.generations == 0
    assert executor.actions == []
    await db.close()


async def test_cancelled_delayed_response_stays_cancelled_after_restart(tmp_path):
    db, _scheduler, manager, _provider, _executor = await make_runtime(tmp_path)
    schedule_id = await insert_delayed(db, 10)
    await manager.interrupt(10)
    await db.close()

    restarted_db = Database(f"sqlite:///{tmp_path / 'delayed.sqlite'}")
    await restarted_db.connect()
    restarted = ResponseScheduler(restarted_db, FastTiming())
    await restarted.recover_after_restart()

    assert (await restarted_db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (schedule_id,)))["status"] == "cancelled"
    assert await restarted.due() == []
    await restarted_db.close()


async def test_claim_cancel_race_cannot_start_stale_generation(tmp_path):
    class BlockingContext(Context):
        def __init__(self, db):
            super().__init__(db)
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def build(self, _user_id, _chat_id, _text):
            self.entered.set()
            await self.release.wait()
            return "system", "context"

    db = Database(f"sqlite:///{tmp_path / 'race.sqlite'}")
    await db.connect()
    context = BlockingContext(db)
    provider, executor = DelayedProvider(), Executor()
    scheduler = ResponseScheduler(db, FastTiming())
    manager = ConversationManager(provider, context, ActionQueue(executor), scheduler=scheduler)
    schedule_id = await insert_delayed(db, 10)

    processing = asyncio.create_task(scheduler.process_due())
    await context.entered.wait()  # The row is claimed; _generate is about to call the LLM.
    await db.record_message(chat_id=10, telegram_message_id=2, user_id=1, sender="user", kind="text", text="message B")
    await manager.interrupt(10)
    context.release.set()
    await processing

    assert (await db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (schedule_id,)))["status"] == "cancelled"
    assert provider.generations == 0
    assert executor.actions == []
    await db.close()


async def test_new_message_only_cancels_its_own_chat(tmp_path):
    db, _scheduler, manager, _provider, _executor = await make_runtime(tmp_path)
    first = await insert_delayed(db, 10, "chat-a")
    second = await insert_delayed(db, 20, "chat-b")

    await manager.interrupt(10)

    assert (await db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (first,)))["status"] == "cancelled"
    assert (await db.fetchone("SELECT status FROM scheduled_responses WHERE id=?", (second,)))["status"] == "pending"
    await db.close()


async def test_new_turn_can_schedule_a_replacement_after_cancellation(tmp_path):
    db, _scheduler, manager, _provider, _executor = await make_runtime(tmp_path)
    old_id = await insert_delayed(db, 10, "old")
    await db.record_message(chat_id=10, telegram_message_id=2, user_id=1, sender="user", kind="text", text="message B")

    await manager.handle_turn(1, 10, "message B")

    rows = await db.fetchall("SELECT id,generation_id,status FROM scheduled_responses WHERE chat_id=10 ORDER BY id")
    assert rows[0]["id"] == old_id and rows[0]["status"] == "cancelled"
    assert len(rows) == 2 and rows[1]["status"] == "pending" and rows[1]["generation_id"] != "old"
    await db.close()
