"""Textual shell for the read-only Anya terminal tamagotchi."""

from __future__ import annotations

import asyncio
import time
from collections import deque

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import Resize
from textual.widgets import Footer, Static

from app.tui import ANIMATION_FPS, POLL_INTERVAL_SECONDS
from app.tui.animation import AnimationController, AnimationFrame
from app.tui.preview import PreviewSession
from app.tui.rendering import StyledSpan, TerminalCapabilities, TerminalRenderer
from app.tui.scenes import SceneDecision, render_scene, resolve_scene
from app.tui.state import (
    AnyaSnapshot,
    ReadOnlyStateReader,
    aggregate_emotions,
    meter,
    relationship_lines,
    transition_events,
)
from app.tui.theme import ColorMode, build_app_css


class AnyaTamagotchiApp(App[None]):
    """A passive display: it has no Telegram, LLM or mutating DB dependency."""

    TITLE = "Аня · terminal tamagotchi"
    SUB_TITLE = "read-only observer"
    BINDINGS = [
        Binding("q", "quit", "выйти"),
        Binding("f", "refresh", "обновить"),
        Binding("e", "show_emotions", "эмоции"),
        Binding("m", "show_memories", "эпизоды"),
        Binding("r", "show_relationships", "отношения"),
        Binding("d", "show_debug", "снимок"),
        Binding("l", "toggle_events", "события"),
        Binding("question_mark", "show_help", "помощь"),
        Binding("escape", "close_overlay", "закрыть", show=False),
        Binding("1", "preview_idle_home", show=False),
        Binding("2", "preview_home_desk", show=False),
        Binding("3", "preview_phone", show=False),
        Binding("4", "preview_sleeping", show=False),
        Binding("t", "preview_time", show=False),
        Binding("+", "preview_increase_intensity", show=False),
        Binding("-", "preview_decrease_intensity", show=False),
        Binding("space", "preview_pause", show=False),
    ]
    CSS = build_app_css()

    def __init__(self, reader: ReadOnlyStateReader | None, *, preview: PreviewSession | None = None) -> None:
        super().__init__()
        self.reader = reader
        self.preview = preview
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
        self.animation = AnimationController()
        self.visual_frame: AnimationFrame | None = None

    def compose(self) -> ComposeResult:
        yield Static("Аня · подключение к сохранённому состоянию…", id="title")
        yield Static("загрузка сцены…", id="scene", markup=True)
        yield Static("активность: —", id="activity", markup=True)
        yield Static("", id="vitals", markup=True)
        yield Static("события текущего запуска: —", id="events", markup=True)
        yield Static("", id="preview_diagnostic", markup=True)
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
        if self.preview is not None:
            self._start_preview()
            return
        self.set_interval(POLL_INTERVAL_SECONDS, self._trigger_poll)
        self._trigger_poll()

    def _start_preview(self) -> None:
        if self.preview is None:
            return
        self.snapshot = self.preview.snapshot()
        self.decision = self.preview.decision
        now = time.monotonic()
        self.animation = AnimationController(seed=self.preview.seed)
        self.animation.sync(self.snapshot, self.decision, now)
        self.visual_frame = self.animation.advance(self.decision, now)
        self._render_state()

    def on_resize(self, event: Resize) -> None:
        compact = event.size.width < 62 or event.size.height < 20
        if compact == self.compact:
            return
        self.compact = compact
        self.screen.set_class(compact, "compact")
        self._render_state()

    def _trigger_poll(self) -> None:
        if not self._polling:
            self.run_worker(self._poll(), group="state-poll", exclusive=True)

    async def _poll(self) -> None:
        if self.reader is None:
            return
        self._polling = True
        try:
            current = await asyncio.to_thread(self.reader.poll)
            for event in transition_events(self.snapshot, current):
                self.events.appendleft(event)
            self.snapshot = current
            self.decision = resolve_scene(current, idle_variant=(self.frame // (ANIMATION_FPS * 30)))
            self.animation.sync(current, self.decision, time.monotonic())
            self.visual_frame = self.animation.advance(self.decision, time.monotonic())
            self._render_state()
        finally:
            self._polling = False

    def animate_frame(self) -> None:
        if self.preview is not None:
            self._animate_preview()
            return
        self.frame += 1
        if self.snapshot and self.decision and self.query("#scene"):
            self.visual_frame = self.animation.advance(self.decision, time.monotonic())
            self.query_one("#scene", Static).update(
                render_scene(
                    self.snapshot,
                    self.decision,
                    self.frame,
                    self.compact,
                    self.renderer,
                    self.visual_frame,
                )
            )

    def _animate_preview(self) -> None:
        if self.preview is None or self.snapshot is None or self.decision is None:
            return
        if not self.preview.paused:
            self.frame += 1
            self.visual_frame = self.animation.advance(self.decision, time.monotonic())
        self.query_one("#scene", Static).update(
            render_scene(self.snapshot, self.decision, self.frame, self.compact, self.renderer, self.visual_frame)
        )
        self._update_preview_diagnostic()

    def _render_state(self) -> None:
        snapshot = self.snapshot
        decision = self.decision
        required = ("#title", "#scene", "#activity", "#vitals", "#events", "#preview_diagnostic")
        if snapshot is None or decision is None or not all(self.query(selector) for selector in required):
            return
        process = {"reading": " · читает", "responding": " · готовит ответ"}.get(snapshot.processing, "")
        title_spans = [
            StyledSpan("Аня", "accent", bold=True, reverse_in_mono=True),
            StyledSpan(f"  {snapshot.presence.local_time} · ", "muted"),
            StyledSpan(decision.location, "text", bold=True),
            StyledSpan(process, "success"),
        ]
        if self.preview is not None:
            title_spans.append(StyledSpan(" · preview", "warning", bold=True))
        if not snapshot.source_available:
            title_spans.append(StyledSpan(" · DB временно недоступна", "danger", bold=True))
        self.query_one("#title", Static).update(self.renderer.line(title_spans))
        self.query_one("#scene", Static).update(
            render_scene(snapshot, decision, self.frame, self.compact, self.renderer, self.visual_frame)
        )
        channels = aggregate_emotions(snapshot.emotions)
        dominant, intensity = max(channels.items(), key=lambda item: item[1], default=("warm", 0.0))
        emotion_label = {"warm": "тепло", "anger": "злость", "sadness": "грусть", "anxiety": "тревога", "happy": "радость", "jealousy": "ревность"}[dominant]
        activity = self.renderer.line((StyledSpan(decision.activity, "text", bold=True),))
        self.query_one("#activity", Static).update(activity)
        energy = snapshot.regulators.get("energy")
        energy_text = "энергия n/a" if energy is None else f"энергия {energy:.0%}"
        self.query_one("#vitals", Static).update(
            self.renderer.line(
                (
                    StyledSpan(f"{emotion_label} {intensity:.0%}", "accent", bold=True),
                    StyledSpan(f" · {energy_text}", "warm"),
                    StyledSpan(f" · {snapshot.presence.availability}", "muted"),
                )
            )
        )
        event_text = "  •  ".join(self.events) or "—"
        self.query_one("#events", Static).update(
            self.renderer.line(
                (
                    StyledSpan("этот запуск: ", "accent_soft", bold=True),
                    StyledSpan(event_text, "muted"),
                )
            )
        )
        self._update_preview_diagnostic()
        if self._overlay_kind:
            self._update_overlay(self._overlay_kind)

    def _update_preview_diagnostic(self) -> None:
        diagnostic = self.query_one("#preview_diagnostic", Static)
        if self.preview is None:
            diagnostic.styles.display = "none"
            return
        diagnostic.styles.display = "block"
        diagnostic.update(
            self.renderer.line((StyledSpan(self.preview.diagnostic(self.frame, ANIMATION_FPS), "muted"),))
        )

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
        elif kind == "relationships":
            body = "\n".join(relationship_lines(snapshot.relationship)) or "нет сохранённых данных"
            text = f"[bold]Отношения[/bold]\n\n{body}\n\nEsc — закрыть"
        elif kind == "debug":
            text = (
                "[bold]Read-only снимок[/bold]\n\n"
                f"сцена: {self.decision.kind.value if self.decision else 'n/a'}\n"
                f"источник: {self.decision.evidence if self.decision else 'n/a'}\n"
                f"lifecycle: {snapshot.lifecycle}\n"
                f"processing: {snapshot.processing or '—'}\n"
                f"SQLite: {'доступна' if snapshot.source_available else 'недоступна'}\n\nEsc — закрыть"
            )
        else:
            preview_help = ""
            if self.preview is not None:
                preview_help = (
                    "\nPreview: 1 idle · 2 desk · 3 phone · 4 sleep\n"
                    "t — time · e — emotion · +/- — intensity · Space — pause · r — reset\n"
                )
            text = (
                "[bold]Горячие клавиши[/bold]\n\n"
                "q — выйти\nf — обновить снимок\ne — эмоциональные каналы\n"
                "m — активные эпизоды\nr — отношения\nd — read-only снимок\n"
                "l — показать/скрыть ленту\n? — эта помощь\n\n"
                "TUI только читает SQLite. Она не вызывает LLM, не пишет память "
                "и не взаимодействует с Telegram."
                f"{preview_help}\nEsc — закрыть"
            )
        overlay.update(text)

    def _show_overlay(self, kind: str) -> None:
        self._overlay_kind = kind
        overlay = self.query_one("#overlay", Static)
        overlay.styles.display = "block"
        self._update_overlay(kind)

    def action_refresh(self) -> None:
        if self.preview is not None:
            self._refresh_preview()
            return
        self._trigger_poll()

    def action_show_emotions(self) -> None:
        if self.preview is not None:
            self.preview.cycle_emotion()
            self._refresh_preview()
            return
        self._show_overlay("emotions")

    def action_show_memories(self) -> None:
        self._show_overlay("memories")

    def action_show_relationships(self) -> None:
        if self.preview is not None:
            self._reset_preview_animation()
            return
        self._show_overlay("relationships")

    def action_show_debug(self) -> None:
        self._show_overlay("debug")

    def action_show_help(self) -> None:
        self._show_overlay("help")

    def action_close_overlay(self) -> None:
        self._overlay_kind = None
        self.query_one("#overlay", Static).styles.display = "none"

    def action_toggle_events(self) -> None:
        if self.preview is not None:
            return
        events = self.query_one("#events", Static)
        events.styles.display = "none" if events.styles.display != "none" else "block"

    def action_preview_idle_home(self) -> None:
        self._set_preview_scene("idle_home")

    def action_preview_home_desk(self) -> None:
        self._set_preview_scene("home_desk")

    def action_preview_phone(self) -> None:
        self._set_preview_scene("phone")

    def action_preview_sleeping(self) -> None:
        self._set_preview_scene("sleeping")

    def action_preview_time(self) -> None:
        if self.preview is not None:
            self.preview.cycle_time()
            self._refresh_preview()

    def action_preview_increase_intensity(self) -> None:
        if self.preview is not None:
            self.preview.adjust_intensity(0.1)
            self._refresh_preview()

    def action_preview_decrease_intensity(self) -> None:
        if self.preview is not None:
            self.preview.adjust_intensity(-0.1)
            self._refresh_preview()

    def action_preview_pause(self) -> None:
        if self.preview is not None:
            self.preview.paused = not self.preview.paused
            self._update_preview_diagnostic()

    def _set_preview_scene(self, scene_name: str) -> None:
        if self.preview is not None:
            self.preview.select_scene(scene_name)
            self._refresh_preview()

    def _reset_preview_animation(self) -> None:
        if self.preview is None:
            return
        self.animation = AnimationController(seed=self.preview.seed)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self.preview is None:
            return
        self.snapshot = self.preview.snapshot()
        self.decision = self.preview.decision
        now = time.monotonic()
        self.animation.sync(self.snapshot, self.decision, now)
        self.visual_frame = self.animation.advance(self.decision, now)
        self._render_state()
