"""Core domain types shared across the application.

Everything here is dependency-free so it can be imported from any module
without creating circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TaskState(StrEnum):
    """Lifecycle state of a planned commit slot."""

    PLANNED = "planned"
    RUNNING = "running"
    COMMITTED = "committed"  # commit created, push pending
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanMode(StrEnum):
    """Distribution strategy used to generate daily commit plans."""

    NATURAL = "natural"
    RANDOM = "random"
    BALANCED = "balanced"
    HEAVY = "heavy"
    LIGHT = "light"
    CUSTOM = "custom"


class ProviderName(StrEnum):
    """Supported AI providers."""

    AUTO = "auto"
    OPENAI = "openai"
    GEMINI = "gemini"
    CLAUDE = "claude"
    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"
    OPENROUTER = "openrouter"


class RepoHealth(StrEnum):
    """Health classification for a managed repository."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CommitCategory(StrEnum):
    """Semantic commit categories (conventional commits)."""

    DOCS = "docs"
    FIX = "fix"
    REFACTOR = "refactor"
    STYLE = "style"
    PERF = "perf"
    TEST = "test"
    BUILD = "build"
    CI = "ci"
    CHORE = "chore"
    FEAT = "feat"


class PlanStatus(StrEnum):
    """Status of a day plan / schedule."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStatus(StrEnum):
    """Status of a full execution cycle."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(slots=True)
class PlannedCommit:
    """A single planned commit slot inside a day plan."""

    scheduled_at: datetime
    repository_path: str
    repository_name: str
    slot_id: int | None = None
    state: TaskState = TaskState.PLANNED
    message: str | None = None
    commit_hash: str | None = None
    error: str | None = None


@dataclass(slots=True)
class DayPlan:
    """Full plan for one execution day."""

    plan_date: datetime
    target_commits: int
    mode: PlanMode
    slots: list[PlannedCommit] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return sum(1 for s in self.slots if s.state in (TaskState.SUCCEEDED, TaskState.COMMITTED))

    @property
    def failed(self) -> int:
        return sum(1 for s in self.slots if s.state == TaskState.FAILED)

    @property
    def remaining(self) -> int:
        return sum(1 for s in self.slots if s.state in (TaskState.PLANNED, TaskState.RUNNING))


@dataclass(slots=True)
class CommitRecord:
    """One executed commit."""

    repo_id: int | None
    repo_path: str
    hash: str
    message: str
    category: CommitCategory
    changed_files: list[str] = field(default_factory=list)
    pushed: bool = False
    provider: str | None = None
    model: str | None = None
    duration_ms: int = 0
    created_at: datetime | None = None
