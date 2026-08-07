"""Textual desktop dashboard for contributor.

Launch with ``keeper gui``. All nine pages read the state database (WAL mode,
safe for concurrent daemon access) and refresh on a timer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from keeper.app import Application, load_cli_app
from keeper.config.manager import ConfigManager
from keeper.gui.pages.config_page import ConfigPage
from keeper.gui.pages.dashboard import DashboardPage
from keeper.gui.pages.logs import LogsPage
from keeper.gui.pages.notifications import NotificationsPage
from keeper.gui.pages.providers import ProvidersPage
from keeper.gui.pages.repositories import RepositoriesPage
from keeper.gui.pages.schedules import SchedulesPage
from keeper.gui.pages.settings import SettingsPage
from keeper.gui.pages.statistics import StatisticsPage

logger = logging.getLogger(__name__)

POLL_SECONDS = 5


@dataclass(slots=True)
class Shared:
    """Mutable context handed to every page."""

    app: Application
    config: object  # AppConfig
    config_manager: ConfigManager
    config_path: Path | None


class ContributorApp(App[None]):
    """Main dashboard application."""

    TITLE = "contributor dashboard"
    SUB_TITLE = "AI-powered repository improvement"

    BINDINGS = [
        Binding("1", "goto('dashboard')", "Dashboard"),
        Binding("2", "goto('repositories')", "Repos"),
        Binding("3", "goto('schedules')", "Schedules"),
        Binding("4", "goto('logs')", "Logs"),
        Binding("5", "goto('statistics')", "Stats"),
        Binding("6", "goto('providers')", "Providers"),
        Binding("7", "goto('config')", "Config"),
        Binding("8", "goto('notifications')", "Notifs"),
        Binding("9", "goto('settings')", "Settings"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 22;
        border-right: solid $primary;
        padding: 0 1;
        background: $surface;
    }
    #sidebar .nav-title {
        color: $text-muted;
        text-style: bold;
        padding: 1 0;
    }
    #sidebar Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    #sidebar Button.selected {
        border: solid $accent;
    }
    #content {
        width: 1fr;
        padding: 1 2;
    }
    .page-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }
    .muted {
        color: $text-muted;
    }
    .detail {
        margin-top: 1;
    }
    .chart {
        border: round $primary;
        padding: 1 2;
        margin: 0 1 1 0;
        width: 1fr;
        height: auto;
    }
    DataTable {
        height: auto;
        max-height: 24;
        margin-bottom: 1;
    }
    """

    def __init__(self, shared: Shared) -> None:
        super().__init__()
        self._shared = shared
        self._pages = {
            "dashboard": DashboardPage(shared),
            "repositories": RepositoriesPage(shared),
            "schedules": SchedulesPage(shared),
            "logs": LogsPage(shared),
            "statistics": StatisticsPage(shared),
            "providers": ProvidersPage(shared),
            "config": ConfigPage(shared),
            "notifications": NotificationsPage(shared),
            "settings": SettingsPage(shared),
        }
        self._current = "dashboard"

    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="sidebar"):
                yield Static("contributor", classes="nav-title")
                for page_id, page in self._pages.items():
                    yield Button(page_id.capitalize(), id=f"nav-{page_id}")
            yield Container(self._pages["dashboard"], id="content")
        yield Footer()

    def on_mount(self) -> None:
        self._highlight("dashboard")
        self.set_interval(POLL_SECONDS, self._poll)

    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("nav-"):
            self.goto(button_id[4:])

    def action_goto(self, page_id: str) -> None:
        if page_id not in self._pages:
            return
        if page_id == self._current:
            self._pages[page_id].refresh_content()
            return
        content = self.query_one("#content", Container)
        current = self._pages[self._current]
        content.remove_children(current)
        content.mount(self._pages[page_id])
        self._current = page_id
        self._highlight(page_id)
        self._pages[page_id].refresh_content()

    def _highlight(self, page_id: str) -> None:
        for candidate_id, page in self._pages.items():
            button = self.query_one(f"#nav-{candidate_id}", Button)
            button.remove_class("selected")
        self.query_one(f"#nav-{page_id}", Button).add_class("selected")

    def _poll(self) -> None:
        try:
            page = self._pages[self._current]
            page.refresh_content()
        except Exception:  # noqa: BLE001 - the dashboard must survive page errors
            logger.exception("Page refresh failed for %s", self._current)

    def action_quit(self) -> None:
        self._shutdown()
        self.exit()

    def on_unmount(self) -> None:
        self._shutdown()

    def _shutdown(self) -> None:
        if self._shared.app is not None:
            try:
                self._shared.app.close()
            except Exception:  # noqa: BLE001
                logger.exception("Error closing application")
            self._shared.app = None


def run_gui(config_path: Path | None = None) -> None:
    """Build the shared context and launch the dashboard."""
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    app_context = load_cli_app(config_path)
    shared = Shared(
        app=app_context,
        config=manager.config,
        config_manager=manager,
        config_path=config_path,
    )
    ContributorApp(shared).run()


if __name__ == "__main__":
    run_gui()