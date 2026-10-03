import pytest

from app.conversation.context import ContextBuilder
from app.database.db import Database
from app.emotions.affective import AffectiveChemistry, AffectiveEngine, REGULATOR_BASELINES, appraisal_to_delta
from app.emotions.profile import behavior, calculate
from app.emotions.relationship import RelationshipBondState, evolve_relationship
from app.episodic_memory import EpisodicMemoryManager
from app.llm.schemas import AffectiveAppraisal, MemoryEpisode


def _stress_sequence(turns=7):
    chemistry = AffectiveChemistry({**REGULATOR_BASELINES, 'attachment': .9,
        'social_safety': .85, 'valence': .7, 'energy': .7, 'vulnerability': .5})
    emotions = calculate(chemistry)
    bond = RelationshipBondState(.9, .85, .85, .86, .04, .84, .9)
    event = AffectiveAppraisal(valence=-.9, intensity=.9, social_relevance=.9,
        threat=.4, rejection=.7, frustration=.9, other_blame=.8,
        dismissal=.8, replacement_threat=.3)
    result = []
    for _ in range(turns):
        chemistry = chemistry.with_delta(appraisal_to_delta(event))
        emotions = calculate(chemistry, emotions.values(), signals=event)
        bond = evolve_relationship(bond, event, social_safety_after=chemistry.values()['social_safety'])
        result.append((chemistry, behavior(chemistry, emotions, bond)))
    return result


def test_extreme_behavior_keeps_headroom_and_sequential_events_keep_moving():
    sequence = _stress_sequence()
    bounded = ('patience', 'irritability', 'expression_intensity',
               'profanity_drive', 'regulation_capacity')
    for _chemistry, profile in sequence:
        assert all(0 < getattr(profile, name) < 1 for name in bounded)
    for name in ('irritability', 'expression_intensity', 'profanity_drive',
                 'avoidance', 'hostility', 'message_burst_drive'):
        values = [getattr(profile, name) for _chemistry, profile in sequence]
        assert len({round(value, 5) for value in values}) == len(values)
    assert sequence[-1][1].expression_intensity > sequence[1][1].expression_intensity
    assert sequence[-1][1].patience < sequence[1][1].patience


def test_inhibition_moves_with_strong_impulse_and_repair_without_becoming_regulation():
    hostile = AffectiveAppraisal(intensity=1, social_relevance=1, threat=.4,
        rejection=.8, frustration=1, other_blame=.9, dismissal=.9)
    repair = AffectiveAppraisal(intensity=.7, social_relevance=1,
        repair_attempt=1, self_blame=.6, warmth=.7)
    hostile_delta = appraisal_to_delta(hostile)
    repair_delta = appraisal_to_delta(repair)
    assert hostile_delta['inhibition'] < 0
    assert repair_delta['inhibition'] > 0
    chemistry, profile = _stress_sequence(1)[0]
    assert chemistry.values()['inhibition'] != REGULATOR_BASELINES['inhibition']
    assert profile.regulation_capacity != chemistry.values()['inhibition']


def test_repair_slightly_restores_relationship_without_resetting_it():
    damaged = RelationshipBondState(.9, .68, .86, .82, .34, .55, .88)
    repair = AffectiveAppraisal(intensity=.8, social_relevance=.9,
        repair_attempt=.9, warmth=.75, care=.7, self_blame=.5)
    restored = evolve_relationship(damaged, repair, social_safety_after=.62)
    assert restored.relational_trust > damaged.relational_trust
    assert restored.relationship_security > damaged.relationship_security
    assert restored.rupture_load < damaged.rupture_load
    assert restored.relational_trust - damaged.relational_trust < .03
    assert damaged.rupture_load - restored.rupture_load < .03
    assert restored.relational_trust < .72 and restored.rupture_load > .30
    assert abs(restored.bond_strength - damaged.bond_strength) < .005
    assert abs(restored.love_strength - damaged.love_strength) < .005


@pytest.mark.asyncio
async def test_conflict_episode_bonus_requires_semantic_relevance(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'episodes.sqlite'}")
    await db.connect()
    manager = EpisodicMemoryManager(db)
    state = {'warmth': .5}
    calm_snapshot = {'version': 2, 'chemistry': {'valence': .7},
        'salient_emotions': {'joy': .8}, 'behavior': {'hostility': .02}}
    conflict_snapshot = {'version': 2, 'chemistry': {'valence': .2},
        'salient_emotions': {'hurt': .85, 'resentment': .75},
        'behavior': {'hostility': .8, 'protest_drive': .7, 'rumination_drive': .8}}
    calm_id = await manager.apply(1, MemoryEpisode(kind='relationship',
        summary='обещание не обесценивать разговор', importance=.8), state,
        affective_snapshot=calm_snapshot)
    conflict_id = await manager.apply(1, MemoryEpisode(kind='relationship',
        summary='обещание не обесценивать разговор снова нарушено', importance=.8), state,
        affective_snapshot=conflict_snapshot)
    await manager.apply(1, MemoryEpisode(kind='shared_event',
        summary='купили новый кабель для ноутбука', importance=1), state,
        affective_snapshot=conflict_snapshot)
    ranked = await manager.relevant(1, 'ты снова обесцениваешь наш разговор',
        current_affective={'chemistry': {'valence': .15}}, conflict_activation=.9)
    assert ranked[0]['id'] == conflict_id
    assert calm_id in {row['id'] for row in ranked}
    assert all('кабель' not in row['summary'] for row in ranked)
    assert await manager.relevant(1, 'привет',
        current_affective={'chemistry': {'valence': .15}}, conflict_activation=1) == []
    await db.close()


@pytest.mark.asyncio
async def test_runtime_contract_binds_expression_reduces_meta_and_gates_conflict_memory(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'prompt.sqlite'}")
    await db.connect()
    episodes = EpisodicMemoryManager(db)
    affective = AffectiveEngine(db)
    conflict = AffectiveAppraisal(valence=-.9, intensity=1, social_relevance=1,
        rejection=1, frustration=1, other_blame=1, dismissal=.9)
    for _ in range(4):
        await affective.apply_appraisal(7, conflict)
    snapshot = {'version': 2, 'chemistry': {'valence': .2},
        'salient_emotions': {'hurt': .9, 'resentment': .8},
        'behavior': {'hostility': .8, 'protest_drive': .8}}
    await episodes.apply(7, MemoryEpisode(kind='relationship',
        summary='обещание не обесценивать разговор снова нарушено', importance=.8),
        {'warmth': .5}, affective_snapshot=snapshot)
    await episodes.apply(7, MemoryEpisode(kind='shared_event',
        summary='купили кабель для ноутбука', importance=.9), {'warmth': .5},
        affective_snapshot=snapshot)
    builder = ContextBuilder(db, episodic_memory=episodes)
    builder.affective_engine = affective
    system, context, _ = await builder.build_with_breakdown(
        1, 7, 'ты снова обесцениваешь наш разговор')
    for marker in ('binding', 'high hostility/protest', 'low repair/control',
                   'unresolved ending', 'short text actions', 'silence',
                   'Implementation talk is context', 'behave as Anya first',
                   'never call yourself a placeholder', 'repeated hurt'):
        assert marker.casefold() in system.casefold()
    assert 'обещание не обесценивать разговор' in context
    assert 'Conflict is active' in context and 'highly relevant past hurt' in context
    assert 'кабель' not in context
    await db.close()
