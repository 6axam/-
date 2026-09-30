"""Lightweight, provider-neutral context token estimation."""
from math import ceil


def estimate_tokens(text: str) -> int:
    """Conservatively estimate text tokens without loading a model tokenizer.

    The application can use multiple OpenAI-compatible models, so no single
    exact tokenizer is available. Three Unicode characters per token is a
    deliberately cautious approximation for Russian prose mixed with JSON.
    Provider usage remains the source of truth after a request completes.
    """
    return ceil(len(text) / 3) if text else 0


def component_size(text: str) -> dict[str, int]:
    return {"chars": len(text), "tokens": estimate_tokens(text)}
