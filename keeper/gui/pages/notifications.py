"""Notifications page: recent notification history."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from keeper.database import repo as dataset
from keeper.gui.pages.base import BasePage

_LEVEL_COLORS = {"info": "white", "warning": "yellow", "error": "red"}


class NotificationsPage(BasePage):
    page_id = "notifications"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Notifications", classes="page-title")
            yield DataTable(id="notifications", cursor_type="row")

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        with shared.app.session() as session:
            rows = dataset.list_notifications(session, limit=100)
        table = self.query_one("#notifications", DataTable)
        if table.row_count == 0:
            table.add_columns("Time", "Level", "Title", "Message")
        table.clear()
        for row in rows:
            color = _LEVEL_COLORS.get(row.level, "white")
            table.add_row(
                row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "-",
                f"[{color}]{row.level}[/{color}]",
                row.title,
                row.message[:120],
            )