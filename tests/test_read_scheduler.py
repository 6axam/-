from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio

from app.conversation.manager import ConversationManager
from app.conversation.read_scheduler import ReadScheduler, ReadTimingEngine
from app.database.db import Database


def settings():
    return SimpleNamespace(
        read_delay_free_min_seconds=2, read_delay_free_max_seconds=20,
        read_delay_active_free_min_seconds=1, read_delay_active_free_max_seconds=5,
        read_delay_college_min_seconds=60, read_delay_college_max_seconds=900,
        read_delay_busy_min_seconds=60, read_delay_busy_max_seconds=600,
        read_delay_away_min_seconds=180, read_delay_away_max_seconds=1200,
        read_delay_after_wake_min_seconds=30, read_delay_after_wake_max_seconds=300,
    )


class Presence:
    def __init__(self, state):
        self.value = state

    async def state(self, _chat_id):
        return self.value


def free_state():
    return {"availability": "available", "phase": "free", "event": None, "sleep_until": None}


async def make_scheduler(tmp_path, *, state=None):
    db = Database(f"sqlite:///{tmp_path / 'reads.sqlite'}")
    await db.connect()
    clock = lambda: datetime.now(timezone.utc)
    timing = ReadTimingEngine(settings(), rng=lambda low, _high: low)
    return db, ReadScheduler(db, Presence(state or free_state()), timing, now=clock)


async def add_message(db, chat_id, message_id, text="hey"):
    await db.ensure_user(1, "owner")
    await db.record_message(
        chat_id=chat_id, telegram_message_id=message_id, user_id=1,
        sender="user", kind="text", text=text,
    )


def test_read_timing_uses_presence_ranges():
    timing = ReadTimingEngine(settings(), rng=lambda low, _high: low)
    assert timing.delay(free_state(), active_conversation=False) == 2
    assert timing.delay(free_state(), active_conversation=True) == 1
    assert timing.delay({**free_state(), "phase": "college"}, active_conversation=False) == 60
    assert timing.delay({**free_state(), "event": {"availability": "busy"}}, active_conversation=False) == 60
    assert timing.delay({**free_state(), "event": {"availability": "away"}}, active_conversation=False) == 180


async def test_no_conversation_callback_before_internal_read_is_due(tmp_path):
    db, scheduler = await make_scheduler(tmp_path)
    calls = []
    scheduler.bind(lambda record: calls.append(record))
    await add_message(db, 10, 1)
    await scheduler.schedule(1, 10, 1)

    await scheduler.process_due()

    assert calls == []
    assert (await db.fetchone("SELECT internally_read_at FROM messages WHERE chat_id=10"))["internally_read_at"] is None
    await db.close()


async def test_coalesced_messages_are_read_once_as_one_batch(tmp_path):
    db, scheduler = await make_scheduler(tmp_path)
    batches = []

    async def read(record):
        rows = await db.unread_user_messages_up_to(record["chat_id"], record["boundary_message_id"])
        batches.append([row["telegram_message_id"] for row in rows])
        await db.mark_messages_read(record["chat_id"], batches[-1])

    scheduler.bind(read)
    for message_id in (1, 2, 3):
        await add_message(db, 10, message_id, f"message {message_id}")
    first = await scheduler.schedule(1, 10, 1)
    row = await db.fetchone("SELECT read_after FROM scheduled_reads WHERE id=?", (first,))
    assert await scheduler.schedule(1, 10, 3) == first
    coalesced = await db.fetchone("SELECT boundary_message_id,read_after FROM scheduled_reads WHERE id=?", (first,))
    assert coalesced["boundary_message_id"] == 3 and coalesced["read_after"] == row["read_after"]
    await db.execute("UPDATE scheduled_reads SET read_after=datetime('now','-1 second') WHERE id=?", (first,))

    await scheduler.process_due()
    await scheduler.process_due()

    assert batches == [[1, 2, 3]]
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM messages WHERE internally_read_at IS NULL"))["n"] == 0
    assert (await db.fetchone("SELECT status FROM scheduled_reads WHERE id=?", (first,)))["status"] == "completed"
    await db.close()


async def test_claimed_boundary_never_marks_a_later_message_read(tmp_path):
    db, scheduler = await make_scheduler(tmp_path)

    async def read(record):
        # Simulates an arrival while this particular read job is processing.
        await add_message(db, 10, 2, "later")
        rows = await db.unread_user_messages_up_to(record["chat_id"], record["boundary_message_id"])
        await db.mark_messages_read(record["chat_id"], [row["telegram_message_id"] for row in rows])

    scheduler.bind(read)
    await add_message(db, 10, 1, "first")
    read_id = await scheduler.schedule(1, 10, 1)
    await db.execute("UPDATE scheduled_reads SET read_after=datetime('now','-1 second') WHERE id=?", (read_id,))
    await scheduler.process_due()

    first = await db.fetchone("SELECT internally_read_at FROM messages WHERE chat_id=10 AND telegram_message_id=1")
    later = await db.fetchone("SELECT internally_read_at FROM messages WHERE chat_id=10 AND telegram_message_id=2")
    assert first["internally_read_at"] is not None and later["internally_read_at"] is None
    await db.close()


