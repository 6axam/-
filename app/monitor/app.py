from __future__ import annotations

import asyncio
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key
from textual.widgets import Footer, Static

from app.monitor import POLL_INTERVAL_SECONDS
from app.monitor.theme import ACCENT, GOOD, MUTED
from app.monitor.views import emotions, events, history, overview, prompt, state, telegram, tokens


CSS = """
Screen { color: #d8e1eb; background: transparent; }
#header { height: 1; padding: 0 1; color: #d8e1eb; background: transparent; }
#body { height: 1fr; padding: 0 1; overflow: auto; background: transparent; }
#content { width: 100%; height: auto; }
Footer { background: transparent; color: #8294a6; }
"""


class AnyaMonitorApp(App[None]):
    """A read-only, responsive operational dashboard; no backend side effects."""
    TITLE = "Аня Monitor"
    CSS = CSS
    BINDINGS = [
        Binding("f1", "page('overview')", "обзор"), Binding("f2", "page('tokens')", "токены"),
        Binding("f3", "page('prompt')", "промпт"), Binding("f4", "page('emotions')", "эмоции"),
        Binding("f5", "page('history')", "история"), Binding("f6", "page('events')", "события"),
        Binding("f7", "page('state')", "состояние"), Binding("f8", "page('telegram')", "Telegram"),
        Binding("r", "refresh", "обновить"), Binding("q", "quit", "выход"),
    ]

    def __init__(self, reader) -> None:
        super().__init__(); self.reader=reader; self.data={"available":False}; self.current_page="overview"; self.selected_generation=0

    def compose(self) -> ComposeResult:
        yield Static("Аня Monitor · подключение к SQLite…", id="header")
        with Container(id="body"): yield Static("Загрузка…", id="content")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(POLL_INTERVAL_SECONDS, self.refresh_data); self.refresh_data()

    def refresh_data(self) -> None: self.run_worker(self._poll(), group="monitor-poll", exclusive=True)
    async def _poll(self) -> None: self.data=await asyncio.to_thread(self.reader.poll); self._render()
    def action_refresh(self) -> None: self.refresh_data()
    def action_page(self, page: str) -> None: self.current_page=page; self._render()

    def on_key(self, event: Key) -> None:
        if self.current_page == "prompt" and event.key in {"up", "down"}:
            delta = -1 if event.key == "up" else 1
            self.selected_generation=max(0, min(self.selected_generation+delta, max(0,len(self.data.get("generations",[]))-1))); self._render(); event.stop()

    def _render(self) -> None:
        header=self.query_one("#header", Static); content=self.query_one("#content", Static)
        if not self.data.get("available"):
            header.update(Text("Аня Monitor · SQLite недоступна · read-only", style=MUTED)); content.update(Text("База пока недоступна. Monitor не создаёт её и не запускает миграции.", style=MUTED)); return
        latest=self.data.get("last_generation", {}); prompt_tokens=latest.get("prompt_tokens") or 0; hit=(latest.get("cached_tokens") or 0)/prompt_tokens if prompt_tokens else None
        header.update(Text.assemble(("Аня Monitor", f"bold {ACCENT}"), ("   OpenRouter" if latest else "   ожидание данных", MUTED), (f"   {latest.get('model','')}" if latest else "", "white"), (f"   cache {hit*100:.0f}%" if hit is not None else "   cache —", GOOD if hit is not None and hit >= .6 else MUTED), (f"   {_cost(latest.get('cost'))}" if latest else "   cost —", MUTED), ("   SQLite RO", GOOD), (f"   {datetime.now().astimezone():%H:%M}", MUTED)))
        renderers={"overview":overview,"tokens":tokens,"prompt":lambda data:prompt(data,self.selected_generation),"emotions":emotions,"history":history,"events":events,"state":state,"telegram":telegram}
        content.update(renderers[self.current_page](self.data))


def _cost(value) -> str: return f"${value:.5f}" if isinstance(value,(int,float)) else "—"
