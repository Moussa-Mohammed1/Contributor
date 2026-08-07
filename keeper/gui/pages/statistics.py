"""Statistics page: daily/weekly/monthly series, repo activity, AI rates."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Static

from keeper.gui.pages.base import BasePage
from keeper.gui.widgets import horizontal_bars, sparkline


class StatisticsPage(BasePage):
    page_id = "statistics"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Statistics", classes="page-title")
            with Horizontal():
                yield Static("", id="spark-daily", classes="chart")
                yield Static("", id="spark-weekly", classes="chart")
            yield Static("Repository activity (30 days)", classes="section-title")
            yield Static("", id="repo-bars", classes="chart")
            yield DataTable(id="rates", cursor_type="row")

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        with shared.app.session() as session:
            daily = shared.app.stats.daily_series(session, 30)
            weekly = shared.app.stats.weekly_series(session, 12)
            repos = shared.app.stats.repo_activity(session, 30)
            ai = shared.app.stats.ai_success_rate(session, 30)
            failures = shared.app.stats.failure_rate(session, 30)
            exec_time = shared.app.stats.execution_time(session, 7)

        self.query_one("#spark-daily", Static).update(
            f"Daily commits (30d):\n{sparkline([d['commits'] for d in daily])}"
        )
        self.query_one("#spark-weekly", Static).update(
            f"Weekly commits (12w):\n{sparkline([w['commits'] for w in weekly])}"
        )
        self.query_one("#repo-bars", Static).update(
            horizontal_bars([(Path(r["repo"]).name, r["commits"]) for r in repos])
        )

        table = self.query_one("#rates", DataTable)
        if table.row_count == 0:
            table.add_columns("Metric", "Value")
        table.clear()
        table.add_row("AI success rate", f"{ai['rate']}% ({ai['successful']}/{ai['calls']} calls)")
        table.add_row("Failure rate", f"{failures['rate']}% ({failures['failures']} failures)")
        avg_ms = sum(e["duration_ms"] for e in exec_time) / max(1, len(exec_time))
        table.add_row("Avg commit duration", f"{avg_ms:.0f} ms")