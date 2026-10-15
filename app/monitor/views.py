from __future__ import annotations

import json
from collections import Counter

from rich.columns import Columns
from rich.console import Group
from rich.table import Table
from rich.text import Text

from app.monitor.labels_ru import AVAILABILITY, LIFECYCLE, REQUEST_KINDS, label
from app.monitor.theme import ACCENT, BAD, GOOD, MUTED, RELATIONSHIP, WARNING
from app.monitor.widgets import leader_row, metric_table, panel, sparkline, tone


def _cache(row: dict) -> float | None:
    tokens = row.get("prompt_tokens") or 0
    return (row.get("cached_tokens") or 0) / tokens if tokens else None


def _cost(value) -> str:
    return f"${value:.5f}" if isinstance(value, (int, float)) else "—"


def _short(text, width=68) -> str:
    text = str(text or "")
    return text if len(text) <= width else text[:width - 1] + "…"


_NEGATIVE = {"rupture_load", "hurt", "anger", "irritation", "anxiety", "resentment", "stress", "threat", "frustration", "fear", "overwhelm"}


def _value_text(key: str, before, after, delta) -> str:
    if key == "expects_reply":
        first, second = ("ДА" if before else "НЕТ"), ("ДА" if after else "НЕТ")
        return second if before == after or before is None else f"{first} → {second}"
    if key == "followup_importance":
        first, second = f"{(before or 0) * 100:.0f}%", f"{(after or 0) * 100:.0f}%"
        return second if before is None else f"{first} → {second}  ({delta * 100:+.0f}%)"
    if before is not None and after is not None:
        return f"{before * 100:.0f}% → {after * 100:.0f}%  {delta * 100:+.0f}%"
    return f"{delta * 100:+.0f}%"


def _delta_rows(item: dict, *, maximum: int = 6, details: bool = False, include_dialogue: bool = False):
    delta, before, after = item.get("delta", {}), item.get("before", {}), item.get("after", {})
    emotional, dialogue = [], []
    for group_name, group in delta.items():
        if not isinstance(group, dict): continue
        for key, value in group.items():
            if not isinstance(value, (float, int)) or (key not in {"expects_reply"} and abs(value) < .01): continue
            source_before, source_after = before.get(group_name, {}), after.get(group_name, {})
            old, new = source_before.get(key) if isinstance(source_before, dict) else None, source_after.get(key) if isinstance(source_after, dict) else None
            good = (key not in _NEGATIVE) == (value > 0)
            row = leader_row(label(key), _value_text(key, old, new, value) if details else f"{value * 100:+.0f}%", value_style=GOOD if good else BAD, indent=2)
            (dialogue if group_name == "lifecycle" else emotional).append(row)
    emotional = emotional[:maximum]
    result = []
    if emotional: result.extend([Text("ЭМОЦИИ / АФФЕКТ", style=MUTED), Group(*emotional)])
    if include_dialogue and dialogue: result.extend([Text("ДИАЛОГ", style=MUTED), Group(*dialogue)])
    return Group(*result) if result else Text("Заметных изменений нет", style=MUTED)


