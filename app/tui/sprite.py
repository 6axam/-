"""Composable logical-pixel sprite for Anya; poses are not pre-rendered frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.tui.pixels import PixelCanvas, PixelToken


class Pose(str, Enum):
    STANDING = "standing"
    SITTING = "sitting"
    SITTING_PHONE = "sitting_phone"
    SITTING_DESK = "sitting_desk"
    SLEEPING = "sleeping"
    WALKING = "walking"


class Expression(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    WARM = "warm"
    SAD = "sad"
    ANGRY = "angry"
    JEALOUS = "jealous"
    ANXIOUS = "anxious"
    TIRED = "tired"


class SpriteAnimation(str, Enum):
    IDLE = "idle"
    BLINK = "blink"
    BREATHE = "breathe"
    TYPING = "typing"
    PHONE_IDLE = "phone_idle"
    SLEEPING = "sleeping"
    WALKING = "walking"
    ANGRY_IDLE = "angry_idle"
    SAD_IDLE = "sad_idle"
    HAPPY_IDLE = "happy_idle"


@dataclass(frozen=True)
class SpriteFrame:
    pose: Pose
    expression: Expression
    animation: SpriteAnimation
    blink: bool = False
    breathe_offset: int = 0
    head_offset: int = 0
    walk_phase: int = 0
    action_phase: int = 0


def draw_anya(canvas: PixelCanvas, x: int, y: int, frame: SpriteFrame) -> None:
    """Draw a 14×20-ish chibi using composable head, body, limb and face pieces."""
    if frame.pose is Pose.SLEEPING:
        _draw_sleeping(canvas, x, y, frame)
        return
    head_y = y + frame.head_offset
    _draw_head(canvas, x, head_y, frame.expression, frame.blink)
    if frame.pose is Pose.STANDING:
        _draw_body(canvas, x, head_y + 9, frame.breathe_offset)
        _draw_standing_legs(canvas, x, head_y + 15, frame.walk_phase if frame.animation is SpriteAnimation.WALKING else 0)
    else:
        _draw_body(canvas, x, head_y + 9, frame.breathe_offset)
        _draw_sitting_legs(canvas, x, head_y + 15)
        if frame.pose is Pose.SITTING_PHONE:
            _draw_phone(canvas, x + 11, head_y + 12, frame.action_phase)
        elif frame.pose is Pose.SITTING_DESK:
            _draw_typing_arm(canvas, x, head_y + 13, frame.action_phase)


def _draw_head(canvas: PixelCanvas, x: int, y: int, expression: Expression, blink: bool) -> None:
    canvas.rect(x + 3, y, 8, 1, PixelToken.OUTLINE)
    canvas.rect(x + 2, y + 1, 10, 1, PixelToken.HAIR)
    canvas.rect(x + 1, y + 2, 12, 5, PixelToken.HAIR)
    canvas.rect(x + 2, y + 3, 10, 5, PixelToken.SKIN)
    canvas.rect(x + 1, y + 5, 1, 3, PixelToken.HAIR)
    canvas.rect(x + 12, y + 5, 1, 3, PixelToken.HAIR)
    eye = PixelToken.OUTLINE
    if blink or expression is Expression.TIRED:
        canvas.rect(x + 4, y + 5, 2, 1, eye)
        canvas.rect(x + 8, y + 5, 2, 1, eye)
    else:
        canvas.put(x + 4, y + 5, eye)
        canvas.put(x + 9, y + 5, eye)
    if expression in {Expression.ANGRY, Expression.JEALOUS}:
        canvas.line(x + 3, y + 4, x + 5, y + 3, eye)
        canvas.line(x + 10, y + 3, x + 11, y + 4, eye)
    if expression is Expression.ANXIOUS:
        canvas.put(x + 7, y + 4, PixelToken.EMOTION_ANXIETY)
    _draw_mouth(canvas, x + 7, y + 7, expression)


def _draw_mouth(canvas: PixelCanvas, x: int, y: int, expression: Expression) -> None:
    if expression in {Expression.HAPPY, Expression.WARM}:
        canvas.rect(x - 1, y, 3, 1, PixelToken.OUTLINE)
        canvas.put(x, y + 1, PixelToken.OUTLINE)
    elif expression in {Expression.SAD, Expression.TIRED}:
        canvas.put(x, y, PixelToken.OUTLINE)
        canvas.put(x - 1, y + 1, PixelToken.OUTLINE)
        canvas.put(x + 1, y + 1, PixelToken.OUTLINE)
    elif expression in {Expression.ANGRY, Expression.JEALOUS}:
        canvas.rect(x - 1, y, 3, 1, PixelToken.OUTLINE)
    else:
        canvas.rect(x, y, 2, 1, PixelToken.OUTLINE)


def _draw_body(canvas: PixelCanvas, x: int, y: int, breathe_offset: int) -> None:
    canvas.rect(x + 3, y, 8, 2, PixelToken.CLOTHES)
    canvas.rect(x + 2, y + 2, 10, 4 + breathe_offset, PixelToken.CLOTHES)
    canvas.rect(x + 1, y + 3, 1, 3, PixelToken.SKIN)
    canvas.rect(x + 12, y + 3, 1, 3, PixelToken.SKIN)
    canvas.rect(x + 3, y + 5 + breathe_offset, 8, 1, PixelToken.CLOTHES_SHADOW)


def _draw_standing_legs(canvas: PixelCanvas, x: int, y: int, phase: int) -> None:
    left_shift = -1 if phase % 4 == 1 else 0
    right_shift = 1 if phase % 4 == 3 else 0
    canvas.rect(x + 4 + left_shift, y, 2, 4, PixelToken.OUTLINE)
    canvas.rect(x + 8 + right_shift, y, 2, 4, PixelToken.OUTLINE)
    canvas.rect(x + 3 + left_shift, y + 3, 4, 1, PixelToken.OUTLINE)
    canvas.rect(x + 8 + right_shift, y + 3, 4, 1, PixelToken.OUTLINE)


def _draw_sitting_legs(canvas: PixelCanvas, x: int, y: int) -> None:
    canvas.rect(x + 3, y, 9, 3, PixelToken.CLOTHES_SHADOW)
    canvas.rect(x + 8, y + 2, 5, 2, PixelToken.OUTLINE)
    canvas.rect(x + 10, y + 4, 4, 1, PixelToken.OUTLINE)


def _draw_phone(canvas: PixelCanvas, x: int, y: int, phase: int) -> None:
    lift = -1 if phase % 3 == 1 else 0
    canvas.rect(x, y + lift, 3, 5, PixelToken.OUTLINE)
    canvas.rect(x + 1, y + 1 + lift, 1, 3, PixelToken.GLASS)
    canvas.line(x - 3, y + 3, x, y + 1 + lift, PixelToken.SKIN)


def _draw_typing_arm(canvas: PixelCanvas, x: int, y: int, phase: int) -> None:
    hand_y = y + (phase % 2)
    canvas.line(x + 1, y, x - 2, hand_y + 2, PixelToken.SKIN)
    canvas.line(x + 12, y, x + 15, hand_y + 2, PixelToken.SKIN)


def _draw_sleeping(canvas: PixelCanvas, x: int, y: int, frame: SpriteFrame) -> None:
    blanket_y = y + 10 + frame.breathe_offset
    canvas.rect(x, y + 3, 8, 5, PixelToken.HAIR)
    canvas.rect(x + 2, y + 4, 7, 4, PixelToken.SKIN)
    canvas.rect(x + 4, y + 6, 4, 1, PixelToken.OUTLINE)
    canvas.rect(x + 8, blanket_y, 17, 6, PixelToken.CLOTHES)
    canvas.rect(x + 8, blanket_y + 5, 17, 1, PixelToken.CLOTHES_SHADOW)