async def test_claim_refreshes_coalesced_boundary_before_callback(tmp_path):
    db, base_scheduler = await make_scheduler(tmp_path)
    await add_message(db, 10, 1, "first")
    read_id = await base_scheduler.schedule(1, 10, 1)
    await db.execute("UPDATE scheduled_reads SET read_after=datetime('now','-1 second') WHERE id=?", (read_id,))

    class RaceScheduler(ReadScheduler):
        async def claim(self, pending_id):
            # `process_due()` has already SELECTed boundary=1. A new arrival
            # coalesces into that still-pending row immediately before claim.
            await add_message(db, 10, 2, "second")
            await self.schedule(1, 10, 2)
            return await super().claim(pending_id)

    scheduler = RaceScheduler(db, Presence(free_state()), ReadTimingEngine(settings(), rng=lambda low, _high: low))
    received_boundaries = []

    async def read(record):
        received_boundaries.append(record["boundary_message_id"])
        rows = await db.unread_user_messages_up_to(record["chat_id"], record["boundary_message_id"])
        await db.mark_messages_read(record["chat_id"], [row["telegram_message_id"] for row in rows])

    scheduler.bind(read)
    await scheduler.process_due()

    assert received_boundaries == [2]
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM messages WHERE internally_read_at IS NULL"))["n"] == 0
    assert (await db.fetchone("SELECT status FROM scheduled_reads WHERE id=?", (read_id,)))["status"] == "completed"
    assert not await db.has_unread_user_message_after(10, 0)
    await db.close()


async def test_sleep_read_waits_until_wake_plus_after_wake_delay(tmp_path):
    wake = datetime.now(timezone.utc) + timedelta(minutes=5)
    state = {"availability": "sleep", "phase": "free", "event": None,
             "sleep_until": wake.strftime("%Y-%m-%d %H:%M:%S")}
    db, scheduler = await make_scheduler(tmp_path, state=state)
    await add_message(db, 10, 1)
    read_id = await scheduler.schedule(1, 10, 1)
    row = await db.fetchone("SELECT read_after FROM scheduled_reads WHERE id=?", (read_id,))
    read_after = datetime.strptime(row["read_after"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert read_after >= wake.replace(microsecond=0) + timedelta(seconds=30)
    await db.close()


async def test_processing_read_recovers_after_restart_and_executes_once(tmp_path):
    db, scheduler = await make_scheduler(tmp_path)
    await add_message(db, 10, 1)
    read_id = await scheduler.schedule(1, 10, 1)
    await db.execute("UPDATE scheduled_reads SET status='processing',read_after=datetime('now','-1 second') WHERE id=?", (read_id,))
    await db.close()

    db = Database(f"sqlite:///{tmp_path / 'reads.sqlite'}")
    await db.connect()
    scheduler = ReadScheduler(db, Presence(free_state()), ReadTimingEngine(settings(), rng=lambda low, _high: low))
    completed = []

    async def read(record):
        completed.append(record["id"])
        rows = await db.unread_user_messages_up_to(record["chat_id"], record["boundary_message_id"])
        await db.mark_messages_read(record["chat_id"], [row["telegram_message_id"] for row in rows])

    scheduler.bind(read)
    await scheduler.recover_after_restart()
    await scheduler.process_due()
    await scheduler.process_due()

    assert completed == [read_id]
    assert (await db.fetchone("SELECT status FROM scheduled_reads WHERE id=?", (read_id,)))["status"] == "completed"
    await db.close()


async def test_new_arrival_during_read_claim_cannot_start_old_generation():
    class BlockingScheduler:
        def __init__(self):
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def cancel_chat(self, _chat_id):
            self.entered.set()
            await self.release.wait()

        def bind(self, _manager):
            pass

    scheduler = BlockingScheduler()
    manager = ConversationManager(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), scheduler=scheduler)
    old_batch = asyncio.create_task(manager.handle_turn(1, 10, "old batch"))
    await scheduler.entered.wait()
    new_arrival = asyncio.create_task(manager.interrupt(10))
    await asyncio.sleep(0)
    scheduler.release.set()
    await asyncio.gather(old_batch, new_arrival)

    assert 10 not in manager.generations
