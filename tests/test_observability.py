import json

import httpx
import pytest

from app.database.db import Database
from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.schemas import LLMRequest
from app.observability import ObservabilityWriter, PromptView, stable_session_id, usage_fields


def test_stable_session_ids_are_opaque_and_namespaced():
    first = stable_session_id(6002167908)
    assert first == stable_session_id(6002167908)
    assert first != stable_session_id(6002167909)
    assert "6002167908" not in first and first.startswith("anya:conversation:")
    assert stable_session_id(1, "initiative").startswith("anya:initiative:")


async def test_openrouter_sends_session_id_but_openai_does_not():
    router_payloads, openai_payloads = [], []

    async def router(request):
        router_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"actions":[]}'}}]})

    async def openai(request):
        openai_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"actions":[]}'}}]})

    request = LLMRequest(system="s", context="c", user_turn="u", telemetry={"chat_id": 123})
    await OpenRouterProvider("key", "model", transport=httpx.MockTransport(router), retries=0).generate(request)
    await OpenAICompatibleProvider("key", "model", transport=httpx.MockTransport(openai), retries=0).generate(request)
    assert router_payloads[0]["session_id"] == stable_session_id(123)
    assert "session_id" not in openai_payloads[0]


def test_usage_extraction_is_defensive_and_computes_cache():
    assert usage_fields({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 75, "cache_write_tokens": 10}, "cost": .01})["cache_hit_percent"] == 75
    assert usage_fields({"completion_tokens": 4})["cached_tokens"] is None


async def test_successful_observability_is_persisted_without_secret(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'obs.sqlite'}")
    await db.connect()
    writer = ObservabilityWriter(db)
    await writer.record_generation(kind="conversation", chat_id=7, turn_id=None, generation_id="g", model="model", session_id="anya:conversation:hash", latency_ms=55,
                                   usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13, "prompt_tokens_details": {"cached_tokens": 4}},
                                   prompt=PromptView("Authorization: Bearer secret", "context", "hello", "{}"))
    row = await db.fetchone("SELECT * FROM llm_generations")
    assert row["cached_tokens"] == 4 and "secret" not in row["system_prompt"]
    await db.close()
