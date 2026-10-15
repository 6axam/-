"""Terminal-native logical pixels and half-block rendering for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from typing import Iterable

from rich.style import Style
from rich.text import Text

from app.tui.rendering import TerminalRenderer
from app.tui.theme import ColorMode, ColorValue


class PixelToken(str, Enum):
    WALL = "wall"
    WALL_SHADOW = "wall_shadow"
    FLOOR = "floor"
    FLOOR_SHADOW = "floor_shadow"
    OUTLINE = "outline"
    WOOD = "wood"
    FABRIC = "fabric"
    GLASS = "glass"
    SKY = "sky"
    GLOW = "glow"
    HAIR = "hair"
    SKIN = "skin"
    CLOTHES = "clothes"
    CLOTHES_SHADOW = "clothes_shadow"
    PLANT = "plant"
    PAPER = "paper"
    EMOTION_WARM = "emotion_warm"
    EMOTION_ANGER = "emotion_anger"
    EMOTION_SAD = "emotion_sad"
    EMOTION_ANXIETY = "emotion_anxiety"
    EMOTION_HAPPY = "emotion_happy"
    EMOTION_JEALOUS = "emotion_jealous"
    FADE = "fade"


PIXEL_PALETTE: dict[PixelToken, ColorValue] = {
    PixelToken.WALL: ColorValue("#594968", 60),
    PixelToken.WALL_SHADOW: ColorValue("#3d314b", 237),
    PixelToken.FLOOR: ColorValue("#745448", 95),
    PixelToken.FLOOR_SHADOW: ColorValue("#4e3835", 238),
    PixelToken.OUTLINE: ColorValue("#35283f", 236),
    PixelToken.WOOD: ColorValue("#9a6a4c", 137),
    PixelToken.FABRIC: ColorValue("#ae769d", 175),
    PixelToken.GLASS: ColorValue("#88b9ce", 110),
    PixelToken.SKY: ColorValue("#486686", 60),
    PixelToken.GLOW: ColorValue("#ffd58a", 222),
    PixelToken.HAIR: ColorValue("#e8d2be", 223),
    PixelToken.SKIN: ColorValue("#f2c5b3", 217),
    PixelToken.CLOTHES: ColorValue("#d586aa", 175),
    PixelToken.CLOTHES_SHADOW: ColorValue("#a65d86", 132),
    PixelToken.PLANT: ColorValue("#78a872", 71),
    PixelToken.PAPER: ColorValue("#eee0c8", 230),
    PixelToken.EMOTION_WARM: ColorValue("#ff8fba", 211),
    PixelToken.EMOTION_ANGER: ColorValue("#ff666f", 203),
    PixelToken.EMOTION_SAD: ColorValue("#77bde5", 110),
    PixelToken.EMOTION_ANXIETY: ColorValue("#d7a1ff", 183),
    PixelToken.EMOTION_HAPPY: ColorValue("#ffe06b", 221),
    PixelToken.EMOTION_JEALOUS: ColorValue("#bd8be6", 140),
    PixelToken.FADE: ColorValue("#17131e", 233),
}


@dataclass(frozen=True)
class GlowSource:
    """A local light mixed after scene layers have been composited."""

    x: float
    y: float
    radius: float
    color: ColorValue
    intensity: float = 1.0


@dataclass(frozen=True)
class Lighting:
    """Ambient tint plus local glow; neither changes the logical scene layers."""

    tint: ColorValue
    tint_strength: float
    glows: tuple[GlowSource, ...] = ()

    def apply(self, color: ColorValue, x: int, y: int) -> ColorValue:
        result = _mix(color, self.tint, self.tint_strength)
        for glow in self.glows:
            distance = sqrt((x - glow.x) ** 2 + (y - glow.y) ** 2)
            if distance < glow.radius:
                amount = glow.intensity * (1 - distance / glow.radius) ** 2
                result = _mix(result, glow.color, amount)
        return result


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _mix(base: ColorValue, tint: ColorValue, amount: float) -> ColorValue:
    amount = max(0.0, min(1.0, amount))
    if amount == 0:
        return base
    first = _rgb(base.truecolor)
    second = _rgb(tint.truecolor)
    mixed = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
    return ColorValue("#" + "".join(f"{value:02x}" for value in mixed), base.ansi256)


class PixelCanvas:
    """A transparent logical-pixel layer with clipped primitive drawing."""

    def __init__(self, width: int = 48, height: int = 28, *, fill: PixelToken | None = None) -> None:
        self.width = width
        self.height = height
        self._pixels: list[list[PixelToken | None]] = [[fill for _ in range(width)] for _ in range(height)]

    def clone(self) -> "PixelCanvas":
        copied = PixelCanvas(self.width, self.height)
        copied._pixels = [row.copy() for row in self._pixels]
        return copied

    def get(self, x: int, y: int) -> PixelToken | None:
        return self._pixels[y][x] if 0 <= x < self.width and 0 <= y < self.height else None

    def put(self, x: int, y: int, token: PixelToken | None) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self._pixels[y][x] = token

    def rect(self, x: int, y: int, width: int, height: int, token: PixelToken | None) -> None:
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.put(column, row, token)

    def line(self, x0: int, y0: int, x1: int, y1: int, token: PixelToken) -> None:
        """Bresenham line, retained mainly for small sprite and prop details."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            self.put(x0, y0, token)
            if x0 == x1 and y0 == y1:
                return
            twice_error = 2 * error
            if twice_error >= dy:
                error += dy
                x0 += sx
            if twice_error <= dx:
                error += dx
                y0 += sy

    def composite(self, *layers: "PixelCanvas") -> "PixelCanvas":
        """Paint non-transparent pixels from each layer in order onto this canvas."""
        for layer in layers:
            for y in range(min(self.height, layer.height)):
                for x in range(min(self.width, layer.width)):
                    token = layer.get(x, y)
                    if token is not None:
                        self.put(x, y, token)
        return self

    def fade_dither(self, progress: float) -> None:
        """A cheap ordered fade used during scene entry/exit transitions."""
        threshold = max(0.0, min(1.0, progress))
        bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
        for y in range(self.height):
            for x in range(self.width):
                if bayer[y % 4][x % 4] / 16 < threshold:
                    self.put(x, y, PixelToken.FADE)


