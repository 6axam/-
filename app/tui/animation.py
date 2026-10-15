"""Time-based visual animation state, isolated from Anya's persisted snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, sin, tau
from random import Random
from typing import TYPE_CHECKING

from app.tui.sprite import Expression, Pose, SpriteAnimation, SpriteFrame
from app.tui.state import AnyaSnapshot, aggregate_emotions

if TYPE_CHECKING:
    from app.tui.scenes import SceneDecision, SceneKind


TRANSITION_SECONDS = 0.48


@dataclass(frozen=True)
class AnimationFrame:
    sprite: SpriteFrame
    dominant_emotion: str
    displayed_intensity: float
    transition_progress: float
    transition_from: SceneKind | None
    transition_to: SceneKind | None


class AnimationController:
    """Owns local RNG and dt-based visual tweening; it never mutates snapshots."""

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = Random(seed)
        self._last_time: float | None = None
        self._scene: SceneKind | None = None
        self._transition_from: SceneKind | None = None
        self._transition_started_at: float | None = None
        self._target_intensity = 0.0
        self._displayed_intensity = 0.0
        self._dominant_emotion = "warm"
        self._next_blink_at = self._rng.uniform(1.8, 3.6)
        self._blink_until = -1.0
        self._next_look_at = self._rng.uniform(3.2, 6.0)
        self._look_until = -1.0

    def sync(self, snapshot: AnyaSnapshot, decision: SceneDecision, now: float) -> None:
        channels = aggregate_emotions(snapshot.emotions)
        self._dominant_emotion, self._target_intensity = max(channels.items(), key=lambda item: item[1], default=("warm", 0.0))
        if self._scene is not None and decision.kind is not self._scene:
            self._transition_from = self._scene
            self._transition_started_at = now
        self._scene = decision.kind

    def advance(self, decision: SceneDecision, now: float) -> AnimationFrame:
        dt = 0.0 if self._last_time is None else max(0.0, min(0.25, now - self._last_time))
        self._last_time = now
        self._displayed_intensity = _approach(self._displayed_intensity, self._target_intensity, dt, 0.32)
        if now >= self._next_blink_at:
            self._blink_until = now + self._rng.uniform(0.10, 0.16)
            self._next_blink_at = now + self._rng.uniform(2.2, 5.4)
        if now >= self._next_look_at:
            self._look_until = now + self._rng.uniform(0.28, 0.55)
            self._next_look_at = now + self._rng.uniform(4.0, 7.5)
        progress = self._transition_progress(now)
        if progress >= 1.0:
            self._transition_from = None
            self._transition_started_at = None
        sprite = self._sprite_frame(decision, now)
        return AnimationFrame(
            sprite=sprite,
            dominant_emotion=self._dominant_emotion,
            displayed_intensity=self._displayed_intensity,
            transition_progress=progress,
            transition_from=self._transition_from,
            transition_to=decision.kind if self._transition_from else None,
        )

    def _transition_progress(self, now: float) -> float:
        if self._transition_started_at is None:
            return 1.0
        return min(1.0, max(0.0, (now - self._transition_started_at) / TRANSITION_SECONDS))

    def _sprite_frame(self, decision: SceneDecision, now: float) -> SpriteFrame:
        pose = _pose_for(decision)
        expression = _expression_for(self._dominant_emotion, self._displayed_intensity)
        animation = _animation_for(pose, expression)
        breathe = round(sin(now * tau / 2.8) * 0.7)
        head = -1 if self._look_until >= now else 0
        action_phase = int(now * 4) % 4
        return SpriteFrame(
            pose=pose,
            expression=expression,
            animation=animation,
            blink=self._blink_until >= now,
            breathe_offset=breathe,
            head_offset=head,
            walk_phase=int(now * 5) % 4,
            action_phase=action_phase,
        )


def _approach(current: float, target: float, dt: float, seconds: float) -> float:
    if dt <= 0:
        return current
    return current + (target - current) * (1 - exp(-dt / seconds))


def _pose_for(decision: SceneDecision) -> Pose:
    return {
        "sleeping": Pose.SLEEPING,
        "phone": Pose.SITTING_PHONE,
        "home_desk": Pose.SITTING_DESK,
        "college_desk": Pose.SITTING_DESK,
        "busy_away": Pose.WALKING,
        "home_bed": Pose.SITTING,
        "idle_home": Pose.STANDING,
    }.get(str(decision.kind.value), Pose.STANDING)


def _expression_for(dominant: str, intensity: float) -> Expression:
    if intensity < 0.18:
        return Expression.NEUTRAL
    return {
        "warm": Expression.WARM,
        "anger": Expression.ANGRY,
        "sadness": Expression.SAD,
        "anxiety": Expression.ANXIOUS,
        "happy": Expression.HAPPY,
        "jealousy": Expression.JEALOUS,
    }.get(dominant, Expression.NEUTRAL)


def _animation_for(pose: Pose, expression: Expression) -> SpriteAnimation:
    if pose is Pose.SLEEPING:
        return SpriteAnimation.SLEEPING
    if pose is Pose.WALKING:
        return SpriteAnimation.WALKING
    if pose is Pose.SITTING_PHONE:
        return SpriteAnimation.PHONE_IDLE
    if pose is Pose.SITTING_DESK:
        return SpriteAnimation.TYPING
    if expression is Expression.ANGRY:
        return SpriteAnimation.ANGRY_IDLE
    if expression is Expression.SAD:
        return SpriteAnimation.SAD_IDLE
    if expression in {Expression.HAPPY, Expression.WARM}:
        return SpriteAnimation.HAPPY_IDLE
    return SpriteAnimation.IDLE
