"""Pydantic configuration models with strict validation.

The models mirror ``examples/config.example.yaml``. Unknown keys are rejected
so configuration mistakes surface immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from keeper.core.exceptions import ConfigError
from keeper.core.types import PlanMode, ProviderName

DEFAULT_DATA_DIR = "~/.contributor"
DEFAULT_TZ = "Africa/Casablanca"


class RepositoryEntry(BaseModel):
    """One managed repository."""

    model_config = ConfigDict(extra="forbid")

    path: str
    priority: float = Field(default=1.0, ge=0.0, le=10.0)
    max_commits_per_day: int = Field(default=8, ge=1, le=100)
    preferred_provider: ProviderName | None = None
    ignore_patterns: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("path")
    @classmethod
    def _expand_path(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())

    @property
    def name(self) -> str:
        return Path(self.path).name or self.path


class CommitCountRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = Field(default=1, ge=1)
    max: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_order(self) -> "CommitCountRange":
        if self.min > self.max:
            raise ValueError("commit_count.min must be <= commit_count.max")
        return self


class CommitInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_minutes: int = Field(default=20, ge=1)
    max_minutes: int = Field(default=180, ge=1)

    @model_validator(mode="after")
    def _check_order(self) -> "CommitInterval":
        if self.min_minutes > self.max_minutes:
            raise ValueError("commit_interval.min_minutes must be <= max_minutes")
        return self


class WorkingDays(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monday: bool = True
    tuesday: bool = True
    wednesday: bool = True
    thursday: bool = True
    friday: bool = True
    saturday: bool = True
    sunday: bool = False

    def as_dict(self) -> dict[str, bool]:
        return self.model_dump()

    def is_enabled(self, day_name: str) -> bool:
        return bool(getattr(self, day_name.lower(), False))


class ScheduleConfig(BaseModel):
    """Daily execution schedule."""

    model_config = ConfigDict(extra="forbid")

    start_date: str
    end_date: str
    timezone: str = DEFAULT_TZ
    run_time: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    commit_count: CommitCountRange = Field(default_factory=CommitCountRange)
    commit_interval: CommitInterval = Field(default_factory=CommitInterval)
    working_days: WorkingDays = Field(default_factory=WorkingDays)
    branch: str = "main"
    dry_run: bool = False
    plan_mode: PlanMode = PlanMode.NATURAL
    custom_distribution: list[int] | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> object:
        """PyYAML parses ISO dates as date objects; normalize them to strings."""
        from datetime import date as _date, datetime as _datetime

        if isinstance(value, (_date, _datetime)):
            return value.isoformat()
        return value

    @model_validator(mode="after")
    def _validate_custom_distribution(self) -> "ScheduleConfig":
        if self.plan_mode == PlanMode.CUSTOM:
            if not self.custom_distribution:
                raise ValueError(
                    "plan_mode=custom requires schedule.custom_distribution (non-empty list of ints)"
                )
            if any(v < 0 for v in self.custom_distribution):
                raise ValueError("custom_distribution values must be >= 0")
        return self


class ProviderCredentials(BaseModel):
    """Credentials and model settings for one AI provider."""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


class AIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName = ProviderName.AUTO
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    timeout_seconds: float = Field(default=120.0, ge=5.0, le=600.0)
    providers: dict[ProviderName, ProviderCredentials] = Field(
        default_factory=lambda: {name: ProviderCredentials() for name in ProviderName if name != ProviderName.AUTO}
    )

    @model_validator(mode="after")
    def _ensure_all_providers(self) -> "AIProviderConfig":
        for name in ProviderName:
            if name == ProviderName.AUTO:
                continue
            if name not in self.providers:
                self.providers[name] = ProviderCredentials()
        return self

    def credentials_for(self, name: ProviderName) -> ProviderCredentials:
        return self.providers.get(name, ProviderCredentials())


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_formatter: bool = True
    run_linter: bool = True
    run_tests: bool = False
    test_timeout: int = Field(default=300, ge=5)
    max_changed_files: int = Field(default=15, ge=1, le=200)
    max_change_bytes: int = Field(default=512_000, ge=1024)


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    desktop: bool = True
    console: bool = False


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    structured: bool = True
    retention_days: int = Field(default=30, ge=1)


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8971, ge=1, le=65535)


class GuardrailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_remote_push: bool = True
    allow_untracked_cleanup: bool = True
    skip_paused_repos: bool = True


class AppConfig(BaseModel):
    """Root configuration model."""

    model_config = ConfigDict(extra="forbid")

    data_dir: str = DEFAULT_DATA_DIR
    repositories: list[RepositoryEntry] = Field(default_factory=list)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    ai: AIProviderConfig = Field(default_factory=AIProviderConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)

    def resolved_data_dir(self) -> Path:
        return Path(os.path.expanduser(self.data_dir)).resolve()

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir() / "contributor.db"

    @property
    def log_dir(self) -> Path:
        return self.resolved_data_dir() / "logs"

    @property
    def pid_path(self) -> Path:
        return self.resolved_data_dir() / "keeper.pid"

    @property
    def stop_flag_path(self) -> Path:
        return self.resolved_data_dir() / "stop.flag"

    @property
    def config_file_candidates(self) -> list[Path]:
        return [
            Path.cwd() / "config.yaml",
            self.resolved_data_dir() / "config.yaml",
        ]

    @staticmethod
    def parse_text(text: str, source: str = "<memory>") -> "AppConfig":
        import yaml

        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {source}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"Configuration root in {source} must be a mapping")
        try:
            return AppConfig.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
            raise ConfigError(f"Configuration validation failed in {source}: {exc}") from exc
