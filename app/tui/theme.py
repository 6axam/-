"""Semantic colors for the terminal UI, independent from scene artwork."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ColorMode(str, Enum):
    TRUECOLOR = "truecolor"
    ANSI256 = "ansi256"
    MONO = "mono"


@dataclass(frozen=True)
class ColorValue:
    truecolor: str
    ansi256: int

    def resolve(self, mode: ColorMode) -> str | None:
        if mode is ColorMode.TRUECOLOR:
            return self.truecolor
        if mode is ColorMode.ANSI256:
            return f"color({self.ansi256})"
        return None


@dataclass(frozen=True)
class CozyPalette:
    canvas: ColorValue
    panel: ColorValue
    panel_lifted: ColorValue
    border: ColorValue
    border_soft: ColorValue
    text: ColorValue
    muted: ColorValue
    accent: ColorValue
    accent_soft: ColorValue
    warm: ColorValue
    success: ColorValue
    warning: ColorValue
    danger: ColorValue

    def value(self, role: str) -> ColorValue:
        try:
            value = getattr(self, role)
        except AttributeError as exc:
            raise KeyError(f"Unknown TUI color role: {role}") from exc
        if not isinstance(value, ColorValue):
            raise KeyError(f"Unknown TUI color role: {role}")
        return value


COZY_PALETTE = CozyPalette(
    canvas=ColorValue("#120f18", 233),
    panel=ColorValue("#1d1724", 234),
    panel_lifted=ColorValue("#2a2031", 236),
    border=ColorValue("#a87391", 132),
    border_soft=ColorValue("#624d69", 60),
    text=ColorValue("#f3e9e3", 255),
    muted=ColorValue("#b9a9b8", 145),
    accent=ColorValue("#ff9fc8", 211),
    accent_soft=ColorValue("#d9a5c1", 181),
    warm=ColorValue("#ffc978", 222),
    success=ColorValue("#8ed6ae", 115),
    warning=ColorValue("#f2b866", 215),
    danger=ColorValue("#ff758f", 204),
)


def detect_color_mode(color_system: str | None, *, no_color: bool = False) -> ColorMode:
    """Normalize Rich/Textual color systems into the renderer's three tiers."""
    if no_color or color_system is None:
        return ColorMode.MONO
    normalized = str(color_system).casefold()
    if normalized in {"truecolor", "24bit", "24-bit"}:
        return ColorMode.TRUECOLOR
    if normalized in {"256", "eight_bit", "eight-bit", "ansi256"}:
        return ColorMode.ANSI256
    # A 16-color terminal gets structural emphasis without unreliable colors.
    return ColorMode.MONO


def build_app_css(palette: CozyPalette = COZY_PALETTE) -> str:
    """Keep Textual chrome and Rich renderables on one semantic palette."""
    color = lambda role: palette.value(role).truecolor
    return f"""
    Screen {{
        background: {color('canvas')};
        color: {color('text')};
        layout: vertical;
    }}
    #title {{
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: {color('accent')};
        background: {color('panel_lifted')};
    }}
    #scene {{
        width: 100%;
        height: 1fr;
        min-height: 16;
        padding: 0;
        content-align: center middle;
        border: round {color('border')};
        background: {color('panel')};
    }}
    #activity, #vitals, #events, #preview_diagnostic {{
        height: auto;
        min-height: 1;
        padding: 0 2;
        background: {color('panel')};
    }}
    #activity {{ color: {color('text')}; text-style: bold; }}
    #vitals {{ color: {color('muted')}; }}
    #events {{ color: {color('muted')}; display: none; }}
    #preview_diagnostic {{ color: {color('muted')}; display: none; }}
    #overlay {{
        display: none;
        layer: overlay;
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        align: center middle;
        padding: 2;
        border: heavy {color('accent')};
        background: {color('panel_lifted')};
        overflow-y: auto;
    }}
    Screen.compact #scene {{ width: 1fr; min-height: 2; padding: 0 1; }}
    Screen.compact #title {{ height: 1; padding: 0 1; }}
    Screen.compact #activity, Screen.compact #vitals {{ min-height: 1; padding: 0 1; }}
    Screen.compact #events {{ display: none; }}
    Footer {{ height: 1; background: {color('panel_lifted')}; }}
    """
