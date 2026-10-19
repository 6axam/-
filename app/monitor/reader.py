"""Synchronous, SQLite read-only access for the monitor process."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.tui.state import database_path


def _json(value: Any) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


class MonitorReader:
    def __init__(self, database_url: str, chat_id: int, *, cwd: Path | None = None) -> None:
        self.path = database_path(database_url, cwd)
        self.chat_id = chat_id

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=.25)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _all(connection, sql, params=()):
        try:
            return connection.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _one(connection, sql, params=()):
        rows = MonitorReader._all(connection, sql, params)
        return rows[0] if rows else None

    def poll(self) -> dict:
        if not self.path.is_file():
            return {"available": False}
        try:
            with self._connect() as conn:
                return self._read(conn)
        except sqlite3.Error:
            return {"available": False}

    def _read(self, conn) -> dict:
        affect = self._one(conn, "SELECT regulators_json FROM affective_states WHERE chat_id=?", (self.chat_id,))
        emotions = self._one(conn, "SELECT emotions_json FROM affective_profiles WHERE chat_id=?", (self.chat_id,))
        bond = self._one(conn, "SELECT * FROM relationship_bonds WHERE chat_id=?", (self.chat_id,))
        lifecycle = self._one(conn, "SELECT * FROM conversation_lifecycle WHERE chat_id=?", (self.chat_id,))
        last_message = self._one(conn, "SELECT sender,text,timestamp FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1", (self.chat_id,))
        last_generation = self._one(conn, "SELECT * FROM llm_generations WHERE chat_id=? ORDER BY id DESC LIMIT 1", (self.chat_id,))
        generations = self._all(conn, "SELECT * FROM llm_generations WHERE chat_id=? ORDER BY id DESC LIMIT 30", (self.chat_id,))
        observation = self._one(conn, "SELECT * FROM turn_state_observations WHERE chat_id=? ORDER BY id DESC LIMIT 1", (self.chat_id,))
        observations = self._all(conn, "SELECT * FROM turn_state_observations WHERE chat_id=? ORDER BY id DESC LIMIT 20", (self.chat_id,))
        events = self._all(conn, "SELECT title,availability,starts_at,ends_at FROM daily_events WHERE chat_id=? ORDER BY starts_at DESC LIMIT 12", (self.chat_id,))
        telegram = self._all(
            conn,
            "SELECT s.direction,s.message_id,s.status,s.read_watermark,s.updated_at,e.status AS event_status,e.source,e.created_at "
            "FROM telegram_message_states s LEFT JOIN telegram_message_state_events e ON e.state_id=s.id "
            "WHERE s.local_chat_id=? ORDER BY s.id DESC,e.id DESC LIMIT 80",
            (self.chat_id,),
        )
        dispatches = self._all(
            conn,
            "SELECT action_type,status,recovery_status,detail,updated_at FROM action_dispatches "
            "WHERE chat_id=? AND (status='failed' OR recovery_status='unknown') ORDER BY id DESC LIMIT 12",
            (self.chat_id,),
        )
        episodes = self._one(conn, "SELECT COUNT(*) active,COALESCE(SUM(unresolved),0) unresolved FROM episodic_memories WHERE chat_id=? AND status='active'", (self.chat_id,))
        relationship = {key: float(bond[key]) for key in ("bond_strength","relational_trust","familiarity","care_investment","rupture_load","relationship_security","love_strength") if bond and key in bond.keys()}
        return {
            "available": True, "regulators": _json(affect["regulators_json"] if affect else None),
            "emotions": _json(emotions["emotions_json"] if emotions else None), "relationship": relationship,
            "lifecycle": dict(lifecycle) if lifecycle else {}, "last_message": dict(last_message) if last_message else {},
            "last_generation": dict(last_generation) if last_generation else {}, "generations": [dict(row) for row in generations],
            "observation": self._observation(observation), "observations": [self._observation(row) for row in observations],
            "events": [dict(row) for row in events], "episodes": dict(episodes) if episodes else {"active": 0, "unresolved": 0},
            "telegram": [dict(row) for row in telegram],
            "dispatches": [dict(row) for row in dispatches],
        }

    @staticmethod
    def _observation(row):
        if not row:
            return {}
        result = dict(row)
        for key in ("before_json", "after_json", "delta_json"):
            result[key[:-5]] = _json(result.get(key))
        return result
