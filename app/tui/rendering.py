"""Capability-aware Rich primitives used by all future scene renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from rich.style import Style
from rich.text import Text

from app.tui.theme import COZY_PALETTE, ColorMode, CozyPalette, detect_color_mode


ASCII_FALLBACK = str.maketrans(
    {
        "▀": "^", "▄": "_", "█": "#", "▓": "#", "▒": "+", "░": ".",
        "╭": "+", "╮": "+", "╰": "+", "╯": "+", "─": "-", "│": "|",
        "┌": "+", "┐": "+", "└": "+", "┘": "+", "━": "=", "┃": "|",
        "♥": "*", "◆": "*", "✦": "*", "☾": "c",
    }
)


@dataclass(frozen=True)
class TerminalCapabilities:
    color_mode: ColorMode
    unicode_blocks: bool = True

    @classmethod
    def detect(
        cls,
        color_system: str | None,
        *,
        encoding: str | None = "utf-8",
        no_color: bool = False,
    ) -> "TerminalCapabilities":
        normalized_encoding = (encoding or "").replace("-", "").casefold()
        unicode_blocks = normalized_encoding in {"utf8", "utf_8"}
        return cls(detect_color_mode(color_system, no_color=no_color), unicode_blocks)


@dataclass(frozen=True)
class StyledSpan:
    text: str
    foreground: str = "text"
    background: str | None = None
    bold: bool = False
    dim: bool = False
    reverse_in_mono: bool = False


class TerminalRenderer:
    """Turn semantic spans into Rich Text with deterministic fallbacks."""

    def __init__(
        self,
        capabilities: TerminalCapabilities,
        palette: CozyPalette = COZY_PALETTE,
    ) -> None:
        self.capabilities = capabilities
        self.palette = palette

    def style(
        self,
        foreground: str = "text",
        background: str | None = None,
        *,
        bold: bool = False,
        dim: bool = False,
        reverse_in_mono: bool = False,
    ) -> Style:
        mode = self.capabilities.color_mode
        return Style(
            color=self.palette.value(foreground).resolve(mode),
            bgcolor=self.palette.value(background).resolve(mode) if background else None,
            bold=bold,
            dim=dim,
            reverse=reverse_in_mono and mode is ColorMode.MONO,
        )

    def sanitize(self, value: str) -> str:
        return value if self.capabilities.unicode_blocks else value.translate(ASCII_FALLBACK)

    def line(self, spans: Sequence[StyledSpan]) -> Text:
        result = Text()
        for span in spans:
            result.append(
                self.sanitize(span.text),
                self.style(
                    span.foreground,
                    span.background,
                    bold=span.bold,
                    dim=span.dim,
                    reverse_in_mono=span.reverse_in_mono,
                ),
            )
        return result

    def lines(self, rows: Iterable[Sequence[StyledSpan]]) -> Text:
        result = Text()
        for index, row in enumerate(rows):
            if index:
                result.append("\n")
            result.append_text(self.line(row))
        return result

    def heading(self, value: str) -> Text:
        return self.line((StyledSpan(value, "accent", bold=True, reverse_in_mono=True),))

    def label_value(self, label: str, value: str, *, value_role: str = "text") -> Text:
        return self.line(
            (
                StyledSpan(label, "muted"),
                StyledSpan(value, value_role, bold=True),
            )
        )
