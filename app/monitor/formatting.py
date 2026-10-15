from __future__ import annotations

from app.monitor.labels_ru import label


def percent(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def bar(value, width: int = 12) -> str:
    try:
        filled = max(0, min(width, round(float(value) * width)))
    except (TypeError, ValueError):
        filled = 0
    return "[green]" + "█" * filled + "[/]" + "[dim]" + "░" * (width - filled) + "[/]"


def metric_lines(values: dict, limit: int | None = None) -> str:
    pairs = sorted(values.items(), key=lambda item: (-float(item[1]), item[0]))[:limit]
    return "\n".join(f"{label(key):<27} {bar(value)} {percent(value):>4}" for key, value in pairs) or "[dim]нет данных[/]"


def short(value: str | None, width: int = 42) -> str:
    if not value:
        return "—"
    return value if len(value) <= width else value[: width - 1] + "…"
