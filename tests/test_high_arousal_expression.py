import pytest

from app.conversation.context import ContextBuilder
from app.database.db import Database
from app.llm.openai_provider import conversation_response_format
from app.llm.schemas import CurrentExpressionIntent


async def _runtime_prompt(tmp_path, **builder_kwargs):
    db = Database(f"sqlite:///{tmp_path/'expression.sqlite'}")
    await db.connect()
    system, context, breakdown = await ContextBuilder(db, **builder_kwargs).build_with_breakdown(
        1, 7, "current conflict"
    )
    await db.close()
    return system, context, breakdown


@pytest.mark.asyncio
async def test_extreme_low_control_contract_permits_unresolved_surface_expression(tmp_path):
    intent = CurrentExpressionIntent(
        activation=.92,
        hostility=.86,
        protest_tendency=.89,
        profanity_tendency=.77,
        caps_tendency=.68,
        repair_tendency=.08,
        reflective_control=.09,
    )
    assert intent.reflective_control <= .15 and intent.activation >= .8

    system, _context, _breakdown = await _runtime_prompt(tmp_path)
    for marker in (
        "activation>=.80",
        "reflective_control<=.15",
        "strongly suppress coherent explanation",
        "direct counterattack",
        "refusal or silence",
        "unresolved ending",
        "no constructive closing",
        "Do not append rational explanation",
    ):
        assert marker in system


@pytest.mark.asyncio
async def test_caps_burst_and_withdrawal_have_bounded_distinct_execution(tmp_path):
    system, _context, _breakdown = await _runtime_prompt(tmp_path)
    for marker in (
        "caps_tendency>=.60",
        "1–4 word CAPS fragment",
        "never uppercase the whole response",
        "several short text actions",
        "weaker protest or low energy",
        "one cold action or silence",
    ):
        assert marker in system


@pytest.mark.asyncio
async def test_high_control_allows_argument_and_moderate_conflict_does_not_force_attack(tmp_path):
    controlled = CurrentExpressionIntent(
        activation=.9, hostility=.75, protest_tendency=.72, reflective_control=.75
    )
    moderate = CurrentExpressionIntent(
        activation=.48, hostility=.32, protest_tendency=.38, reflective_control=.5
    )
    assert controlled.reflective_control >= .6
    assert moderate.hostility < .7 and moderate.protest_tendency < .7

    system, _context, _breakdown = await _runtime_prompt(tmp_path)
    assert "reflective_control>=.60" in system
    assert "coherent explanation or argument is allowed" in system
    assert "Moderate conflict or low hostility/protest must not force" in system


@pytest.mark.asyncio
async def test_conflict_memory_is_relevance_gated_and_sensitive_facts_are_protected(tmp_path):
    class MemoryRetrieval:
        async def search(self, _user_id, _chat_id, _query, _limit):
            return [{"id": 4, "content": "a relevant established promise"}]

    system, context, _breakdown = await _runtime_prompt(
        tmp_path, memory_retrieval=MemoryRetrieval()
    )
    assert "clearly relevant prior behavior, promise, repeated pattern" in system
    assert "Never pull in an unrelated grievance" in system
    assert "private/sensitive vulnerability" in system
    assert "a relevant established promise" in context
    assert "Never use a private/sensitive fact or vulnerability as conflict ammunition" in context


def test_expression_schema_describes_bounded_caps_and_control_semantics():
    schema = conversation_response_format()["json_schema"]["schema"]
    properties = schema["$defs"]["CurrentExpressionIntent"]["properties"]
    caps = properties["caps_tendency"]
    control = properties["reflective_control"]
    assert caps["minimum"] == 0 and caps["maximum"] == 1
    assert "bounded CAPS fragment" in caps["description"]
    assert "never an all-CAPS reply" in caps["description"]
    assert control["minimum"] == 0 and control["maximum"] == 1
    assert "very low suppresses explanations" in control["description"]
    assert "high permits argument" in control["description"]