class HalfBlockRenderer:
    """Render two logical pixel rows in one terminal cell using Unicode ``▀``."""

    def __init__(self, renderer: TerminalRenderer, palette: dict[PixelToken, ColorValue] | None = None) -> None:
        self.renderer = renderer
        self.palette = palette or PIXEL_PALETTE

    def render(self, canvas: PixelCanvas, lighting: Lighting) -> Text:
        output = Text()
        for y in range(0, canvas.height, 2):
            if y:
                output.append("\n")
            cells = (
                self._cell(canvas.get(x, y), canvas.get(x, y + 1), x, y, lighting)
                for x in range(canvas.width)
            )
            self._append_runs(output, cells)
        return output

    def _cell(
        self,
        top: PixelToken | None,
        bottom: PixelToken | None,
        x: int,
        y: int,
        lighting: Lighting,
    ) -> tuple[str, Style]:
        top_color = lighting.apply(self.palette[top], x, y) if top else None
        bottom_color = lighting.apply(self.palette[bottom], x, y + 1) if bottom else None
        capabilities = self.renderer.capabilities
        if capabilities.color_mode is ColorMode.MONO or not capabilities.unicode_blocks:
            return self._fallback_cell(top_color, bottom_color)
        return "▀", self.renderer.color_style(top_color, bottom_color)

    def _fallback_cell(self, top: ColorValue | None, bottom: ColorValue | None) -> tuple[str, Style]:
        brightness = _brightness(top) * 0.55 + _brightness(bottom) * 0.45
        if self.renderer.capabilities.unicode_blocks:
            glyph = " " if brightness < 0.08 else "░" if brightness < 0.34 else "▒" if brightness < 0.62 else "▓"
        else:
            glyph = " " if brightness < 0.08 else "." if brightness < 0.34 else "+" if brightness < 0.62 else "#"
        return glyph, self.renderer.color_style()

    @staticmethod
    def _append_runs(output: Text, cells: Iterable[tuple[str, Style]]) -> None:
        current_text = ""
        current_style: Style | None = None
        for glyph, style in cells:
            if current_style is None or style == current_style:
                current_text += glyph
            else:
                output.append(current_text, current_style)
                current_text = glyph
            current_style = style
        if current_text:
            output.append(current_text, current_style)


def _brightness(color: ColorValue | None) -> float:
    if color is None:
        return 0.0
    red, green, blue = _rgb(color.truecolor)
    return (red * 0.2126 + green * 0.7152 + blue * 0.0722) / 255
