from datetime import datetime, timezone
from types import SimpleNamespace

from app.actions.models import Action, ActionType, QueuedAction
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.presence import DailyPresenceManager
from app.telegram.executor import TelegramActionExecutor


async def test_sleep_is_persistent_and_restart_safe(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'presence.sqlite'}"); await db.connect()
    presence = DailyPresenceManager(db, "Europe/Kyiv", sleep_start=1, wake_hour=9)
    night = datetime(2026, 9, 29, 1, 30, tzinfo=timezone.utc)
    first = await presence.state(7, night)
    assert first["availability"] == "sleep" and first["sleep_until"]
    await db.close()
    db = Database(f"sqlite:///{tmp_path / 'presence.sqlite'}"); await db.connect()
    restored = await DailyPresenceManager(db, "Europe/Kyiv", sleep_start=1, wake_hour=9).state(7, night)
    assert restored["sleep_until"] == first["sleep_until"]
    await db.close()


async def test_sleep_state_recomputes_after_wake_in_same_local_day(tmp_path, monkeypatch):
    monkeypatch.setattr(DailyPresenceManager, "_wake_jitter", staticmethod(lambda *_: 30))
    db = Database(f"sqlite:///{tmp_path / 'presence-transition.sqlite'}"); await db.connect()
    presence = DailyPresenceManager(db, "UTC", sleep_start=1, wake_hour=7)
    early = await presence.state(7, datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc))
    afternoon = await presence.state(7, datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
    assert early["availability"] == "sleep" and early["sleep_until"] == "2026-10-01 07:30:00"
    assert afternoon["availability"] == "available" and afternoon["sleep_until"] is None
    await db.close()


async def test_full_wake_minute_controls_sleep_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(DailyPresenceManager, "_wake_jitter", staticmethod(lambda *_: 30))
    db = Database(f"sqlite:///{tmp_path / 'presence-minute.sqlite'}"); await db.connect()
    presence = DailyPresenceManager(db, "UTC", sleep_start=1, wake_hour=7)
    before = await presence.state(7, datetime(2026, 10, 1, 7, 10, tzinfo=timezone.utc))
    after = await presence.state(7, datetime(2026, 10, 1, 7, 40, tzinfo=timezone.utc))
    assert before["availability"] == "sleep" and after["availability"] == "available"
    await db.close()


async def test_sleep_begins_after_sleep_start_without_waiting_for_new_day(tmp_path, monkeypatch):
    monkeypatch.setattr(DailyPresenceManager, "_wake_jitter", staticmethod(lambda *_: 0))
    db = Database(f"sqlite:///{tmp_path / 'presence-start.sqlite'}"); await db.connect()
    presence = DailyPresenceManager(db, "UTC", sleep_start=1, wake_hour=7)
    before = await presence.state(7, datetime(2026, 10, 1, 0, 50, tzinfo=timezone.utc))
    after = await presence.state(7, datetime(2026, 10, 1, 1, 5, tzinfo=timezone.utc))
    assert before["availability"] == "available" and after["availability"] == "sleep"
    await db.close()


async def test_cross_midnight_episode_keeps_same_wake_across_midnight_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(DailyPresenceManager, "_wake_jitter", staticmethod(lambda *_: 30))
    path = tmp_path / "presence-cross.sqlite"
    db = Database(f"sqlite:///{path}"); await db.connect()
    presence = DailyPresenceManager(db, "UTC", sleep_start=23, wake_hour=7)
    before_midnight = await presence.state(7, datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc))
    after_midnight = await presence.state(7, datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))
    assert before_midnight["availability"] == after_midnight["availability"] == "sleep"
    assert before_midnight["sleep_until"] == after_midnight["sleep_until"] == "2026-10-02 07:30:00"
    await db.close()
    db = Database(f"sqlite:///{path}"); await db.connect()
    restarted = await DailyPresenceManager(db, "UTC", sleep_start=23, wake_hour=7).state(7, datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))
    assert restarted["sleep_until"] == "2026-10-02 07:30:00"
    await db.close()


async def test_sleep_overrides_event_until_wake_then_event_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(DailyPresenceManager, "_wake_jitter", staticmethod(lambda *_: 30))
    db = Database(f"sqlite:///{tmp_path / 'presence-event.sqlite'}"); await db.connect()
    await db.execute(
        "INSERT INTO daily_events(chat_id,title,availability,starts_at,ends_at,mentionable) VALUES(?,?,?, ?,?,1)",
        (7, "busy", "away", "2026-10-01 06:00:00", "2026-10-01 09:00:00"),
    )
    presence = DailyPresenceManager(db, "UTC", sleep_start=1, wake_hour=7, college_start_hour=6, college_end_hour=15)
    sleeping = await presence.state(7, datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc))
    awake = await presence.state(7, datetime(2026, 10, 1, 7, 40, tzinfo=timezone.utc))
    assert sleeping["availability"] == "sleep" and sleeping["phase"] == "free"
    assert awake["availability"] == "away" and awake["event"] is not None
    await db.close()


async def test_invalid_reply_target_is_removed_but_real_target_survives(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'reply.sqlite'}"); await db.connect()
    await db.record_message(chat_id=3, telegram_message_id=10, sender="user", user_id=1, kind="text", text="question")
    manager = ConversationManager(SimpleNamespace(), SimpleNamespace(db=db), SimpleNamespace())
    actions = await manager._validate_action_targets(3, [
        Action(type=ActionType.text, text="yes", reply_to_message_id=10),
        Action(type=ActionType.text, text="no", reply_to_message_id=999),
    ], 10)
    assert actions[0].reply_to_message_id == 10 and actions[1].reply_to_message_id is None
    await db.close()


async def test_executor_sends_telegram_reply(tmp_path):
    class Bot:
        async def send_chat_action(self, *_): pass
        async def send_message(self, chat_id, text, **kwargs):
            self.call = (chat_id, text, kwargs); return SimpleNamespace(message_id=50)
    db = Database(f"sqlite:///{tmp_path / 'reply.sqlite'}"); await db.connect()
    bot = Bot(); executor = TelegramActionExecutor(bot, db); executor.timing.typing_seconds = lambda _: 0
    await executor.execute(QueuedAction(chat_id=3, generation_id="g", action=Action(type=ActionType.text, text="ответ", reply_to_message_id=10)))
    assert bot.call == (3, "ответ", {"reply_parameters": {"message_id": 10}})
    await db.close()


async def test_scheduler_recovers_processing_row_after_restart(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'schedule.sqlite'}"); await db.connect()
    await db.execute("INSERT INTO scheduled_responses(chat_id,user_id,respond_after,generation_id,status) VALUES(1,1,datetime('now'),'g','processing')")
    from app.conversation.response_scheduler import ResponseScheduler
    scheduler = ResponseScheduler(db, SimpleNamespace())
    await scheduler.recover_after_restart()
    assert (await db.fetchone("SELECT status FROM scheduled_responses"))["status"] == "pending"
    await db.close()
