import json

import pytest

from app.conversation.context import ContextBuilder
from app.database.db import Database
from app.emotions.coupling import advance_contact_affect
from app.llm.schemas import AffectiveAppraisal
from app.mind.world_state import NEW_CONTACT_RELATIONSHIP, SharedStateManager


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'shared-state.sqlite'}")
    await database.connect()
    yield database
    await database.close()


async def _legacy_affect(db, chat_id, *, valence, safety=.7, frustration=.1):
    values = {
        "valence": valence, "arousal": .3, "energy": .6, "reward_sensitivity": .55,
        "stress": .15, "social_safety": safety, "attachment": .7, "social_need": .45,
        "novelty_drive": .55, "threat": .08, "frustration": frustration, "self_worth": .62,
        "grief_load": .04, "resentment": .03, "vulnerability": .25, "inhibition": .55,
    }
    await db.execute(
        "INSERT INTO affective_states(chat_id,regulators_json,last_advanced_at,updated_at) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        (chat_id, json.dumps(values)),
    )
    return values


async def _legacy_bond(db, chat_id, *, trust, love):
    await db.execute(
        "INSERT INTO relationship_bonds(chat_id,bond_strength,relational_trust,familiarity,care_investment,rupture_load,relationship_security,love_strength,meaningful_interactions) VALUES(?,?,?,?,?,?,?,?,?)",
        (chat_id, .6, trust, .4, .3, .1, .5, love, 12),
    )


async def test_bootstrap_uses_one_primary_legacy_mind_without_averaging_and_keeps_contacts_distinct(db):
    await db.ensure_user(10, "maks")
    await db.ensure_user(20, "toma")
    await db.record_message(chat_id=100, telegram_message_id=1, user_id=10, sender="user", kind="text", text="one")
    await db.record_message(chat_id=100, telegram_message_id=2, user_id=10, sender="user", kind="text", text="two")
    await db.record_message(chat_id=200, telegram_message_id=1, user_id=20, sender="user", kind="text", text="other")
    source = await _legacy_affect(db, 100, valence=.18, safety=.42, frustration=.31)
    await _legacy_affect(db, 200, valence=.91, safety=.87, frustration=.02)
    await _legacy_bond(db, 100, trust=.22, love=.18)
    await _legacy_bond(db, 200, trust=.83, love=.72)

    state = SharedStateManager(db)
    world = await state.bootstrap()

    assert world.migration_source_chat_id == 100
    assert world.mind["valence"] == pytest.approx(.18)
    maxim, toma = await state.contact(10), await state.contact(20)
    assert maxim["relationship"]["relational_trust"] == pytest.approx(.22)
    assert toma["relationship"]["relational_trust"] == pytest.approx(.83)
    assert maxim["relationship"]["love_strength"] == pytest.approx(.18)
    assert toma["relationship"]["love_strength"] == pytest.approx(.72)
    row = await db.fetchone("SELECT regulators_json FROM affective_states WHERE chat_id=100")
    assert json.loads(row["regulators_json"]) == source

    await state.bootstrap()
    assert (await db.fetchone("SELECT COUNT(*) AS count FROM anya_contacts"))["count"] == 2
    assert (await db.fetchone("SELECT COUNT(*) AS count FROM anya_2_migrations"))["count"] == 1


async def test_new_contact_starts_conservatively_and_global_appraisal_does_not_change_their_relation(db):
    state = SharedStateManager(db)
    await state.bootstrap()
    before = await state.ensure_contact(42, 420, username="new")
    world_before = await state.get_world_state()

    assert before["relationship"] == NEW_CONTACT_RELATIONSHIP
    assert before["relationship"]["love_strength"] == 0
    await state.apply_appraisal(AffectiveAppraisal(valence=-.8, intensity=.9, threat=.7, dismissal=.6))

    after, world_after = await state.contact(42), await state.get_world_state()
    assert after["relationship"] == before["relationship"]
    assert world_after.mind["valence"] < world_before.mind["valence"]
    assert world_after.mind["stress"] > world_before.mind["stress"]


async def test_context_exposes_shared_mind_and_only_current_contact_state(db):
    state = SharedStateManager(db)
    await state.bootstrap()
    await state.ensure_contact(10, 100, username="maks")
    await state.ensure_contact(20, 200, username="toma")
    await db.execute(
        "UPDATE anya_contacts SET relationship_json=? WHERE telegram_user_id=20",
        (json.dumps({**NEW_CONTACT_RELATIONSHIP, "love_strength": .91}),),
    )

    _, context = await ContextBuilder(db, shared_state=state).build(10, 100, "привет")

    assert "SHARED ANYA MIND" in context
    assert "CURRENT CONTACT STATE" in context
    assert "contact_id=10" in context
    assert "contact_id=20" not in context
    assert "love_strength=0.91" not in context


async def test_contact_affect_and_relationship_change_only_for_the_addressed_person_and_repair_is_slow(db):
    state = SharedStateManager(db)
    await state.bootstrap()
    await state.ensure_contact(10, 100)
    await state.ensure_contact(20, 200)
    initial = await state.contact(10)
    other_before = await state.contact(20)

    conflict = AffectiveAppraisal(
        valence=-.9, intensity=.95, social_relevance=.95, rejection=.9,
        dismissal=.8, other_blame=.7, frustration=.7,
    )
    hurt = await state.apply_contact_appraisal(10, conflict)
    other_after = await state.contact(20)

    assert hurt["affect"]["hurt"] > initial["affect"]["hurt"]
    assert hurt["affect"]["resentment"] > initial["affect"]["resentment"]
    assert hurt["relationship"]["relational_trust"] < initial["relationship"]["relational_trust"]
    assert other_after["relationship"] == other_before["relationship"]
    assert other_after["affect"] == other_before["affect"]

    repaired = await state.apply_contact_appraisal(
        10, AffectiveAppraisal(intensity=.9, social_relevance=.95, repair_attempt=.95, warmth=.6, care=.7),
    )
    assert repaired["relationship"]["rupture_load"] < hurt["relationship"]["rupture_load"]
    assert repaired["relationship"]["relational_trust"] > hurt["relationship"]["relational_trust"]
    assert repaired["relationship"]["relational_trust"] < initial["relationship"]["relational_trust"]
    assert repaired["relationship"]["love_strength"] < .1


def test_contact_affect_uses_individual_real_time_half_lives():
    later = advance_contact_affect({"irritation": .9, "resentment": .9}, 20)
    assert later["irritation"] < later["resentment"]
    assert later["irritation"] > .05
    assert later["resentment"] > .5


async def test_global_mind_uses_elapsed_time_not_number_of_context_reads(db):
    state = SharedStateManager(db)
    await state.bootstrap()
    await db.execute(
        "UPDATE anya_world_state SET mind_json=?,updated_at=datetime('now','-20 hours') WHERE id=1",
        (json.dumps({"valence": .05, "arousal": .95, "energy": .2, "stress": .95, "social_need": .45, "novelty_drive": .55, "self_worth": .62, "inhibition": .55, "general_irritation": .9, "general_joy": .1}),),
    )
    first = await state.advance_global()
    second = await state.advance_global()

    assert first.mind["valence"] > .05
    assert first.mind["arousal"] < .95
    assert first.mind["general_irritation"] < .9
    assert second.mind == first.mind
