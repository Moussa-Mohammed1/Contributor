"""Configuration page: show current configuration and validate."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from keeper.config.loader import validate_config
from keeper.gui.pages.base import BasePage


class ConfigPage(BasePage):
    page_id = "config"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Configuration", classes="page-title")
            yield Static("", id="config-path", classes="muted")
            yield Static("", id="config-yaml")

    def refresh_content(self) -> None:
        shared = self._shared
        from keeper.config.loader import dump_config

        self.query_one("#config-path", Static).update(
            f"file: {shared.config_manager.config_path}"
        )
        yaml_text = dump_config(shared.config)
        warnings = validate_config(shared.config)
        if warnings:
            yaml_text += "\n\n# warnings:\n" + "\n".join(f"# - {w}" for w in warnings)
        self.query_one("#config-yaml", Static).update(yaml_text)