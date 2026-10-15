"""Backwards-compatible shared world and contact-state foundation."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from app.emotions.affective import REGULATOR_BASELINES, clamp
from app.emotions.coupling import advance_contact_affect, apply_contact_appraisal

WORLD_MIND_DEFAULTS = {"valence": .54, "arousal": .30, "energy": .62, "stress": .15, "social_need": .45, "novelty_drive": .55, "self_worth": .62, "inhibition": .55, "general_irritation": .05, "general_joy": .50}
WORLD_LIFE_DEFAULTS = {"location": "unspecified", "activity": "unconfirmed", "availability": "available", "source": "migration_default"}
WORLD_MIND_HALF_LIVES_HOURS = {"valence": 20.0, "arousal": 4.0, "energy": 9.0, "stress": 16.0, "social_need": 30.0, "novelty_drive": 36.0, "self_worth": 96.0, "inhibition": 18.0, "general_irritation": 9.0, "general_joy": 18.0}
NEW_CONTACT_RELATIONSHIP = {"bond_strength": .05, "relational_trust": .35, "familiarity": .0, "care_investment": .02, "rupture_load": .0, "relationship_security": .45, "love_strength": .0, "meaningful_interactions": 0}
NEW_CONTACT_AFFECT = {"hurt": .02, "resentment": .03, "irritation": .05, "social_safety": .55, "willingness_to_talk": .55}


def _json(value: str | None, fallback: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        parsed = None
    return dict(parsed) if isinstance(parsed, dict) else dict(fallback)


@dataclass(frozen=True)
class AnyaWorldState:
    mind: dict[str, float]
    life: dict[str, Any]
    needs: dict[str, float]
    intentions: tuple[dict[str, Any], ...]
    migration_source_chat_id: int | None = None


class SharedStateManager:
    """Serialize the transitional global-mind/contact bridge.

    Old per-chat tables are never changed.  If startup stops halfway through,
    the missing records are filled on the next startup and the marker is saved
    only after the conservative import completes.
    """
    MIGRATION_NAME = "shared_state_v1"

    def __init__(self, db, *, enabled: bool = True):
        self.db, self.enabled, self._lock = db, enabled, asyncio.Lock()

    @staticmethod
    def _mind_from_legacy(values: Mapping[str, Any] | None) -> dict[str, float]:
        source, mind = dict(values or {}), dict(WORLD_MIND_DEFAULTS)
        for name in ("valence", "arousal", "energy", "stress", "social_need", "novelty_drive", "self_worth", "inhibition"):
            if isinstance(source.get(name), (int, float)):
                mind[name] = clamp(source[name])
        mind["general_irritation"] = clamp(source.get("frustration", mind["general_irritation"]))
        mind["general_joy"] = clamp(.5 + (mind["valence"] - .54) * .5)
        return mind

    @staticmethod
    def _relationship_from_row(row) -> dict[str, Any]:
        if not row:
            return dict(NEW_CONTACT_RELATIONSHIP)
        result = dict(NEW_CONTACT_RELATIONSHIP)
        for key in result:
            if key in row.keys() and isinstance(row[key], (int, float)):
                result[key] = int(row[key]) if key == "meaningful_interactions" else clamp(row[key])
        return result

    @staticmethod
    def _affect_from_legacy(values: Mapping[str, Any] | None) -> dict[str, float]:
        source, affect = dict(values or {}), dict(NEW_CONTACT_AFFECT)
        affect["resentment"] = clamp(source.get("resentment", affect["resentment"]))
        affect["irritation"] = clamp(source.get("frustration", affect["irritation"]))
        affect["hurt"] = clamp(source.get("vulnerability", affect["hurt"]) * .4)
        affect["social_safety"] = clamp(source.get("social_safety", affect["social_safety"]))
        return affect

    async def bootstrap(self) -> AnyaWorldState | None:
        if not self.enabled:
            return None
        async with self._lock:
            marker = await self.db.fetchone("SELECT 1 FROM anya_2_migrations WHERE name=?", (self.MIGRATION_NAME,))
            await self._ensure_world()
            if not marker:
                await self._migrate_contacts()
                await self.db.execute("INSERT INTO anya_2_migrations(name,details_json) VALUES(?,?) ON CONFLICT(name) DO NOTHING", (self.MIGRATION_NAME, json.dumps({"strategy": "primary_chat_no_averaging"}, sort_keys=True)))
            return await self.get_world_state()

    async def _ensure_world(self) -> None:
        if await self.db.fetchone("SELECT id FROM anya_world_state WHERE id=1"):
            return
        legacy = await self.db.fetchone("SELECT a.chat_id,a.regulators_json,COUNT(m.id) AS message_count,MAX(m.timestamp) AS latest FROM affective_states a LEFT JOIN messages m ON m.chat_id=a.chat_id GROUP BY a.chat_id ORDER BY message_count DESC,latest DESC,a.chat_id ASC LIMIT 1")
        source_chat = legacy["chat_id"] if legacy else None
        legacy_values = _json(legacy["regulators_json"], REGULATOR_BASELINES) if legacy else REGULATOR_BASELINES
        await self.db.execute("INSERT OR IGNORE INTO anya_world_state(id,mind_json,life_json,needs_json,intentions_json,migration_source_chat_id) VALUES(1,?,?,?,?,?)", (json.dumps(self._mind_from_legacy(legacy_values), sort_keys=True), json.dumps(WORLD_LIFE_DEFAULTS, sort_keys=True), "{}", "[]", source_chat))

    async def _migrate_contacts(self) -> None:
        rows = await self.db.fetchall("SELECT chat_id,user_id,MAX(timestamp) AS last_seen FROM messages WHERE sender='user' AND user_id IS NOT NULL GROUP BY chat_id,user_id ORDER BY last_seen DESC")
        seen: set[int] = set()
        for row in rows:
            user_id, chat_id = int(row["user_id"]), int(row["chat_id"])
            if user_id not in seen:
                seen.add(user_id)
                legacy_affect = await self.db.fetchone("SELECT regulators_json FROM affective_states WHERE chat_id=?", (chat_id,))
                legacy_bond = await self.db.fetchone("SELECT * FROM relationship_bonds WHERE chat_id=?", (chat_id,))
                user = await self.db.fetchone("SELECT username FROM users WHERE id=?", (user_id,))
                await self._insert_contact(user_id, chat_id, username=user["username"] if user else None, relationship=self._relationship_from_row(legacy_bond), affect=self._affect_from_legacy(_json(legacy_affect["regulators_json"], {}) if legacy_affect else None), migrated_from_chat_id=chat_id)
            await self._link_chat(chat_id, user_id)

    async def _insert_contact(self, user_id: int, chat_id: int, *, username: str | None, relationship: Mapping[str, Any] | None = None, affect: Mapping[str, Any] | None = None, migrated_from_chat_id: int | None = None) -> None:
        await self.db.execute("INSERT INTO anya_contacts(telegram_user_id,display_name,username,first_chat_id,relationship_json,affect_json,migrated_from_chat_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_user_id) DO NOTHING", (user_id, username or None, username, chat_id, json.dumps(dict(relationship or NEW_CONTACT_RELATIONSHIP), sort_keys=True), json.dumps(dict(affect or NEW_CONTACT_AFFECT), sort_keys=True), migrated_from_chat_id))

    async def _link_chat(self, chat_id: int, user_id: int, *, context_kind: str = "private") -> None:
        await self.db.execute("INSERT INTO anya_conversation_contacts(chat_id,telegram_user_id,context_kind) VALUES(?,?,?) ON CONFLICT(chat_id,telegram_user_id) DO UPDATE SET last_seen_at=CURRENT_TIMESTAMP", (chat_id, user_id, context_kind))

    async def ensure_contact(self, user_id: int, chat_id: int, *, username: str | None = None, context_kind: str = "private") -> dict[str, Any] | None:
        if not self.enabled:
            return None
        async with self._lock:
            if not await self.db.fetchone("SELECT id FROM anya_world_state WHERE id=1"):
                await self._ensure_world()
            if username is None:
                user = await self.db.fetchone("SELECT username FROM users WHERE id=?", (user_id,))
                username = user["username"] if user else None
            await self._insert_contact(user_id, chat_id, username=username)
            await self._link_chat(chat_id, user_id, context_kind=context_kind)
            return await self.contact(user_id)

    async def get_world_state(self) -> AnyaWorldState | None:
        if not self.enabled:
            return None
        row = await self.db.fetchone("SELECT * FROM anya_world_state WHERE id=1")
        if not row:
            return None
        needs = _json(row["needs_json"], {})
        try:
            intentions = json.loads(row["intentions_json"] or "[]")
        except (TypeError, ValueError):
            intentions = []
        return AnyaWorldState(mind={key: clamp(value) for key, value in _json(row["mind_json"], WORLD_MIND_DEFAULTS).items() if isinstance(value, (int, float))}, life=_json(row["life_json"], WORLD_LIFE_DEFAULTS), needs={key: clamp(value) for key, value in needs.items() if isinstance(value, (int, float))}, intentions=tuple(item for item in intentions if isinstance(item, dict)), migration_source_chat_id=row["migration_source_chat_id"])

    async def _advance_world_locked(self) -> AnyaWorldState | None:
        """Apply elapsed-time decay once, without any relationship side effect."""
        row = await self.db.fetchone("SELECT *,(julianday('now')-julianday(updated_at))*24 AS age_hours FROM anya_world_state WHERE id=1")
        if not row:
            await self._ensure_world()
            row = await self.db.fetchone("SELECT *,0 AS age_hours FROM anya_world_state WHERE id=1")
        if not row:
            return None
        hours = max(0.0, float(row["age_hours"] or 0.0))
        # Avoid write churn while retaining dt-based rather than frame/call
        # based dynamics. A later event always writes immediately.
        if hours >= 1 / 120:
            mind = _json(row["mind_json"], WORLD_MIND_DEFAULTS)
            for name, baseline in WORLD_MIND_DEFAULTS.items():
                value = clamp(mind.get(name, baseline))
                factor = math.exp(-math.log(2) * hours / WORLD_MIND_HALF_LIVES_HOURS[name])
                mind[name] = clamp(baseline + (value - baseline) * factor)
            await self.db.execute("UPDATE anya_world_state SET mind_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (json.dumps(mind, sort_keys=True),))
        return await self.get_world_state()

    async def advance_global(self) -> AnyaWorldState | None:
        if not self.enabled:
            return None
        async with self._lock:
            return await self._advance_world_locked()

    async def contact(self, user_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM anya_contacts WHERE telegram_user_id=?", (user_id,))
        if not row:
            return None
        return {"telegram_user_id": int(row["telegram_user_id"]), "display_name": row["display_name"], "username": row["username"], "state": row["state"], "relationship": _json(row["relationship_json"], NEW_CONTACT_RELATIONSHIP), "affect": _json(row["affect_json"], NEW_CONTACT_AFFECT), "boundaries": _json(row["boundaries_json"], {}), "policy": _json(row["policy_json"], {})}

    async def context_for(self, user_id: int, chat_id: int) -> tuple[AnyaWorldState, dict[str, Any]] | None:
        if not self.enabled:
            return None
        await self.advance_global()
        contact = await self.ensure_contact(user_id, chat_id)
        world = await self.get_world_state()
        return (world, contact) if world and contact else None

    async def _apply_global_appraisal_locked(self, appraisal) -> AnyaWorldState | None:
        values = appraisal.values() if hasattr(appraisal, "values") else dict(appraisal or {})
        intensity, valence = float(values.get("intensity") or .35), float(values.get("valence") or 0)
        world = await self._advance_world_locked()
        if not world:
            return None
        mind = dict(world.mind)
        def move(name: str, amount: float) -> None: mind[name] = clamp(mind.get(name, WORLD_MIND_DEFAULTS[name]) + amount)
        move("valence", valence * intensity * .05)
        move("general_joy", max(0., valence) * intensity * .03 + float(values.get("warmth") or 0) * .008)
        move("general_irritation", (float(values.get("frustration") or 0) + float(values.get("dismissal") or 0) * .5) * intensity * .025)
        move("stress", (float(values.get("threat") or 0) + float(values.get("uncertainty") or 0)) * intensity * .035 - float(values.get("relief") or 0) * .025)
        move("arousal", (float(values.get("threat") or 0) + float(values.get("novelty") or 0)) * intensity * .025)
        move("energy", -float(values.get("loss") or 0) * intensity * .012)
        await self.db.execute("UPDATE anya_world_state SET mind_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (json.dumps(mind, sort_keys=True),))
        return await self.get_world_state()

    async def apply_appraisal(self, appraisal) -> AnyaWorldState | None:
        """Apply broad global consequences without touching a contact relation."""
        if not self.enabled:
            return None
        async with self._lock:
            return await self._apply_global_appraisal_locked(appraisal)

    async def apply_contact_appraisal(self, user_id: int, appraisal, *, episode=None, test_mode: bool = False) -> dict[str, Any] | None:
        """Advance one contact's affect/relationship and the shared mood once."""
        if not self.enabled:
            return None
        async with self._lock:
            row = await self.db.fetchone("SELECT *,(julianday('now')-julianday(updated_at))*24 AS age_hours FROM anya_contacts WHERE telegram_user_id=?", (user_id,))
            if not row:
                return None
            from app.emotions.relationship import RelationshipBondState, evolve_relationship
            relationship_values = _json(row["relationship_json"], NEW_CONTACT_RELATIONSHIP)
            before = RelationshipBondState(**{key: relationship_values.get(key, value) for key, value in NEW_CONTACT_RELATIONSHIP.items()})
            affect = advance_contact_affect(_json(row["affect_json"], NEW_CONTACT_AFFECT), max(0.0, float(row["age_hours"] or 0.0)))
            affect = apply_contact_appraisal(affect, appraisal)
            after = evolve_relationship(before, appraisal, test_mode=test_mode, episode=episode, social_safety_after=affect["social_safety"])
            values = after.values() | {"meaningful_interactions": after.meaningful_interactions}
            await self.db.execute("UPDATE anya_contacts SET relationship_json=?,affect_json=?,updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=?", (json.dumps(values, sort_keys=True), json.dumps(affect, sort_keys=True), user_id))
            await self._apply_global_appraisal_locked(appraisal)
            return await self.contact(user_id)
