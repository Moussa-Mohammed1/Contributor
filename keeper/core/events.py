"""Lightweight synchronous pub/sub event bus.

Used to decouple producers (orchestrator, execution service, workers) from
consumers (GUI, API server, notifications). There is no global state: an
instance is created in the composition root and injected everywhere.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[["Event"], None]


@dataclass(slots=True)
class Event:
    """A single bus event."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class EventBus:
    """Thread-safe pub/sub bus with optional event history for late subscribers."""

    def __init__(self, keep_history: int = 200) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._any_subscribers: list[EventHandler] = []
        self._history: list[Event] = []
        self._keep_history = keep_history
        self._lock = threading.RLock()

    def subscribe(self, name: str | None, handler: EventHandler) -> None:
        """Subscribe to a specific event name or to all events when ``name`` is None."""
        with self._lock:
            if name is None:
                self._any_subscribers.append(handler)
            else:
                self._subscribers[name].append(handler)

    def unsubscribe(self, name: str | None, handler: EventHandler) -> None:
        with self._lock:
            if name is None:
                self._any_subscribers.remove(handler)
            else:
                self._subscribers[name].remove(handler)

    def publish(self, name: str, payload: dict[str, Any] | None = None) -> Event:
        """Publish an event and deliver it synchronously to all subscribers."""
        event = Event(name=name, payload=payload or {})
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._keep_history:
                del self._history[:-self._keep_history]
            handlers = list(self._any_subscribers) + list(self._subscribers.get(name, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - a subscriber must never break the bus
                logger.exception("Event handler %r failed for event %r", handler, name)
        return event

    def history(self, name: str | None = None, limit: int = 50) -> list[Event]:
        """Return recent events, optionally filtered by name."""
        with self._lock:
            if name is None:
                events = list(self._history)
            else:
                events = [e for e in self._history if e.name == name]
        return events[-limit:]