def overview(data: dict):
    last = data.get("last_generation", {})
    if last:
        cache = _cache(last) or 0
        token_body = Table.grid(expand=True, padding=(0, 1)); token_body.add_column(); token_body.add_column(justify="right")
        for left, right in (("Модель", last.get("model", "—")), ("Вход", f"{last.get('prompt_tokens') or 0:,}"), ("Из кэша", f"{last.get('cached_tokens') or 0:,}"), ("Без кэша", f"{max(0, (last.get('prompt_tokens') or 0) - (last.get('cached_tokens') or 0)):,}"), ("Ответ", f"{last.get('completion_tokens') or 0:,}"), ("Задержка", f"{last.get('latency_ms') or '—'} мс"), ("Стоимость", _cost(last.get("cost")))):
            token_body.add_row(left, str(right))
        token_body.add_row(Text("Кэш", style=ACCENT), sparkline([cache] * 10))
    else:
        token_body = Text("Ожидание первого LLM-запроса…", style=MUTED)
    life = data.get("lifecycle", {}); message = data.get("last_message", {})
    state_body = Table.grid(expand=True, padding=(0, 1)); state_body.add_column(); state_body.add_column(justify="right")
    state_body.add_row("Диалог", LIFECYCLE.get(life.get("conversation_status"), "неизвестно"))
    state_body.add_row("Ожидает ответ", "да" if life.get("expects_reply") else "нет")
    state_body.add_row("Последняя реплика", _short(message.get("text"), 42) or "—")
    top = Columns([panel("ТОКЕНЫ / КЭШ", token_body), panel("ТЕКУЩЕЕ СОСТОЯНИЕ", state_body)], expand=True, equal=True)
    emotions = panel("ЭМОЦИИ", metric_table(data.get("emotions", {}), limit=8))
    relationship = panel("ОТНОШЕНИЯ", metric_table(data.get("relationship", {}), inverse={"rupture_load"}))
    observation_cards = []
    for item in data.get("observations", [])[:5]:
        observation_cards.append(Group(Text(f"{str(item.get('created_at',''))[-8:]}  «{_short(item.get('user_message'), 58)}»", style=ACCENT), _delta_rows(item, maximum=4)))
    changes = panel("ПОСЛЕДНИЕ ИЗМЕНЕНИЯ", Group(*observation_cards) if observation_cards else Text("История появится после следующего обработанного сообщения.", style=MUTED))
    return Group(top, Columns([emotions, relationship], expand=True, equal=True), changes)


def tokens(data: dict):
    rows = data.get("generations", []); last = data.get("last_generation", {})
    if not last:
        return panel("ТОКЕНЫ", Text("Ожидание первого LLM-запроса…", style=MUTED))
    details = Table.grid(expand=True, padding=(0, 1)); details.add_column(); details.add_column(justify="right")
    for name, value in (("Модель", last.get("model")), ("Тип", REQUEST_KINDS.get(last.get("kind"), last.get("kind", "—"))), ("Время", last.get("created_at")), ("Сессия", str(last.get("session_id") or "—").replace("anya:", "")[-30:]), ("Вход", last.get("prompt_tokens")), ("Из кэша", last.get("cached_tokens")), ("Без кэша", max(0, (last.get("prompt_tokens") or 0) - (last.get("cached_tokens") or 0))), ("Cache write", last.get("cache_write_tokens")), ("Ответ", last.get("completion_tokens")), ("Всего", last.get("total_tokens")), ("Задержка", f"{last.get('latency_ms')} мс"), ("Стоимость", _cost(last.get("cost")))):
        details.add_row(name, str(value if value is not None else "—"))
    hits = [hit for row in reversed(rows) if (hit := _cache(row)) is not None]
    chart = Group(Text("Последние cache-hit", style=MUTED), sparkline(hits, 30), Text("  ".join(f"{value * 100:.0f}%" for value in hits[-8:]), style=MUTED))
    table = Table(expand=True, header_style=f"bold {ACCENT}", box=None)
    for col in ("Время", "Тип", "Вход", "Кэш", "Hit", "Ответ", "Стоимость", "Latency"): table.add_column(col)
    for row in rows[:18]: table.add_row(str(row.get("created_at", ""))[-8:], REQUEST_KINDS.get(row.get("kind"), str(row.get("kind", "—"))), f"{row.get('prompt_tokens') or 0:,}", f"{row.get('cached_tokens') or 0:,}", f"{(_cache(row) or 0) * 100:.0f}%" if _cache(row) is not None else "—", f"{row.get('completion_tokens') or 0:,}", _cost(row.get("cost")), f"{row.get('latency_ms') or '—'} мс")
    return Group(Columns([panel("ПОСЛЕДНИЙ ЗАПРОС", details), panel("CACHE HIT", chart, color=GOOD)], equal=True, expand=True), panel("ПОСЛЕДНИЕ ЗАПРОСЫ", table))


