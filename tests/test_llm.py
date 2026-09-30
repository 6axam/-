import httpx
import json
import pytest
from pydantic import ValidationError

from app.actions.models import ActionType
from app.config import Settings
from app.llm.openai_provider import (
    PRIMARY_STRUCTURED_OUTPUT_INSTRUCTION,
    REPAIR_PROMPT,
    INITIATIVE_STRUCTURED_OUTPUT_INSTRUCTION,
    OpenAICompatibleProvider,
    conversation_response_format,
    repair_prompt_for,
)
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.schemas import InitiativeDecision, LLMRequest, LLMResponse


def test_structured_response_validation():
    response = LLMResponse.model_validate_json('{"actions":[{"type":"text","text":"привет"},{"type":"pause","duration":"short"}]}')
    assert [action.type for action in response.actions] == [ActionType.text, ActionType.pause]
    with pytest.raises(ValidationError):
        LLMResponse.model_validate_json('{"actions":[{"type":"text"}]}')


async def test_provider_repairs_broken_json_once():
    calls = 0
    payloads = []

    async def handler(request):
        nonlocal calls
        calls += 1
        payloads.append(json.loads(request.content))
        content = "not json" if calls == 1 else '{"actions":[{"type":"text","text":"готово"}]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=0)
    response = await provider.generate(LLMRequest(system="s", context="c", user_turn="u"))
    assert calls == 2
    assert response.actions[0].text == "готово"
    assert payloads[0]["response_format"]["type"] == "json_schema"
    assert REPAIR_PROMPT not in payloads[0]["messages"][0]["content"]
    assert PRIMARY_STRUCTURED_OUTPUT_INSTRUCTION in payloads[0]["messages"][0]["content"]
    assert payloads[1]["response_format"] == {"type": "json_object"}
    assert payloads[1]["messages"][0]["content"] == REPAIR_PROMPT


async def test_timing_call_logs_provider_usage(caplog):
    async def handler(_request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"mode":"immediate","urgency":"normal"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16, "cost": .001},
        })

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=0)
    with caplog.at_level("INFO"):
        result = await provider.decide_timing(LLMRequest(system="s", context="c", user_turn="u"))
    assert result.mode == "immediate"
    assert "timing_response_usage model=model" in caplog.text
    assert "prompt_tokens=12" in caplog.text and "total_tokens=16" in caplog.text


def test_primary_structured_output_contract_is_derived_from_pydantic(monkeypatch):
    monkeypatch.setattr(
        LLMResponse,
        "model_json_schema",
        classmethod(lambda cls: {"type": "object", "properties": {"fresh": {"type": "string"}}}),
    )

    contract = conversation_response_format()

    assert contract["type"] == "json_schema"
    assert contract["json_schema"]["strict"] is True
    assert contract["json_schema"]["schema"]["properties"] == {"fresh": {"type": "string"}}
    assert contract["json_schema"]["schema"]["required"] == ["fresh"]
    assert contract["json_schema"]["schema"]["additionalProperties"] is False


async def test_initiative_uses_request_character_and_native_schema_without_text_schema():
    payloads = []

    async def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"should_message":false,"reason":"no concrete reason"}'}}]})

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=0)
    decision = await provider.decide_initiative(LLMRequest(
        system="CORE CHARACTER: Тебя зовут Аня",
        context="EMOTIONAL STATE\nmood=warm",
        user_turn="",
        telemetry={"chat_id": 10, "target_input_tokens": 3500, "components": {}},
    ))

    assert not decision.should_message
    assert payloads[0]["response_format"]["json_schema"]["name"] == "initiative_decision"
    assert "CORE CHARACTER: Тебя зовут Аня" in payloads[0]["messages"][0]["content"]
    assert INITIATIVE_STRUCTURED_OUTPUT_INSTRUCTION in payloads[0]["messages"][0]["content"]
    assert repair_prompt_for(InitiativeDecision) not in payloads[0]["messages"][0]["content"]


async def test_invalid_initiative_output_repairs_with_text_schema_only_on_repair():
    payloads = []

    async def handler(request):
        payloads.append(json.loads(request.content))
        content = "bad" if len(payloads) == 1 else '{"should_message":false,"reason":"repaired"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=0)
    decision = await provider.decide_initiative(LLMRequest(system="CHARACTER", context="CONTEXT", user_turn=""))

    assert decision.reason == "repaired"
    assert payloads[0]["response_format"]["type"] == "json_schema"
    assert payloads[1]["response_format"] == {"type": "json_object"}
    assert payloads[1]["messages"][0]["content"] == repair_prompt_for(InitiativeDecision)


def test_openrouter_uses_its_own_default_base_url():
    assert OpenRouterProvider("key", "model").base_url == "https://openrouter.ai/api/v1"


async def test_provider_retries_temporary_http_error():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"actions":[]}'}}]})

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=1)
    response = await provider.generate(LLMRequest(system="s", context="c", user_turn="u"))
    assert response.actions == []
    assert calls == 2


async def test_configured_temperature_reaches_openai_compatible_request():
    async def handler(request):
        assert json.loads(request.content)["temperature"] == 0.65
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"actions":[]}'}}]})

    provider = OpenAICompatibleProvider("key", "model", temperature=0.65, transport=httpx.MockTransport(handler), retries=0)
    response = await provider.generate(LLMRequest(system="s", context="c", user_turn="u"))
    assert response.actions == []


def test_llm_temperature_loads_and_validates_range():
    settings = Settings(_env_file=None, telegram_bot_token="token", owner_telegram_id=1, llm_api_key="key", llm_temperature=1.25)
    assert settings.llm_temperature == 1.25
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token="token", owner_telegram_id=1, llm_api_key="key", llm_temperature=2.01)


def test_college_weekdays_parses_compact_env_value_and_validates_days():
    settings = Settings(
        _env_file=None, telegram_bot_token="token", owner_telegram_id=1,
        llm_api_key="key", college_weekdays="0,1,2,3,4",
    )
    assert settings.college_weekdays == (0, 1, 2, 3, 4)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token="token", owner_telegram_id=1, llm_api_key="key", college_weekdays="0,7")


def test_unknown_dotenv_key_is_not_silently_ignored(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TELEGRAM_BOT_TOKEN=token\nOWNER_TELEGRAM_ID=1\nLLM_API_KEY=key\nSTALE_SETTING=1\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="stale_setting"):
        Settings(_env_file=dotenv)
