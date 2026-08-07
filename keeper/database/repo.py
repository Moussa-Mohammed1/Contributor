"""Data access functions for the state database.

These thin helpers keep SQL hidden from services while preserving full control.
All functions take an explicit ``Session`` (dependency injection, no globals).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from keeper.core.exceptions import StateError
from keeper.core.types import PlanStatus, TaskState
from keeper.database.models import (
    CommitRecordRecord,
    ExecutionRecord,
    FailureRecord,
    LogRecord,
    NotificationRecord,
    PromptRecord,
    RepoRecord,
    ScheduleRecord,
    Setting,
    SlotRecord,
    StatRecord,
)
from keeper.utils.time import from_storage, now_utc, to_storage

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()


# --------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------

def upsert_repo(session: Session, *, path: str, name: str, priority: float, preferred_provider: str | None,
                ignore_patterns: list[str], max_commits_per_day: int, enabled: bool) -> RepoRecord:
    repo = session.scalar(select(RepoRecord).where(RepoRecord.path == path))
    if repo is None:
        repo = RepoRecord(path=path)
        session.add(repo)
    repo.name = name
    repo.priority = priority
    repo.preferred_provider = preferred_provider
    repo.ignore_patterns = ignore_patterns or []
    repo.max_commits_per_day = max_commits_per_day
    repo.enabled = enabled
    session.commit()
    session.refresh(repo)
    return repo


def get_repo(session: Session, repo_id: int) -> RepoRecord | None:
    return session.get(RepoRecord, repo_id)


def get_repo_by_path(session: Session, path: str) -> RepoRecord | None:
    return session.scalar(select(RepoRecord).where(RepoRecord.path == path))


def list_repos(session: Session, *, include_disabled: bool = True) -> list[RepoRecord]:
    stmt = select(RepoRecord).order_by(RepoRecord.priority.desc(), RepoRecord.name)
    if not include_disabled:
        stmt = stmt.where(RepoRecord.enabled.is_(True))
    return list(session.scalars(stmt))


def update_repo_health(session: Session, repo_id: int, *, health: str, score: float, message: str | None,
                       last_commit_hash: str | None = None, last_commit_at=None, default_branch: str | None = None) -> None:
    repo = session.get(RepoRecord, repo_id)
    if repo is None:
        return
    repo.health = health
    repo.health_score = score
    repo.health_message = message
    repo.last_checked_at = now_utc()
    if last_commit_hash is not None:
        repo.last_commit_hash = last_commit_hash
    if last_commit_at is not None:
        repo.last_commit_at = to_storage(last_commit_at)
    if default_branch is not None:
        repo.default_branch = default_branch
    session.commit()


def count_repo_commits_on(session: Session, repo_id: int, day: str) -> int:
    """Count commits recorded for ``repo_id`` on the UTC day ``YYYY-MM-DD``."""
    from sqlalchemy import func

    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    end = start + timedelta(days=1)
    return int(
        session.scalar(
            select(func.count())
            .select_from(CommitRecordRecord)
            .where(
                CommitRecordRecord.repo_id == repo_id,
                CommitRecordRecord.created_at >= to_storage(start),
                CommitRecordRecord.created_at < to_storage(end),
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Schedules & slots
# ---------------------------------------------------------------------------

def get_schedule(session: Session, plan_date: str) -> ScheduleRecord | None:
    return session.scalar(select(ScheduleRecord).where(ScheduleRecord.plan_date == plan_date))


def list_schedules(session: Session, *, limit: int = 30) -> list[ScheduleRecord]:
    return list(
        session.scalars(
            select(ScheduleRecord).order_by(ScheduleRecord.plan_date.desc()).limit(limit)
        )
    )


def create_schedule(session: Session, *, plan_date: str, mode: str, target_commits: int,
                    run_time: str, dry_run: bool) -> ScheduleRecord:
    schedule = ScheduleRecord(
        plan_date=plan_date,
        mode=mode,
        target_commits=target_commits,
        status=PlanStatus.PENDING.value,
        run_time=run_time,
        dry_run=dry_run,
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def add_slot(session: Session, *, schedule_id: int, repo_id: int | None, repo_path: str,
             repo_name: str, scheduled_at) -> SlotRecord:
    slot = SlotRecord(
        schedule_id=schedule_id,
        repo_id=repo_id,
        repo_path=repo_path,
        repo_name=repo_name,
        scheduled_at=to_storage(scheduled_at),
        state=TaskState.PLANNED.value,
    )
    session.add(slot)
    session.commit()
    session.refresh(slot)
    return slot


def get_slot(session: Session, slot_id: int) -> SlotRecord | None:
    return session.get(SlotRecord, slot_id)


def list_slots(session: Session, schedule_id: int | None = None) -> list[SlotRecord]:
    stmt = select(SlotRecord).order_by(SlotRecord.scheduled_at)
    if schedule_id is not None:
        stmt = stmt.where(SlotRecord.schedule_id == schedule_id)
    return list(session.scalars(stmt))


def due_slots(session: Session, *, now=None, scope: str | None = None) -> list[SlotRecord]:
    """Slots that are ready to execute (planned and scheduled_at <= now).

    ``scope`` restricts to a schedule id when provided.
    """
    now = now or now_utc()
    stmt = (
        select(SlotRecord)
        .join(ScheduleRecord, SlotRecord.schedule_id == ScheduleRecord.id)
        .where(
            SlotRecord.state == TaskState.PLANNED.value,
            SlotRecord.scheduled_at <= to_storage(now),
            ScheduleRecord.status.in_([PlanStatus.PENDING.value, PlanStatus.RUNNING.value]),
        )
        .order_by(SlotRecord.scheduled_at)
    )
    if scope is not None:
        stmt = stmt.where(SlotRecord.schedule_id == int(scope))
    return list(session.scalars(stmt))


def set_slot_state(session: Session, slot_id: int, state: TaskState, *, error: str | None = None,
                   commit_hash: str | None = None, message: str | None = None) -> None:
    slot = session.get(SlotRecord, slot_id)
    if slot is None:
        raise StateError(f"Unknown slot {slot_id}")
    slot.state = state.value
    if error is not None:
        slot.error = error
    if commit_hash is not None:
        slot.commit_hash = commit_hash
    if message is not None:
        slot.attempted_message = message
    if state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.SKIPPED, TaskState.COMMITTED):
        slot.finished_at = now_utc()
    session.commit()


def set_slot_started(session: Session, slot_id: int, attempts: int) -> None:
    slot = session.get(SlotRecord, slot_id)
    if slot is None:
        raise StateError(f"Unknown slot {slot_id}")
    slot.state = TaskState.RUNNING.value
    slot.started_at = now_utc()
    slot.attempts = attempts
    session.commit()


def set_slot_metadata(session: Session, slot_id: int, *, provider: str | None, model: str | None,
                      attempted_message: str | None) -> None:
    slot = session.get(SlotRecord, slot_id)
    if slot is None:
        return
    slot.provider = provider
    slot.model = model
    if attempted_message:
        slot.attempted_message = attempted_message
    session.commit()


def reset_running_slots(session: Session) -> int:
    """Reset RUNNING slots to PLANNED on startup (crash recovery)."""
    result = session.execute(
        update(SlotRecord)
        .where(SlotRecord.state == TaskState.RUNNING.value)
        .values(state=TaskState.PLANNED.value, error="reset after restart")
    )
    session.commit()
    return result.rowcount or 0


def plan_status(session: Session, schedule_id: int) -> str:
    slots = list_slots(session, schedule_id)
    if not slots:
        return PlanStatus.PENDING.value
    states = {s.state for s in slots}
    if states == {TaskState.SUCCEEDED.value} or states == {TaskState.COMMITTED.value, TaskState.SUCCEEDED.value}:
        return PlanStatus.COMPLETED.value
    if TaskState.RUNNING.value in states or TaskState.PLANNED.value in states:
        if TaskState.FAILED.value in states:
            return PlanStatus.PARTIAL.value
        return PlanStatus.PENDING.value
    return PlanStatus.FAILED.value


def update_schedule_status(session: Session, schedule_id: int, status: str) -> None:
    schedule = session.get(ScheduleRecord, schedule_id)
    if schedule is None:
        return
    schedule.status = status
    if status in (PlanStatus.COMPLETED.value, PlanStatus.FAILED.value, PlanStatus.CANCELLED.value):
        schedule.completed_at = now_utc()
    session.commit()


# --------------------------------------------------------------------------
# Commits
# --------------------------------------------------------------------------

def add_commit_record(session: Session, *, repo_id: int | None, slot_id: int | None, repo_path: str,
                      hash_: str, message: str, category: str, changed_files: list[str],
                      pushed: bool, provider: str | None, model: str | None, duration_ms: int) -> CommitRecordRecord:
    record = CommitRecordRecord(
        repo_id=repo_id,
        slot_id=slot_id,
        repo_path=repo_path,
        hash=hash_,
        message=message,
        category=category,
        changed_files=changed_files,
        pushed=pushed,
        provider=provider,
        model=model,
        duration_ms=duration_ms,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def mark_push_result(session: Session, commit_id: int, *, pushed: bool, error: str | None = None) -> None:
    from datetime import UTC

    record = session.get(CommitRecordRecord, commit_id)
    if record is None:
        return
    record.pushed = pushed
    record.push_error = error
    record.push_at = now_utc()
    session.commit()


def get_commit_by_hash(session: Session, hash_: str) -> CommitRecordRecord | None:
    return session.scalar(select(CommitRecordRecord).where(CommitRecordRecord.hash == hash_))


def list_commits(session: Session, *, limit: int = 50, repo_id: int | None = None) -> list[CommitRecordRecord]:
    stmt = select(CommitRecordRecord).order_by(CommitRecordRecord.created_at.desc()).limit(limit)
    if repo_id is not None:
        stmt = stmt.where(CommitRecordRecord.repo_id == repo_id)
    return list(session.scalars(stmt))


def commit_count_since(session: Session, since) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(CommitRecordRecord)
            .where(CommitRecordRecord.created_at >= to_storage(since))
        )
        or 0
    )


# --------------------------------------------------------------------------
# Failures, prompts, notifications
# --------------------------------------------------------------------------

def record_failure(session: Session, *, slot_id: int | None, repo_id: int | None, repo_path: str | None,
                   phase: str, message: str, traceback: str | None = None) -> FailureRecord:
    failure = FailureRecord(
        slot_id=slot_id,
        repo_id=repo_id,
        repo_path=repo_path,
        phase=phase,
        message=message,
        traceback=traceback,
    )
    session.add(failure)
    session.commit()
    session.refresh(failure)
    return failure


def record_prompt(session: Session, *, slot_id: int | None, repo_id: int | None, provider: str,
                  model: str | None, prompt: str, response: str | None, success: bool, latency_ms: int) -> None:
    session.add(
        PromptRecord(
            slot_id=slot_id,
            repo_id=repo_id,
            provider=provider,
            model=model,
            prompt=prompt,
            response=response,
            success=success,
            latency_ms=latency_ms,
        )
    )
    session.commit()


def add_notification(session: Session, *, title: str, message: str, level: str, delivered: bool = True) -> None:
    session.add(NotificationRecord(title=title, message=message, level=level, delivered=delivered))
    session.commit()


def list_notifications(session: Session, *, limit: int = 100) -> list[NotificationRecord]:
    return list(
        session.scalars(
            select(NotificationRecord).order_by(NotificationRecord.created_at.desc()).limit(limit)
        )
    )


# --------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------

def start_execution(session: Session, *, kind: str, schedule_id: int | None = None) -> ExecutionRecord:
    record = ExecutionRecord(kind=kind, status="started", schedule_id=schedule_id)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def finish_execution(session: Session, execution_id: int, *, status: str, summary: str | None = None) -> None:
    record = session.get(ExecutionRecord, execution_id)
    if record is None:
        return
    record.status = status
    record.ended_at = now_utc()
    record.summary = summary
    session.commit()


def list_executions(session: Session, *, limit: int = 20) -> list[ExecutionRecord]:
    return list(
        session.scalars(select(ExecutionRecord).order_by(ExecutionRecord.started_at.desc()).limit(limit))
    )


# ---------------------------------------------------------------------------
# Logs & stats
# ---------------------------------------------------------------------------

def list_logs(session: Session, *, limit: int = 500, level: str | None = None, since=None) -> list[LogRecord]:
    stmt = select(LogRecord).order_by(LogRecord.ts.desc()).limit(limit)
    if level:
        stmt = stmt.where(LogRecord.level == level.upper())
    if since is not None:
        stmt = stmt.where(LogRecord.ts >= to_storage(since))
    return list(reversed(list(session.scalars(stmt))))


def delete_logs_older_than(session: Session, days: int) -> int:
    from datetime import timedelta

    cutoff = to_storage(now_utc() - timedelta(days=days))
    result = session.execute(delete(LogRecord).where(LogRecord.ts < cutoff))
    session.commit()
    return result.rowcount or 0


def upsert_stat(session: Session, *, bucket: str, period_start: str, repo_id: int | None,
                commit_count: int, success_count: int, failure_count: int,
                ai_calls: int, ai_success: int, total_duration_ms: int) -> None:
    record = session.scalar(
        select(StatRecord).where(
            StatRecord.bucket == bucket,
            StatRecord.period_start == period_start,
            StatRecord.repo_id == repo_id,
        )
    )
    if record is None:
        record = StatRecord(bucket=bucket, period_start=period_start, repo_id=repo_id)
        session.add(record)
    record.commit_count = commit_count
    record.success_count = success_count
    record.failure_count = failure_count
    record.ai_calls = ai_calls
    record.ai_success = ai_success
    record.total_duration_ms = total_duration_ms
    session.commit()