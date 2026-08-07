"""Dashboard page: today's plan, progress, health, AI status and timeline."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Static

from keeper.database import repo as dataset
from keeper.gui.pages.base import BasePage
from keeper.gui.widgets import StatTile
from keeper.utils.time import local_date, utc_to_tz

_STATE_COLORS = {
    "succeeded": "green",
    "committed": "green",
    "running": "cyan",
    "planned": "white",
    "failed": "red",
    "skipped": "yellow",
}


class DashboardPage(BasePage):
    page_id = "dashboard"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Dashboard", classes="page-title")
            with Horizontal():
                yield StatTile("Target today", "-", "planned commits")
                yield StatTile("Completed", "-", "today")
                yield StatTile("Remaining", "-", "still to run")
                yield StatTile("AI status", "-", "active provider")
            with Horizontal():
                yield StatTile("Repositories", "-", "healthy")
                yield StatTile("Last commit", "-", "repo / time")
                yield StatTile("Next execution", "-", "scheduled slot")
                yield StatTile("Current repo", "-", "being worked on")
            yield Static("Today's timeline", classes="section-title")
            yield DataTable(id="timeline", cursor_type="row")
            yield Static("", id="no-plan-hint", classes="muted")

    def refresh_content(self) -> None:
        shared = self._shared
        app = shared.app
        if app is None:
            return
        tz = shared.config.schedule.timezone
        day = local_date(tz).isoformat()
        with app.session() as session:
            schedule = dataset.get_schedule(session, day)
            slots = dataset.list_slots(session, schedule.id) if schedule else []
            repos = app.repo_manager.snapshots(session, shared.config)
            last_commit = dataset.list_commits(session, limit=1)
            next_slot = app.tracker.next_planned(session)

        target = schedule.target_commits if schedule else 0
        done = sum(1 for s in slots if s.state in ("succeeded", "committed"))
        remaining = sum(1 for s in slots if s.state in ("planned", "running"))

        self._tile("Target today", str(target))
        self._tile("Completed", str(done), f"{done}/{target or 0}")
        self._tile("Remaining", str(remaining))
        self._tile("Repositories", str(len(repos)),
                   f"{sum(1 for r in repos if r.health == 'healthy')} healthy")

        if last_commit:
            record = last_commit[0]
            self._tile("Last commit", record.hash[:8],
                       f"{Path(record.repo_path).name} · {record.category}")
        else:
            self._tile("Last commit", "-")

        if next_slot is not None:
            local_time = utc_to_tz(next_slot.scheduled_at, tz).strftime("%H:%M")
            self._tile("Next execution", local_time, next_slot.repo_name)
        else:
            self._tile("Next execution", "-")

        running = next((s for s in slots if s.state == "running"), None)
        self._tile("Current repo", running.repo_name if running else "-")

        status = shared.app.providers.status() if shared.app else {}
        active = status.get("_active")
        self._tile("AI status", active or "none", f"provider auto-selection")

        table = self.query_one("#timeline", DataTable)
        if table.row_count == 0:
            table.add_columns("Time", "Repository", "State", "Commit")
        table.clear()
        for slot in slots:
            color = _STATE_COLORS.get(slot.state, "white")
            table.add_row(
                utc_to_tz(slot.scheduled_at, tz).strftime("%H:%M"),
                slot.repo_name,
                f"[{color}]{slot.state}[/{color}]",
                slot.commit_hash[:10] if slot.commit_hash else "-",
            )
        hint = self.query_one("#no-plan-hint", Static)
        if not schedule:
            hint.update(f"No plan for {day} yet. The daemon creates it at {shared.config.schedule.run_time}.")
        else:
            hint.update("")

    def _tile(self, label: str, value: str, hint: str = "") -> None:
        try:
            tile = next(t for t in self.query(StatTile) if t._label == label)
        except StopIteration:
            return
        tile.set_value(value, hint)