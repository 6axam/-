from __future__ import annotations

from rich.panel import Panel
from rich.console import Console, ConsoleOptions, RenderResult
from rich.cells import cell_len
from rich.table import Table
from rich.text import Text

from app.monitor.labels_ru import label
from app.monitor.theme import ACCENT, BAD, GOOD, MUTED, RELATIONSHIP, WARNING

POSITIVE = {"joy", "contentment", "excitement", "amusement", "relief", "hope", "gratitude", "pride", "affection", "tenderness", "trust", "closeness", "curiosity", "interest", "relationship_security", "bond_strength", "relational_trust", "familiarity", "care_investment", "love_strength"}
NEGATIVE = {"hurt", "irritation", "anger", "resentment", "anxiety", "fear", "sadness", "melancholy", "grief", "depressive_tone", "jealousy", "envy", "disgust", "overwhelm", "rupture_load", "stress", "threat", "frustration"}


def tone(key: str, value: float | None = None) -> str:
    if key in NEGATIVE:
        return BAD
    if key in POSITIVE:
        return GOOD
    if "relationship" in key or key in {"attachment", "care_investment"}:
        return RELATIONSHIP
    return ACCENT


def meter(key: str, value: float, width: int = 12, *, inverse: bool = False) -> Text:
    value = max(0, min(1, float(value)))
    filled = round(value * width)
    color = tone(key, value)
    if inverse:
        color = GOOD if value < .35 else WARNING if value < .65 else BAD
    text = Text()
    text.append(f"{label(key):<24} ", style="white")
    text.append("█" * filled, style=color)
    text.append("░" * (width - filled), style=MUTED)
    text.append(f" {value * 100:>3.0f}%", style="white")
    return text


def metric_table(values: dict, *, limit: int | None = None, inverse: set[str] | None = None) -> Table:
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(ratio=1)
    keys = sorted(values, key=lambda key: (-float(values[key]), key))[:limit]
    for key in keys:
        table.add_row(meter(key, values[key], inverse=key in (inverse or set())))
    if not keys:
        table.add_row(Text("Нет данных", style=MUTED))
    return table


def panel(title: str, body, *, color: str = ACCENT) -> Panel:
    return Panel(body, title=f"[bold {color}]{title}[/]", border_style=color, padding=(0, 1), expand=True)


def sparkline(values: list[float], width: int = 22) -> Text:
    if not values:
        return Text("Ожидание первых запросов…", style=MUTED)
    bars = "▁▂▃▄▅▆▇█"
    values = values[-width:]
    text = Text()
    for value in values:
        index = max(0, min(7, round(float(value) * 7)))
        text.append(bars[index], style=GOOD if value >= .6 else WARNING if value >= .3 else BAD)
    return text


class LeaderRow:
    """A width-aware metric row with muted leaders and a right-aligned value."""

    def __init__(self, name: str, value: str, *, value_style: str = "white", indent: int = 0) -> None:
        self.name, self.value, self.value_style, self.indent = name, value, value_style, indent

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = max(12, options.max_width)
        prefix = " " * self.indent
        available_label = max(4, width - cell_len(prefix) - cell_len(self.value) - 4)
        label_text = Text(self.name, style="white", overflow="ellipsis", end="")
        label_text.truncate(available_label, overflow="ellipsis")
        leaders = max(1, width - cell_len(prefix) - cell_len(label_text.plain) - cell_len(self.value) - 2)
        row = Text(prefix)
        row.append_text(label_text)
        row.append(" ")
        row.append("─" * leaders, style=MUTED)
        row.append(" ")
        row.append(self.value, style=self.value_style)
        yield row


def leader_row(name: str, value: str, *, value_style: str = "white", indent: int = 0) -> LeaderRow:
    return LeaderRow(name, value, value_style=value_style, indent=indent)
