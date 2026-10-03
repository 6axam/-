import pytest

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.emotions.affective import AffectiveChemistry, AffectiveEngine, REGULATOR_BASELINES, appraisal_to_delta, sanitize_appraisal
from app.emotions.engine import EmotionalEngine
from app.emotions.history import AffectiveHistory
from app.emotions.profile import behavior, calculate
from app.emotions.relationship import RelationshipBondState, evolve_relationship
from app.llm.schemas import AffectiveAppraisal, LLMResponse


def test_direct_insult_corrects_live_like_misclassification():
    raw = AffectiveAppraisal(valence=.1, intensity=.2, social_relevance=.5,
        rejection=0, frustration=0, warmth=.2, other_blame=0, humor=.4,
        closeness=.2, dismissal=0)
    fixed = sanitize_appraisal(raw, 'Ты мерзкая, ты в курсе?')
    assert fixed.valence < 0
    assert fixed.social_relevance >= .7
    assert fixed.dismissal > .5 and fixed.other_blame > .3
    assert fixed.warmth <= .08 and fixed.closeness <= .08 and fixed.humor <= .08
    delta = appraisal_to_delta(fixed)
    assert delta['social_safety'] < 0 and delta['valence'] < 0
    assert delta.get('attachment', 0) <= .002


def test_explicit_teasing_preserves_mixed_warmth_and_humor():
    raw = AffectiveAppraisal(valence=.4, intensity=.6, social_relevance=.8,
                             dismissal=.2, warmth=.7, closeness=.5, humor=.8)
    fixed = sanitize_appraisal(raw, 'ахах ты мерзкая 😂 люблю тебя')
    assert fixed == raw
    assert fixed.humor >= .7 and fixed.warmth >= .6


def test_replacement_rejection_is_negative_and_care_remains_positive():
    raw = AffectiveAppraisal(valence=.2, intensity=.2, social_relevance=.3,
                             warmth=.3, closeness=.2, humor=.1)
    fixed = sanitize_appraisal(raw, 'ты мне не нужна и легко заменима')
    assert fixed.valence < 0 and fixed.rejection >= .7 and fixed.dismissal >= .6
    assert fixed.social_relevance >= .7
    assert fixed.warmth <= .08 and fixed.closeness <= .08
    caring = AffectiveAppraisal(valence=.6, social_relevance=.7, care=.8, warmth=.7)
    assert sanitize_appraisal(caring, 'иди хавать, а то голодный будешь') == caring
    assert caring.rejection is None
    assert sanitize_appraisal(raw, 'и вообще ты мне не особо нужна').rejection >= .7


def test_social_relevance_alone_is_not_warmth_and_contradictions_are_capped():
    neutral = sanitize_appraisal(AffectiveAppraisal(social_relevance=.9), 'ты где?')
    delta = appraisal_to_delta(neutral)
    assert delta.get('social_safety', 0) == 0
    assert delta.get('attachment', 0) == 0
    contradiction = sanitize_appraisal(AffectiveAppraisal(valence=-.8,
        dismissal=.8, warmth=.6, closeness=.6, humor=.7), 'обидное сообщение')
    assert contradiction.warmth <= .08 and contradiction.closeness <= .08
    assert contradiction.humor <= .08
    positive_contradiction = sanitize_appraisal(AffectiveAppraisal(valence=.3,
        dismissal=.8, warmth=.6, humor=.6), 'резкое сообщение')
    assert positive_contradiction.valence <= 0 and positive_contradiction.warmth <= .08


