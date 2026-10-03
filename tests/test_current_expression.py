import pytest
from pydantic import ValidationError

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.emotions.affective import AffectiveEngine
from app.emotions.expression import sanitize_current_expression
from app.emotions.history import AffectiveHistory
from app.emotions.engine import EmotionalEngine
from app.llm.openai_provider import conversation_response_format
from app.llm.schemas import AffectiveAppraisal, CurrentExpressionIntent, LLMResponse


def test_expression_bounds_and_consistency_for_live_replacement_threat():
    with pytest.raises(ValidationError):
        CurrentExpressionIntent(impatience=1.01)
    with pytest.raises(ValidationError):
        CurrentExpressionIntent.model_validate({'extra_signal': .5})
    appraisal = AffectiveAppraisal(valence=-.65, intensity=.7, social_relevance=.8,
        rejection=.65, replacement_threat=.75, dismissal=.55)
    proposed = CurrentExpressionIntent(repair_tendency=.9, reflective_control=.9)
    actual = sanitize_current_expression(proposed, appraisal)
    assert actual.warmth_suppression >= .55
    assert actual.impatience >= .35 and actual.protest_tendency >= .4
    assert actual.repair_tendency <= .25 and actual.reflective_control <= .55
    assert actual.profanity_tendency == proposed.profanity_tendency
    calm = AffectiveAppraisal(valence=.6, warmth=.8, repair_attempt=.8)
    assert sanitize_current_expression(proposed, calm) is proposed


def test_emitted_conversation_schema_orders_event_intent_then_reply():
    schema = conversation_response_format()['json_schema']['schema']
    assert list(schema['properties'])[:3] == ['affective_appraisal', 'current_expression', 'actions']
    assert schema['required'][:3] == ['affective_appraisal', 'current_expression', 'actions']
    intent_schema = schema['$defs']['CurrentExpressionIntent']
    assert intent_schema['additionalProperties'] is False
    assert all(field in intent_schema['required'] for field in CurrentExpressionIntent.model_fields)


@pytest.mark.asyncio
async def test_runtime_prompt_expresses_current_event_before_actions_without_forced_repair(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'prompt.sqlite'}")
    await db.connect()
    system, _, _ = await ContextBuilder(db).build_with_breakdown(1, 7, 'current')
    for marker in ('affective_appraisal', 'current_expression', '`actions`',
                   'same-turn', 'therapist/mediator', 'unresolved', 'persistent love',
                   'repair_drive', 'Profanity', 'CAPS', 'text actions',
                   'more often than naming it'):
        assert marker in system
    await db.close()


@pytest.mark.asyncio
async def test_one_call_same_turn_intent_then_one_persistent_update_next_turn(tmp_path, monkeypatch):
    db = Database(f"sqlite:///{tmp_path/'lifecycle.sqlite'}")
    await db.connect()
    affective = AffectiveEngine(db)
    before = await affective.get(7)
    bond_before = await affective.relationship_manager.get(7)
    records = []
    monkeypatch.setattr(AffectiveHistory, 'append', lambda _self, **kwargs: records.append(kwargs))
    applied = []
    original_apply = affective.apply_appraisal

    async def counted_apply(*args, **kwargs):
        applied.append(args[1])
        return await original_apply(*args, **kwargs)

    monkeypatch.setattr(affective, 'apply_appraisal', counted_apply)

    class Provider:
        requests = []

        async def generate(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(actions=[Action(type=ActionType.text, text='Да ладно?')],
                    affective_appraisal=AffectiveAppraisal(valence=-.65, intensity=.7,
                        social_relevance=.8, rejection=.65, replacement_threat=.75,
                        dismissal=.55, loss=.35),
                    current_expression=CurrentExpressionIntent(activation=.8,
                        protest_tendency=.7, profanity_tendency=.5,
                        warmth_suppression=.8, repair_tendency=.05, reflective_control=.3))
            return LLMResponse(actions=[Action(type=ActionType.text, text='хз')])

    class Executor:
        async def execute(self, _item):
            pass

    context = ContextBuilder(db)
    context.affective_engine = affective
    context.relationship_manager = affective.relationship_manager
    provider = Provider()
    manager = ConversationManager(provider, context, ActionQueue(Executor()),
        emotion_engine=EmotionalEngine(db))
    manager.affective_engine = affective
    await manager.handle_turn(1, 7, 'Ты мне не нужна и легко заменима')
    after = await affective.get(7)
    bond_after = await affective.relationship_manager.get(7)
    assert len(provider.requests) == len(applied) == len(records) == 1
    assert records[0]['current_expression']['protest_tendency'] == .7
    assert records[0]['current_expression']['repair_tendency'] == .05
    assert after.values()['social_safety'] < before.values()['social_safety']
    assert bond_after.relationship_security < bond_before.relationship_security
    assert abs(bond_after.love_strength - bond_before.love_strength) < .005
    assert f"security={bond_before.relationship_security:.2f}" in provider.requests[0].context

    await manager.handle_turn(1, 7, 'а че ты щас делаешь?')
    assert len(provider.requests) == len(applied) == len(records) == 2
    assert f"security={bond_after.relationship_security:.2f}" in provider.requests[1].context
    assert 'irritability=' in provider.requests[1].context
    assert records[1]['relationship_before']['relationship_security'] == bond_after.relationship_security
    await db.close()
