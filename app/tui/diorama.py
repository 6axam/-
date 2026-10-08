"""Small colored terminal canvas for block-based room dioramas."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from app.tui.rendering import TerminalRenderer
from app.tui.theme import ColorValue


@dataclass(frozen=True)
class Cell:
    glyph: str = " "
    foreground: ColorValue | None = None
    background: ColorValue | None = None
    bold: bool = False


class DioramaCanvas:
    """Fixed-width character canvas; all drawing operations clip safely."""

    def __init__(self, width: int = 42, height: int = 14, *, background: ColorValue | None = None) -> None:
        self.width = width
        self.height = height
        self._cells = [[Cell(background=background) for _ in range(width)] for _ in range(height)]

    def put(
        self,
        x: int,
        y: int,
        glyph: str,
        foreground: ColorValue | None = None,
        background: ColorValue | None = None,
        *,
        bold: bool = False,
    ) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._cells[y][x] = Cell(glyph[:1] or " ", foreground, background, bold)

    def text(
        self,
        x: int,
        y: int,
        value: str,
        foreground: ColorValue | None = None,
        background: ColorValue | None = None,
        *,
        bold: bool = False,
    ) -> None:
        for offset, glyph in enumerate(value):
            self.put(x + offset, y, glyph, foreground, background, bold=bold)

    def fill(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        glyph: str = " ",
        foreground: ColorValue | None = None,
        background: ColorValue | None = None,
    ) -> None:
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.put(column, row, glyph, foreground, background)

    def hline(
        self,
        x: int,
        y: int,
        width: int,
        glyph: str,
        foreground: ColorValue | None = None,
        background: ColorValue | None = None,
    ) -> None:
        self.fill(x, y, width, 1, glyph, foreground, background)

    def box(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        foreground: ColorValue,
        background: ColorValue | None = None,
    ) -> None:
        if width < 2 or height < 2:
            return
        self.text(x, y, "╭" + "─" * (width - 2) + "╮", foreground, background)
        for row in range(y + 1, y + height - 1):
            self.put(x, row, "│", foreground, background)
            self.put(x + width - 1, row, "│", foreground, background)
        self.text(x, y + height - 1, "╰" + "─" * (width - 2) + "╯", foreground, background)

    def render(self, renderer: TerminalRenderer) -> Text:
        output = Text()
        for y, row in enumerate(self._cells):
            if y:
                output.append("\n")
            run_text = ""
            run_style = None
            for cell in row:
                glyph = renderer.sanitize(cell.glyph)
                style = renderer.color_style(cell.foreground, cell.background, bold=cell.bold)
                if run_style is None or style == run_style:
                    run_text += glyph
                else:
                    output.append(run_text, run_style)
                    run_text = glyph
                run_style = style
            if run_text:
                output.append(run_text, run_style)
        return output
