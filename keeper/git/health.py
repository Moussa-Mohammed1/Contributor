"""Repository health monitoring.

A health score in [0, 100] is computed from observable signals:
  * repository exists and is a git repo             (0..20)
  * working tree is clean / has no conflicts        (0..25)
  * no open merge/rebase state                      (0..10)
  * stash count is low                              (0..10)
  * last commit is recent                           (0..20)
  * unpushed commits are bounded                    (0..10)
  * remote is reachable when configured             (0..5, degraded only)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from keeper.core.types import RepoHealth
from keeper.git.engine import GitEngine
from keeper.utils.time import now_utc

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HealthReport:
    """Full health evaluation of one repository."""

    path: str
    healthy: RepoHealth = RepoHealth.UNKNOWN
    score: float = 0.0
    message: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def to_db(self) -> tuple[str, float, str]:
        return self.healthy.value, self.score, self.message


class RepoHealthChecker:
    """Computes health reports for repositories."""

    def __init__(self, git: GitEngine | None = None) -> None:
        self._git = git or GitEngine()

    def check(self, path: str) -> HealthReport:
        report = HealthReport(path=path)
        score = 0.0
        problems: list[str] = []

        if not self._git.is_git_repo(path):
            report.healthy = RepoHealth.UNHEALTHY
            report.score = 0.0
            report.message = "Not a git repository"
            report.details = {"is_repo": False}
            return report

        status = self._git.status(path)
        score += 20.0

        if status.conflicts:
            problems.append(f"{len(status.conflicts)} conflicted files")
        elif status.merge_state:
            problems.append("merge/rebase in progress")
        elif status.dirty:
            problems.append(f"{len(status.staged) + len(status.unstaged)} uncommitted changes")
            score += 15.0
        else:
            score += 25.0

        if status.merge_state:
            score -= 10.0

        stash_count = status.stash_count
        if stash_count == 0:
            score += 10.0
        elif stash_count <= 2:
            score += 5.0
        else:
            problems.append(f"{stash_count} stashes")

        last = self._git.last_commit(path)
        report.details["last_commit"] = None
        if last is not None and last.when is not None:
            age = now_utc() - last.when
            report.details["last_commit"] = {
                "hash": last.hash,
                "when": last.when.isoformat(),
                "message": last.message,
                "age_hours": round(age.total_seconds() / 3600, 1),
            }
            if age <= timedelta(days=14):
                score += 20.0
            elif age <= timedelta(days=60):
                score += 10.0
            elif age <= timedelta(days=180):
                score += 5.0
            else:
                problems.append(f"no commit in {age.days} days")
        else:
            problems.append("no commits yet")

        unpushed = self._git.unpushed_commits(path)
        report.details["unpushed"] = unpushed
        if unpushed <= 5:
            score += 10.0
        elif unpushed <= 20:
            score += 5.0
        else:
            problems.append(f"{unpushed} unpushed commits")

        if self._git.has_remote(path):
            reachable = self._git.remote_reachable(path, timeout=10)
            report.details["remote_reachable"] = reachable
            if reachable:
                score += 5.0
            else:
                problems.append("remote unreachable")
        else:
            report.details["remote_reachable"] = None

        score = max(0.0, min(100.0, score))
        report.score = score

        if not problems and score >= 85:
            report.healthy = RepoHealth.HEALTHY
            report.message = "Healthy"
        elif score >= 50:
            report.healthy = RepoHealth.DEGRADED
            report.message = "Degraded: " + "; ".join(problems)
        else:
            report.healthy = RepoHealth.UNHEALTHY
            report.message = "Unhealthy: " + "; ".join(problems)
        return report

    def refresh_all(self, paths: list[str]) -> dict[str, HealthReport]:
        """Check many repositories and return path -> report mapping."""
        reports: dict[str, HealthReport] = {}
        for path in paths:
            try:
                reports[path] = self.check(path)
            except Exception:  # noqa: BLE001
                logger.exception("Health check failed for %s", path)
                reports[path] = HealthReport(
                    path=path,
                    healthy=RepoHealth.UNKNOWN,
                    score=0.0,
                    message="health check error",
                )
        return reports