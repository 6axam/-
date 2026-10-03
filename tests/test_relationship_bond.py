import pytest

from app.database.db import Database
from app.emotions.relationship import RelationshipBondManager, RelationshipBondState, evolve_relationship
from app.llm.schemas import AffectiveAppraisal, MemoryEpisode


@pytest.mark.asyncio
async def test_bond_seeds_conservatively_and_persists_per_chat(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'bonds.sqlite'}")
    await db.connect()
    manager = RelationshipBondManager(db)
    first = await manager.get(1)
    assert .3 < first.bond_strength < .6
    appraisal = AffectiveAppraisal(social_relevance=1, rejection=1)
    await manager.apply(1, appraisal)
    assert (await RelationshipBondManager(db).get(1)).relational_trust < first.relational_trust
    assert await manager.get(2) == first
    await db.close()


def test_single_rupture_resilience_repeated_damage_and_slow_repair():
    state = RelationshipBondState(.9, .88, .9, .85, .03, .88, .89)
    insult = AffectiveAppraisal(social_relevance=1, rejection=1, dismissal=1)
    once = evolve_relationship(state, insult)
    assert once.bond_strength >= .89 and once.love_strength >= .88
    damaged = state
    for _ in range(50):
        damaged = evolve_relationship(damaged, insult)
    assert damaged.relational_trust < once.relational_trust - .3
    assert damaged.rupture_load > .7
    assert damaged.bond_strength < once.bond_strength - .08
    assert damaged.love_strength < once.love_strength
    repaired = evolve_relationship(damaged, AffectiveAppraisal(social_relevance=1, warmth=1, repair_attempt=1))
    assert repaired.rupture_load < damaged.rupture_load
    assert repaired.rupture_load > .6
    assert repaired.relational_trust < state.relational_trust


def test_test_mode_preserves_durable_bond_but_positive_history_can_form_love():
    low = RelationshipBondState(.25, .3, .2, .2, .02, .3, .25)
    support = AffectiveAppraisal(social_relevance=1, warmth=1, care=1)
    one = evolve_relationship(low, support)
    assert one.love_strength < .3
    formed = low
    for _ in range(300):
        formed = evolve_relationship(formed, support)
    assert formed.bond_strength > low.bond_strength
    assert formed.care_investment > low.care_investment
    assert formed.love_strength > one.love_strength
    tested = evolve_relationship(formed, AffectiveAppraisal(social_relevance=1, rejection=1), test_mode=True)
    assert formed.bond_strength - tested.bond_strength < .001


def test_meaningful_relationship_episode_has_small_bounded_weight():
    state = RelationshipBondState(.7, .7, .7, .7, .1, .7, .7)
    appraisal = AffectiveAppraisal(social_relevance=1, care=1, warmth=1)
    ordinary = evolve_relationship(state, appraisal)
    episode = MemoryEpisode(kind='relationship', summary='shared repair', importance=.8)
    remembered = evolve_relationship(state, appraisal, episode=episode)
    assert remembered.care_investment > ordinary.care_investment
    assert remembered.care_investment - ordinary.care_investment < .002


def test_replacement_threat_lowers_security_without_erasing_love_and_repair_is_slow():
    state = RelationshipBondState(.9, .84, .85, .87, .08, .82, .9)
    threat = AffectiveAppraisal(valence=-.65, intensity=.70, social_relevance=.80,
        loss=.35, rejection=.65, frustration=.20, replacement_threat=.75,
        dismissal=.55)
    harmed = evolve_relationship(state, threat, social_safety_after=.65)
    assert harmed.relationship_security < state.relationship_security
    assert harmed.relational_trust < state.relational_trust
    assert harmed.rupture_load > state.rupture_load
    assert abs(harmed.bond_strength - state.bond_strength) < .005
    assert abs(harmed.love_strength - state.love_strength) < .005
    repaired = evolve_relationship(harmed, AffectiveAppraisal(social_relevance=.9,
        warmth=.8, repair_attempt=.8, care=.7), social_safety_after=.75)
    assert repaired.relationship_security > harmed.relationship_security
    assert repaired.relational_trust < state.relational_trust
    assert repaired.rupture_load > state.rupture_load
    assert abs(repaired.bond_strength - state.bond_strength) < .005
    assert evolve_relationship(state, AffectiveAppraisal(social_relevance=.9), social_safety_after=.9) == state
