"""Textual shell for the read-only Anya terminal tamagotchi."""

from __future__ import annotations

import asyncio
from collections import deque

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Resize
from textual.widgets import Footer, Static

from app.tui import ANIMATION_FPS, POLL_INTERVAL_SECONDS
from app.tui.rendering import StyledSpan, TerminalCapabilities, TerminalRenderer
from app.tui.scenes import SceneDecision, render_scene, resolve_scene
from app.tui.state import (
    AnyaSnapshot,
    ReadOnlyStateReader,
    aggregate_emotions,
    meter,
    relationship_lines,
    top_emotions,
    transition_events,
)
from app.tui.theme import ColorMode, build_app_css


class AnyaTamagotchiApp(App[None]):
    """A passive display: it has no Telegram, LLM or mutating DB dependency."""

    TITLE = "Аня · terminal tamagotchi"
    SUB_TITLE = "read-only observer"
    BINDINGS = [
        Binding("q", "quit", "выйти"),
        Binding("r", "refresh", "обновить"),
        Binding("e", "show_emotions", "эмоции"),
        Binding("m", "show_memories", "эпизоды"),
        Binding("l", "toggle_events", "события"),
        Binding("question_mark", "show_help", "помощь"),
        Binding("escape", "close_overlay", "закрыть", show=False),
    ]
    CSS = build_app_css()

    def __init__(self, reader: ReadOnlyStateReader) -> None:
        super().__init__()
        self.reader = reader
        self.snapshot: AnyaSnapshot | None = None
        self.decision: SceneDecision | None = None
        self.frame = 1
        self.compact = False
        self.events: deque[str] = deque(maxlen=6)
        self._polling = False
        self._overlay_kind: str | None = None
        self._observer_started = False
        self._startup_handle: asyncio.TimerHandle | None = None
        self.renderer = TerminalRenderer(TerminalCapabilities(ColorMode.TRUECOLOR))

    def compose(self) -> ComposeResult:
        yield Static("Аня · подключение к сохранённому состоянию…", id="title")
        with Horizontal(id="main"):
            yield Static("загрузка сцены…", id="scene", markup=True)
            yield Static("нет снимка", id="status", markup=True)
        yield Static("активность: —", id="activity", markup=True)
        yield Static("события текущего запуска: —", id="events", markup=True)
        yield Static("", id="overlay", markup=True)
        yield Footer()

    async def _ready(self) -> None:
        await super()._ready()
        console = self.console
        self.renderer = TerminalRenderer(
            TerminalCapabilities.detect(
                getattr(console, "color_system", None),
                encoding=getattr(console, "encoding", "utf-8"),
                no_color=bool(getattr(console, "no_color", False)),
            )
        )
        # _process_messages invokes its public ready callback immediately after
        # this hook; the delay lets its first layout settle before animation.
        self._startup_handle = asyncio.get_running_loop().call_later(1.5, self._start_if_running)

    def _start_if_running(self) -> None:
        if self._running and not self._exit:
            self.start_observer()

    def start_observer(self) -> None:
        if self._observer_started:
            return
        self._observer_started = True
        self.set_interval(1 / ANIMATION_FPS, self.animate_frame)
        self.set_interval(POLL_INTERVAL_SECONDS, self._trigger_poll)
        self._trigger_poll()

    def on_resize(self, event: Resize) -> None:
        compact = event.size.width < 72 or event.size.height < 20
        if compact == self.compact:
            return
        self.compact = compact
        self.screen.set_class(compact, "compact")
        self._render_state()

    def _trigger_poll(self) -> None:
        if not self._polling:
            self.run_worker(self._poll(), group="state-poll", exclusive=True)

    async def _poll(self) -> None:
        self._polling = True
        try:
            current = await asyncio.to_thread(self.reader.poll)
            for event in transition_events(self.snapshot, current):
                self.events.appendleft(event)
            self.snapshot = current
            self.decision = resolve_scene(current, idle_variant=(self.frame // (ANIMATION_FPS * 30)))
            self._render_state()
        finally:
            self._polling = False

    def animate_frame(self) -> None:
        self.frame += 1
        if self.snapshot and self.decision and self.query("#scene"):
            self.query_one("#scene", Static).update(
                render_scene(self.snapshot, self.decision, self.frame, self.compact, self.renderer)
            )

    def _render_state(self) -> None:
        snapshot = self.snapshot
        decision = self.decision
        required = ("#title", "#scene", "#status", "#activity", "#events")
        if snapshot is None or decision is None or not all(self.query(selector) for selector in required):
            return
        process = {"reading": " · читает", "responding": " · готовит ответ"}.get(snapshot.processing, "")
        title_spans = [
            StyledSpan("Аня", "accent", bold=True, reverse_in_mono=True),
            StyledSpan(f"  {snapshot.presence.local_time} · ", "muted"),
            StyledSpan(decision.location, "text", bold=True),
            StyledSpan(process, "success"),
        ]
        if not snapshot.source_available:
            title_spans.append(StyledSpan(" · DB временно недоступна", "danger", bold=True))
        self.query_one("#title", Static).update(self.renderer.line(title_spans))
        self.query_one("#scene", Static).update(
            render_scene(snapshot, decision, self.frame, self.compact, self.renderer)
        )
        top = top_emotions(snapshot.emotions)
        status_rows: list[tuple[StyledSpan, ...]] = [
            (StyledSpan("СОСТОЯНИЕ", "accent", bold=True, reverse_in_mono=True),),
            (StyledSpan("место       ", "muted"), StyledSpan(decision.location, "text", bold=True)),
            (StyledSpan("занятие     ", "muted"), StyledSpan(decision.activity, "text", bold=True)),
            (StyledSpan("доступность ", "muted"), StyledSpan(snapshot.presence.availability, "success")),
            (StyledSpan("диалог      ", "muted"), StyledSpan(snapshot.lifecycle, "text")),
            (StyledSpan("энергия     ", "muted"), StyledSpan(meter(snapshot.regulators.get("energy")), "warm")),
            (),
            (StyledSpan("ЭМОЦИИ", "accent", bold=True, reverse_in_mono=True),),
        ]
        if top:
            status_rows.extend(
                (StyledSpan(f"{name:<18}", "muted"), StyledSpan(f"{value:4.0%}", "warm", bold=True))
                for name, value in top
            )
        else:
            status_rows.append((StyledSpan("нет сохранённых данных", "muted", dim=True),))
        status_rows.extend(
            [
                (),
                (StyledSpan("ОТНОШЕНИЯ", "accent", bold=True, reverse_in_mono=True),),
                *((StyledSpan(line, "text"),) for line in relationship_lines(snapshot.relationship)),
            ]
        )
        self.query_one("#status", Static).update(self.renderer.lines(status_rows))
        evidence = "визуальная idle-вариация" if decision.evidence == "visual idle variation" else decision.evidence
        if self.compact:
            energy = snapshot.regulators.get("energy")
            trust = snapshot.relationship.get("relational_trust")
            security = snapshot.relationship.get("relationship_security")
            compact_value = lambda value: "n/a" if value is None else f"{value:.0%}"
            activity = self.renderer.line(
                (
                    StyledSpan(decision.activity, "accent", bold=True),
                    StyledSpan(f" · энергия {compact_value(energy)}", "warm"),
                    StyledSpan(f" · trust {compact_value(trust)} · security {compact_value(security)}", "muted"),
                )
            )
        else:
            activity = self.renderer.line(
                (
                    StyledSpan("сейчас: ", "accent", bold=True),
                    StyledSpan(decision.activity, "text", bold=True),
                    StyledSpan(f"  ·  источник: {evidence}", "muted"),
                )
            )
        self.query_one("#activity", Static).update(activity)
        event_text = "  •  ".join(self.events) or "—"
        self.query_one("#events", Static).update(
            self.renderer.line(
                (
                    StyledSpan("этот запуск: ", "accent_soft", bold=True),
                    StyledSpan(event_text, "muted"),
                )
            )
        )
        if self._overlay_kind:
            self._update_overlay(self._overlay_kind)

    def _update_overlay(self, kind: str) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        overlay = self.query_one("#overlay", Static)
        if kind == "emotions":
            channels = aggregate_emotions(snapshot.emotions)
            body = "\n".join(f"{name:<12} {meter(value, 14)}" for name, value in channels.items())
            text = f"[bold]Эмоциональные каналы[/bold]\n\n{body}\n\nEsc — закрыть"
        elif kind == "memories":
            recent = "\n".join(f"• {escape(item)}" for item in snapshot.memories.recent_summaries) or "• нет активных эпизодов"
            text = (
                f"[bold]Активные эпизоды отношений[/bold]\n\n"
                f"активных: {snapshot.memories.active_count}\n"
                f"незакрытых: {snapshot.memories.unresolved_count}\n\n"
                f"последние:\n{recent}\n\nEsc — закрыть"
            )
        else:
            text = (
                "[bold]Горячие клавиши[/bold]\n\n"
                "q — выйти\nr — обновить снимок\ne — эмоциональные каналы\n"
                "m — активные эпизоды\nl — показать/скрыть ленту\n? — эта помощь\n\n"
                "TUI только читает SQLite. Она не вызывает LLM, не пишет память "
                "и не взаимодействует с Telegram.\n\nEsc — закрыть"
            )
        overlay.update(text)

    def _show_overlay(self, kind: str) -> None:
        self._overlay_kind = kind
        overlay = self.query_one("#overlay", Static)
        overlay.styles.display = "block"
        self._update_overlay(kind)

    def action_refresh(self) -> None:
        self._trigger_poll()

    def action_show_emotions(self) -> None:
        self._show_overlay("emotions")

    def action_show_memories(self) -> None:
        self._show_overlay("memories")

    def action_show_help(self) -> None:
        self._show_overlay("help")

    def action_close_overlay(self) -> None:
        self._overlay_kind = None
        self.query_one("#overlay", Static).styles.display = "none"

    def action_toggle_events(self) -> None:
        events = self.query_one("#events", Static)
        events.styles.display = "none" if events.styles.display != "none" else "block"
