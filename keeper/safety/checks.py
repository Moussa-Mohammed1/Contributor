"""Safety engine: run formatters, linters and optional tests before committing.

Every command is run with a hard timeout inside the repository directory. On
failure the mutation has already been rolled back by the caller; this module
records the failure together with the offending command output.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 180


@dataclass(slots=True)
class CommandResult:
    """Aggregate result of the safety gate."""

    formatter_ok: bool = True
    linter_ok: bool = True
    tests_ok: bool = True
    failed_stage: str | None = None
    command: str | None = None
    output: str = ""
    command_exit: int | None = None
    commands_run: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.formatter_ok and self.linter_ok and self.tests_ok


def which(binary: str) -> bool:
    """True when the binary is on PATH."""
    return shutil.which(binary) is not None


def on_windows() -> bool:
    return sys.platform == "win32"


class SafetyEngine:
    """Discover and run the appropriate formatter / linter / test commands."""

    def __init__(self, *, run_formatter: bool = True, run_linter: bool = True,
                 run_tests: bool = False, test_timeout: int = 300, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._run_formatter = run_formatter
        self._run_linter = run_linter
        self._run_tests = run_tests
        self._test_timeout = test_timeout
        self._timeout = timeout

    # ------------------------------------------------------------------

    def detect_commands(self, repo_path: str | Path) -> dict[str, list[str]]:
        """Discover the best safety commands based on repository contents."""
        root = Path(repo_path)
        commands: dict[str, list[str]] = {"formatter": [], "linter": [], "test": []}

        has_py = self._has_file(root, ".py")
        has_js = self._has_file(root, ".js") or self._has_file(root, ".ts")
        has_go = (root / "go.mod").exists()
        has_rust = (root / "Cargo.toml").exists()

        if has_py:
            if which("ruff"):
                commands["formatter"].append("ruff format .")
                commands["linter"].append("ruff check .")
            elif which("black"):
                commands["formatter"].append("black .")
            elif which("autopep8"):
                commands["formatter"].append("autopep8 -r -i .")
            if not commands["linter"]:
                if which("flake8"):
                    commands["linter"].append("flake8 .")
                elif which("pylint"):
                    commands["linter"].append("pylint . --reports=n")

        if has_js and (root / "package.json").exists():
            if which("prettier"):
                commands["formatter"].append("npx prettier --write ." if which("npx") else "prettier --write .")
            if not commands["linter"] and which("eslint"):
                commands["linter"].append("npx eslint ." if which("npx") else "eslint .")

        if has_go:
            if which("gofmt"):
                commands["formatter"].append("gofmt -w .")
            if not commands["linter"]:
                if which("golangci-lint"):
                    commands["linter"].append("golangci-lint run")
                elif which("go"):
                    commands["linter"].append("go vet ./...")

        if has_rust and which("cargo"):
            commands["formatter"].append("cargo fmt")
            commands["linter"].append("cargo clippy -- -D warnings")

        if has_py:
            if which("pytest"):
                commands["test"].append("pytest -q")
            elif which("python"):
                commands["test"].append("python -m unittest discover -q")
        elif has_js and (root / "package.json").exists():
            commands["test"].append("npm test --if-present" + (" 2>nul" if on_windows() else ""))
        elif has_go:
            commands["test"].append("go test ./...")
        elif has_rust:
            commands["test"].append("cargo test --quiet")

        for stage in commands:
            commands[stage] = list(dict.fromkeys(commands[stage]))
        return commands

    # ------------------------------------------------------------------

    def run(self, repo_path: str | Path) -> CommandResult:
        """Run the configured safety pipeline, stopping at the first failure."""
        root = Path(repo_path)
        commands = self.detect_commands(root)
        result = CommandResult()

        if self._run_formatter and commands["formatter"]:
            cmd = commands["formatter"][0]
            ok, output, exit_code = self._execute(root, cmd)
            result.commands_run.append(f"formatter: {cmd}")
            if not ok:
                result.formatter_ok = False
                result.failed_stage = "formatter"
                result.command = cmd
                result.output = output
                result.command_exit = exit_code
                logger.warning("Formatter failed in %s", root)
                return result

        if self._run_linter and commands["linter"]:
            cmd = commands["linter"][0]
            ok, output, exit_code = self._execute(root, cmd)
            result.commands_run.append(f"linter: {cmd}")
            if not ok:
                result.linter_ok = False
                result.failed_stage = "linter"
                result.command = cmd
                result.output = output
                result.command_exit = exit_code
                logger.warning("Linter failed in %s", root)
                return result

        if self._run_tests and commands["test"]:
            cmd = commands["test"][0]
            ok, output, exit_code = self._execute(root, cmd, timeout=self._test_timeout)
            result.commands_run.append(f"tests: {cmd}")
            if not ok:
                result.tests_ok = False
                result.failed_stage = "tests"
                result.command = cmd
                result.output = output
                result.command_exit = exit_code
                logger.warning("Tests failed in %s", root)
                return result

        logger.info(
            "Safety gate passed in %s (formatter=%s linter=%s tests=%s)",
            root,
            bool(commands["formatter"]) if self._run_formatter else False,
            bool(commands["linter"]) if self._run_linter else False,
            bool(commands["test"]) if self._run_tests else False,
        )
        return result

    # ------------------------------------------------------------------

    def _execute(self, root: Path, command: str, *, timeout: int | None = None) -> tuple[bool, str, int]:
        """Run a shell command inside the repository with a timeout."""
        try:
            result = subprocess.run(
                command,
                cwd=str(root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("Safety command timed out in %s: %s", root, command)
            return False, f"command timed out after {timeout or self._timeout}s", -1
        except OSError as exc:
            return False, str(exc), -1
        output = (result.stderr or result.stdout or "")[-4000:]
        if result.returncode == 0:
            return True, output, 0
        logger.warning(
            "Safety command failed in %s: %s\n%s",
            root, command, (result.stderr or result.stdout)[-2000:],
        )
        return False, output, result.returncode

    def _has_file(self, root: Path, suffix: str) -> bool:
        try:
            return next((p for p in root.rglob(f"*{suffix}") if p.is_file()), None) is not None
        except OSError:
            return False