def emotions(data: dict):
    history = Group(*[Group(Text(str(item.get('created_at',''))[-8:], style=ACCENT), _delta_rows(item, maximum=5, details=True)) for item in data.get("observations", [])[:8]])
    return Group(Columns([panel("ЭМОЦИИ", metric_table(data.get("emotions", {}))), panel("РЕГУЛЯТОРЫ", metric_table(data.get("regulators", {})))], equal=True, expand=True), Columns([panel("ПОВЕДЕНИЕ", Text("Появится в новых turn observations.", style=MUTED)), panel("ОТНОШЕНИЯ", metric_table(data.get("relationship", {}), inverse={"rupture_load"}), color=RELATIONSHIP)], equal=True, expand=True), panel("ИСТОРИЯ ИЗМЕНЕНИЙ", history or Text("История появится после следующего turn.", style=MUTED)))


def prompt(data: dict, selected: int):
    rows = data.get("generations", [])
    if not rows: return panel("ПРОМПТ", Text("Пока нет сохранённых prompt snapshots.", style=MUTED))
    selected = min(selected, len(rows)-1); row = rows[selected]
    listing = Table.grid(expand=True); listing.add_column()
    for index, item in enumerate(rows[:20]):
        style = f"bold {ACCENT}" if index == selected else MUTED
        listing.add_row(Text(f"{'› ' if index == selected else '  '}{str(item.get('created_at',''))[-8:]}  {REQUEST_KINDS.get(item.get('kind'), item.get('kind','—')):<15} {(item.get('prompt_tokens') or 0)/1000:.1f}k  {_cache(item) * 100:.0f}%" if _cache(item) is not None else f"  {str(item.get('created_at',''))[-8:]}  {item.get('kind','—')}", style=style))
    if row.get("system_prompt") is None: detail = Text("Сохранение текста промпта отключено.", style=MUTED)
    else:
        components = json.loads(row.get("components_json") or "{}")
        component_text = "\n".join(f"{key.replace('_',' '):<28} ~{value.get('tokens',0)}" for key, value in components.items() if isinstance(value, dict))
        detail = Group(Text("СИСТЕМА", style=f"bold {ACCENT}"), Text(row.get("system_prompt") or "—"), Text("\nКОНТЕКСТ", style=f"bold {ACCENT}"), Text(row.get("dynamic_context") or "—"), Text("\nСООБЩЕНИЕ", style=f"bold {ACCENT}"), Text(row.get("user_turn") or "—"), Text("\nКОМПОНЕНТЫ (оценка)", style=f"bold {ACCENT}"), Text(component_text or "—", style=MUTED))
    return Columns([panel("GENERATIONS · ↑/↓ выбрать", listing), panel(f"PROMPT #{row.get('id','—')}", detail)], expand=True, equal=False, column_first=True)


def history(data: dict):
    lines=[]
    for item in data.get("observations", []):
        lines.extend([Text(f"{str(item.get('created_at',''))[-8:]}  СОБЕСЕДНИК", style=MUTED), Text(f"«{_short(item.get('user_message'), 104)}»", style="white"), _delta_rows(item, maximum=8, details=True, include_dialogue=True), Text("─" * 72, style=MUTED)])
    for event in data.get("events", [])[:10]: lines.extend([Text(f"{event.get('starts_at','')}  СОБЫТИЕ", style=WARNING), Text(f"{event.get('title','—')} · {AVAILABILITY.get(event.get('availability'),event.get('availability','—'))}")])
    return panel("ИСТОРИЯ · ВСЁ", Group(*lines) if lines else Text("История ещё пуста.", style=MUTED))


