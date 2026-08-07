"""Logs page: tail live structured logs from the database."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from keeper.database import repo as dataset
from keeper.gui.pages.base import BasePage

_LEVEL_COLORS = {
    "DEBUG": "dim",
    "INFO": "white",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red bold",
}


class LogsPage(BasePage):
    page_id = "logs"

    def __init__(self, shared, limit: int = 500) -> None:
        super().__init__()
        self._shared = shared
        self._limit = limit

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Logs", classes="page-title")
            yield DataTable(id="logs", cursor_type="row")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        with shared.app.session() as session:
            rows = dataset.list_logs(session, limit=self._limit)
        table = self.query_one("#logs", DataTable)
        if table.row_count == 0:
            table.add_columns("Time", "Level", "Logger", "Message")
        table.clear()
        for row in rows:
            color = _LEVEL_COLORS.get(row.level, "white")
            table.add_row(
                row.ts.strftime("%H:%M:%S"),
                f"[{color}]{row.level}[/{color}]",
                row.logger.split(".")[-1],
                row.message[:160],
            )