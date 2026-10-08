"""Pure scene selection and ASCII rendering for Anya's terminal room."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rich.text import Text

from app.tui.diorama import DioramaCanvas
from app.tui.rendering import StyledSpan, TerminalCapabilities, TerminalRenderer
from app.tui.state import AnyaSnapshot, aggregate_emotions, particle_budget
from app.tui.theme import ColorMode, ColorValue


class SceneKind(str, Enum):
    HOME_BED = "home_bed"
    HOME_DESK = "home_desk"
    COLLEGE_DESK = "college_desk"
    SLEEPING = "sleeping"
    PHONE = "phone"
    IDLE_HOME = "idle_home"
    BUSY_AWAY = "busy_away"


class ActivityKind(str, Enum):
    """Stable presentation vocabulary; new durable activities extend this enum."""

    UNKNOWN = "unknown"
    FREE = "free"
    SLEEPING = "sleeping"
    STUDYING = "studying"
    PHONE = "phone"
    COMPUTER = "computer"
    BUSY = "busy"
    AWAY = "away"


@dataclass(frozen=True)
class SceneDecision:
    kind: SceneKind
    activity_kind: ActivityKind
    location: str
    activity: str
    evidence: str


PHONE_WORDS = ("телефон", "phone", "telegram", "твиттер", "twitter", "reddit")
DESK_WORDS = ("комп", "ноут", "computer", "код", "игр", "рису", "пишу")


def classify_event_activity(title: str | None) -> ActivityKind:
    """Conservative registry for known event wording; easy to extend later."""
    normalized = (title or "").casefold()
    if normalized and any(word in normalized for word in PHONE_WORDS):
        return ActivityKind.PHONE
    if normalized and any(word in normalized for word in DESK_WORDS):
        return ActivityKind.COMPUTER
    return ActivityKind.UNKNOWN


def resolve_scene(snapshot: AnyaSnapshot, idle_variant: int = 0) -> SceneDecision:
    """Resolve only known state; idle_variant changes visuals, not canonical activity."""
    presence = snapshot.presence
    known_activity = classify_event_activity(presence.event_title)
    if presence.sleeping or presence.availability == "sleep":
        return SceneDecision(SceneKind.SLEEPING, ActivityKind.SLEEPING, "дома", "спит", "sleep schedule")
    if presence.phase == "college":
        return SceneDecision(SceneKind.COLLEGE_DESK, ActivityKind.STUDYING, "колледж", "на занятиях", "college schedule")
    if known_activity is ActivityKind.PHONE:
        return SceneDecision(SceneKind.PHONE, ActivityKind.PHONE, "не указано", presence.event_title or "телефон", "daily event")
    if known_activity is ActivityKind.COMPUTER:
        return SceneDecision(SceneKind.HOME_DESK, ActivityKind.COMPUTER, "дома", presence.event_title or "за столом", "daily event")
    if presence.availability in {"busy", "away"}:
        kind = ActivityKind.AWAY if presence.availability == "away" else ActivityKind.BUSY
        return SceneDecision(SceneKind.BUSY_AWAY, kind, "вне сцены", presence.event_title or presence.availability, "daily event")
    if presence.phase == "free" and presence.availability == "available":
        kind = SceneKind.HOME_BED if idle_variant % 2 == 0 else SceneKind.IDLE_HOME
        return SceneDecision(kind, ActivityKind.FREE, "дома", "свободное время", "visual idle variation")
    return SceneDecision(SceneKind.BUSY_AWAY, ActivityKind.UNKNOWN, "неизвестно", presence.event_title or "нет данных", "fallback")


def _c(truecolor: str, ansi256: int) -> ColorValue:
    return ColorValue(truecolor, ansi256)


@dataclass(frozen=True)
class ScenePalette:
    wall: ColorValue
    wall_shadow: ColorValue
    floor: ColorValue
    floor_dark: ColorValue
    outline: ColorValue
    wood: ColorValue
    fabric: ColorValue
    sky: ColorValue
    glow: ColorValue
    accent: ColorValue
    hair: ColorValue = _c("#ead8c4", 223)
    skin: ColorValue = _c("#f2c7b5", 217)
    clothes: ColorValue = _c("#d78ba7", 175)


SCENE_PALETTES = {
    SceneKind.HOME_BED: ScenePalette(
        _c("#33243d", 237), _c("#251b30", 235), _c("#5a3b45", 95), _c("#3b2934", 238),
        _c("#d7a7bd", 181), _c("#8d5e48", 131), _c("#b96f8e", 168), _c("#263b61", 24),
        _c("#ffd27d", 222), _c("#ef9fbd", 211),
    ),
    SceneKind.IDLE_HOME: ScenePalette(
        _c("#3d3247", 238), _c("#2b2435", 236), _c("#705044", 95), _c("#49372f", 239),
        _c("#d7b8c6", 181), _c("#986d4c", 137), _c("#9b7bb2", 139), _c("#5b88a5", 67),
        _c("#ffe0a3", 223), _c("#9ed6bd", 151),
    ),
    SceneKind.HOME_DESK: ScenePalette(
        _c("#202b3b", 235), _c("#17202e", 234), _c("#473a35", 238), _c("#302925", 236),
        _c("#86b4c8", 110), _c("#805a43", 95), _c("#597087", 67), _c("#182b4c", 17),
        _c("#72d7e8", 116), _c("#c194df", 176), clothes=_c("#6d92bc", 67),
    ),
    SceneKind.COLLEGE_DESK: ScenePalette(
        _c("#d2c9b8", 251), _c("#b7ab98", 249), _c("#8c765e", 137), _c("#665748", 95),
        _c("#41474b", 238), _c("#926d49", 137), _c("#6d8291", 67), _c("#83b5d1", 110),
        _c("#f7dd91", 223), _c("#b45f72", 131), clothes=_c("#566f91", 67),
    ),
    SceneKind.SLEEPING: ScenePalette(
        _c("#161827", 233), _c("#10121e", 232), _c("#252139", 235), _c("#181726", 233),
        _c("#77769b", 103), _c("#4b3545", 238), _c("#5a527d", 60), _c("#111c3d", 17),
        _c("#c9d4ff", 189), _c("#827ab7", 103), clothes=_c("#69628f", 60),
    ),
    SceneKind.PHONE: ScenePalette(
        _c("#382735", 237), _c("#281d2a", 235), _c("#644537", 95), _c("#453128", 238),
        _c("#db9eb9", 175), _c("#89563f", 131), _c("#aa6e87", 132), _c("#243b5b", 24),
        _c("#78dfff", 117), _c("#ff9fc8", 211),
    ),
    SceneKind.BUSY_AWAY: ScenePalette(
        _c("#6f91ad", 67), _c("#526f89", 60), _c("#63636b", 59), _c("#404149", 238),
        _c("#20242b", 235), _c("#725747", 95), _c("#7c8797", 103), _c("#79a9c7", 110),
        _c("#ffd27d", 222), _c("#d4778f", 168), clothes=_c("#8f5870", 132),
    ),
}


PARTICLE_GLYPHS = {"warm": "♥", "anger": "#", "sadness": "·", "anxiety": "~", "happy": "✦", "jealousy": "◆"}
PARTICLE_COLORS = {
    "warm": _c("#ff92b8", 211),
    "anger": _c("#ff5b5b", 203),
    "sadness": _c("#72b7e6", 110),
    "anxiety": _c("#d39cff", 183),
    "happy": _c("#ffd75e", 221),
    "jealousy": _c("#b78be4", 140),
}


def _room_shell(canvas: DioramaCanvas, palette: ScenePalette) -> None:
    canvas.fill(0, 0, canvas.width, 10, " ", background=palette.wall)
    canvas.fill(0, 9, canvas.width, 1, "▄", palette.wall_shadow, palette.wall)
    canvas.fill(0, 10, canvas.width, 4, "░", palette.floor_dark, palette.floor)
    canvas.hline(0, 13, canvas.width, "▀", palette.floor_dark, palette.floor)
    for x, y in ((1, 1), (17, 3), (39, 2), (21, 7), (7, 8)):
        canvas.put(x, y, "·", palette.wall_shadow, palette.wall)


def _window(canvas: DioramaCanvas, palette: ScenePalette, x: int, y: int, *, night: bool = False) -> None:
    canvas.fill(x + 1, y + 1, 10, 4, " ", background=palette.sky)
    canvas.box(x, y, 12, 6, palette.outline, palette.wall_shadow)
    canvas.hline(x + 1, y + 3, 10, "─", palette.outline, palette.sky)
    canvas.fill(x + 6, y + 1, 1, 4, "│", palette.outline, palette.sky)
    canvas.put(x + 2, y + 1, "✦" if night else "·", palette.glow, palette.sky, bold=True)
    canvas.put(x + 9, y + 2, "·", palette.glow, palette.sky)


def _plant(canvas: DioramaCanvas, palette: ScenePalette, x: int, y: int) -> None:
    leaf = _c("#72a56f", 71)
    canvas.text(x + 1, y, "▄", leaf)
    canvas.text(x, y + 1, "▄█▄", leaf)
    canvas.text(x + 1, y + 2, "│", palette.wood)
    canvas.text(x, y + 3, "▟█▙", palette.wood)


def _standing_anya(canvas: DioramaCanvas, palette: ScenePalette, x: int, y: int, *, blink: bool = False) -> tuple[int, int]:
    canvas.text(x + 1, y, "▄██▄", palette.hair, palette.wall_shadow, bold=True)
    canvas.text(x, y + 1, "▐    ▌", palette.hair, palette.skin)
    canvas.put(x + 2, y + 1, "─" if blink else "•", palette.outline, palette.skin, bold=True)
    canvas.put(x + 4, y + 1, "─" if blink else "•", palette.outline, palette.skin, bold=True)
    canvas.text(x + 1, y + 2, "╰─·─╯", palette.outline, palette.skin)
    canvas.text(x + 1, y + 3, "▐███▌", palette.clothes, palette.clothes)
    canvas.text(x + 1, y + 4, "▐███▌", palette.clothes, palette.clothes)
    canvas.text(x + 1, y + 5, "▀   ▀", palette.outline)
    return x + 3, y - 1


def _sitting_anya(canvas: DioramaCanvas, palette: ScenePalette, x: int, y: int, *, phone: bool = False, blink: bool = False) -> tuple[int, int]:
    anchor = _standing_anya(canvas, palette, x, y, blink=blink)
    canvas.text(x + 1, y + 4, "▐██▙▄", palette.clothes, palette.clothes)
    canvas.text(x + 1, y + 5, "▀  ▀▀", palette.outline)
    if phone:
        canvas.text(x + 6, y + 3, "▟█▙", palette.glow, palette.outline, bold=True)
        canvas.put(x + 7, y + 3, "▪", palette.sky, palette.glow, bold=True)
    return anchor


def _home_bed(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    _window(canvas, palette, 2, 1, night=True)
    _plant(canvas, palette, 16, 6)
    canvas.text(21, 4, "╭─╮", palette.outline)
    canvas.text(21, 5, "│◆│", palette.glow, palette.wall_shadow)
    canvas.text(20, 6, "─┴─", palette.wood)
    canvas.fill(24, 8, 17, 4, "░", palette.fabric, palette.wall_shadow)
    canvas.text(24, 8, "╭───────────────╮", palette.outline, palette.fabric)
    canvas.text(24, 11, "╰───────────────╯", palette.outline, palette.fabric)
    canvas.text(26, 9, "▓▓▓", palette.glow, palette.fabric)
    canvas.hline(3, 12, 17, "▄", palette.accent, palette.floor)
    return _sitting_anya(canvas, palette, 29, 3, blink=frame % 23 == 0)


def _idle_home(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    _window(canvas, palette, 2, 1)
    canvas.box(31, 1, 9, 6, palette.outline, palette.wall_shadow)
    canvas.text(33, 2, "▰ ▰", palette.accent)
    canvas.text(33, 4, "▰ ▰", palette.glow)
    canvas.fill(3, 10, 16, 3, "▒", palette.fabric, palette.floor_dark)
    canvas.text(3, 9, "╭──────────────╮", palette.outline, palette.fabric)
    _plant(canvas, palette, 36, 8)
    canvas.hline(20, 12, 14, "▄", palette.accent, palette.floor)
    return _standing_anya(canvas, palette, 22, 4, blink=frame % 23 == 0)


def _home_desk(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    _window(canvas, palette, 29, 1, night=True)
    canvas.fill(2, 2, 9, 1, "▄", palette.wood)
    canvas.text(3, 1, "▥ ▥ ▥", palette.accent)
    canvas.box(3, 5, 14, 5, palette.outline, palette.wall_shadow)
    canvas.fill(4, 6, 12, 3, "░", palette.glow, palette.sky)
    canvas.text(6, 7, ">_ anya", palette.outline, palette.sky, bold=True)
    canvas.text(9, 10, "┴", palette.outline)
    canvas.hline(1, 11, 24, "█", palette.wood, palette.floor_dark)
    canvas.text(18, 9, "▥▥", palette.accent)
    canvas.text(18, 10, "▥▥", palette.glow)
    canvas.text(27, 10, "╭──╮", palette.outline, palette.fabric)
    canvas.text(27, 11, "╰──╯", palette.outline, palette.fabric)
    return _sitting_anya(canvas, palette, 26, 4, blink=frame % 23 == 0)


def _college(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    canvas.fill(1, 1, 23, 6, "░", _c("#adc7b1", 151), _c("#365446", 23))
    canvas.box(1, 1, 23, 6, palette.outline, _c("#365446", 23))
    canvas.text(4, 3, "f(x) = x² + 1", _c("#e8eadb", 254), _c("#365446", 23))
    _window(canvas, palette, 29, 1)
    canvas.hline(3, 10, 19, "█", palette.wood, palette.floor_dark)
    canvas.text(5, 9, "▱ конспект ▱", palette.accent)
    canvas.text(26, 11, "▟██▙", palette.fabric)
    canvas.text(27, 12, "███", palette.fabric)
    return _sitting_anya(canvas, palette, 27, 5, blink=frame % 23 == 0)


def _sleeping(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    _window(canvas, palette, 3, 1, night=True)
    canvas.text(7, 2, "☾", palette.glow, palette.sky, bold=True)
    canvas.fill(3, 8, 36, 4, "░", palette.fabric, palette.wall_shadow)
    canvas.text(3, 7, "╭──────────────────────────────────╮", palette.outline, palette.fabric)
    canvas.text(3, 11, "╰──────────────────────────────────╯", palette.outline, palette.fabric)
    canvas.text(6, 8, "▓▓▓▓▓", palette.glow, palette.fabric)
    canvas.text(10, 8, "▄████▄", palette.hair, palette.fabric)
    canvas.text(9, 9, "▐ -  - ▌", palette.outline, palette.skin)
    canvas.text(17, 9, "██████████████████", palette.clothes, palette.clothes)
    canvas.text(19, 6, "z", palette.glow, bold=True)
    canvas.text(22, 4, "Z", palette.glow, bold=True)
    canvas.put(26, 2, "·", palette.glow)
    return 16, 6


def _phone(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    _room_shell(canvas, palette)
    _window(canvas, palette, 2, 1, night=True)
    canvas.fill(3, 9, 22, 3, "▒", palette.fabric, palette.wall_shadow)
    canvas.text(3, 8, "╭────────────────────╮", palette.outline, palette.fabric)
    canvas.text(3, 11, "╰────────────────────╯", palette.outline, palette.fabric)
    canvas.text(32, 5, "╭─╮", palette.outline)
    canvas.text(32, 6, "│◆│", palette.glow, palette.wall_shadow)
    canvas.text(31, 7, "─┴─", palette.wood)
    canvas.hline(28, 12, 11, "▄", palette.accent, palette.floor)
    return _sitting_anya(canvas, palette, 13, 4, phone=True, blink=frame % 23 == 0)


def _busy_away(canvas: DioramaCanvas, palette: ScenePalette, frame: int) -> tuple[int, int]:
    canvas.fill(0, 0, canvas.width, 8, " ", background=palette.sky)
    canvas.text(3, 1, "▄▄▄▄", _c("#dce7ef", 255), palette.sky)
    canvas.text(29, 2, "▄▄▄▄▄▄", _c("#dce7ef", 255), palette.sky)
    canvas.fill(0, 6, 10, 5, "▒", palette.wall_shadow, palette.wall)
    canvas.fill(32, 5, 10, 6, "▒", palette.wall_shadow, palette.wall)
    for x in (2, 6, 34, 38):
        canvas.put(x, 7, "▪", palette.glow, palette.wall_shadow)
        canvas.put(x, 9, "▪", palette.glow, palette.wall_shadow)
    canvas.fill(0, 11, canvas.width, 3, "░", palette.floor_dark, palette.floor)
    canvas.hline(0, 11, canvas.width, "═", palette.outline, palette.floor)
    canvas.text(13, 9, "│", palette.outline)
    canvas.text(12, 7, "╭◆╮", palette.glow)
    canvas.text(12, 10, "┴", palette.outline)
    canvas.text(28, 12, "→", palette.glow, bold=True)
    return _standing_anya(canvas, palette, 19, 5, blink=frame % 23 == 0)


SCENE_PAINTERS = {
    SceneKind.HOME_BED: _home_bed,
    SceneKind.IDLE_HOME: _idle_home,
    SceneKind.HOME_DESK: _home_desk,
    SceneKind.COLLEGE_DESK: _college,
    SceneKind.SLEEPING: _sleeping,
    SceneKind.PHONE: _phone,
    SceneKind.BUSY_AWAY: _busy_away,
}


def _paint_particles(
    canvas: DioramaCanvas,
    snapshot: AnyaSnapshot,
    anchor: tuple[int, int],
    frame: int,
) -> tuple[str, float]:
    channels = aggregate_emotions(snapshot.emotions)
    dominant, intensity = max(channels.items(), key=lambda item: item[1], default=("warm", 0.0))
    count = min(6, particle_budget(intensity))
    if not count or frame % max(1, 5 - int(intensity * 4)):
        return dominant, intensity
    glyph = PARTICLE_GLYPHS[dominant]
    color = PARTICLE_COLORS[dominant]
    offsets = ((0, 0), (-3, 1), (3, 1), (-5, 0), (5, 0), (1, -1))
    for offset_x, offset_y in offsets[:count]:
        canvas.put(anchor[0] + offset_x, max(0, anchor[1] + offset_y), glyph, color, bold=intensity >= 0.65)
    return dominant, intensity


def _compact_scene(
    snapshot: AnyaSnapshot,
    decision: SceneDecision,
    renderer: TerminalRenderer,
    frame: int,
) -> Text:
    channels = aggregate_emotions(snapshot.emotions)
    dominant, intensity = max(channels.items(), key=lambda item: item[1], default=("warm", 0.0))
    sleeping = decision.kind is SceneKind.SLEEPING
    face = "(-.-)" if sleeping else ("(•̀_•́)" if dominant == "anger" and intensity > 0.55 else "(•ᴗ•)")
    particle = PARTICLE_GLYPHS[dominant] * min(4, particle_budget(intensity))
    return renderer.line(
        (
            StyledSpan(face + "  ", "accent", bold=True),
            StyledSpan(f"{decision.location} · {decision.activity}", "text", bold=True),
            StyledSpan(f"  {particle}", "warm", bold=intensity >= 0.65),
        )
    )


def render_scene(
    snapshot: AnyaSnapshot,
    decision: SceneDecision,
    frame: int = 0,
    compact: bool = False,
    renderer: TerminalRenderer | None = None,
) -> Text:
    renderer = renderer or TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))
    if compact:
        return _compact_scene(snapshot, decision, renderer, frame)
    palette = SCENE_PALETTES[decision.kind]
    canvas = DioramaCanvas(background=palette.wall)
    anchor = SCENE_PAINTERS[decision.kind](canvas, palette, frame)
    _paint_particles(canvas, snapshot, anchor, frame)
    return canvas.render(renderer)
