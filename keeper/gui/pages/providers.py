"""AI Providers page: configuration status and live connectivity test."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Select, Static

from keeper.ai.base import AIRequest
from keeper.ai.prompts import build_analysis_prompt
from keeper.core.exceptions import ContributorError
from keeper.core.types import ProviderName
from keeper.gui.pages.base import BasePage


class ProvidersPage(BasePage):
    page_id = "providers"

    def __init__(self, shared) -> None:
        super().__init__()
        self._shared = shared

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("AI Providers", classes="page-title")
            yield DataTable(id="providers", cursor_type="row")
            with Horizontal():
                yield Select(
                    options=[(p.value, p.value) for p in ProviderName if p != ProviderName.AUTO],
                    value=None,
                    id="provider-select",
                    prompt="select provider to test",
                )
                yield Button("Test provider", id="test-provider", variant="primary")
            yield Static("", id="test-result", classes="detail")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-provider":
            self._test_provider()

    def _test_provider(self) -> None:
        shared = self._shared
        selector = self.query_one("#provider-select", Select)
        result_widget = self.query_one("#test-result", Static)
        if selector.value is None:
            result_widget.update("[yellow]select a provider first[/yellow]")
            return
        name = ProviderName(selector.value)
        try:
            provider = shared.app.providers.build(name)
            if not provider.is_configured():
                result_widget.update(f"[red]{name.value} is not configured[/red]")
                return
            result_widget.update("[cyan]calling provider...[/cyan]")
            request = AIRequest(
                prompt=build_analysis_prompt({"repository": "contributor"}),
                system="Reply with valid JSON only.",
                temperature=0.0,
                max_tokens=200,
                json_mode=True,
            )
            response = provider.complete(request)
            result_widget.update(
                f"[green]{name.value} OK[/green] ({response.model or '-'}, "
                f"{response.latency_ms}ms): {response.text[:120]}"
            )
        except ContributorError as exc:
            result_widget.update(f"[red]{exc}[/red]")
        except Exception as exc:  # noqa: BLE001
            result_widget.update(f"[red]{exc}[/red]")

    def refresh_content(self) -> None:
        shared = self._shared
        if shared.app is None:
            return
        status = shared.app.providers.status()
        table = self.query_one("#providers", DataTable)
        if table.row_count == 0:
            table.add_columns("Provider", "Model", "Configured", "Base URL")
        table.clear()
        for name, info in status.items():
            if name.startswith("_"):
                continue
            configured = "[green]yes[/green]" if info.get("configured") else "[red]no[/red]"
            table.add_row(
                name,
                info.get("model") or "-",
                configured,
                info.get("base_url") or "-",
            )
        active = status.get("_active")
        self.query_one("#test-result", Static).update(
            f"[dim]active selection: {active or 'none'}[/dim]"
        )