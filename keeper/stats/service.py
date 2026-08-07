"""Statistics service: aggregate data for the GUI, CLI and API.

All time-window queries use the stored UTC timestamps; rendering to the user's
timezone happens in the presentation layer.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from keeper.database.models import CommitRecordRecord, FailureRecord, PromptRecord

logger = logging.getLogger(__name__)


class StatsService:
    """Computes statistics from the commit / failure / prompt tables."""

    def today(self, session: Session, day: date | None = None) -> dict[str, Any]:
        """Dashboard aggregate for a calendar day (default: today)."""
        day = day or date.today()
        start = _day_start(day)
        end = start + timedelta(days=1)
        commits = self._count(session, CommitRecordRecord, start, end)
        failures = self._count(session, FailureRecord, start, end)
        return {
            "day": day.isoformat(),
            "commits": commits,
            "failures": failures,
            "ai_calls": self._count(session, PromptRecord, start, end),
            "success_rate": round(commits / max(1, commits + failures) * 100, 1),
        }

    def daily_series(self, session: Session, days: int = 30) -> list[dict[str, Any]]:
        """Commit counts per day for the last ``days`` days (including gaps)."""
        start = _day_start(date.today()) - timedelta(days=days - 1)
        counts = self._counts(session, start)
        by_day = {row[0].date().isoformat(): row[1] for row in counts}
        series, current = [], start.date()
        while current <= date.today():
            series.append({"day": current.isoformat(), "commits": by_day.get(current.isoformat(), 0)})
            current += timedelta(days=1)
        return series

    def weekly_series(self, session: Session, weeks: int = 12) -> list[dict[str, Any]]:
        """Commit counts per ISO year-week."""
        start = _day_start(date.today()) - timedelta(weeks=weeks)
        buckets: dict[str, int] = {}
        for row in self._counts(session, start):
            iso = row[0].isocalendar()
            buckets[f"{iso[0]}-W{iso[1]:02d}"] = buckets.get(f"{iso[0]}-W{iso[1]:02d}", 0) + row[1]
        return [{"period": k, "commits": v} for k, v in sorted(buckets.items())]

    def monthly_series(self, session: Session, months: int = 12) -> list[dict[str, Any]]:
        """Commit counts per calendar month."""
        start = _day_start(date.today()) - timedelta(days=30 * months)
        buckets: dict[str, int] = {}
        for row in self._counts(session, start):
            label = row[0].strftime("%Y-%m")
            buckets[label] = buckets.get(label, 0) + row[1]
        return [{"period": k, "commits": v} for k, v in sorted(buckets.items())]

    def repo_activity(self, session: Session, days: int = 30) -> list[dict[str, Any]]:
        """Commit counts grouped by repository for the window."""
        start = _days_ago(days)
        rows = session.execute(
            select(CommitRecordRecord.repo_path, func.count(CommitRecordRecord.id))
            .where(CommitRecordRecord.created_at >= start)
            .group_by(CommitRecordRecord.repo_path)
            .order_by(func.count(CommitRecordRecord.id).desc())
        ).all()
        return [{"repo": repo or "unknown", "commits": count} for repo, count in rows]

    def ai_success_rate(self, session: Session, days: int = 30) -> dict[str, Any]:
        """Share of successful AI prompt calls in the window."""
        start = _days_ago(days)
        total = int(
            session.scalar(select(func.count()).select_from(PromptRecord).where(PromptRecord.created_at >= start)) or 0
        )
        successful = int(
            session.scalar(
                select(func.count())
                .select_from(PromptRecord)
                .where(PromptRecord.created_at >= start, PromptRecord.success.is_(True))
            )
            or 0
        )
        return {"calls": total, "successful": successful,
                "rate": round(successful / total * 100, 1) if total else None}

    def failure_rate(self, session: Session, days: int = 30) -> dict[str, Any]:
        start = _days_ago(days)
        commits = int(
            session.scalar(
                select(func.count()).select_from(CommitRecordRecord).where(CommitRecordRecord.created_at >= start)
            )
            or 0
        )
        failures = int(
            session.scalar(
                select(func.count()).select_from(FailureRecord).where(FailureRecord.created_at >= start)
            )
            or 0
        )
        total = commits + failures
        return {"commits": commits, "failures": failures, "rate": round(failures / max(1, total) * 100, 1)}

    def execution_time(self, session: Session, days: int = 7) -> list[dict[str, Any]]:
        """Daily total commit duration (ms) for the last days."""
        start = _days_ago(days)
        rows = session.execute(
            select(CommitRecordRecord.created_at, CommitRecordRecord.duration_ms)
            .where(CommitRecordRecord.created_at >= start)
        ).all()
        buckets: dict[str, int] = {}
        for created_at, duration in rows:
            label = created_at.date().isoformat()
            buckets[label] = buckets.get(label, 0) + (duration or 0)
        return [{"day": k, "duration_ms": v} for k, v in sorted(buckets.items())]

    # ------------------------------------------------------------------

    def _count(self, session: Session, model, start, end) -> int:
        return int(
            session.scalar(select(func.count()).select_from(model).where(model.created_at >= start, model.created_at < end))
            or 0
        )

    def _counts(self, session: Session, start) -> list:
        return (
            session.execute(
                select(CommitRecordRecord.created_at, func.count(CommitRecordRecord.id))
                .where(CommitRecordRecord.created_at >= start)
                .group_by(func.strftime("%Y-%m-%d", CommitRecordRecord.created_at))
            )
            .all()
        )


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _days_ago(days: int) -> datetime:
    from keeper.utils.time import now_utc

    return now_utc() - timedelta(days=days)