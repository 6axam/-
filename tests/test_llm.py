import httpx
import json
import pytest
from pydantic import ValidationError

from app.actions.models import ActionType
from app.config import Settings
from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.schemas import LLMRequest, LLMResponse


def test_structured_response_validation():
    response = LLMResponse.model_validate_json('{"actions":[{"type":"text","text":"привет"},{"type":"pause","duration":"short"}]}')
    assert [action.type for action in response.actions] == [ActionType.text, ActionType.pause]
    with pytest.raises(ValidationError):
        LLMResponse.model_validate_json('{"actions":[{"type":"text"}]}')


async def test_provider_repairs_broken_json_once():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        content = "not json" if calls == 1 else '{"actions":[{"type":"text","text":"готово"}]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(handler), retries=0)
    response = await provider.generate(LLMRequest(system="s", context="c", user_turn="u"))
    assert calls == 2
    assert response.actions[0].text == "готово"


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


def test_unknown_dotenv_key_is_not_silently_ignored(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TELEGRAM_BOT_TOKEN=token\nOWNER_TELEGRAM_ID=1\nLLM_API_KEY=key\nSTALE_SETTING=1\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="stale_setting"):
        Settings(_env_file=dotenv)
