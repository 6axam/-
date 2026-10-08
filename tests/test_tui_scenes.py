from datetime import datetime, timezone

import pytest

from app.tui.rendering import TerminalCapabilities, TerminalRenderer
from app.tui.scenes import (
    ActivityKind,
    PARTICLE_COLORS,
    SCENE_PALETTES,
    SceneDecision,
    SceneKind,
    render_scene,
)
from app.tui.state import AnyaSnapshot, PresenceView
from app.tui.theme import ColorMode


def scene_snapshot(emotions=None):
    return AnyaSnapshot(
        datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc),
        7,
        presence=PresenceView(phase="free", availability="available", local_time="21:00"),
        emotions=emotions or {},
    )


def decision(kind: SceneKind) -> SceneDecision:
    return SceneDecision(kind, ActivityKind.FREE, "дома", kind.value, "test")


@pytest.mark.parametrize("kind", list(SceneKind))
def test_every_scene_is_a_fixed_size_colored_diorama(kind):
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    result = render_scene(scene_snapshot(), decision(kind), frame=2, renderer=renderer)
    lines = result.plain.splitlines()
    assert len(lines) == 14
    assert {len(line) for line in lines} == {42}
    assert len(result.spans) >= 20
    assert any(span.style.color is not None or span.style.bgcolor is not None for span in result.spans)


def test_scene_layouts_and_palettes_are_visibly_distinct():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    rendered = {
        kind: render_scene(scene_snapshot(), decision(kind), frame=2, renderer=renderer).plain
        for kind in SceneKind
    }
    assert len(set(rendered.values())) == len(SceneKind)
    assert len({palette.wall.truecolor for palette in SCENE_PALETTES.values()}) == len(SceneKind)


def test_dioramas_use_block_and_box_drawing_shapes():
    result = render_scene(scene_snapshot(), decision(SceneKind.HOME_DESK), frame=2)
    assert any(glyph in result.plain for glyph in "█▓▒░▄▀")
    assert "╭" in result.plain and "╯" in result.plain


def test_emotion_particles_reach_scene_with_their_own_color():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    result = render_scene(
        scene_snapshot({"anger": 0.9}),
        decision(SceneKind.IDLE_HOME),
        frame=4,
        renderer=renderer,
    )
    assert result.plain.count("#") >= 4
    particle_color = PARTICLE_COLORS["anger"].truecolor
    assert any(span.style.color and span.style.color.name == particle_color for span in result.spans)


def test_ascii_fallback_removes_pixel_and_box_unicode():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.MONO, unicode_blocks=False))
    result = render_scene(scene_snapshot(), decision(SceneKind.HOME_BED), frame=2, renderer=renderer)
    forbidden = set("▀▄█▓▒░╭╮╰╯─│┌┐└┘━┃♥◆✦☾")
    assert not forbidden.intersection(result.plain)
    assert "+" in result.plain and "#" in result.plain


def test_compact_scene_stays_single_line_and_colored():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.ANSI256))
    result = render_scene(
        scene_snapshot({"joy": 0.8}),
        decision(SceneKind.PHONE),
        frame=2,
        compact=True,
        renderer=renderer,
    )
    assert "\n" not in result.plain
    assert "дома" in result.plain and SceneKind.PHONE.value in result.plain
    assert any(span.style.color is not None for span in result.spans)
