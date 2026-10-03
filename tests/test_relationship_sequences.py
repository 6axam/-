import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.emotions.affective import AffectiveChemistry, AffectiveEngine, REGULATOR_BASELINES, appraisal_to_delta
from app.emotions.engine import EmotionalEngine
from app.emotions.history import AffectiveHistory
from app.emotions.profile import behavior, calculate
from app.emotions.relationship import RelationshipBondState, evolve_relationship
from app.episodic_memory import EpisodicMemoryManager
from app.llm.schemas import AffectiveAppraisal, LLMResponse, MemoryEpisode


def step(chemistry, emotions, bond, appraisal):
    chemistry = chemistry.with_delta(appraisal_to_delta(appraisal))
    emotions = calculate(chemistry, emotions.values(), signals=appraisal)
    bond = evolve_relationship(bond, appraisal)
    return chemistry, emotions, bond, behavior(chemistry, emotions, bond)


def test_conflict_absence_then_longing_preserves_love():
    chemistry = AffectiveChemistry({**REGULATOR_BASELINES, 'attachment': .92, 'social_safety': .9,
                                   'valence': .75, 'energy': .7, 'vulnerability': .65})
    emotions = calculate(chemistry)
    bond = RelationshipBondState(.9, .85, .9, .88, .02, .87, .9)
    baseline = behavior(chemistry, emotions, bond)
    insult = AffectiveAppraisal(valence=-1, intensity=1, social_relevance=1, rejection=1,
                                dismissal=1, other_blame=1, frustration=1)
    chemistry, emotions, bond, first = step(chemistry, emotions, bond, insult)
    assert emotions.values()['hurt'] > baseline.hostility
    assert chemistry.values()['social_safety'] < .9
    assert bond.relational_trust < .85 and bond.relational_trust > .8
    assert bond.bond_strength > .89 and bond.love_strength > .88
    for _ in range(3):
        chemistry, emotions, bond, second = step(chemistry, emotions, bond, insult)
    assert second.protest_drive > first.protest_drive
    assert second.hostility > baseline.hostility
    assert second.patience < baseline.patience
    assert second.expression_intensity > baseline.expression_intensity
    assert bond.love_strength > .85 and second.warmth_expression < baseline.warmth_expression

    # A later quiet interval reduces fast anger while durable hurt and bond
    # remain; absence independently raises longing and contact pressure.
    evolved = chemistry.values()
    evolved.update(arousal=.25, frustration=.20, social_need=.9, energy=.38)
    quiet_chemistry = AffectiveChemistry(evolved)
    quiet_emotions = calculate(quiet_chemistry, emotions.values())
    quiet = behavior(quiet_chemistry, quiet_emotions, bond, absence_hours=48)
    assert quiet.hostility < second.hostility
    assert quiet_emotions.values()['hurt'] > .4
    assert quiet.longing > .5
    assert quiet.contact_drive > behavior(quiet_chemistry, quiet_emotions, bond).contact_drive


def test_neutral_person_is_not_jealousy_replacement_is_and_repair_is_gradual():
    chemistry = AffectiveChemistry({**REGULATOR_BASELINES, 'attachment': .9, 'vulnerability': .8})
    emotions = calculate(chemistry)
    bond = RelationshipBondState(.9, .85, .8, .86, .05, .86, .88)
    neutral = AffectiveAppraisal(social_relevance=1)
    chemistry, emotions, bond, _ = step(chemistry, emotions, bond, neutral)
    assert emotions.values()['jealousy'] == 0
    replacement = AffectiveAppraisal(social_relevance=1, intensity=1, replacement_threat=1,
                                     rival_salience=1, rejection=1)
    chemistry, emotions, bond, threatened = step(chemistry, emotions, bond, replacement)
    assert emotions.values()['jealousy'] > .2
    assert threatened.fear_of_loss > .2 and threatened.protest_drive > .15
    assert bond.bond_strength > .89
    repair = AffectiveAppraisal(social_relevance=1, intensity=1, repair_attempt=1, warmth=1, care=1)
    before = bond
    chemistry, emotions, bond, repaired = step(chemistry, emotions, bond, repair)
    assert repaired.hostility < threatened.hostility
    assert chemistry.values()['social_safety'] > REGULATOR_BASELINES['social_safety'] - .1
    assert bond.relational_trust > before.relational_trust
    assert bond.rupture_load < before.rupture_load
    assert bond.rupture_load > 0


