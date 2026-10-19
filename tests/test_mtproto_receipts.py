from datetime import datetime, timezone
from types import SimpleNamespace

from app.conversation.read_scheduler import ReadScheduler, ReadTimingEngine
from app.database.db import Database
from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport


class _Presence:
    async def state(self, _chat_id):
        return {"availability": "available", "phase": "free", "event": None, "sleep_until": None}


def _settings():
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


async def test_mtproto_receipt_state_is_durable_and_outbox_never_overwrites_local_send(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'state.sqlite'}")
    await db.connect()
    await db.record_telegram_message_state(
        transport="mtproto", account_id=11, peer_type="user", peer_id=42,
        message_id=7, direction="incoming", local_chat_id=-42,
        status="received", source="test",
    )
    await db.transition_telegram_messages(
        local_chat_id=-42, message_ids=[7], status="scheduled_read", source="test",
    )
    await db.transition_telegram_messages(
        local_chat_id=-42, message_ids=[7], status="read_ack_requested", source="test",
    )
    await db.confirm_mtproto_inbox_watermark(
        account_id=11, peer_id=42, max_message_id=7, source="telegram_update",
    )
    await db.transition_telegram_messages(
        local_chat_id=-42, message_ids=[7], status="decision_pending", source="test",
    )
    await db.record_telegram_message_state(
        transport="mtproto", account_id=11, peer_type="user", peer_id=42,
        message_id=9, direction="outgoing", local_chat_id=-42,
        status="sent", source="test",
    )
    await db.record_mtproto_outbox_read_watermark(
        account_id=11, peer_id=42, max_message_id=9, source="telegram_update",
    )

    incoming = await db.fetchone("SELECT status,read_watermark FROM telegram_message_states WHERE direction='incoming'")
    outgoing = await db.fetchone("SELECT status,read_watermark FROM telegram_message_states WHERE direction='outgoing'")
    history = await db.fetchall(
        "SELECT status FROM telegram_message_state_events ORDER BY id",
    )
    assert tuple(incoming) == ("decision_pending", 7)
    assert tuple(outgoing) == ("sent", 9)
    assert [row["status"] for row in history] == [
        "received", "scheduled_read", "read_ack_requested", "read_ack_confirmed",
        "decision_pending", "sent", "recipient_read",
    ]
    await db.close()


async def test_raced_mtproto_read_update_promotes_waiting_state_without_regression(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'receipt-race.sqlite'}")
    await db.connect()
    await db.record_telegram_message_state(
        transport="mtproto", account_id=11, peer_type="user", peer_id=42,
        message_id=7, direction="incoming", local_chat_id=-42,
        status="read_ack_requested", source="test",
    )
    # The RPC's immediate watermark is behind, so the callback parks it.
    assert await db.mark_mtproto_read_confirmation_pending(
        local_chat_id=-42, message_ids=[7], read_watermark=6, source="rpc_pending",
    ) == 1
    assert (await db.fetchone("SELECT status FROM telegram_message_states"))["status"] == "read_ack_waiting"

    # Telethon's MessageRead update wins the race and is a real confirmation.
    await db.confirm_mtproto_inbox_watermark(
        account_id=11, peer_id=42, max_message_id=7, source="telethon_update",
    )
    assert (await db.fetchone("SELECT status,read_watermark FROM telegram_message_states"))["status"] == "read_ack_confirmed"
    assert await db.mtproto_inbox_read_confirmed(local_chat_id=-42, message_ids=[7])

    # Conversely, a delayed callback must not move the already-confirmed
    # receipt back to a waiting state.
    assert await db.mark_mtproto_read_confirmation_pending(
        local_chat_id=-42, message_ids=[7], read_watermark=6, source="late_rpc_pending",
    ) == 0
    row = await db.fetchone("SELECT status,read_watermark FROM telegram_message_states")
    assert tuple(row) == ("read_ack_confirmed", 7)
    await db.close()


async def test_read_scheduler_retries_until_transport_confirmation(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'retry.sqlite'}")
    await db.connect()
    scheduler = ReadScheduler(
        db, _Presence(), ReadTimingEngine(_settings(), rng=lambda *_: 0),
        now=lambda: datetime.now(timezone.utc), transport="mtproto",
    )
    read_id = await scheduler.schedule(42, -42, 7)
    scheduler.bind(lambda _record: _not_confirmed())

    await scheduler.process_due()

    row = await db.fetchone("SELECT status,read_confirmation_attempts FROM scheduled_reads WHERE id=?", (read_id,))
    assert row["status"] == "pending"
    assert row["read_confirmation_attempts"] == 1
    await db.close()


async def test_read_scheduler_bounds_fast_confirmation_retries_before_diagnostic_backoff(tmp_path, caplog):
    db = Database(f"sqlite:///{tmp_path / 'bounded-retry.sqlite'}")
    await db.connect()
    scheduler = ReadScheduler(
        db, _Presence(), ReadTimingEngine(_settings(), rng=lambda *_: 0),
        now=lambda: datetime.now(timezone.utc), transport="mtproto",
    )
    read_id = await scheduler.schedule(42, -42, 7)
    await db.execute("UPDATE scheduled_reads SET read_confirmation_attempts=? WHERE id=?", (11, read_id))
    scheduler.bind(lambda _record: _not_confirmed())
    with caplog.at_level("WARNING"):
        await scheduler.process_due()
    row = await db.fetchone("SELECT status,read_confirmation_attempts,read_after FROM scheduled_reads WHERE id=?", (read_id,))
    assert row["status"] == "pending"
    assert row["read_confirmation_attempts"] == 0
    assert "read_confirmation_retry_window_exhausted" in caplog.text
    await db.close()


async def _not_confirmed():
    return False


class _ReadEventClient:
    def __init__(self):
        self.handlers = []

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return SimpleNamespace(id=11)

    def on(self, builder):
        def register(handler):
            self.handlers.append((builder, handler))
            return handler
        return register

    async def run_until_disconnected(self):
        return None

    async def disconnect(self):
        return None


async def test_mtproto_inbox_and_outbox_read_updates_are_reported_separately(tmp_path):
    client = _ReadEventClient()
    settings = MTProtoSettings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="secret",
        telegram_session_path=str(tmp_path / "anya.session"),
        telegram_mtproto_enabled=True, telegram_mtproto_dry_run=False,
        telegram_allowlist="42",
    )
    updates = []
    async def collect(update):
        updates.append(update)
    transport = MTProtoTransport(settings, client_factory=lambda *_: client)
    await transport.run_private_inbound(lambda *_args: None, collect)

    read_handlers = [(builder, handler) for builder, handler in client.handlers if type(builder).__name__ == "MessageRead"]
    assert len(read_handlers) == 2
    for builder, handler in read_handlers:
        await handler(SimpleNamespace(chat_id=42, max_id=19))
    assert {(update.inbox, update.peer_id, update.max_message_id) for update in updates} == {
        (True, 42, 19), (False, 42, 19),
    }
