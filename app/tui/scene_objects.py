"""Reusable logical-pixel furniture and room props for layered TUI scenes."""

from __future__ import annotations

from app.tui.pixels import PixelCanvas, PixelToken


def window(canvas: PixelCanvas, x: int, y: int, *, night: bool = False) -> None:
    canvas.rect(x, y, 14, 10, PixelToken.OUTLINE)
    canvas.rect(x + 1, y + 1, 12, 8, PixelToken.SKY if night else PixelToken.GLASS)
    canvas.rect(x + 6, y + 1, 1, 8, PixelToken.OUTLINE)
    canvas.rect(x + 1, y + 4, 12, 1, PixelToken.OUTLINE)
    canvas.rect(x + 2, y + 2, 2, 1, PixelToken.GLOW)


def plant(canvas: PixelCanvas, x: int, y: int) -> None:
    canvas.rect(x + 3, y, 2, 2, PixelToken.PLANT)
    canvas.rect(x + 1, y + 2, 6, 3, PixelToken.PLANT)
    canvas.rect(x + 3, y + 4, 2, 4, PixelToken.WOOD)
    canvas.rect(x + 1, y + 8, 6, 2, PixelToken.WOOD)


def lamp(canvas: PixelCanvas, x: int, y: int) -> None:
    canvas.rect(x + 1, y, 6, 3, PixelToken.GLOW)
    canvas.rect(x, y + 3, 8, 1, PixelToken.OUTLINE)
    canvas.rect(x + 3, y + 4, 2, 6, PixelToken.WOOD)
    canvas.rect(x + 1, y + 10, 6, 1, PixelToken.WOOD)


def shelf(canvas: PixelCanvas, x: int, y: int) -> None:
    canvas.rect(x, y, 12, 2, PixelToken.WOOD)
    canvas.rect(x, y + 2, 2, 10, PixelToken.WOOD)
    canvas.rect(x + 10, y + 2, 2, 10, PixelToken.WOOD)
    canvas.rect(x + 2, y + 5, 8, 1, PixelToken.WOOD)
    canvas.rect(x + 3, y + 2, 2, 3, PixelToken.FABRIC)
    canvas.rect(x + 6, y + 2, 2, 3, PixelToken.PAPER)
    canvas.rect(x + 3, y + 7, 5, 2, PixelToken.FABRIC)


def rug(canvas: PixelCanvas, x: int, y: int, width: int, height: int) -> None:
    canvas.rect(x, y, width, height, PixelToken.FABRIC)
    canvas.rect(x + 2, y + 2, max(0, width - 4), max(0, height - 4), PixelToken.CLOTHES_SHADOW)
