from datetime import datetime, timezone
from types import SimpleNamespace

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.read_scheduler import ReadScheduler, ReadTimingEngine
from app.conversation.response_scheduler import ResponseScheduler
from app.database.db import Database


class _Timing:
    def delay(self, *_args, **_kwargs):
        return 0


class _Presence:
    async def state(self, _chat_id):
        return {"availability": "available", "phase": "free", "event": None, "sleep_until": None}


def _read_settings():
    return SimpleNamespace(
        active_conversation_window_seconds=600,
        read_delay_free_min_seconds=0, read_delay_free_max_seconds=0,
        read_delay_active_free_min_seconds=0, read_delay_active_free_max_seconds=0,
        read_delay_college_min_seconds=0, read_delay_college_max_seconds=0,
        read_delay_busy_min_seconds=0, read_delay_busy_max_seconds=0,
        read_delay_away_min_seconds=0, read_delay_away_max_seconds=0,
        read_delay_after_wake_min_seconds=0, read_delay_after_wake_max_seconds=0,
        active_busy_read_min_seconds=0, active_busy_read_max_seconds=0,
        active_away_read_min_seconds=0, active_away_read_max_seconds=0,
    )


async def test_scheduled_responses_are_owned_by_their_transport_across_restart(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'transport.sqlite'}")
    await db.connect()
    bot = ResponseScheduler(db, _Timing(), transport="bot_api")
    mtproto = ResponseScheduler(db, _Timing(), transport="mtproto")

    bot_id = await bot.schedule_at(1, 7, "bot", "2000-01-01 00:00:00")
    mt_id = await mtproto.schedule_at(1, 7, "mt", "2000-01-01 00:00:00")
    assert bot_id != mt_id
    assert [row["generation_id"] for row in await bot.due()] == ["bot"]
    assert [row["generation_id"] for row in await mtproto.due()] == ["mt"]

    await db.execute("UPDATE scheduled_responses SET status='processing' WHERE id IN (?,?)", (bot_id, mt_id))
    await mtproto.recover_after_restart()
    rows = await db.fetchall("SELECT generation_id,status FROM scheduled_responses ORDER BY id")
    assert [(row["generation_id"], row["status"]) for row in rows] == [("bot", "processing"), ("mt", "pending")]
    await db.close()


async def test_scheduled_reads_and_message_ids_are_isolated_between_transports(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'transport.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "owner")
    # A Telegram message ID can occur in both APIs; MTProto uses a separate
    # logical chat namespace and the scheduler has a separate owner field.
    assert await db.record_message(chat_id=42, telegram_message_id=9, user_id=1, sender="user", kind="text", text="bot")
    assert await db.record_message(chat_id=-42, telegram_message_id=9, user_id=1, sender="user", kind="text", text="mt")
    bot = ReadScheduler(db, _Presence(), ReadTimingEngine(_read_settings(), rng=lambda *_: 0), now=lambda: datetime.now(timezone.utc), transport="bot_api")
    mtproto = ReadScheduler(db, _Presence(), ReadTimingEngine(_read_settings(), rng=lambda *_: 0), now=lambda: datetime.now(timezone.utc), transport="mtproto")
    bot_id = await bot.schedule(1, 42, 9)
    mt_id = await mtproto.schedule(1, -42, 9)
    assert bot_id != mt_id

    calls = []

    async def on_bot(record):
        calls.append(("bot", record["chat_id"]))

    async def on_mtproto(record):
        calls.append(("mt", record["chat_id"]))

    bot.bind(on_bot)
    mtproto.bind(on_mtproto)
    await bot.process_due()
    assert calls == [("bot", 42)]
    await mtproto.process_due()
    assert calls == [("bot", 42), ("mt", -42)]
    await db.close()


class _DurableExecutor:
    def __init__(self, db):
        self.db = db
        self.sent = []

    async def execute(self, item):
        self.sent.append((item.chat_id, item.generation_id, item.action.text))


async def test_three_mtproto_turns_stay_isolated_and_are_not_resent_after_restart(tmp_path):
    """A shared DB must not let either transport replay the other's work."""
    path = tmp_path / "three-turns.sqlite"
    db = Database(f"sqlite:///{path}")
    await db.connect()
    executor = _DurableExecutor(db)
    queue = ActionQueue(executor, transport="mtproto")
    mtproto = ResponseScheduler(db, _Timing(), transport="mtproto")
    bot = ResponseScheduler(db, _Timing(), transport="bot_api")

    class Manager:
        async def handle_scheduled(self, record):
            await queue.enqueue_many(
                record["chat_id"], record["generation_id"],
                [Action(type=ActionType.text, text=f"reply-{record['generation_id']}")],
            )
            await mtproto.complete(record["id"])

    mtproto.bind(Manager())
    for index in range(3):
        await mtproto.schedule_at(index + 1, -(index + 1), f"mt-{index}", "2000-01-01 00:00:00")
        await bot.schedule_at(index + 1, index + 1, f"bot-{index}", "2000-01-01 00:00:00")

    await mtproto.process_due()
    for chat_id in (-1, -2, -3):
        await queue._queues[chat_id].join()
    assert executor.sent == [
        (-1, "mt-0", "reply-mt-0"), (-2, "mt-1", "reply-mt-1"), (-3, "mt-2", "reply-mt-2"),
    ]
    assert [row["generation_id"] for row in await bot.due()] == ["bot-0", "bot-1", "bot-2"]
    await db.close()

    # A fresh process sees completed MTProto jobs and durable action claims.
    restarted_db = Database(f"sqlite:///{path}")
    await restarted_db.connect()
    restarted_executor = _DurableExecutor(restarted_db)
    restarted_queue = ActionQueue(restarted_executor, transport="mtproto")
    restarted_mtproto = ResponseScheduler(restarted_db, _Timing(), transport="mtproto")
    await restarted_mtproto.recover_after_restart()
    assert await restarted_mtproto.due() == []
    for index in range(3):
        await restarted_queue.enqueue_many(
            -(index + 1), f"mt-{index}",
            [Action(type=ActionType.text, text=f"reply-mt-{index}")],
        )
    assert restarted_executor.sent == []
    assert [row["transport"] for row in await restarted_db.fetchall("SELECT transport FROM action_dispatches ORDER BY id")] == ["mtproto"] * 3
    await restarted_db.close()
