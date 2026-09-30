from abc import ABC, abstractmethod
from app.llm.schemas import DailyLifeDecision, ImageContent, InitiativeDecision, LLMRequest, LLMResponse, ResponseTiming, StickerSemantics


class LLMProvider(ABC):
    supports_vision: bool = False
    supports_multiple_images: bool = False
    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    async def decide_timing(self, request: LLMRequest) -> ResponseTiming:
        return ResponseTiming()

    async def decide_initiative(self, request: LLMRequest) -> InitiativeDecision:
        return InitiativeDecision(should_message=False, reason="provider has no initiative decision")

    async def decide_daily_life(self, request: LLMRequest) -> DailyLifeDecision:
        return DailyLifeDecision()

    async def analyze_sticker(self, frames: list[ImageContent]) -> StickerSemantics:
        raise ProviderError("This LLM provider does not support vision analysis")

    async def describe_image(self, image: ImageContent) -> str:
        raise ProviderError("This LLM provider does not support vision descriptions")


class ProviderError(RuntimeError):
    pass


class StructuredOutputError(ProviderError):
    pass
