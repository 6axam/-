import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.tui.app import AnyaTamagotchiApp
from app.tui.scenes import ActivityKind, SceneKind, classify_event_activity, render_scene, resolve_scene
from app.tui.state import (
    AnyaSnapshot,
    PresenceView,
    ReadOnlyStateReader,
    aggregate_emotions,
    meter,
    particle_budget,
    relationship_lines,
)


NOW = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)


def snapshot(**presence_overrides):
    presence = {
        "phase": "free",
        "availability": "available",
        "local_time": "19:00",
    }
    presence.update(presence_overrides)
    return AnyaSnapshot(NOW, 7, presence=PresenceView(**presence))


@pytest.mark.parametrize(
    ("presence", "expected"),
    [
        ({"sleeping": True, "availability": "sleep", "phase": "college", "event_title": "телефон"}, SceneKind.SLEEPING),
        ({"phase": "college", "event_title": "Telegram"}, SceneKind.COLLEGE_DESK),
        ({"event_title": "сижу в телефоне"}, SceneKind.PHONE),
        ({"event_title": "играю за компьютером"}, SceneKind.HOME_DESK),
        ({"availability": "busy", "event_title": "по делам"}, SceneKind.BUSY_AWAY),
        ({}, SceneKind.HOME_BED),
    ],
)
def test_scene_priority_and_known_states(presence, expected):
    assert resolve_scene(snapshot(**presence)).kind is expected


def test_free_idle_variation_does_not_invent_activity():
    first = resolve_scene(snapshot(), idle_variant=0)
    second = resolve_scene(snapshot(), idle_variant=1)
    assert {first.kind, second.kind} == {SceneKind.HOME_BED, SceneKind.IDLE_HOME}
    assert first.activity == second.activity == "свободное время"
    assert first.activity_kind is second.activity_kind is ActivityKind.FREE
    assert first.evidence == second.evidence == "visual idle variation"


def test_activity_registry_is_conservative_and_extensible():
    assert classify_event_activity("листаю Telegram в телефоне") is ActivityKind.PHONE
    assert classify_event_activity("играю за компьютером") is ActivityKind.COMPUTER
    assert classify_event_activity("встреча с куратором") is ActivityKind.UNKNOWN


def test_emotion_aggregation_and_particle_budget_preserve_peaks():
    channels = aggregate_emotions({"affection": 0.7, "anger": 0.82, "hurt": 0.6, "resentment": 0.55})
    assert channels["warm"] == pytest.approx(0.7)
    assert channels["anger"] == pytest.approx(0.82)
    assert channels["sadness"] == pytest.approx(0.6)
    assert channels["jealousy"] == pytest.approx(0.55)
    assert particle_budget(0.1) == 0
    assert particle_budget(0.9) > particle_budget(0.3) > 0
    assert particle_budget(3) == 8


def test_relationship_meters_are_readable_without_color():
    lines = relationship_lines({"bond_strength": 0.8, "relational_trust": 0.4})
    assert len(lines) == 5
    assert "bond" in lines[0] and "80%" in lines[0] and "█" in lines[0]
    assert "trust" in lines[1] and "40%" in lines[1]
    assert "n/a" in lines[2]
    assert meter(1.2).endswith("100%")


def _create_observer_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE bedtime_overrides(chat_id INTEGER,sleep_start_date TEXT,delay_minutes INTEGER);
        CREATE TABLE daily_events(id INTEGER PRIMARY KEY,chat_id INTEGER,title TEXT,availability TEXT,starts_at TEXT,ends_at TEXT);
        CREATE TABLE conversation_lifecycle(chat_id INTEGER PRIMARY KEY,conversation_status TEXT);
        CREATE TABLE scheduled_responses(id INTEGER PRIMARY KEY,chat_id INTEGER,status TEXT);
        CREATE TABLE scheduled_reads(id INTEGER PRIMARY KEY,chat_id INTEGER,status TEXT);
        CREATE TABLE affective_states(chat_id INTEGER PRIMARY KEY,regulators_json TEXT);
        CREATE TABLE affective_profiles(chat_id INTEGER PRIMARY KEY,emotions_json TEXT);
        CREATE TABLE relationship_bonds(
          chat_id INTEGER PRIMARY KEY,bond_strength REAL,relational_trust REAL,familiarity REAL,
          care_investment REAL,rupture_load REAL,relationship_security REAL,love_strength REAL
        );
        CREATE TABLE episodic_memories(
          id INTEGER PRIMARY KEY,chat_id INTEGER,status TEXT,unresolved INTEGER,summary TEXT,updated_at TEXT
        );
        """
    )
    connection.execute("INSERT INTO conversation_lifecycle VALUES(7,'active')")
    connection.execute("INSERT INTO affective_states VALUES(7,?)", (json.dumps({"energy": 0.61}),))
    connection.execute("INSERT INTO affective_profiles VALUES(7,?)", (json.dumps({"joy": 0.4, "anger": 0.2}),))
    connection.execute("INSERT INTO relationship_bonds VALUES(7,.8,.7,.9,.8,.1,.65,.85)")
    connection.execute("INSERT INTO episodic_memories VALUES(1,7,'active',1,'незакрытый разговор','2026-10-05')")
    connection.commit()
    connection.close()


def test_polling_reads_existing_state_without_writing_database(tmp_path):
    path = tmp_path / "observer.db"
    _create_observer_db(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    reader = ReadOnlyStateReader(f"sqlite:///{path}", 7)

    result = reader.poll(NOW)

    after = hashlib.sha256(path.read_bytes()).digest()
    assert before == after
    assert result.source_available is True
    assert result.lifecycle == "active"
    assert result.regulators["energy"] == pytest.approx(0.61)
    assert result.relationship["love_strength"] == pytest.approx(0.85)
    assert result.memories.active_count == result.memories.unresolved_count == 1
    with pytest.raises(sqlite3.OperationalError):
        with reader._connect() as connection:
            connection.execute("UPDATE conversation_lifecycle SET conversation_status='ended'")


def test_missing_optional_tables_and_database_are_safe(tmp_path):
    partial = tmp_path / "partial.db"
    sqlite3.connect(partial).close()
    result = ReadOnlyStateReader(f"sqlite:///{partial}", 7).poll(NOW)
    missing = ReadOnlyStateReader(f"sqlite:///{tmp_path / 'missing.db'}", 7).poll(NOW)
    assert result.source_available is True
    assert result.lifecycle == "unknown"
    assert result.emotions == {}
    assert missing.source_available is False
    assert not (tmp_path / "missing.db").exists()


def test_compact_scene_is_deterministic_and_contains_state():
    current = AnyaSnapshot(
        NOW,
        7,
        presence=PresenceView(phase="free", availability="available", local_time="19:00"),
        emotions={"anger": 0.8},
    )
    decision = resolve_scene(current, idle_variant=1)
    first = render_scene(current, decision, frame=5, compact=True)
    assert first == render_scene(current, decision, frame=5, compact=True)
    assert decision.location in first and decision.activity in first


@pytest.mark.asyncio
async def test_textual_shell_mounts_in_compact_mode_without_starting_services(tmp_path):
    reader = ReadOnlyStateReader(f"sqlite:///{tmp_path / 'absent.db'}", 7)
    app = AnyaTamagotchiApp(reader)
    async with app.run_test(size=(60, 18)):
        assert app.compact is True
        assert app.screen.has_class("compact")
        app.action_show_help()
        assert app.query_one("#overlay").styles.display == "block"
        app.action_close_overlay()
        assert app.query_one("#overlay").styles.display == "none"
        app.exit()
