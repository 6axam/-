import pytest
from rich.text import Text

from app.tui.rendering import StyledSpan, TerminalCapabilities, TerminalRenderer
from app.tui.theme import COZY_PALETTE, ColorMode, build_app_css, detect_color_mode


@pytest.mark.parametrize(
    ("reported", "no_color", "expected"),
    [
        ("truecolor", False, ColorMode.TRUECOLOR),
        ("24bit", False, ColorMode.TRUECOLOR),
        ("256", False, ColorMode.ANSI256),
        ("eight_bit", False, ColorMode.ANSI256),
        ("standard", False, ColorMode.MONO),
        (None, False, ColorMode.MONO),
        ("truecolor", True, ColorMode.MONO),
    ],
)
def test_color_capability_detection(reported, no_color, expected):
    assert detect_color_mode(reported, no_color=no_color) is expected


def test_truecolor_renderer_uses_semantic_palette():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    result = renderer.line(
        (
            StyledSpan("Аня", "accent", bold=True),
            StyledSpan(" дома", "muted"),
        )
    )
    assert isinstance(result, Text)
    assert result.plain == "Аня дома"
    assert result.spans[0].style.color.name == COZY_PALETTE.accent.truecolor
    assert result.spans[0].style.bold is True
    assert result.spans[1].style.color.name == COZY_PALETTE.muted.truecolor


def test_ansi256_renderer_uses_indexed_colors():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.ANSI256))
    style = renderer.style("warm", "panel")
    assert style.color.number == COZY_PALETTE.warm.ansi256
    assert style.bgcolor.number == COZY_PALETTE.panel.ansi256


def test_mono_renderer_keeps_structure_without_color():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.MONO))
    result = renderer.heading("СОСТОЯНИЕ")
    style = result.spans[0].style
    assert style.color is None and style.bgcolor is None
    assert style.bold is True
    assert style.reverse is True


def test_non_unicode_terminal_gets_ascii_glyph_fallback():
    capabilities = TerminalCapabilities.detect("256", encoding="ascii")
    renderer = TerminalRenderer(capabilities)
    result = renderer.line((StyledSpan("╭▀█♥╯", "accent"),))
    assert capabilities.unicode_blocks is False
    assert result.plain == "+^#*+"


def test_utf8_terminal_preserves_pixel_and_box_glyphs():
    renderer = TerminalRenderer(TerminalCapabilities.detect("truecolor", encoding="UTF-8"))
    assert renderer.sanitize("╭▀█♥╯") == "╭▀█♥╯"


def test_unknown_color_role_fails_fast():
    renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    with pytest.raises(KeyError, match="Unknown TUI color role"):
        renderer.style("not-a-role")


def test_textual_css_is_derived_from_same_palette():
    css = build_app_css()
    assert COZY_PALETTE.canvas.truecolor in css
    assert COZY_PALETTE.accent.truecolor in css
    assert COZY_PALETTE.border.truecolor in css
