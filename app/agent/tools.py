"""Typed tools with explicit authorization and no ambient machine access."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AuthorizationPolicy(str, Enum):
    owner = "owner"
    internal = "internal"
    permitted_source = "permitted_source"


class SideEffectLevel(str, Enum):
    none = "none"
    local_write = "local_write"
    external_read = "external_read"
    external_write = "external_write"


class ToolStatus(str, Enum):
    completed = "completed"
    denied = "denied"
    unavailable = "unavailable"
    invalid_input = "invalid_input"
    timed_out = "timed_out"
    failed = "failed"


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    status: ToolStatus
    output: dict = Field(default_factory=dict)
    error_code: str | None = None
    actual_cost: float = Field(default=.0, ge=0)


@dataclass(frozen=True)
class ToolExecutionContext:
    actor_kind: str
    capabilities: frozenset[str]
    chat_id: int | None = None
    user_id: int | None = None
    task_id: str | None = None


ToolHandler = Callable[[BaseModel, ToolExecutionContext], Awaitable[BaseModel | dict]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    required_capabilities: frozenset[str]
    authorization_policy: AuthorizationPolicy
    side_effect_level: SideEffectLevel
    timeout_seconds: float
    estimated_cost: float = .0
    handler: ToolHandler | None = None


class ToolRegistry:
    """Small explicit registry; registering a name twice is an error."""

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or spec.name in self._specs:
            raise ValueError(f"duplicate or empty tool name: {spec.name!r}")
        if spec.timeout_seconds <= 0 or spec.estimated_cost < 0:
            raise ValueError("tool timeout must be positive and estimated cost non-negative")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())


class ToolExecutor:
    """Validate, authorize and execute one bounded registered tool call."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    @staticmethod
    def _authorized(spec: ToolSpec, context: ToolExecutionContext) -> bool:
        if not spec.required_capabilities.issubset(context.capabilities):
            return False
        if spec.authorization_policy == AuthorizationPolicy.owner:
            return context.actor_kind == "owner"
        if spec.authorization_policy == AuthorizationPolicy.internal:
            return context.actor_kind in {"owner", "system"}
        return context.actor_kind in {"owner", "system", "permitted_contact"}

    async def execute(self, name: str, raw_input: dict, context: ToolExecutionContext) -> ToolResult:
        spec = self.registry.get(name)
        if not spec or not spec.handler:
            return ToolResult(tool_name=name, status=ToolStatus.unavailable, error_code="tool_unavailable")
        if not self._authorized(spec, context):
            return ToolResult(tool_name=name, status=ToolStatus.denied, error_code="authorization_denied")
        try:
            parsed = spec.input_model.model_validate(raw_input)
        except ValidationError:
            return ToolResult(tool_name=name, status=ToolStatus.invalid_input, error_code="invalid_input")
        try:
            value = await asyncio.wait_for(spec.handler(parsed, context), timeout=spec.timeout_seconds)
            output = spec.output_model.model_validate(value).model_dump(mode="json")
            return ToolResult(tool_name=name, status=ToolStatus.completed, output=output)
        except asyncio.TimeoutError:
            return ToolResult(tool_name=name, status=ToolStatus.timed_out, error_code="tool_timeout")
        except Exception:
            return ToolResult(tool_name=name, status=ToolStatus.failed, error_code="tool_failed")
