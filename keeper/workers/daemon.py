"""Daemon process management: start / stop / status.

The daemon is a detached child process running ``keeper.workers.runner``.
On Windows it uses ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` flags;
on POSIX a plain ``Popen`` with ``start_new_session=True``.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from keeper.config.manager import ConfigManager

logger = logging.getLogger(__name__)


class DaemonError(Exception):
    """Raised when daemon lifecycle operations fail."""


DaemonStatus = DaemonError  # alias kept for backward compatibility with early versions


def _pid(pid_path: Path) -> int | None:
    if not pid_path.is_file():
        return None
    try:
        value = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value or None


def is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_daemon(config_path: Path | None = None, *, foreground: bool = False) -> int:
    """Spawn the detached daemon process and return its PID (0 on failure)."""
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    pid_path = manager.config.pid_path

    existing = _pid(pid_path)
    if existing and is_alive(existing):
        raise DaemonStatus(f"Daemon already running (pid {existing})")

    module = "keeper.workers.runner"
    command = [sys.executable, "-m", module]
    if config_path is not None:
        command += ["--config", str(config_path)]
    if foreground:
        command += ["--foreground"]

    kwargs: dict = {
        "cwd": str(Path.cwd()),
        "stdout": subprocess.DEVNULL if not foreground else None,
        "stderr": subprocess.DEVNULL if not foreground else None,
        "stdin": subprocess.DEVNULL if not foreground else None,
    }
    if sys.platform == "win32" and not foreground:
        kwargs["creationflags"] = 0x00000208 | 0x00000008 | 0x08000000
        # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = not foreground

    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise DaemonError(f"Failed to start daemon: {exc}") from exc

    if foreground:
        return process.pid or 0

    # Wait up to 15s for the PID file (the child writes it after init).
    import time

    for _ in range(30):
        if pid_path.is_file():
            break
        if process.poll() is not None:
            raise DaemonError("Daemon exited during startup; see logs for details")
        time.sleep(0.5)
    return process.pid or _pid(pid_path) or 0


def stop_daemon(config_path: Path | None = None) -> tuple[int | None, str]:
    """Stop a running daemon. Returns (pid, message)."""
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    pid_path = manager.config.pid_path
    stop_path = manager.config.stop_flag_path

    pid = _pid(pid_path)
    if pid is None:
        return None, "daemon is not running"

    # Signal graceful shutdown via the stop flag first.
    try:
        stop_path.write_text("1", encoding="utf-8")
    except OSError:
        pass

    terminated = terminate(pid)
    for _ in range(30):
        if not is_alive(pid):
            break
        import time

        time.sleep(0.3)
    if terminated and not is_alive(pid):
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return pid, "stopped gracefully"
    # Force kill as a last resort.
    force = force_kill(pid)
    if force and not is_alive(pid):
        return pid, "stopped (forced)"
    return pid, "could not stop daemon"


def terminate(pid: int) -> bool:
    """Send SIGTERM (POSIX) or a WM_CLOSE-equivalent best-effort on Windows."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:  # noqa: BLE001
        return False


def force_kill(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:  # noqa: BLE001
        return False


def daemon_status(config_path: Path | None = None) -> dict:
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    pid = _pid(manager.config.pid_path)
    alive = is_alive(pid)
    return {
        "running": alive,
        "pid": pid,
        "pid_file": str(manager.config.pid_path),
        "config": str(manager.config_path),
    }


def pid_path(config_path: Path | None = None) -> Path:
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    return manager.config.pid_path