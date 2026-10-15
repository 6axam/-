"""Local, opt-in observability for completed LLM generations.

This module is deliberately one-way: saved prompt views are never read by the
conversation context or passed back to a model.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


def stable_session_id(chat_id: int, kind: str = "conversation") -> str:
    """Stable opaque session id; never expose the raw Telegram chat id."""
    digest = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:28]
    namespace = kind if kind in {"conversation", "initiative", "timing", "daily-life"} else "conversation"
    return f"anya:{namespace}:{digest}"


def usage_fields(usage: Mapping[str, Any] | None) -> dict[str, float | int | None]:
    """Extract optional OpenRouter/OpenAI usage fields without assumptions."""
    raw = dict(usage or {})
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, Mapping) else {}

    def number(name: str, source: Mapping[str, Any] = raw):
        value = source.get(name)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    prompt = number("prompt_tokens")
    cached = number("cached_tokens", details)
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "cache_write_tokens": number("cache_write_tokens", details),
        "completion_tokens": number("completion_tokens"),
        "total_tokens": number("total_tokens"),
        "uncached_tokens": max(0, prompt - (cached or 0)) if prompt is not None else None,
        "cache_hit_percent": (cached / prompt * 100) if prompt and cached is not None else None,
        "cost": number("cost"),
        "cache_discount": number("cache_discount"),
    }


_SECRET = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_ -]?key\s*[:=]\s*)([^\s,;]+)")


def safe_prompt_text(value: str) -> str:
    """Defence in depth for local prompt inspection; request headers are never accepted."""
    return _SECRET.sub(r"\1[REDACTED]", value)


@dataclass(frozen=True)
class PromptView:
    system_prompt: str | None
    dynamic_context: str | None
    user_turn: str | None
    response_schema_json: str | None


class ObservabilityWriter:
    def __init__(self, db, *, store_prompts: bool = True) -> None:
        self.db = db
        self.store_prompts = store_prompts

    async def record_generation(self, *, kind: str, chat_id: int | None, turn_id: int | None,
                                generation_id: str | None, model: str, session_id: str | None,
                                latency_ms: int, usage: Mapping[str, Any] | None,
                                estimated_input_tokens: int | None = None,
                                components: Mapping[str, Any] | None = None,
                                prompt: PromptView | None = None,
                                provider_generation_id: str | None = None) -> None:
        fields = usage_fields(usage)
        prompt_values = (None, None, None, None)
        if self.store_prompts and prompt:
            prompt_values = (
                safe_prompt_text(prompt.system_prompt or ""),
                safe_prompt_text(prompt.dynamic_context or ""),
                safe_prompt_text(prompt.user_turn or ""),
                prompt.response_schema_json,
            )
        await self.db.execute(
            "INSERT INTO llm_generations(chat_id,turn_id,generation_id,provider_generation_id,kind,model,session_id,latency_ms,"
            "prompt_tokens,cached_tokens,cache_write_tokens,completion_tokens,total_tokens,cost,cache_discount,estimated_input_tokens,components_json,"
            "system_prompt,dynamic_context,user_turn,response_schema_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, turn_id, generation_id, provider_generation_id, kind, model, session_id, latency_ms,
             fields["prompt_tokens"], fields["cached_tokens"], fields["cache_write_tokens"], fields["completion_tokens"], fields["total_tokens"],
             fields["cost"], fields["cache_discount"], estimated_input_tokens,
             json.dumps(components or {}, ensure_ascii=False, separators=(",", ":")), *prompt_values),
        )

    async def record_turn_state(self, *, chat_id: int, turn_id: int | None, generation_id: str,
                                user_message: str, before: Mapping[str, Any], after: Mapping[str, Any],
                                delta: Mapping[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO turn_state_observations(chat_id,turn_id,generation_id,user_message,before_json,after_json,delta_json) VALUES(?,?,?,?,?,?,?)",
            (chat_id, turn_id, generation_id, user_message,
             json.dumps(before, ensure_ascii=False, separators=(",", ":")),
             json.dumps(after, ensure_ascii=False, separators=(",", ":")),
             json.dumps(delta, ensure_ascii=False, separators=(",", ":"))),
        )


def nested_delta(before: Mapping[str, Any], after: Mapping[str, Any], *, epsilon: float = 1e-6) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group, after_value in after.items():
        before_value = before.get(group)
        if isinstance(after_value, Mapping) and isinstance(before_value, Mapping):
            changes = {key: round(float(value) - float(before_value.get(key, 0)), 8)
                       for key, value in after_value.items()
                       if isinstance(value, (int, float)) and abs(float(value) - float(before_value.get(key, 0))) > epsilon}
            if changes:
                result[group] = changes
        elif after_value != before_value:
            result[group] = {"before": before_value, "after": after_value}
    return result
