"""Create throwaway git repositories for tests (real git, real commits)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SANDBOX_APP_PY = '''\
"""Demo module used by the contributor integration tests."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
'''

SANDBOX_README = """\
# sandbox

A tiny repository used to exercise the contributor pipeline in isolation.
"""


def git_user() -> list[str]:
    """Config flags that make git commits work on any machine."""
    return [
        "-c", "user.name=contributor test",
        "-c", "user.email=test@example.invalid",
    ]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


def create_git_repo(root: Path, *, name: str = "sandbox", files: dict[str, str] | None = None) -> Path:
    """Create a git repository at ``root / name`` with one initial commit."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main"], repo)
    for file_name, content in (files or {"app.py": SANDBOX_APP_PY, "README.md": SANDBOX_README}).items():
        target = repo / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _run(["git", *git_user(), "add", "-A"], repo)
    _run(["git", *git_user(), "commit", "-m", "chore: initial commit"], repo)
    return repo


def commit_file(repo: Path, file_name: str, content: str, message: str) -> str:
    """Add or overwrite a file and commit; returns the short hash."""
    target = repo / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(["git", *git_user(), "add", "-A"], repo)
    _run(["git", *git_user(), "commit", "-m", message], repo)
    return _run(["git", "rev-parse", "--short", "HEAD"], repo).stdout.strip()
