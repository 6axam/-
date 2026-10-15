import sqlite3

from app.database.db import Database
from app.monitor.labels_ru import label
from app.monitor.reader import MonitorReader


async def test_monitor_reader_is_read_only_and_renders_missing_data_safely(tmp_path):
    path = tmp_path / "monitor.sqlite"
    db = Database(f"sqlite:///{path}")
    await db.connect()
    await db.execute("INSERT INTO affective_states(chat_id,regulators_json,last_advanced_at,updated_at) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (7, '{"stress":0.4}'))
    await db.close()
    reader = MonitorReader(f"sqlite:///{path}", 7)
    assert reader.poll()["regulators"] == {"stress": 0.4}
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
