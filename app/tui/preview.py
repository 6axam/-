"""Synthetic, read-only visual preview state for the procedural TUI renderer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.tui.scenes import ActivityKind, SceneDecision, SceneKind
from app.tui.state import AnyaSnapshot, PresenceView


PREVIEW_SCENES: dict[str, tuple[SceneKind, ActivityKind, str, str]] = {
    "idle_home": (SceneKind.IDLE_HOME, ActivityKind.FREE, "дома", "свободное время"),
    "home_desk": (SceneKind.HOME_DESK, ActivityKind.COMPUTER, "дома", "за столом"),
    "phone": (SceneKind.PHONE, ActivityKind.PHONE, "дома", "в телефоне"),
    "sleeping": (SceneKind.SLEEPING, ActivityKind.SLEEPING, "дома", "спит"),
}
PREVIEW_TIMES = ("morning", "day", "evening", "night")
PREVIEW_CLOCKS = {"morning": "08:00", "day": "13:00", "evening": "19:00", "night": "23:00"}
PREVIEW_EMOTIONS = ("warm", "happy", "sadness", "anger", "jealousy", "anxiety")
EMOTION_CHANNEL = {"warm": "affection", "happy": "joy", "sadness": "sadness", "anger": "anger", "jealousy": "jealousy", "anxiety": "anxiety"}


@dataclass
class PreviewSession:
    """Mutable only in process memory; production data is never opened or changed."""

    scene_name: str = "idle_home"
    time_of_day: str = "evening"
    emotion: str = "warm"
    intensity: float = 0.70
    seed: int = 7
    paused: bool = False

    def __post_init__(self) -> None:
        if self.scene_name not in PREVIEW_SCENES:
            raise ValueError(f"Unknown preview scene: {self.scene_name}")
        if self.time_of_day not in PREVIEW_TIMES:
            raise ValueError(f"Unknown preview time: {self.time_of_day}")
        if self.emotion not in PREVIEW_EMOTIONS:
            raise ValueError(f"Unknown preview emotion: {self.emotion}")
        self.intensity = max(0.0, min(1.0, float(self.intensity)))

    @property
    def decision(self) -> SceneDecision:
        kind, activity_kind, location, activity = PREVIEW_SCENES[self.scene_name]
        return SceneDecision(kind, activity_kind, location, activity, "synthetic preview")

    def snapshot(self) -> AnyaSnapshot:
        return AnyaSnapshot(
            datetime.now(timezone.utc),
            chat_id=0,
            presence=PresenceView(
                phase="free",
                availability="sleep" if self.scene_name == "sleeping" else "available",
                local_time=PREVIEW_CLOCKS[self.time_of_day],
                sleeping=self.scene_name == "sleeping",
            ),
            lifecycle="preview",
            regulators={"energy": 0.68},
            emotions={EMOTION_CHANNEL[self.emotion]: self.intensity},
            source_available=True,
        )

    def select_scene(self, scene_name: str) -> None:
        if scene_name in PREVIEW_SCENES:
            self.scene_name = scene_name

    def cycle_time(self) -> None:
        self.time_of_day = PREVIEW_TIMES[(PREVIEW_TIMES.index(self.time_of_day) + 1) % len(PREVIEW_TIMES)]

    def cycle_emotion(self) -> None:
        self.emotion = PREVIEW_EMOTIONS[(PREVIEW_EMOTIONS.index(self.emotion) + 1) % len(PREVIEW_EMOTIONS)]

    def adjust_intensity(self, delta: float) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity + delta))

    def diagnostic(self, frame: int, fps: int) -> str:
        return (
            f"scene={self.decision.kind.value.upper()} · time={self.time_of_day} · "
            f"emotion={self.emotion} {self.intensity:.2f} · fps={fps} · frame={frame}"
        )
