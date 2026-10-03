import pytest

from app.database.db import Database
from app.emotions.relationship import RelationshipBondManager, RelationshipBondState, evolve_relationship
from app.llm.schemas import AffectiveAppraisal


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
