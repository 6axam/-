from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools import AuthorizationPolicy, SideEffectLevel, ToolExecutionContext, ToolExecutor, ToolRegistry, ToolSpec, ToolStatus
from app.agent.core_tools import register_core_tools


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


async def test_typed_tool_executes_only_after_owner_capability_and_validates_output():
    calls = []
    async def handler(data, context):
        calls.append((data.text, context.chat_id))
        return {"value": data.text.upper()}
    registry = ToolRegistry()
    registry.register(ToolSpec("fixture.echo", "fixture", Input, Output, frozenset({"fixture"}), AuthorizationPolicy.owner, SideEffectLevel.none, 1, handler=handler))
    executor = ToolExecutor(registry)
    context = ToolExecutionContext("owner", frozenset({"fixture"}), chat_id=7)

    result = await executor.execute("fixture.echo", {"text": "ok"}, context)

    assert result.status == ToolStatus.completed and result.output == {"value": "OK"}
    assert calls == [("ok", 7)]


async def test_tool_is_fail_closed_for_contact_missing_capability_invalid_input_and_unavailable():
    async def handler(data, _context): return {"value": data.text}
    registry = ToolRegistry()
    registry.register(ToolSpec("fixture.echo", "fixture", Input, Output, frozenset({"fixture"}), AuthorizationPolicy.owner, SideEffectLevel.none, 1, handler=handler))
    executor = ToolExecutor(registry)
    denied = await executor.execute("fixture.echo", {"text": "x"}, ToolExecutionContext("permitted_contact", frozenset({"fixture"})))
    invalid = await executor.execute("fixture.echo", {"other": "x"}, ToolExecutionContext("owner", frozenset({"fixture"})))
    unknown = await executor.execute("nope", {}, ToolExecutionContext("owner", frozenset()))
    assert (denied.status, invalid.status, unknown.status) == (ToolStatus.denied, ToolStatus.invalid_input, ToolStatus.unavailable)


async def test_core_tools_use_existing_memory_and_belief_retrieval_without_new_llm_call():
    class Memory:
        async def search(self, user_id, chat_id, query, limit):
            assert (user_id, chat_id, query, limit) == (1, 2, "rust", 3)
            return [{"content": "пользователь интересуется Rust"}]

    class Beliefs:
        async def search(self, query, limit):
            assert (query, limit) == ("privacy", 2)
            return [{"proposition": "privacy matters"}]

    registry = ToolRegistry()
    register_core_tools(registry, memory_retrieval=Memory(), belief_retrieval=Beliefs())
    executor = ToolExecutor(registry)
    context = ToolExecutionContext("owner", frozenset({"local_read"}), chat_id=2, user_id=1)
    memory = await executor.execute("memory.search", {"query": "rust", "limit": 3}, context)
    beliefs = await executor.execute("beliefs.search", {"query": "privacy", "limit": 2}, context)
    assert memory.output == {"items": ["пользователь интересуется Rust"]}
    assert beliefs.output == {"items": ["privacy matters"]}
