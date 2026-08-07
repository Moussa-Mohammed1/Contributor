"""Shared GUI widgets: stat tiles and text sparklines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class StatTile(Vertical):
    """A dashboard tile showing a label, a big value and an optional hint."""

    DEFAULT_CSS = """
    StatTile {
        border: round $primary;
        padding: 1 2;
        height: auto;
        margin: 0 1 1 0;
        width: 1fr;
        background: $surface;
    }
    StatTile .tile-value {
        text-style: bold;
        color: $text;
        content-align: center middle;
    }
    StatTile .tile-label {
        color: $text-muted;
        text-align: center;
    }
    StatTile .tile-hint {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, label: str, value: str = "-", hint: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._hint = hint

    def compose(self) -> ComposeResult:
        yield Static(self._value, classes="tile-value")
        yield Static(self._label, classes="tile-label")
        if self._hint:
            yield Static(self._hint, classes="tile-hint")

    def set_value(self, value: str, hint: str = "") -> None:
        value_widget = self.query_one(".tile-value", Static)
        value_widget.update(value)
        if self._hint:
            hint_widget = self.query_one(".tile-hint", Static)
            hint_widget.update(hint)


def sparkline(values: list[int | float], width: int = 40) -> str:
    """Render a unicode block sparkline for a series of numbers."""
    if not values:
        return ""
    max_value = max(values) or 1
    blocks = "▁▂▃▄▅▆▇█"
    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    return "".join(blocks[min(7, int(v / max_value * 8))] for v in sampled)


def horizontal_bars(data: list[tuple[str, int | float]], width: int = 30) -> str:
    """Render labeled horizontal bars (Rich text markup not used; plain)."""
    if not data:
        return "(no data)"
    max_value = max(v for _, v in data) or 1
    lines = []
    for label, value in data:
        filled = int(value / max_value * width)
        lines.append(f"{label:<22} {'█' * filled} {value}")
    return "\n".join(lines)