def test_late_night_only_modestly_changes_regulation_and_burst():
    chemistry = AffectiveChemistry({**REGULATOR_BASELINES, 'attachment': .9, 'social_need': .9,
                                   'arousal': .7, 'inhibition': .35})
    emotions = calculate(chemistry)
    bond = RelationshipBondState(.9, .8, .8, .8, .1, .8, .85)
    daytime = behavior(chemistry, emotions, bond, absence_hours=48)
    night = behavior(chemistry, emotions, bond, absence_hours=48, night=True)
    assert 0 < daytime.regulation_capacity - night.regulation_capacity <= .05
    assert 0 < night.message_burst_drive - daytime.message_burst_drive < .05


class Context:
    def __init__(self, db): self.db = db
    async def build(self, *_args): return 'system', 'context'


class Executor:
    async def execute(self, _item): pass


@pytest.mark.asyncio
async def test_stale_and_failed_generations_do_not_change_relationship_or_create_episode(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'safety.sqlite'}")
    await db.connect()
    affective = AffectiveEngine(db)
    legacy = EmotionalEngine(db)
    episodes = EpisodicMemoryManager(db)
    before = await affective.relationship_manager.get(7)

    class Failed:
        async def generate(self, _request): raise RuntimeError('offline')

    failed = ConversationManager(Failed(), Context(db), ActionQueue(Executor()), emotion_engine=legacy, episodic_memory=episodes)
    failed.affective_engine = affective
    await failed.handle_turn(1, 7, 'failure')
    assert await affective.relationship_manager.get(7) == before
    assert await db.fetchone('SELECT id FROM episodic_memories WHERE chat_id=7') is None

    class Stale:
        def __init__(self): self.started = asyncio.Event(); self.release = asyncio.Event()
        async def generate(self, _request):
            self.started.set()
            try: await self.release.wait()
            except asyncio.CancelledError: pass
            return LLMResponse(actions=[Action(type=ActionType.text, text='old')],
                affective_appraisal=AffectiveAppraisal(social_relevance=1, rejection=1),
                memory_episode=MemoryEpisode(kind='relationship', summary='old', importance=.8))

    provider = Stale()
    stale = ConversationManager(provider, Context(db), ActionQueue(Executor()), emotion_engine=legacy, episodic_memory=episodes)
    stale.affective_engine = affective
    task = asyncio.create_task(stale.handle_turn(1, 7, 'old'))
    await provider.started.wait()
    await stale.interrupt(7)
    provider.release.set()
    await task
    assert await affective.relationship_manager.get(7) == before
    assert await db.fetchone('SELECT id FROM episodic_memories WHERE chat_id=7') is None
    await db.close()


@pytest.mark.asyncio
async def test_owner_test_mode_keeps_immediate_reaction_but_limits_bond_damage(tmp_path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path/'test-mode.sqlite'}")
    await db.connect()
    affective = AffectiveEngine(db)
    legacy = EmotionalEngine(db)
    records = []
    monkeypatch.setattr(AffectiveHistory, 'append', lambda _self, **kwargs: records.append(kwargs))

    class Provider:
        calls = 0
        async def generate(self, request):
            self.calls += 1
            appraisal = (AffectiveAppraisal(social_relevance=1, intensity=1, rejection=1,
                                           dismissal=1, other_blame=1)
                         if request.user_turn == 'bad' else AffectiveAppraisal())
            return LLMResponse(actions=[Action(type=ActionType.text, text='ok')], affective_appraisal=appraisal)

    provider = Provider()
    manager = ConversationManager(provider, Context(db), ActionQueue(Executor()), emotion_engine=legacy)
    manager.affective_engine = affective
    await manager.handle_turn(1, 7, 'аня, режим теста')
    before = await affective.relationship_manager.get(7)
    await manager.handle_turn(1, 7, 'bad')
    during = await affective.relationship_manager.get(7)
    assert (await affective.get_emotions(7)).values()['hurt'] > .4
    assert before.relational_trust - during.relational_trust < .005
    await manager.handle_turn(1, 7, 'режим теста закончен')
    await manager.handle_turn(1, 7, 'bad')
    after = await affective.relationship_manager.get(7)
    assert during.relational_trust - after.relational_trust > before.relational_trust - during.relational_trust
    assert provider.calls == 4 and len(records) == 4
    assert records[-1]['relationship_before']['relational_trust'] == during.relational_trust
    assert records[-1]['relationship_after']['relational_trust'] == after.relational_trust
    await db.close()
