from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.actions.models import Action


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["IGNORE", "SAVE", "UPDATE_EXISTING"]
    content: str = Field(max_length=1000)
    importance: float = Field(default=.5, ge=0, le=1)
    confidence: float = Field(default=.7, ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=8)
    # UPDATE_EXISTING is valid only for an id included in RELEVANT MEMORIES.
    target_memory_id: int | None = Field(default=None, gt=0)


class AutobiographicalEventCandidate(BaseModel):
    """A durable fact from Anya's own life, never user memory."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["IGNORE", "SAVE", "UPDATE_EXISTING"] = "IGNORE"
    summary: str = Field(default="", max_length=600)
    details: str | None = Field(default=None, max_length=1200)
    kind: str = Field(default="ordinary", max_length=80)
    occurred_at_hint: str | None = Field(default=None, max_length=120)
    participants: list[str] = Field(default_factory=list, max_length=8)
    target_event_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_summary_for_saved_event(self):
        if self.decision != "IGNORE" and not self.summary.strip():
            raise ValueError("saved autobiographical event requires summary")
        return self


class SpontaneousContinuation(BaseModel):
    """At most one backend-gated extra Telegram message per user turn."""
    model_config = ConfigDict(extra="forbid")
    send: bool = False
    text: str = Field(default="", max_length=800)
    event_candidate: AutobiographicalEventCandidate | None = None

    @model_validator(mode="after")
    def require_text_when_sent(self):
        if self.send and not self.text.strip():
            raise ValueError("sent continuation requires text")
        return self


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


class EmotionDelta(BaseModel):
    """Sparse, bounded emotional movement proposed by the response model."""
    model_config = ConfigDict(extra="forbid")
    warmth: float | None = Field(default=None, ge=-.12, le=.12)
    trust: float | None = Field(default=None, ge=-.12, le=.12)
    joy: float | None = Field(default=None, ge=-.12, le=.12)
    sadness: float | None = Field(default=None, ge=-.12, le=.12)
    irritation: float | None = Field(default=None, ge=-.12, le=.12)
    hurt: float | None = Field(default=None, ge=-.12, le=.12)
    anxiety: float | None = Field(default=None, ge=-.12, le=.12)
    fatigue: float | None = Field(default=None, ge=-.12, le=.12)
    curiosity: float | None = Field(default=None, ge=-.12, le=.12)
    social_need: float | None = Field(default=None, ge=-.12, le=.12)

    def values(self) -> dict[str, float]:
        return {name: value for name, value in self.model_dump().items() if value is not None}

class AffectiveAppraisal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valence: float | None = Field(default=None, ge=-1, le=1)
    intensity: float | None = Field(default=None, ge=0, le=1)
    social_relevance: float | None = Field(default=None, ge=0, le=1)
    novelty: float | None = Field(default=None, ge=0, le=1)
    threat: float | None = Field(default=None, ge=0, le=1)
    loss: float | None = Field(default=None, ge=0, le=1)
    rejection: float | None = Field(default=None, ge=0, le=1)
    frustration: float | None = Field(default=None, ge=0, le=1)
    warmth: float | None = Field(default=None, ge=0, le=1)
    achievement: float | None = Field(default=None, ge=0, le=1)
    relief: float | None = Field(default=None, ge=0, le=1)
    self_blame: float | None = Field(default=None, ge=0, le=1)
    other_blame: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    humor: float | None = Field(default=None, ge=0, le=1)
    closeness: float | None = Field(default=None, ge=0, le=1)
    def values(self): return {key:value for key,value in self.model_dump().items() if value is not None}

class MemoryEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["shared_event", "anya_experience", "user_update", "relationship", "opinion_change", "open_loop"]
    summary: str = Field(min_length=1, max_length=600)
    reflection: str = Field(default="", max_length=500)
    importance: float = Field(default=.5, ge=0, le=1)
    confidence: float = Field(default=.7, ge=0, le=1)
    unresolved: bool = False
    supersede_episode_id: int | None = Field(default=None, gt=0)


class ResponseTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["immediate", "delayed"] = "immediate"
    urgency: Literal["urgent", "normal", "low"] = "normal"


class BedtimeAdjustment(BaseModel):
    """A one-night delay requested directly by the conversation owner."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "delay_once"] = "none"
    delay_minutes: int = Field(default=0, ge=0, le=240)

    @model_validator(mode="after")
    def valid_delay(self):
        if self.mode == "none" and self.delay_minutes:
            raise ValueError("no bedtime adjustment must use zero delay")
        if self.mode == "delay_once" and not 15 <= self.delay_minutes <= 240:
            raise ValueError("bedtime delay must be between 15 and 240 minutes")
        return self


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
    kind: Literal["followup", "self_life", "observation", "question", "tease", "random_thought", "daily_event", "callback", "bedtime"] = "random_thought"
    actions: list[Action] = Field(default_factory=list, max_length=8)
    self_life_event_candidate: AutobiographicalEventCandidate | None = None

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
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=4)
    spontaneous_continuation: SpontaneousContinuation = Field(default_factory=SpontaneousContinuation)
    self_updates: list[SelfUpdateProposal] = Field(default_factory=list, max_length=3)
    emotion_delta: EmotionDelta = Field(default_factory=EmotionDelta)
    affective_appraisal: AffectiveAppraisal = Field(default_factory=AffectiveAppraisal)
    memory_episode: MemoryEpisode | None = None
    resolve_episode_ids: list[int] = Field(default_factory=list, max_length=4)
    # Kept temporarily for backwards-compatible validation of old persisted
    # fixtures; the application no longer applies this global singleton state.
    emotional_update: EmotionalUpdate = Field(default_factory=EmotionalUpdate)
    response_timing: ResponseTiming = Field(default_factory=ResponseTiming)
    bedtime_adjustment: BedtimeAdjustment = Field(default_factory=BedtimeAdjustment)
    conversation: ConversationMetadata = Field(default_factory=ConversationMetadata)
