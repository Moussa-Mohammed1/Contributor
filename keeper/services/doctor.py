"""Doctor: environment, configuration and repository diagnostics.

Implements `keeper doctor` and produces a structured diagnostic report used
by both the CLI and the GUI.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import logging
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from keeper.config.models import AppConfig
from keeper.core.types import ProviderName
from keeper.database.engine import Database
from keeper.git.health import RepoHealthChecker
from keeper.utils.time import now_utc

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    ok: bool = True
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "OK" if self.ok else "FAIL"


@dataclass(slots=True)
class DoctorReport:
    """Full diagnostic report."""

    generated_at: str
    python: str
    platform: str
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    @property
    def passed(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "python": self.python,
            "platform": self.platform,
            "ok": self.passed,
            "checks": [
                {"name": c.name, "ok": c.ok, "message": c.message, "status": c.status,
                 "warnings": c.warnings}
                for c in self.checks
            ],
        }


_MIN_DEPENDENCIES = {
    "GitPython": "3.1.44",
    "APScheduler": "3.10.0",
    "SQLAlchemy": "2.0.40",
    "pydantic": "2.10.0",
    "typer": "0.15.0",
    "rich": "13.9.0",
    "PyYAML": "6.0.2",
    "httpx": "0.28.0",
}


def _version_at_least(installed: str | None, minimum: str) -> bool:
    if not installed:
        return False

    def key(s: str):
        return tuple(int(p) if p.isdigit() else p for p in s.split("."))

    return key(installed) >= key(minimum)


class Doctor:
    """Runs a complete environment / configuration / repository diagnostic."""

    def __init__(self, config: AppConfig, database: Database | None = None) -> None:
        self._config = config
        self._database = database
        self._health = RepoHealthChecker()

    def run(self) -> DoctorReport:
        report = DoctorReport(
            generated_at=now_utc().isoformat(),
            python=platform.python_version(),
            platform=platform.platform(),
        )
        self._check_python(report)
        self._check_git(report)
        self._check_dependencies(report)
        self._check_config(report)
        self._check_database(report)
        self._check_repositories(report)
        self._check_providers(report)
        self._check_daemon(report)
        return report

    # ------------------------------------------------------------------

    def _check_python(self, report: DoctorReport) -> None:
        version = sys.version_info
        report.add(
            CheckResult(
                name="python",
                ok=version >= (3, 13),
                message=f"{platform.python_version()} (requires >= 3.13)",
            )
        )

    def _check_git(self, report: DoctorReport) -> None:
        if not shutil.which("git"):
            report.add(CheckResult(name="git", ok=False, message="git binary not found on PATH"))
            return
        try:
            version = subprocess.run(
                ["git", "--version"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            report.add(CheckResult(name="git", ok=True, message=version))
        except Exception:  # noqa: BLE001
            report.add(CheckResult(name="git", ok=False, message="git failed to execute"))

    def _check_dependencies(self, report: DoctorReport) -> None:
        for package, minimum in _MIN_DEPENDENCIES.items():
            installed: str | None = None
            try:
                installed = md.version(package)
            except md.PackageNotFoundError:
                module = package.lower().replace("-", "_")
                try:
                    mod = importlib.import_module(module)
                    installed = getattr(mod, "__version__", None)
                except (ImportError, ModuleNotFoundError):
                    installed = None
            ok = _version_at_least(installed, minimum)
            report.add(
                CheckResult(
                    name=f"dep:{package}",
                    ok=ok,
                    message=f"{installed or 'NOT INSTALLED'} (required >= {minimum})",
                )
            )

    def _check_config(self, report: DoctorReport) -> None:
        from keeper.config.loader import validate_config

        warnings = validate_config(self._config)
        report.add(
            CheckResult(
                name="config",
                ok=True,
                message=(
                    f"valid (window {self._config.schedule.start_date}.."
                    f"{self._config.schedule.end_date}, "
                    f"mode {self._config.schedule.plan_mode.value})"
                ),
                warnings=warnings,
            )
        )

    def _check_database(self, report: DoctorReport) -> None:
        if self._database is None:
            report.add(CheckResult(name="database", ok=False, message="not connected"))
            return
        try:
            with self._database.session() as session:
                session.execute(select_one_stmt())
            report.add(
                CheckResult(name="database", ok=True, message=f"connected at {self._database.path}")
            )
        except Exception as exc:  # noqa: BLE001
            report.add(CheckResult(name="database", ok=False, message=str(exc)))

    def _check_repositories(self, report: DoctorReport) -> None:
        if not self._config.repositories:
            report.add(CheckResult(name="repos", ok=False, message="no repositories configured"))
            return
        reports = self._health.refresh_all([r.path for r in self._config.repositories])
        for path, health in reports.items():
            report.add(
                CheckResult(
                    name=f"repo:{Path(path).name}",
                    ok=health.healthy.value in ("healthy", "degraded"),
                    message=f"{health.healthy.value} ({health.score:.0f}/100): {health.message}",
                )
            )

    def _check_providers(self, report: DoctorReport) -> None:
        configured: list[str] = []
        missing: list[str] = []
        local = {ProviderName.OLLAMA, ProviderName.LMSTUDIO}
        for provider in ProviderName:
            if provider == ProviderName.AUTO:
                continue
            creds = self._config.ai.credentials_for(provider)
            if provider in local:
                if creds.base_url:
                    configured.append(provider.value)
                else:
                    missing.append(provider.value)
            elif creds.api_key:
                configured.append(provider.value)
            else:
                missing.append(provider.value)
        warnings = [f"not configured: {', '.join(missing)}"] if missing else []
        report.add(
            CheckResult(
                name="ai-providers",
                ok=bool(configured),
                message=f"configured: {', '.join(configured) or 'none'}",
                warnings=warnings,
            )
        )

    def _check_daemon(self, report: DoctorReport) -> None:
        pid_file = self._config.pid_path
        if not pid_file.is_file():
            report.add(CheckResult(name="daemon", ok=False, message="not running"))
            return
        try:
            process_id = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            report.add(CheckResult(name="daemon", ok=False, message="pid file invalid"))
            return
        alive = self._pid_alive(process_id)
        report.add(
            CheckResult(
                name="daemon",
                ok=alive,
                message=f"pid {process_id} {'running' if alive else 'stale'}",
            )
        )

    def _pid_alive(self, process_id: int) -> bool:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, process_id)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        try:
            import os

            os.kill(process_id, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def select_one_stmt() -> object:
    from sqlalchemy import text

    return text("SELECT 1")