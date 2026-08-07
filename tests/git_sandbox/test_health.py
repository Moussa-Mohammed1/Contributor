"""RepoHealthChecker scoring against real sandbox repositories."""

from __future__ import annotations

from pathlib import Path

from keeper.git.health import RepoHealthChecker
from tests.helpers.sandbox import commit_file


def test_clean_repo_is_healthy(sandbox_repo: Path) -> None:
    report = RepoHealthChecker().check(str(sandbox_repo))
    assert report.healthy.value == "healthy"
    assert report.score >= 70


def test_missing_repo_unhealthy(tmp_path: Path) -> None:
    report = RepoHealthChecker().check(str(tmp_path / "nowhere"))
    assert report.healthy.value == "unhealthy"
    assert report.score == 0.0
    assert "Not a git repository" in report.message


def test_dirty_repo_degrades(sandbox_repo: Path) -> None:
    (sandbox_repo / "dirty.txt").write_text("x", encoding="utf-8")
    report = RepoHealthChecker().check(str(sandbox_repo))
    assert report.score < 100


def test_recent_commits_score_full(sandbox_repo: Path) -> None:
    commit_file(sandbox_repo, "app.py", "x", "feat: recent work")
    report = RepoHealthChecker().check(str(sandbox_repo))
    assert report.healthy.value == "healthy"
    assert report.score >= 80


def test_refresh_all_maps_paths(sandbox_repo: Path) -> None:
    reports = RepoHealthChecker().refresh_all([str(sandbox_repo)])
    assert str(sandbox_repo) in reports
    assert reports[str(sandbox_repo)].healthy.value in ("healthy", "degraded")
