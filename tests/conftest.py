"""Shared pytest fixtures for the contributor test suite.

Everything lives under tmp_path: no test touches the real user data dir,
home directory or any repository outside the sandbox.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from keeper.ai.factory import ProviderFactory
from keeper.config.manager import ConfigManager
from keeper.config.models import AppConfig
from keeper.core.types import PlanMode, ProviderName
from keeper.database.engine import Database
from keeper.utils.time import local_date
from tests.helpers.mock_provider import MockProvider

_TZ = "UTC"


@pytest.fixture(scope="session", autouse=True)
def _register_mock_provider() -> None:
    """Make the deterministic provider available as ``ollama`` for all tests."""
    ProviderFactory.register(ProviderName.OLLAMA, MockProvider)


@pytest.fixture
def sandbox_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sandbox_repo(sandbox_root: Path) -> Path:
    from tests.helpers.sandbox import create_git_repo

    return create_git_repo(sandbox_root, name="sandbox")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def config_yaml(sandbox_repo: Path, data_dir: Path) -> str:
    today = local_date(_TZ)
    return f"""\
data_dir: {data_dir.as_posix()}

repositories:
  - path: {sandbox_repo.as_posix()}
    priority: 1.0
    max_commits_per_day: 8
    preferred_provider: ollama

schedule:
  start_date: {today.isoformat()}
  end_date: {(today + timedelta(days=7)).isoformat()}
  timezone: {_TZ}
  run_time: "09:00"
  commit_count:
    min: 1
    max: 2
  commit_interval:
    min_minutes: 10
    max_minutes: 120
  working_days:
    monday: true
    tuesday: true
    wednesday: true
    thursday: true
    friday: true
    saturday: true
    sunday: true
  branch: main
  dry_run: true
  plan_mode: balanced

ai:
  provider: ollama
  temperature: 0.3
  max_tokens: 1024
  timeout_seconds: 30
  providers:
    ollama:
      model: mock-1
      base_url: http://mock

safety:
  run_formatter: false
  run_linter: false
  run_tests: false
  max_changed_files: 15
  max_change_bytes: 512000

notifications:
  enabled: false
  desktop: false
  console: false

logging:
  level: WARNING
  structured: false
  retention_days: 7

api:
  enabled: false
  host: 127.0.0.1
  port: 8971

guardrails:
  require_remote_push: false
  allow_untracked_cleanup: true
  skip_paused_repos: true
"""


@pytest.fixture
def config_manager(tmp_path: Path, config_yaml: str) -> ConfigManager:
    path = tmp_path / "config.yaml"
    path.write_text(config_yaml, encoding="utf-8")
    manager = ConfigManager(path, watch=False)
    manager.load(path)
    return manager


@pytest.fixture
def app_config(config_manager: ConfigManager) -> AppConfig:
    return config_manager.config


@pytest.fixture
def database(data_dir: Path) -> Database:
    db = Database(data_dir / "contributor.db")
    return db.connect()


@pytest.fixture
def app(config_manager: ConfigManager, data_dir: Path):
    """The full composed application with a mock AI provider."""
    from keeper.app import build_application

    ctx = build_application(config_manager, db_path=data_dir / "contributor.db")
    yield ctx
    ctx.close()


@pytest.fixture
def plan_mode() -> PlanMode:
    return PlanMode.BALANCED


def make_planner_config(data_dir: Path, repo_path: Path, **overrides) -> AppConfig:  # noqa: ANN003
    """Build an AppConfig directly (for unit tests that need no file)."""
    today = date.today()
    values = {
        "data_dir": str(data_dir),
        "repositories": [
            {
                "path": str(repo_path),
                "priority": 1.0,
                "max_commits_per_day": 8,
                "enabled": True,
            }
        ],
        "schedule": {
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=7)).isoformat(),
            "timezone": _TZ,
            "run_time": "09:00",
            "commit_count": {"min": 1, "max": 3},
            "commit_interval": {"min_minutes": 10, "max_minutes": 120},
            "working_days": {
                "monday": True, "tuesday": True, "wednesday": True,
                "thursday": True, "friday": True, "saturday": True, "sunday": True,
            },
            "branch": "main",
            "dry_run": False,
            "plan_mode": "balanced",
        },
        "ai": {"provider": "auto", "providers": {"ollama": {"base_url": "http://mock"}}},
        "safety": {"run_formatter": False, "run_linter": False, "run_tests": False},
        "notifications": {"enabled": False},
        "logging": {"level": "WARNING", "retention_days": 7},
        "api": {"enabled": False},
        "guardrails": {"require_remote_push": False},
    }
    return AppConfig.model_validate(_deep_merge(values, overrides))


def _deep_merge(base: dict, extra: dict) -> dict:
    """Recursively merge nested override dicts into the base configuration."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
