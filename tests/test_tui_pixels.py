from datetime import datetime, timezone

import pytest

from app.tui.animation import AnimationController, TRANSITION_SECONDS
from app.tui.pixel_scenes import idle_home_geometry, lighting_for_local_time, render_idle_home
from app.tui.pixels import HalfBlockRenderer, Lighting, PIXEL_PALETTE, PixelCanvas, PixelToken
from app.tui.rendering import TerminalCapabilities, TerminalRenderer
from app.tui.scenes import ActivityKind, SceneDecision, SceneKind, render_scene
from app.tui.sprite import Expression, Pose, SpriteAnimation, SpriteFrame, draw_anya
from app.tui.state import AnyaSnapshot, PresenceView
from app.tui.theme import ColorMode


def renderer(mode=ColorMode.TRUECOLOR, unicode_blocks=True):
    return TerminalRenderer(TerminalCapabilities(mode, unicode_blocks=unicode_blocks))


def decision(kind=SceneKind.IDLE_HOME):
    return SceneDecision(kind, ActivityKind.FREE, "дома", "свободное время", "test")


def snapshot(*, emotions=None, local_time="20:00"):
    return AnyaSnapshot(
        datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc),
        7,
        presence=PresenceView(phase="free", availability="available", local_time=local_time),
        emotions=emotions or {},
    )


def test_half_block_renderer_encodes_two_logical_rows_in_one_terminal_row():
    canvas = PixelCanvas(4, 4, fill=PixelToken.WALL)
    canvas.put(1, 0, PixelToken.GLOW)
    output = HalfBlockRenderer(renderer()).render(canvas, Lighting(PIXEL_PALETTE[PixelToken.WALL], 0))
    lines = output.plain.splitlines()
    assert len(lines) == 2
    assert {len(line) for line in lines} == {4}
    assert set("▀") == {character for line in lines for character in line}
    # The whole row is grouped into a tiny number of Rich style runs, never one span per pixel.
    assert len(output.spans) <= 4


def test_logical_canvas_composites_transparent_layers_and_clips_primitives():
    base = PixelCanvas(4, 4, fill=PixelToken.WALL)
    overlay = PixelCanvas(4, 4)
    overlay.put(1, 1, PixelToken.GLOW)
    overlay.rect(-5, -5, 3, 3, PixelToken.EMOTION_HAPPY)
    base.composite(overlay)
    assert base.get(1, 1) is PixelToken.GLOW
    assert base.get(0, 0) is PixelToken.WALL


def test_mono_and_non_unicode_pixel_renderers_degrade_to_readable_density():
    canvas = PixelCanvas(2, 2, fill=PixelToken.GLOW)
    lighting = Lighting(PIXEL_PALETTE[PixelToken.WALL], 0)
    mono = HalfBlockRenderer(renderer(ColorMode.MONO)).render(canvas, lighting)
    ascii_output = HalfBlockRenderer(renderer(ColorMode.ANSI256, unicode_blocks=False)).render(canvas, lighting)
    assert "▀" not in mono.plain
    assert "▀" not in ascii_output.plain
    assert len(ascii_output.plain) == 2
    assert set(ascii_output.plain) <= {"#", "+", ".", " "}


def test_sprite_clips_safely_at_canvas_edges():
    canvas = PixelCanvas(10, 10, fill=PixelToken.WALL)
    draw_anya(canvas, -6, -7, SpriteFrame(Pose.STANDING, Expression.NEUTRAL, SpriteAnimation.IDLE))
    assert canvas.get(0, 0) is not None


@pytest.mark.parametrize(
    ("kind", "expected_pose"),
    [
        (SceneKind.IDLE_HOME, Pose.STANDING),
        (SceneKind.HOME_DESK, Pose.SITTING_DESK),
        (SceneKind.PHONE, Pose.SITTING_PHONE),
        (SceneKind.SLEEPING, Pose.SLEEPING),
        (SceneKind.BUSY_AWAY, Pose.WALKING),
    ],
)
def test_animation_maps_scene_kind_to_appropriate_sprite_pose(kind, expected_pose):
    controller = AnimationController(seed=3)
    current = snapshot()
    resolved = decision(kind)
    controller.sync(current, resolved, 0.0)
    assert controller.advance(resolved, 0.0).sprite.pose is expected_pose


def test_animation_is_seeded_dt_based_and_blink_is_deterministic():
    first = AnimationController(seed=7)
    second = AnimationController(seed=7)
    current = snapshot(emotions={"joy": 0.8})
    resolved = decision()
    for controller in (first, second):
        controller.sync(current, resolved, 0.0)
    frames_one = [first.advance(resolved, now) for now in (0.0, 1.0, 2.0, 3.0, 3.12)]
    frames_two = [second.advance(resolved, now) for now in (0.0, 1.0, 2.0, 3.0, 3.12)]
    assert frames_one == frames_two
    assert any(frame.sprite.blink for frame in frames_one)
    assert frames_one[-1].displayed_intensity > frames_one[1].displayed_intensity > 0


def test_tween_converges_without_mutating_persisted_snapshot():
    controller = AnimationController(seed=1)
    low = snapshot(emotions={"joy": 0.1})
    high = snapshot(emotions={"joy": 0.9})
    resolved = decision()
    controller.sync(low, resolved, 0.0)
    controller.advance(resolved, 0.0)
    controller.sync(high, resolved, 0.0)
    early = controller.advance(resolved, 0.16)
    later = controller.advance(resolved, 0.80)
    assert 0 < early.displayed_intensity < later.displayed_intensity < 0.9
    assert high.emotions == {"joy": 0.9}


def test_scene_transition_uses_the_requested_half_second_window():
    controller = AnimationController(seed=1)
    current = snapshot()
    idle = decision(SceneKind.IDLE_HOME)
    phone = decision(SceneKind.PHONE)
    controller.sync(current, idle, 0.0)
    controller.advance(idle, 0.0)
    controller.sync(current, phone, 0.0)
    midpoint = controller.advance(phone, TRANSITION_SECONDS / 2)
    completed = controller.advance(phone, TRANSITION_SECONDS + 0.01)
    assert midpoint.transition_from is SceneKind.IDLE_HOME
    assert midpoint.transition_progress == pytest.approx(0.5)
    assert completed.transition_progress == 1.0


def test_idle_scene_uses_cached_geometry_and_time_of_day_post_lighting():
    assert idle_home_geometry() is idle_home_geometry()
    controller = AnimationController(seed=2)
    current = snapshot(emotions={"anger": 0.8}, local_time="08:00")
    resolved = decision()
    controller.sync(current, resolved, 0.0)
    frame = controller.advance(resolved, 0.0)
    morning = render_idle_home(renderer(), "08:00", frame)
    night = render_idle_home(renderer(), "23:00", frame)
    assert len(morning.plain.splitlines()) == 14
    assert {len(line) for line in morning.plain.splitlines()} == {48}
    assert morning.plain == night.plain
    assert morning.spans != night.spans


def test_primary_scene_renderer_routes_idle_home_to_half_block_engine():
    controller = AnimationController(seed=2)
    current = snapshot()
    resolved = decision()
    controller.sync(current, resolved, 0.0)
    output = render_scene(current, resolved, renderer=renderer(), animation=controller.advance(resolved, 0.0))
    assert len(output.plain.splitlines()) == 14
    assert set("▀") == {character for line in output.plain.splitlines() for character in line}
