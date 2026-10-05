"""Pure scene selection and ASCII rendering for Anya's terminal room."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.tui.state import AnyaSnapshot, aggregate_emotions, particle_budget


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


ART = {
    SceneKind.HOME_BED: (
        "╭────────── комната ──────────╮",
        "│   ╭──── окно ────╮     ✦    │",
        "│   ╰──────────────╯          │",
        "│        ∧＿∧        ╭─────╮  │",
        "│       ( •‿•)       │кровать│ │",
        "│       /|   |\\      ╰─────╯  │",
        "│       / \\ / \\              │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.IDLE_HOME: (
        "╭────────── комната ──────────╮",
        "│   ╭──── окно ────╮          │",
        "│   ╰──────────────╯      ♪   │",
        "│             ∧＿∧            │",
        "│            (•ᴗ• )           │",
        "│            /|  |\\     ╭──╮ │",
        "│             /  \\      ╰──╯ │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.HOME_DESK: (
        "╭────────── рабочий стол ─────╮",
        "│  ┌──────────┐      ∧＿∧     │",
        "│  │  > _     │     (•̀ᴗ•́ )    │",
        "│  │          │     /|  |\\    │",
        "│  └──────────┘    ─┴──┴─     │",
        "│      клавиатура              │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.COLLEGE_DESK: (
        "╭────────── аудитория ────────╮",
        "│  доска:  f(x) = ...         │",
        "│                    ∧＿∧     │",
        "│   ┌────────┐      (•_• )    │",
        "│   │ конспект│      /|  |\\   │",
        "│   └────────┘      ─┴──┴─    │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.SLEEPING: (
        "╭────────── тихая комната ────╮",
        "│       ☾              z      │",
        "│                   z         │",
        "│   ╭─────────────────────╮   │",
        "│   │   (  -.-) ＿＿＿     │   │",
        "│   │   / つ⌒⌒⌒⌒⌒⌒＼     │   │",
        "│   ╰─────────────────────╯   │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.PHONE: (
        "╭────────── комната ──────────╮",
        "│              ┌───┐          │",
        "│       ∧＿∧   │ ▪ │  ·       │",
        "│      (•ᴗ• )  │   │    ·     │",
        "│      /|  |\\  └───┘          │",
        "│       /  \\                  │",
        "╰─────────────────────────────╯",
    ),
    SceneKind.BUSY_AWAY: (
        "╭────────── вне дома ─────────╮",
        "│        ·        ·           │",
        "│             ∧＿∧      →     │",
        "│            (•_• )           │",
        "│            /|  |\\          │",
        "│             /  \\           │",
        "╰─────────────────────────────╯",
    ),
}

PARTICLES = {
    "warm": "♥", "anger": "#", "sadness": "·", "anxiety": "~", "happy": "✦", "jealousy": "◆",
}


def render_scene(snapshot: AnyaSnapshot, decision: SceneDecision, frame: int = 0, compact: bool = False) -> str:
    art = list(ART[decision.kind])
    channels = aggregate_emotions(snapshot.emotions)
    dominant, intensity = max(channels.items(), key=lambda item: item[1], default=("warm", 0.0))
    count = particle_budget(intensity)
    particle_line = ""
    if count and frame % max(1, 5 - int(intensity * 4)) == 0:
        particle_line = " ".join(PARTICLES[dominant] for _ in range(count))
    if compact:
        face = "(-.-)" if decision.kind == SceneKind.SLEEPING else ("(•̀_•́)" if dominant == "anger" and intensity > 0.55 else "(•ᴗ•)")
        return f"{face}  {decision.location} · {decision.activity}\n{particle_line}"
    if frame % 23 == 0 and decision.kind != SceneKind.SLEEPING:
        art = [line.replace("•‿•", "-‿-").replace("•ᴗ•", "-ᴗ-").replace("•_•", "-_-" ) for line in art]
    return "\n".join(art) + (f"\n[bold]{particle_line}[/bold]" if particle_line else "")
