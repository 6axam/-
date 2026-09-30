from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionType(str, Enum):
    text = "text"; sticker = "sticker"; reaction = "reaction"; image = "image"; pause = "pause"; silence = "silence"


class Duration(str, Enum):
    instant = "instant"; short = "short"; medium = "medium"; long = "long"


class StickerIntent(BaseModel):
    """What the character wants to communicate before catalog retrieval."""
    model_config = ConfigDict(extra="forbid")
    meaning: str = Field(min_length=1, max_length=240)
    emotion: str | None = Field(default=None, max_length=120)
    intensity: float | None = Field(default=None, ge=0, le=1)


class ImageIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=40)
    scene: str = Field(min_length=1, max_length=600)
    importance: float = Field(default=.5, ge=0, le=1)
    caption: str | None = Field(default=None, max_length=800)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ActionType
    text: str | None = Field(default=None, max_length=4096)
    sticker_id: int | None = None
    sticker_intent: StickerIntent | None = None
    emoji: str | None = Field(default=None, max_length=32)
    duration: Duration | None = None
    target_message_id: int | None = None
    reply_to_message_id: int | None = None
    image_intent: ImageIntent | None = None
    cancelable: bool = True
    automatic: bool = False
    priority: int = 0

    @model_validator(mode="after")
    def validate_payload(self):
        if self.type == ActionType.text and not self.text:
            raise ValueError("text action requires text")
        if self.type == ActionType.pause and not self.duration:
            raise ValueError("pause action requires duration")
        if self.type == ActionType.sticker and not (self.sticker_id or self.sticker_intent):
            raise ValueError("sticker action requires sticker_id or sticker_intent")
        if self.type == ActionType.reaction and not self.emoji:
            raise ValueError("reaction action requires emoji")
        if self.type == ActionType.image and not self.image_intent:
            raise ValueError("image action requires image_intent")
        return self


class QueuedAction(BaseModel):
    chat_id: int
    generation_id: str
    action: Action
