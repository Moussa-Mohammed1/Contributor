"""RepositoryAnalyzer against sandbox repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from keeper.analysis.analyzer import RepositoryAnalyzer
from keeper.core.exceptions import RepositoryError


def test_analyze_sandbox(sandbox_repo: Path) -> None:
    report = RepositoryAnalyzer().analyze(str(sandbox_repo))
    assert report.exists is True
    assert report.is_git is True
    assert report.languages == ["Python", "Markdown"]
    assert report.file_count >= 2
    assert "README.md" in report.doc_files
    assert report.branch == "main"
    assert report.git_status is not None
    assert report.git_status.dirty is False
    assert report.to_prompt_json()["git_clean"] is True


def test_analyze_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(RepositoryError):
        RepositoryAnalyzer().analyze(str(tmp_path / "missing"))


def test_analyze_plain_directory(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "notes.md").write_text("# hello", encoding="utf-8")
    report = RepositoryAnalyzer().analyze(str(folder))
    assert report.exists is True
    assert report.is_git is False
    assert "Markdown" in report.languages
    assert "notes.md" in report.doc_files
