"""Page base class for the contributor dashboard."""

from __future__ import annotations

from textual.widget import Widget


class BasePage(Widget):
    """All dashboard pages implement a pollable refresh contract."""

    page_id = "base"

    def refresh_content(self) -> None:
        """Re-render the page from the latest application state."""
        raise NotImplementedError(f"{type(self).__name__} must implement refresh_content()")