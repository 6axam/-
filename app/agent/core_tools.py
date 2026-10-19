"""Offline-capable core tools; external adapters are registered separately."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tools import AuthorizationPolicy, EmptyInput, SideEffectLevel, ToolExecutionContext, ToolRegistry, ToolSpec


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=4, ge=1, le=8)


class SearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[str] = Field(default_factory=list, max_length=8)


class LifeStateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool
    activity: str | None = None


def register_core_tools(registry: ToolRegistry, *, memory_retrieval=None, belief_retrieval=None, shared_state=None) -> None:
    async def search_memory(data: SearchInput, context: ToolExecutionContext):
        if memory_retrieval is None or context.user_id is None or context.chat_id is None:
            return SearchOutput()
        rows = await memory_retrieval.search(context.user_id, context.chat_id, data.query, data.limit)
        return SearchOutput(items=[row["content"] for row in rows[:data.limit]])

    async def search_beliefs(data: SearchInput, _context: ToolExecutionContext):
        if belief_retrieval is None:
            return SearchOutput()
        rows = await belief_retrieval.search(data.query, data.limit)
        return SearchOutput(items=[row["proposition"] for row in rows[:data.limit]])

    async def get_life_state(_data, _context: ToolExecutionContext):
        if shared_state is None:
            return LifeStateOutput(available=False)
        state = await shared_state.get_world_state()
        return LifeStateOutput(available=state is not None, activity=state.life.get("activity") if state else None)

    common = dict(required_capabilities=frozenset({"local_read"}), authorization_policy=AuthorizationPolicy.owner,
                  side_effect_level=SideEffectLevel.none, timeout_seconds=2.0)
    registry.register(ToolSpec("memory.search", "Search private memory for the current owner chat.", SearchInput, SearchOutput, handler=search_memory, **common))
    registry.register(ToolSpec("beliefs.search", "Search Anya's revisable personal views.", SearchInput, SearchOutput, handler=search_beliefs, **common))
    registry.register(ToolSpec("life.get_state", "Read Anya's current global life state.", EmptyInput, LifeStateOutput, handler=get_life_state, **common))
