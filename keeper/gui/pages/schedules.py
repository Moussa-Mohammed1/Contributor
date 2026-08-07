"""Schedules page: day plans, slot lists and execution history."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Select, Static

from keeper.database import repo as dataset
from keeper.gui.pages.base import BasePage
from keeper.utils.time import utc_to_tz


class SchedulesPage(BasePage):
    page_id = "schedules"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Schedules", classes="page-title")
            yield Static("Select a day:", classes="muted")
            yield Select(options=[], id="day-select", value=None)
            yield DataTable(id="slots", cursor_type="row")
            yield Static("Executions", classes="section-title")
            yield DataTable(id="executions", cursor_type="row")

    def on_mount(self) -> None:
        self.refresh_content()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "day-select":
            self.refresh_content()

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        with shared.app.session() as session:
            schedules = dataset.list_schedules(session, limit=30)
            executions = dataset.list_executions(session, limit=15)

        selector = self.query_one("#day-select", Select)
        selected = selector.value
        options = [(s.plan_date, s.plan_date) for s in schedules] or [("(no plans yet)", "none")]
        selector.set_options(options)
        if selected and any(s.plan_date == selected for s in schedules):
            pass
        elif schedules:
            selected = schedules[0].plan_date

        tz = shared.config.schedule.timezone
        table = self.query_one("#slots", DataTable)
        if table.row_count == 0:
            table.add_columns("Time", "Repo", "State", "Commit", "Attempts")
        table.clear()
        if schedules:
            current = next((s for s in schedules if s.plan_date == selected), schedules[0])
            for slot in dataset.list_slots(shared.app.session(), current.id):
                table.add_row(
                    utc_to_tz(slot.scheduled_at, tz).strftime("%H:%M"),
                    slot.repo_name,
                    slot.state,
                    slot.commit_hash[:10] if slot.commit_hash else "-",
                    str(slot.attempts),
                )

        exec_table = self.query_one("#executions", DataTable)
        if exec_table.row_count == 0:
            exec_table.add_columns("Kind", "Status", "Started", "Summary")
        exec_table.clear()
        for record in executions:
            exec_table.add_row(
                record.kind,
                record.status,
                record.started_at.strftime("%Y-%m-%d %H:%M") if record.started_at else "-",
                record.summary or "-",
            )