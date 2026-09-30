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
