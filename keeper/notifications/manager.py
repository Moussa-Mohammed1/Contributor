"""Notification manager: dispatches events to all enabled backends and history."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from keeper.database import repo as dataset
from keeper.notifications.backends import (
    ConsoleBackend,
    DesktopBackend,
    NotificationBackend,
)

logger = logging.getLogger(__name__)

EVENT_NAMES = {
    "schedule_started": "Today's schedule started",
    "commit_completed": "Commit completed",
    "push_failed": "Push failed",
    "repo_unhealthy": "Repository unhealthy",
    "schedule_completed": "Schedule completed",
    "day_empty": "No commits planned",
}


@dataclass(slots=True)
class Notification:
    """A user-facing notification with a level."""

    title: str
    message: str
    level: str = "info"


class NotificationManager:
    """Routes notifications to desktop + console and persists history."""

    def __init__(self, *, desktop: bool = True, console: bool = False) -> None:
        self._backends: list[NotificationBackend] = []
        if console:
            self._backends.append(ConsoleBackend())
        if desktop:
            self._backends.append(DesktopBackend())

    def add_backend(self, backend: NotificationBackend) -> None:
        self._backends.append(backend)

    def notify(self, title: str, message: str, *, level: str = "info",
               session: Session | None = None) -> None:
        """Deliver a notification and store it in history when a session is given."""
        delivered = False
        for backend in self._backends:
            delivered = backend.notify(title, message, level=level) or delivered
        logger.debug("Notification %r -> %s", title, delivered)
        if session is not None:
            try:
                dataset.add_notification(session, title=title, message=message, level=level,
                                         delivered=delivered)
            except Exception:  # noqa: BLE001
                logger.warning("Could not persist notification", exc_info=True)

    # -- convenience wrappers for the known event types ---------------- ---

    def event(self, event_name: str, context: str = "", *, session: Session | None = None) -> None:
        title = EVENT_NAMES.get(event_name, event_name.replace("_", " ").title())
        message = context or title
        level = "error" if event_name in ("push_failed", "repo_unhealthy") else "info"
        self.notify(title, message, level=level, session=session)