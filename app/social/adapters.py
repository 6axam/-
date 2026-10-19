"""Adapter contracts and deterministic offline fixtures; no live scraping."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from app.social.models import SocialEvent, SourceDescriptor


class SocialSourceAdapter(Protocol):
    source: SourceDescriptor

    async def connect(self) -> None: ...
    def capabilities(self) -> frozenset[str]: ...
    async def fetch_events(self, cursor: str | None) -> Iterable[dict[str, Any]]: ...
    def normalize(self, raw_event: dict[str, Any]) -> SocialEvent: ...
    async def get_cursor(self) -> str | None: ...
    async def acknowledge(self, event: SocialEvent) -> None: ...
    async def close(self) -> None: ...


class OfflineFixtureAdapter:
    """A deterministic adapter for own/explicitly permitted test material."""

    def __init__(self, source: SourceDescriptor, events: Iterable[SocialEvent]):
        self.source = source
        self._events = tuple(events)
        self._cursor: str | None = None
        self.acknowledged: list[str] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    def capabilities(self) -> frozenset[str]:
        return frozenset({"offline_fixture", "cursor", "deletion", "restore"})

    async def fetch_events(self, cursor: str | None) -> Iterable[dict[str, Any]]:
        start = int(cursor or 0)
        return tuple({"event": event, "cursor": str(index + 1)} for index, event in enumerate(self._events[start:], start=start))

    def normalize(self, raw_event: dict[str, Any]) -> SocialEvent:
        event = raw_event["event"]
        if not isinstance(event, SocialEvent) or event.source != self.source:
            raise ValueError("offline fixture event does not belong to this source")
        return event

    async def get_cursor(self) -> str | None:
        return self._cursor

    async def acknowledge(self, event: SocialEvent) -> None:
        self.acknowledged.append(event.event_id)
        index = next(index for index, candidate in enumerate(self._events, start=1) if candidate.event_id == event.event_id)
        self._cursor = str(index)

    async def close(self) -> None:
        self.connected = False
