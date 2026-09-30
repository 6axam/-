from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field
from app.actions.models import Action


class MemoryCandidate(BaseModel):
    decision: str = Field(pattern="^(IGNORE|SAVE|UPDATE_EXISTING)$")
    content: str = Field(max_length=1000)
    importance: float = Field(default=.5, ge=0, le=1)
    confidence: float = Field(default=.7, ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=8)


class StateUpdate(BaseModel):
    mood: str | None = None
    energy_delta: float = Field(default=0, ge=-.1, le=.1)
    relationship_delta: float = Field(default=0, ge=-.02, le=.02)
    interest_delta: float = Field(default=0, ge=-.1, le=.1)
    current_topics: list[str] | None = Field(default=None, max_length=8)


class TextContent(BaseModel):
    """Provider-neutral text content. Telegram objects must never reach this layer."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class ImageContent(BaseModel):
    """An image already downloaded by the media layer, kept out of SQLite blobs."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["image"] = "image"
    data: bytes = Field(min_length=1)
    mime_type: str = Field(pattern=r"^image/[a-zA-Z0-9.+-]+$")
    source: str | None = Field(default=None, max_length=80)


ContentPart = Annotated[TextContent | ImageContent, Field(discriminator="type")]


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: list[ContentPart] = Field(min_length=1)


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: str
    user_turn: str
    context: str
    # Images belong only to the current turn. History stays compact text/semantic metadata.
    user_content: list[ContentPart] = Field(default_factory=list)
    # Internal observability metadata; never sent to the provider as message content.
    telemetry: dict = Field(default_factory=dict)


PersonalityCategory = Literal["music", "games", "technology", "media", "interest", "opinion", "habit", "communication", "inside_joke", "other"]


class SelfUpdateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: PersonalityCategory
    subject: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=240)
    strength_delta: float = Field(ge=-0.1, le=0.1)
    reason: str = Field(min_length=1, max_length=400)


class EmotionalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    energy_delta: float = Field(default=0, ge=-0.08, le=0.08)
    social_energy_delta: float = Field(default=0, ge=-0.08, le=0.08)
    offense_delta: float = Field(default=0, ge=-0.06, le=0.06)
    conversation_interest_delta: float = Field(default=0, ge=-0.08, le=0.08)
    availability: Literal["available", "busy", "away"] | None = None


class ResponseTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["immediate", "delayed"] = "immediate"
    urgency: Literal["urgent", "normal", "low"] = "normal"


class ConversationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_status: Literal["active", "cooling_down", "ended"] = "active"
    expects_reply: bool = False
    followup_importance: float = Field(default=0, ge=0, le=1)
    followup_reason: str | None = Field(default=None, max_length=300)


class InitiativeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    should_message: bool
    reason: str = Field(min_length=1, max_length=300)
    urgency: Literal["urgent", "normal", "low"] = "low"
    actions: list[Action] = Field(default_factory=list, max_length=8)

class DailyLifeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    create_event: bool = False
    title: str = Field(default="", max_length=160)
    availability: Literal["available", "busy", "away"] = "available"
    duration_minutes: int = Field(default=30, ge=15, le=240)
    mentionable: bool = True


class StickerSemantics(BaseModel):
    """Stable, provider-independent visual meaning of one sticker."""
    model_config = ConfigDict(extra="forbid")
    visual_description: str = Field(min_length=1, max_length=500)
    animation_description: str | None = Field(default=None, max_length=500)
    characters: list[str] = Field(default_factory=list, max_length=16)
    emotions: list[str] = Field(default_factory=list, max_length=16)
    meanings: list[str] = Field(default_factory=list, max_length=20)
    usage: list[str] = Field(default_factory=list, max_length=20)
    intensity: float = Field(default=.5, ge=0, le=1)

    def searchable_text(self) -> str:
        parts = [self.visual_description, self.animation_description or "", *self.characters, *self.emotions, *self.meanings, *self.usage]
        return " ".join(part.strip() for part in parts if part and part.strip())


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[Action] = Field(default_factory=list, max_length=12)
    self_updates: list[SelfUpdateProposal] = Field(default_factory=list, max_length=3)
    emotional_update: EmotionalUpdate = Field(default_factory=EmotionalUpdate)
    response_timing: ResponseTiming = Field(default_factory=ResponseTiming)
    conversation: ConversationMetadata = Field(default_factory=ConversationMetadata)
