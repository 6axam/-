"""Per-chat emotional continuity primitives."""

from app.emotions.engine import EmotionalEngine
from app.emotions.model import EMOTION_BASELINE, EMOTION_DIMENSIONS, EmotionDelta, EmotionalState

__all__ = ["EMOTION_BASELINE", "EMOTION_DIMENSIONS", "EmotionDelta", "EmotionalEngine", "EmotionalState"]