def events(data: dict):
    event_lines = [Text(f"{row.get('starts_at','—')}–{row.get('ends_at','—')}  {row.get('title','—')} · {AVAILABILITY.get(row.get('availability'), row.get('availability','—'))}") for row in data.get("events", [])]
    return Group(panel("СЕЙЧАС", Text("Активное событие определяется по существующим daily events.\nНовых событий monitor не создаёт.", style=MUTED)), panel("НЕДАВНО / ЗАПЛАНИРОВАНО", Group(*event_lines) if event_lines else Text("Активных и запланированных событий нет.", style=MUTED)))


def state(data: dict):
    life=data.get("lifecycle", {}); episode=data.get("episodes", {}); generation=data.get("last_generation", {}); message=data.get("last_message", {})
    dialogue=Text(f"Диалог: {LIFECYCLE.get(life.get('conversation_status'),'неизвестно')}\nОжидает ответ: {'да' if life.get('expects_reply') else 'нет'}\nПоследняя реплика: {_short(message.get('text'),70)}")
    memory=Text(f"Активные эпизоды: {episode.get('active',0)}\nНеразрешённые: {episode.get('unresolved',0)}")
    runtime=Text(f"БД: read-only\nProvider/model: {generation.get('model','—')}\nСессия: {str(generation.get('session_id') or '—')[-30:]}\nRuntime heartbeat: неизвестно")
    return Columns([panel("ДИАЛОГ", dialogue), panel("ПАМЯТЬ", memory), panel("RUNTIME", runtime)], equal=True, expand=True)


_TELEGRAM_STATE = {
    "received": "получено", "scheduled_read": "ожидает прочтения",
    "read_ack_requested": "прочтение запрошено", "read_ack_confirmed": "прочитано Telegram",
    "decision_pending": "решение готовится", "response_scheduled": "ответ запланирован",
    "silence": "выбрано молчание", "sent": "отправлено", "recipient_read": "прочитано собеседником",
}


def telegram(data: dict):
    rows = data.get("telegram", [])
    current = {}
    for row in rows:
        key = (row.get("direction"), row.get("message_id"))
        current.setdefault(key, row)
    summary = Table.grid(expand=True, padding=(0, 1)); summary.add_column(); summary.add_column(justify="right")
    incoming = list(row for key, row in current.items() if key[0] == "incoming")
    outgoing = list(row for key, row in current.items() if key[0] == "outgoing")
    summary.add_row("Получено", str(len(incoming)))
    summary.add_row("Прочитано Telegram", str(sum(row.get("read_watermark") is not None for row in incoming)))
    summary.add_row("Ответ запланирован", str(sum(row.get("status") == "response_scheduled" for row in incoming)))
    summary.add_row("Отправлено", str(sum(row.get("status") == "sent" for row in outgoing)))
    table = Table(expand=True, header_style=f"bold {ACCENT}", box=None)
    for heading in ("Время", "Направление", "ID", "Статус", "Watermark", "Источник"):
        table.add_column(heading)
    for row in rows[:36]:
        event_status = row.get("event_status") or row.get("status")
        table.add_row(
            str(row.get("created_at") or row.get("updated_at") or "")[-8:],
            "входящее" if row.get("direction") == "incoming" else "исходящее",
            str(row.get("message_id") or "—"),
            _TELEGRAM_STATE.get(event_status, str(event_status or "—")),
            str(row.get("read_watermark") if row.get("read_watermark") is not None else "—"),
            str(row.get("source") or "—"),
        )
    notice = Text(
        "Watermark подтверждает Telegram. Отметка прочтения исходящего сообщения приходит только от собеседника; monitor её не подменяет.",
        style=MUTED,
    )
    return Group(Columns([panel("ЦЕПОЧКА ДОСТАВКИ", summary), panel("ПРАВИЛО", notice)], expand=True, equal=True), panel("TELEGRAM · ПОСЛЕДНИЕ ПЕРЕХОДЫ", table if rows else Text("MTProto-статусов пока нет.", style=MUTED)))
