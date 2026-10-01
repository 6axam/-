from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceGenerationResult:
    data: bytes
    mime_type: str = "audio/ogg"
    duration_seconds: float | None = None
    cost: float | None = None


class VoiceProviderError(RuntimeError):
    """A controlled failure from a voice-generation backend."""


class VoiceProvider(ABC):
    name = "disabled"
    enabled = True

    @abstractmethod
    async def generate(self, text: str, *, mood: str | None = None, pace: str | None = None, energy: float = .5) -> VoiceGenerationResult | None: ...


class DisabledVoiceProvider(VoiceProvider):
    """Safe placeholder until a real provider such as Seed Audio is added."""
    enabled = False

    async def generate(self, text: str, *, mood=None, pace=None, energy=.5):
        return None
