"""GitEngine against real sandbox repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from keeper.core.exceptions import DirtyRepositoryError, RepositoryError
from keeper.git.engine import GitEngine
from tests.helpers.sandbox import commit_file


@pytest.fixture
def engine() -> GitEngine:
    return GitEngine()


def test_status_clean_repo(engine: GitEngine, sandbox_repo: Path) -> None:
    status = engine.status(sandbox_repo)
    assert status.is_repo is True
    assert status.branch == "main"
    assert status.dirty is False
    assert status.conflicts == []
    assert status.stash_count == 0


def test_status_not_a_repo(engine: GitEngine, tmp_path: Path) -> None:
    status = engine.status(tmp_path / "missing")
    assert status.is_repo is False


def test_status_detects_dirty(engine: GitEngine, sandbox_repo: Path) -> None:
    (sandbox_repo / "notes.txt").write_text("dirty", encoding="utf-8")
    status = engine.status(sandbox_repo)
    assert status.dirty is True
    assert status.untracked == ["notes.txt"]


def test_status_detects_staged(engine: GitEngine, sandbox_repo: Path) -> None:
    (sandbox_repo / "staged.txt").write_text("x", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "staged.txt"], cwd=sandbox_repo, check=True, capture_output=True)
    status = engine.status(sandbox_repo)
    assert "staged.txt" in status.staged


def test_require_clean_raises_when_dirty(engine: GitEngine, sandbox_repo: Path) -> None:
    (sandbox_repo / "dirty.txt").write_text("x", encoding="utf-8")
    with pytest.raises(DirtyRepositoryError):
        engine.require_clean(sandbox_repo)


def test_commit_creates_commit(engine: GitEngine, sandbox_repo: Path) -> None:
    commit_file(sandbox_repo, "app.py", "# changed\n", "refactor: simplify app")
    status = engine.status(sandbox_repo)
    assert status.dirty is False
    commits = engine.recent_commits(sandbox_repo, limit=3)
    assert commits[0].message == "refactor: simplify app"


def test_recent_commits_order(engine: GitEngine, sandbox_repo: Path) -> None:
    commit_file(sandbox_repo, "a.txt", "1", "chore: first extra")
    commit_file(sandbox_repo, "b.txt", "2", "chore: second extra")
    commits = engine.recent_commits(sandbox_repo, limit=3)
    assert commits[0].message == "chore: second extra"
    assert commits[2].message == "chore: initial commit"


def test_find_commit_by_message(engine: GitEngine, sandbox_repo: Path) -> None:
    commit_file(sandbox_repo, "app.py", "new content", "feat: add magical powers")
    found = engine.find_commit_by_message(sandbox_repo, "feat: add magical powers")
    assert found is not None
    assert len(found) == 40
    assert engine.find_commit_by_message(sandbox_repo, "nope: nothing like this") is None


def test_last_commit(engine: GitEngine, sandbox_repo: Path) -> None:
    last = engine.last_commit(sandbox_repo)
    assert last is not None
    assert last.message == "chore: initial commit"


def test_default_branch(engine: GitEngine, sandbox_repo: Path) -> None:
    assert engine.default_branch(sandbox_repo) == "main"


def test_unpushed_commits_without_remote(engine: GitEngine, sandbox_repo: Path) -> None:
    # With no remote configured every local commit is considered unpushed.
    assert engine.unpushed_commits(sandbox_repo) == 1
    assert engine.has_remote(sandbox_repo) is False


def test_reset_index(engine: GitEngine, sandbox_repo: Path) -> None:
    (sandbox_repo / "staged.txt").write_text("x", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "staged.txt"], cwd=sandbox_repo, check=True, capture_output=True)
    engine.reset_index(sandbox_repo)
    status = engine.status(sandbox_repo)
    assert status.staged == []
    assert "staged.txt" in status.untracked


def test_restore_removes_untracked(engine: GitEngine, sandbox_repo: Path) -> None:
    (sandbox_repo / "new_file.py").write_text("x", encoding="utf-8")
    engine.restore(sandbox_repo, ["new_file.py"])
    assert not (sandbox_repo / "new_file.py").exists()


def test_open_repo_errors(engine: GitEngine, tmp_path: Path) -> None:
    with pytest.raises(RepositoryError):
        engine.open_repo(tmp_path / "nope")
