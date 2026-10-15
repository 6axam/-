"""First layered pixel-art scene. Other SceneKind values remain on the legacy path for now."""

from __future__ import annotations

from functools import lru_cache

from rich.text import Text

from app.tui.animation import AnimationFrame
from app.tui.pixels import GlowSource, HalfBlockRenderer, Lighting, PIXEL_PALETTE, PixelCanvas, PixelToken
from app.tui.rendering import TerminalRenderer
from app.tui.scene_objects import lamp, plant, rug, shelf, window
from app.tui.scenes import SceneKind
from app.tui.sprite import draw_anya


def lighting_for_local_time(local_time: str, intensity: float = 0.0) -> Lighting:
    """Apply the same ambient lighting model to any scene without rebuilding it."""
    try:
        hour = int(local_time.split(":", 1)[0])
    except (TypeError, ValueError):
        hour = 20
    if 6 <= hour < 10:
        tint, amount = PixelToken.GLASS, 0.16
    elif 10 <= hour < 17:
        tint, amount = PixelToken.PAPER, 0.08
    elif 17 <= hour < 21:
        tint, amount = PixelToken.GLOW, 0.14
    else:
        tint, amount = PixelToken.SKY, 0.28
    lamp_intensity = 0.72 if hour >= 17 or hour < 7 else 0.2
    return Lighting(
        PIXEL_PALETTE[tint],
        amount,
        (GlowSource(39, 8, 12, PIXEL_PALETTE[PixelToken.GLOW], lamp_intensity + intensity * 0.08),),
    )


@lru_cache(maxsize=1)
def idle_home_geometry() -> PixelCanvas:
    """Cached opaque background and props: no static rebuild occurs per animation tick."""
    canvas = PixelCanvas(fill=PixelToken.WALL)
    canvas.rect(0, 18, 48, 10, PixelToken.FLOOR)
    canvas.rect(0, 17, 48, 1, PixelToken.WALL_SHADOW)
    canvas.rect(0, 25, 48, 3, PixelToken.FLOOR_SHADOW)
    window(canvas, 3, 3)
    shelf(canvas, 33, 2)
    plant(canvas, 5, 15)
    lamp(canvas, 36, 10)
    rug(canvas, 14, 23, 20, 4)
    for x, y in ((1, 3), (22, 5), (29, 14), (11, 15)):
        canvas.put(x, y, PixelToken.WALL_SHADOW)
    return canvas


def render_idle_home(
    renderer: TerminalRenderer,
    local_time: str,
    frame: AnimationFrame,
) -> Text:
    """Compose cached geometry, dynamic sprite and effects, then half-block render it."""
    canvas = idle_home_geometry().clone()
    draw_anya(canvas, 18, 4, frame.sprite)
    _emotion_effect(canvas, frame)
    # First pixel scenes use an entrance fade. Cross-scene dither is activated as
    # the remaining scenes migrate to this renderer.
    if frame.transition_from is not None and frame.transition_to is SceneKind.IDLE_HOME:
        canvas.fade_dither(max(0.0, 1 - frame.transition_progress * 2))
    lighting = lighting_for_local_time(local_time, frame.displayed_intensity)
    return HalfBlockRenderer(renderer).render(canvas, lighting)


def _emotion_effect(canvas: PixelCanvas, frame: AnimationFrame) -> None:
    if frame.displayed_intensity < 0.18:
        return
    token = {
        "warm": PixelToken.EMOTION_WARM,
        "anger": PixelToken.EMOTION_ANGER,
        "sadness": PixelToken.EMOTION_SAD,
        "anxiety": PixelToken.EMOTION_ANXIETY,
        "happy": PixelToken.EMOTION_HAPPY,
        "jealousy": PixelToken.EMOTION_JEALOUS,
    }[frame.dominant_emotion]
    count = min(4, 1 + int(frame.displayed_intensity * 4))
    points = ((25, 2), (20, 3), (30, 4), (17, 6))
    for x, y in points[:count]:
        canvas.rect(x, y, 1, 1, token)
