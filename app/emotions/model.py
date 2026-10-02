"""Small, deterministic data model for Anya's per-chat emotional state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


EMOTION_DIMENSIONS = (
    "warmth", "trust", "joy", "sadness", "irritation", "hurt", "anxiety",
    "fatigue", "curiosity", "social_need",
)

EMOTION_BASELINE = {
    "warmth": .65,
    "trust": .65,
    "joy": .50,
    "sadness": .10,
    "irritation": .05,
    "hurt": .02,
    "anxiety": .10,
    "fatigue": .25,
    "curiosity": .55,
    "social_need": .45,
}


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class EmotionDelta:
    """Sparse model-facing delta.  Backend limits each individual change."""

    values: Mapping[str, float]

    def bounded(self, maximum: float = .12) -> dict[str, float]:
        return {
            name: max(-maximum, min(maximum, float(value)))
            for name, value in self.values.items()
            if name in EMOTION_DIMENSIONS and float(value)
        }


@dataclass(frozen=True)
class EmotionalState:
    warmth: float = .65
    trust: float = .65
    joy: float = .50
    sadness: float = .10
    irritation: float = .05
    hurt: float = .02
    anxiety: float = .10
    fatigue: float = .25
    curiosity: float = .55
    social_need: float = .45

    @classmethod
    def baseline(cls) -> "EmotionalState":
        return cls(**EMOTION_BASELINE)

    @classmethod
    def from_row(cls, row) -> "EmotionalState":
        return cls(**{name: float(row[name]) for name in EMOTION_DIMENSIONS})

    def values(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in EMOTION_DIMENSIONS}

    def with_values(self, values: Mapping[str, float]) -> "EmotionalState":
        merged = self.values()
        merged.update({name: clamp(value) for name, value in values.items() if name in merged})
        return EmotionalState(**merged)
