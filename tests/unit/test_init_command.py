"""Regression tests for the `keeper init` command.

Covers the chicken-and-egg bug where `init` required an existing config
file before it could create one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from keeper.cli import app

runner = CliRunner()


@pytest.fixture
def init_args(tmp_path: Path) -> list[str]:
    return [
        "init",
        "--start", "2026-08-04",
        "--end", "2026-08-10",
        "--config", str(tmp_path / "config.yaml"),
    ]


def test_init_creates_config_without_existing_file(tmp_path: Path, init_args: list[str]) -> None:
    result = runner.invoke(app, init_args)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "config.yaml").is_file()
    assert "contributor initialized" in result.output


def test_init_with_repo_option(tmp_path: Path, init_args: list[str]) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    result = runner.invoke(app, [*init_args, "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert f"- path: {repo}" in text


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, init_args: list[str]) -> None:
    runner.invoke(app, init_args)
    result = runner.invoke(app, init_args)
    assert result.exit_code == 0, result.output
    assert "already exists" in result.output
    assert "Use --force to overwrite" in result.output


def test_init_force_overwrites(tmp_path: Path, init_args: list[str]) -> None:
    runner.invoke(app, init_args)
    result = runner.invoke(app, [*init_args, "--force"])
    assert result.exit_code == 0, result.output
    assert "contributor initialized" in result.output


def test_init_rejects_invalid_dates(tmp_path: Path, init_args: list[str]) -> None:
    result = runner.invoke(app, [
        "init",
        "--start", "not-a-date",
        "--end", "2026-08-10",
        "--config", str(tmp_path / "config.yaml"),
    ])
    assert result.exit_code != 0
    assert "invalid date" in result.output
    assert not (tmp_path / "config.yaml").exists()


def test_init_rejects_reversed_window(tmp_path: Path, init_args: list[str]) -> None:
    result = runner.invoke(app, [
        "init",
        "--start", "2026-08-10",
        "--end", "2026-08-04",
        "--config", str(tmp_path / "config.yaml"),
    ])
    assert result.exit_code != 0
    assert "start must be before end" in result.output
