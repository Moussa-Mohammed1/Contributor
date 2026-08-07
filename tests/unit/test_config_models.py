"""Configuration models: validation, coercion and defaults."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from keeper.config.loader import generate_default_config
from keeper.config.models import AppConfig, ProviderName, ScheduleConfig
from keeper.core.exceptions import ConfigError
from keeper.core.types import PlanMode


def test_minimal_config_defaults() -> None:
    cfg = AppConfig.model_validate({
        "schedule": {"start_date": "2026-08-01", "end_date": "2026-08-31"}
    })
    assert cfg.schedule.plan_mode == PlanMode.NATURAL
    assert cfg.schedule.run_time == "08:00"
    assert cfg.schedule.working_days.sunday is False
    assert cfg.ai.provider == ProviderName.AUTO
    assert cfg.database_path.name == "contributor.db"


def test_date_objects_coerced_to_strings() -> None:
    cfg = ScheduleConfig(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
    )
    assert cfg.start_date == "2026-08-01"
    assert cfg.end_date == "2026-08-31"


def test_commit_count_min_max_validated() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({
            "schedule": {
                "start_date": "2026-08-01", "end_date": "2026-08-31",
                "commit_count": {"min": 5, "max": 2},
            }
        })


def test_custom_mode_requires_distribution() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({
            "schedule": {
                "start_date": "2026-08-01", "end_date": "2026-08-31",
                "plan_mode": "custom",
            }
        })
    cfg = AppConfig.model_validate({
        "schedule": {
            "start_date": "2026-08-01", "end_date": "2026-08-31",
            "plan_mode": "custom", "custom_distribution": [2, 5],
        }
    })
    assert cfg.schedule.custom_distribution == [2, 5]


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"nope": 1})


def test_repository_path_expanded() -> None:
    cfg = AppConfig.model_validate({
        "repositories": [{"path": "~/foo/bar"}],
        "schedule": {"start_date": "2026-08-01", "end_date": "2026-08-31"},
    })
    assert cfg.repositories[0].path.startswith(str(__import__("pathlib").Path.home()))


def test_parse_text_yaml() -> None:
    cfg = AppConfig.parse_text(
        "schedule:\n  start_date: 2026-08-01\n  end_date: 2026-08-31\n"
    )
    assert cfg.schedule.start_date == "2026-08-01"


def test_parse_text_invalid_yaml() -> None:
    with pytest.raises(ConfigError):
        AppConfig.parse_text("schedule: [unclosed")


def test_generate_default_config_matches_schema() -> None:
    text = generate_default_config("2026-08-01", "2026-08-31")
    cfg = AppConfig.parse_text(text)
    assert cfg.schedule.start_date == "2026-08-01"
    assert cfg.schedule.end_date == "2026-08-31"
    assert cfg.schedule.dry_run is False


def test_credentials_for_defaults() -> None:
    cfg = AppConfig.model_validate({
        "schedule": {"start_date": "2026-08-01", "end_date": "2026-08-31"}
    })
    for provider in ProviderName:
        if provider == ProviderName.AUTO:
            continue
        creds = cfg.ai.credentials_for(provider)
        assert creds.api_key is None
        assert creds.base_url is None