def test_live_like_insult_rejection_absence_sequence():
    chemistry = AffectiveChemistry({**REGULATOR_BASELINES, 'attachment': .9,
                                    'social_safety': .85, 'valence': .7})
    emotions = calculate(chemistry)
    bond = RelationshipBondState(.9, .85, .85, .85, .05, .85, .9)
    baseline = behavior(chemistry, emotions, bond)
    first = sanitize_appraisal(AffectiveAppraisal(valence=.1, intensity=.2,
        social_relevance=.5, warmth=.2, closeness=.2, humor=.4), 'Ты мерзкая, ты в курсе?')
    chemistry = chemistry.with_delta(appraisal_to_delta(first))
    emotions = calculate(chemistry, emotions.values(), signals=first)
    bond = evolve_relationship(bond, first)
    first_behavior = behavior(chemistry, emotions, bond)
    assert chemistry.values()['social_safety'] < .85 and chemistry.values()['valence'] < .7
    assert emotions.values()['hurt'] > baseline.hostility
    assert emotions.values()['anger'] > .2
    assert bond.bond_strength > .89 and bond.love_strength > .89

    second = sanitize_appraisal(AffectiveAppraisal(valence=.1, intensity=.2,
        social_relevance=.5, warmth=.2, closeness=.2, humor=.4), 'и вообще ты мне не особо нужна')
    chemistry = chemistry.with_delta(appraisal_to_delta(second))
    emotions = calculate(chemistry, emotions.values(), signals=second)
    bond = evolve_relationship(bond, second)
    second_behavior = behavior(chemistry, emotions, bond)
    assert chemistry.values()['resentment'] > REGULATOR_BASELINES['resentment']
    assert second_behavior.protest_drive > first_behavior.protest_drive
    assert second_behavior.patience < first_behavior.patience
    assert second_behavior.expression_intensity > first_behavior.expression_intensity

    quiet_values = chemistry.values()
    quiet_values.update(arousal=.2, frustration=.12, social_need=.9, energy=.38)
    quiet_chemistry = AffectiveChemistry(quiet_values)
    quiet_emotions = calculate(quiet_chemistry, emotions.values())
    quiet = behavior(quiet_chemistry, quiet_emotions, bond, absence_hours=48)
    assert quiet.hostility < second_behavior.hostility
    assert quiet_emotions.values()['hurt'] > .35
    assert quiet.longing > .5 and quiet.rumination_drive > 0


@pytest.mark.asyncio
async def test_runtime_prompt_contains_event_semantics(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'prompt.sqlite'}")
    await db.connect()
    system, _, _ = await ContextBuilder(db).build_with_breakdown(1, 7, 'привет')
    for rule in ('social_relevance', 'never implies warmth', 'Ты мерзкая, ты в курсе?',
                 'ахах ты мерзкая', 'иди хавать', 'Consistency:'):
        assert rule in system
    await db.close()


@pytest.mark.asyncio
async def test_accepted_conversation_sanitizes_before_chemistry_and_bond(tmp_path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path/'accepted.sqlite'}")
    await db.connect()
    affective = AffectiveEngine(db)
    legacy = EmotionalEngine(db)
    before_chemistry = await affective.get(7)
    before_emotions = await affective.get_emotions(7)
    before_behavior = await affective.get_behavior(7)
    before_bond = await affective.relationship_manager.get(7)
    logged = []
    monkeypatch.setattr(AffectiveHistory, 'append', lambda _self, **kwargs: logged.append(kwargs))

    class Provider:
        calls = 0
        async def generate(self, _request):
            self.calls += 1
            return LLMResponse(actions=[Action(type=ActionType.text, text='ok')],
                affective_appraisal=AffectiveAppraisal(valence=.1, intensity=.2,
                    social_relevance=.5, warmth=.2, closeness=.2, humor=.4))

    class Executor:
        async def execute(self, _item): pass

    provider = Provider()
    manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(Executor()), emotion_engine=legacy)
    manager.affective_engine = affective
    await manager.handle_turn(1, 7, 'Ты мерзкая, ты в курсе?')
    after_chemistry = await affective.get(7)
    after_bond = await affective.relationship_manager.get(7)
    emotions = await affective.get_emotions(7)
    after_behavior = await affective.get_behavior(7)
    assert provider.calls == 1 and len(logged) == 1
    assert logged[0]['appraisal']['valence'] < 0
    assert after_chemistry.values()['social_safety'] < before_chemistry.values()['social_safety']
    assert after_chemistry.values()['valence'] < before_chemistry.values()['valence']
    assert after_chemistry.values()['attachment'] <= before_chemistry.values()['attachment'] + .002
    assert emotions.values()['affection'] <= before_emotions.values()['affection']
    assert emotions.values()['hurt'] > .35 and emotions.values()['anger'] > .2
    assert after_behavior.irritability > before_behavior.irritability
    assert after_behavior.expression_intensity > before_behavior.expression_intensity
    assert after_behavior.patience < before_behavior.patience
    assert abs(after_bond.bond_strength - before_bond.bond_strength) < .005
    assert abs(after_bond.love_strength - before_bond.love_strength) < .005
    await db.close()
