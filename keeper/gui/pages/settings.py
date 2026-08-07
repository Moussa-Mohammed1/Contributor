"""Settings page: daemon control, dry-run toggle, paths and doctor report."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Static

from keeper.core.exceptions import ContributorError
from keeper.gui.pages.base import BasePage
from keeper.workers import daemon


class SettingsPage(BasePage):
    page_id = "settings"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Settings", classes="page-title")
            with Horizontal():
                yield Button("Start daemon", id="daemon-start", variant="success")
                yield Button("Stop daemon", id="daemon-stop", variant="error")
                yield Button("Run doctor", id="doctor-run", variant="primary")
                yield Button("Trigger tick", id="tick-run", variant="default")
            yield Static("", id="daemon-status", classes="muted")
            yield Static("", id="action-result", classes="detail")
            yield Static("Paths", classes="section-title")
            yield Static("", id="paths")
            yield Static("Doctor report", classes="section-title")
            yield Static("", id="doctor-report", classes="chart")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        result = self.query_one("#action-result", Static)
        button_id = event.button.id or ""
        try:
            if button_id == "daemon-start":
                daemon.start_daemon(self._shared.config_path)
                result.update("[green]daemon started[/green]")
            elif button_id == "daemon-stop":
                pid, message = daemon.stop_daemon(self._shared.config_path)
                result.update(f"[green]{message}[/green]" if pid else "[yellow]not running[/yellow]")
            elif button_id == "tick-run":
                summary = self._shared.app.orchestrator.run_now()
                result.update(f"tick result: {summary}")
            elif button_id == "doctor-run":
                report = self._shared.app.doctor.run()
                lines = [f"[{'green' if c.ok else 'red'}]{c.name}:[/] {c.message}" for c in report.checks]
                self.query_one("#doctor-report", Static).update("\n".join(lines))
                result.update(f"[{'green' if report.passed else 'red'}]doctor {'passed' if report.passed else 'found issues'}[/]")
        except (daemon.DaemonError, ContributorError) as exc:
            result.update(f"[red]{exc}[/red]")
        self.refresh_content()

    def refresh_content(self) -> None:
        shared = self._shared
        status = daemon.daemon_status(shared.config_path)
        self.query_one("#daemon-status", Static).update(
            f"daemon: {'[green]running[/green]' if status['running'] else '[yellow]not running[/yellow]'} "
            f"(pid {status['pid'] or '-'})"
        )
        cfg = shared.config
        self.query_one("#paths", Static).update(
            f"config: {shared.config_manager.config_path}\n"
            f"data dir: {cfg.resolved_data_dir()}\n"
            f"database: {cfg.database_path}\n"
            f"logs: {cfg.log_dir}\n"
            f"dry run: {cfg.schedule.dry_run}\n"
            f"run time: {cfg.schedule.run_time} ({cfg.schedule.timezone})"
        )