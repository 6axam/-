import sqlite3

from app.database.db import Database
from app.monitor.labels_ru import label
from app.monitor.reader import MonitorReader


async def test_monitor_reader_is_read_only_and_renders_missing_data_safely(tmp_path):
    path = tmp_path / "monitor.sqlite"
    db = Database(f"sqlite:///{path}")
    await db.connect()
    await db.execute("INSERT INTO affective_states(chat_id,regulators_json,last_advanced_at,updated_at) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (7, '{"stress":0.4}'))
    await db.record_telegram_message_state(
        transport="mtproto", account_id=1, peer_type="user", peer_id=7,
        message_id=3, direction="incoming", local_chat_id=7,
        status="read_ack_confirmed", source="test", read_watermark=3,
    )
    await db.claim_action_dispatch(
        transport="mtproto", generation_id="g", action_index=0, chat_id=7, action_type="text",
    )
    await db.start_action_dispatch(transport="mtproto", generation_id="g", action_index=0)
    await db.reconcile_action_dispatches(transport="mtproto")
    await db.close()
    reader = MonitorReader(f"sqlite:///{path}", 7)
    data = reader.poll()
    assert data["regulators"] == {"stress": 0.4}
    assert data["telegram"][0]["event_status"] == "read_ack_confirmed"
    assert data["dispatches"] == [{
        "action_type": "text", "status": "failed", "recovery_status": "unknown",
        "detail": "process stopped during external dispatch", "updated_at": data["dispatches"][0]["updated_at"],
    }]
    connection = reader._connect()
    try:
        try:
            connection.execute("INSERT INTO users(id) VALUES(999)")
            assert False, "read-only connection unexpectedly accepted a write"
        except sqlite3.OperationalError:
            pass
    finally:
        connection.close()
    assert label("relationship_security") == "чувство надёжности"
