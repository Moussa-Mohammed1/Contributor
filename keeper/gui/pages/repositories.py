"""Repositories page: registry table with health and details."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Static

from keeper.gui.pages.base import BasePage

_COLORS = {"healthy": "green", "degraded": "yellow", "unhealthy": "red", "unknown": "white"}


class RepositoriesPage(BasePage):
    page_id = "repositories"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Repositories", classes="page-title")
            with Horizontal():
                yield Button("Refresh health", id="refresh-health", variant="primary")
                yield Static("", id="health-hint", classes="muted")
            yield DataTable(id="repos", cursor_type="row")
            yield Static("", id="repo-detail", classes="detail")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-health":
            self._shared.app.repo_manager.check_health(self._shared.app.session(), self._shared.config)
            self.query_one("#health-hint", Static).update("Health refreshed")
            self.refresh_content()

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        with shared.app.session() as session:
            rows = shared.app.repo_manager.snapshots(session, shared.config)
        table = self.query_one("#repos", DataTable)
        if table.row_count == 0:
            table.add_columns("Name", "Path", "Branch", "Health", "Score", "Last commit")
        table.clear()
        for row in rows:
            color = _COLORS.get(row.health, "white")
            table.add_row(
                row.name,
                row.path,
                row.default_branch,
                f"[{color}]{row.health}[/{color}]",
                f"{row.health_score:.0f}",
                (row.last_commit_at or "-")[:16].replace("T", " "),
            )
        self._detail(rows)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        shared = self._shared
        with shared.app.session() as session:
            rows = shared.app.repo_manager.snapshots(session, shared.config)
        index = event.cursor_row
        if 0 <= index < len(rows):
            row = rows[index]
            detail = (
                f"[bold]{row.name}[/bold]\n"
                f"path: {row.path}\n"
                f"health: {row.health} ({row.health_score:.0f}/100)\n"
                f"message: {row.health_message or '-'}\n"
                f"branch: {row.default_branch} · max commits/day: {row.max_commits_per_day}\n"
                f"last commit: {row.last_commit_hash or '-'} at {row.last_commit_at or '-'}"
            )
            self.query_one("#repo-detail", Static).update(detail)

    def _detail(self, rows) -> None:
        if rows:
            worst = min(rows, key=lambda r: r.health_score)
            self.query_one("#health-hint", Static).update(
                f"worst: {worst.name} ({worst.health})" if worst.health != "healthy" else "all healthy"
            )