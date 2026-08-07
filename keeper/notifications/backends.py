"""Notification backends: desktop and console."""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class NotificationBackend(ABC):
    """A destination for user-facing notifications."""

    name = "base"

    @abstractmethod
    def notify(self, title: str, message: str, *, level: str = "info") -> bool:
        """Deliver a notification. Returns True when delivered."""


class ConsoleBackend(NotificationBackend):
    """Prints notifications to the log stream (always available)."""

    name = "console"

    def notify(self, title: str, message: str, *, level: str = "info") -> bool:
        log_level = {"error": logging.ERROR, "warning": logging.WARNING}.get(level, logging.INFO)
        logger.log(log_level, "%s: %s", title, message)
        return True


class DesktopBackend(NotificationBackend):
    """Platform-native desktop toast:

      * Windows: winotify (when installed) with PowerShell fallback,
      * Linux:   notify-send,
      * macOS:   osascript.
    """

    name = "desktop"

    def notify(self, title: str, message: str, *, level: str = "info") -> bool:
        try:
            if self._notify_windows(title, message, level):
                return True
            if self._notify_linux(title, message, level):
                return True
            if self._notify_macos(title, message, level):
                return True
        except Exception:  # noqa: BLE001
            logger.warning("Desktop notification failed", exc_info=True)
        return False

    # -- platform implementations ---------------------------------------

    def _notify_windows(self, title: str, message: str, level: str) -> bool:
        import sys

        if sys.platform != "win32":
            return False
        try:
            from winotify import Notification, Level  # type: ignore

            win_level = {
                "error": Level.ERROR,
                "warning": Level.WARNING,
                "info": Level.INFO,
            }.get(level, Level.INFO)
            toast = Notification(
                app_id="contributor",
                title=title,
                msg=message,
                duration="short",
            )
            toast.set_icon(icon_path=str(Path.home()))
            toast.set_level(win_level)
            toast.show()
            return True
        except ImportError:
            return self._notify_windows_powershell(title, message)

    def _notify_windows_powershell(self, title: str, message: str) -> bool:
        """PowerShell fallback using the built-in BurntToast-less toast API."""
        import sys

        if sys.platform != "win32":
            return False
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "[System.Windows.Forms.NotifyIcon]$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            f"$n.ShowBalloonTip(4000, '{title}', '{message}', [System.Windows.Forms.ToolTipIcon]::Info)"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    def _notify_linux(self, title: str, message: str, level: str) -> bool:
        import sys

        if sys.platform not in ("linux", "darwin") and not sys.platform.startswith("linux"):
            return False
        if not shutil.which("notify-send"):
            return False
        urgency = {"error": "critical", "warning": "normal", "info": "low"}.get(level, "normal")
        result = subprocess.run(
            ["notify-send", "-u", urgency, title, message],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0

    def _notify_macos(self, title: str, message: str, level: str) -> bool:
        import sys

        if sys.platform != "darwin":
            return False
        script = f'display notification "{message}" with title "{title}"'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0