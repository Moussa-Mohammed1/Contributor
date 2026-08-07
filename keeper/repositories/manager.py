"""Repository manager: configuration <-> registry synchronization and selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from keeper.config.models import AppConfig, RepositoryEntry
from keeper.database import repo as dataset
from keeper.database.models import RepoRecord
from keeper.git.engine import GitEngine
from keeper.git.health import RepoHealthChecker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RepoSummary:
    """Metadata view of a configured repository combining config + DB + git."""

    name: str
    path: str
    priority: float
    max_commits_per_day: int
    repo_id: int | None = None
    default_branch: str = "main"
    health: str = "unknown"
    health_score: float = 0.0
    health_message: str | None = None
    last_commit_hash: str | None = None
    last_commit_at: str | None = None
    dirty: bool = False
    preferred_provider: str | None = None
    ignore_patterns: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.repo_id,
            "name": self.name,
            "path": self.path,
            "priority": self.priority,
            "max_commits_per_day": self.max_commits_per_day,
            "default_branch": self.default_branch,
            "health": self.health,
            "health_score": self.health_score,
            "health_message": self.health_message,
            "last_commit_hash": self.last_commit_hash,
            "last_commit_at": self.last_commit_at,
            "dirty": self.dirty,
            "preferred_provider": self.preferred_provider,
        }


class RepoManager:
    """Keeps the database registry in sync with configuration and exposes health."""

    def __init__(self, git: GitEngine | None = None) -> None:
        self._git = git or GitEngine()
        self._health = RepoHealthChecker(git)

    def sync_from_config(self, session: Session, config: AppConfig) -> list[RepoSummary]:
        """Upsert every configured repository and return their summaries."""
        for entry in config.repositories:
            dataset.upsert_repo(
                session,
                path=entry.path,
                name=entry.name,
                priority=entry.priority,
                preferred_provider=entry.preferred_provider.value if entry.preferred_provider else None,
                ignore_patterns=entry.ignore_patterns,
                max_commits_per_day=entry.max_commits_per_day,
                enabled=entry.enabled,
            )
        # Flag repositories removed from configuration (history is preserved).
        for record in dataset.list_repos(session):
            if record.path not in {r.path for r in config.repositories}:
                if record.enabled:
                    record.enabled = False
                    session.commit()
        return self.snapshots(session, config)

    def snapshots(self, session: Session, config: AppConfig | None = None) -> list[RepoSummary]:
        """Build RepoSummary views for every repository in the registry."""
        enabled_paths = {e.path for e in config.repositories} if config else None
        summaries: list[RepoSummary] = []
        from keeper.utils.time import from_storage

        for record in dataset.list_repos(session):
            if enabled_paths is not None and record.path not in enabled_paths:
                continue
            summaries.append(
                RepoSummary(
                    name=record.name,
                    path=record.path,
                    priority=record.priority,
                    max_commits_per_day=record.max_commits_per_day,
                    repo_id=record.id,
                    default_branch=record.default_branch,
                    health=record.health,
                    health_score=record.health_score,
                    health_message=record.health_message,
                    last_commit_hash=record.last_commit_hash,
                    last_commit_at=record.last_commit_at.isoformat() if record.last_commit_at else None,
                    dirty=False,
                    preferred_provider=record.preferred_provider,
                    ignore_patterns=record.ignore_patterns,
                )
            )
        return summaries

    def check_health(self, session: Session, config: AppConfig) -> None:
        """Re-run health checks for every enabled repository and persist scores."""
        for entry in config.repositories:
            if not entry.enabled:
                continue
            record = dataset.get_repo_by_path(session, entry.path)
            if record is None:
                record = dataset.upsert_repo(
                    session, path=entry.path, name=entry.name, priority=entry.priority,
                    preferred_provider=entry.preferred_provider.value if entry.preferred_provider else None,
                    ignore_patterns=entry.ignore_patterns, max_commits_per_day=entry.max_commits_per_day,
                    enabled=entry.enabled,
                )
            report = self._health.check(entry.path)
            last = self._git.last_commit(entry.path)
            dataset.update_repo_health(
                session,
                record.id,
                health=report.healthy.value,
                score=report.score,
                message=report.message,
                last_commit_hash=last.hash if last else record.last_commit_hash,
                last_commit_at=last.when if last else record.last_commit_at,
                default_branch=self._git.default_branch(entry.path),
            )
            logger.debug("Health %s -> %s (%.0f/100)", entry.name, report.healthy.value, report.score)
        logger.info("Health refreshed for %d repositories", len(config.repositories))

    def healthy_paths(self, session: Session, config: AppConfig) -> list[str]:
        """Return repository paths that are safe to operate on (healthy/degraded)."""
        paths: list[str] = []
        for entry in config.repositories:
            if not entry.enabled:
                continue
            record = dataset.get_repo_by_path(session, entry.path)
            if record is None or record.health in ("healthy", "degraded"):
                paths.append(entry.path)
        return paths