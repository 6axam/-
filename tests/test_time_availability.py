from datetime import datetime, timezone
from types import SimpleNamespace

from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.conversation.response_scheduler import ResponseScheduler
from app.conversation.response_timing import ResponseTimingEngine
from app.database.db import Database
from app.llm.schemas import LLMResponse, ResponseTiming
from app.presence import DailyPresenceManager


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'time.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "owner")
    return db


async def test_configurable_college_window_changes_presence_phase(tmp_path):
    db = await make_db(tmp_path)
    presence = DailyPresenceManager(db, "Europe/Kyiv", sleep_start=1, wake_hour=7, college_start_hour=8, college_end_hour=15)
    # UTC+3 in Kyiv on this date: 06:00 UTC is 09:00 local.
    college = await presence.state(10, datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    free = await presence.state(10, datetime(2026, 10, 1, 14, tzinfo=timezone.utc))
    assert college["phase"] == "college" and free["phase"] == "free"
    await db.close()


def test_college_and_real_event_change_backend_delay_only():
    timing = ResponseTimingEngine(college_normal_delay_multiplier=2, college_active_delay_cap_seconds=30)
    free = timing.delay("normal", active_conversation=False, daily_phase="free")
    college = timing.delay("normal", active_conversation=False, daily_phase="college")
    busy_event = timing.delay("normal", active_conversation=False, daily_phase="free", event_availability="busy")
    assert college == free * 2
    assert busy_event == free * 1.5
    # In an active morning exchange, there is still a bounded but visibly
    # longer window than the ordinary five-second conversational cap.
    assert timing.delay("normal", active_conversation=True, daily_phase="college") == 30


async def test_free_period_skips_llm_requested_delay_without_real_event(tmp_path):
    class Presence:
        async def state(self, _chat_id):
            return {"availability": "available", "phase": "free", "event": None}
        @staticmethod
        def allows_delayed_reply(_state):
            return False

    class Provider:
        async def decide_timing(self, _request): return ResponseTiming(mode="delayed", urgency="normal")
        async def generate(self, _request):
            self.generated = True
            return LLMResponse()

    class Scheduler:
        def bind(self, _manager): pass
        async def cancel_chat(self, _chat_id): pass
        async def has_pending(self, _chat_id): return False
        async def schedule(self, *_args, **_kwargs): raise AssertionError("free period must not schedule")

    provider = Provider(); provider.generated = False
    manager = ConversationManager(provider, SimpleNamespace(build=lambda *_: None), ActionQueue(SimpleNamespace(execute=lambda _: None)), scheduler=Scheduler(), presence=Presence())
    # A narrow async context is clearer than a lambda in this test.
    async def build(*_args): return "system", "context"
    manager.context.build = build
    await manager.handle_turn(1, 10, "привет")
    assert provider.generated


async def test_only_real_busy_event_is_persisted_as_delay_reason_and_reaches_context(tmp_path):
    db = await make_db(tmp_path)
    await db.record_message(chat_id=10, telegram_message_id=1, user_id=1, sender="user", kind="text", text="привет")
    event = await db.execute(
        "INSERT INTO daily_events(chat_id,title,availability,starts_at,ends_at,mentionable) "
        "VALUES(?,?,?,datetime('now','-1 minute'),datetime('now','+20 minutes'),1)",
        (10, "душ", "busy"),
    )

    class Timing:
        def delay(self, *_args, **_kwargs): return 0

    scheduler = ResponseScheduler(db, Timing())
    await scheduler.schedule(1, 10, "g", "normal", daily_state={
        "phase": "free", "event": {"id": event.lastrowid, "availability": "busy"},
    })
    record = await db.fetchone("SELECT * FROM scheduled_responses WHERE generation_id='g'")
    assert record["delay_event_id"] == event.lastrowid
    await db.execute("UPDATE scheduled_responses SET status='processing' WHERE id=?", (record["id"],))
    record = await db.fetchone("SELECT * FROM scheduled_responses WHERE id=?", (record["id"],))

    class Context:
        def __init__(self): self.db, self.delay_event = db, None
        async def build_with_breakdown(self, _user, _chat, _text, *, delay_event=None):
            self.delay_event = delay_event
            return "system", "context", {}

    class Provider:
        async def generate(self, _request): return LLMResponse()

    context = Context()
    manager = ConversationManager(Provider(), context, ActionQueue(SimpleNamespace(execute=lambda _: None)), scheduler=scheduler)
    await manager.handle_scheduled(record)
    assert context.delay_event["id"] == event.lastrowid and context.delay_event["title"] == "душ"
    event_row = await db.fetchone("SELECT id,title,availability,mentionable FROM daily_events WHERE id=?", (event.lastrowid,))
    _, rendered_context, breakdown = await ContextBuilder(db).build_with_breakdown(1, 10, "привет", delay_event=event_row)
    assert "DELAY CONTEXT" in rendered_context and "душ" in rendered_context
    assert breakdown["components"]["delay_event_context"]["tokens"] > 0
    await db.close()


async def test_college_delay_has_no_post_hoc_event_context(tmp_path):
    db = await make_db(tmp_path)
    scheduler = ResponseScheduler(db, SimpleNamespace(delay=lambda *_args, **_kwargs: 0))
    await scheduler.schedule(1, 10, "college", "normal", daily_state={"phase": "college", "event": None})
    row = await db.fetchone("SELECT delay_event_id FROM scheduled_responses WHERE generation_id='college'")
    assert row["delay_event_id"] is None
    builder = ContextBuilder(db)
    _, context, breakdown = await builder.build_with_breakdown(1, 10, "привет")
    assert "DELAY CONTEXT" not in context and breakdown["components"]["delay_event_context"]["tokens"] == 0
    await db.close()
