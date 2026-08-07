"""SQLAlchemy ORM models for the contributor state database.

All timestamps are stored as timezone-aware UTC datetimes (SQLite stores them
as ISO strings via SQLAlchemy's DateTime(timezone=True)).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def utcnow() -> datetime:
    from keeper.utils.time import now_utc

    return now_utc()


class Setting(Base):
    """Key/value application settings."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class RepoRecord(Base):
    """A managed repository and its accumulated metadata."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(128), default="main")
    priority: Mapped[float] = mapped_column(Float, default=1.0)
    preferred_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ignore_patterns: Mapped[list] = mapped_column(JSON, default=list)
    max_commits_per_day: Mapped[int] = mapped_column(Integer, default=8)
    enabled: Mapped[bool] = mapped_column(default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health: Mapped[str] = mapped_column(String(32), default="unknown")
    health_score: Mapped[float] = mapped_column(Float, default=0.0)
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    slots: Mapped[list["SlotRecord"]] = relationship(back_populates="repo", passive_deletes=True)
    commits: Mapped[list["CommitRecordRecord"]] = relationship(back_populates="repo", passive_deletes=True)

    def __repr__(self) -> str:
        return f"<RepoRecord {self.name} ({self.path}) health={self.health}>"


class ScheduleRecord(Base):
    """One execution day: the generated plan and its aggregate status."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # YYYY-MM-DD
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    target_commits: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    run_time: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dry_run: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    slots: Mapped[list["SlotRecord"]] = relationship(
        back_populates="schedule", passive_deletes=True, order_by="SlotRecord.scheduled_at"
    )

    __table_args__ = (UniqueConstraint("plan_date", name="uq_schedule_plan_date"),)


class SlotRecord(Base):
    """One planned commit inside a schedule."""

    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    repo_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="planned")
    attempted_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)

    schedule: Mapped[ScheduleRecord] = relationship(back_populates="slots")
    repo: Mapped[RepoRecord | None] = relationship(back_populates="slots")

    def __repr__(self) -> str:
        return f"<SlotRecord #{self.id} {self.state} {self.scheduled_at}>"


class CommitRecordRecord(Base):
    """One executed commit."""

    __tablename__ = "commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repo_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    pushed: Mapped[bool] = mapped_column(default=False, index=True)
    push_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    repo: Mapped[RepoRecord | None] = relationship(back_populates="commits")

    def __repr__(self) -> str:
        return f"<CommitRecordRecord {self.hash[:8]} {self.category}>"


class FailureRecord(Base):
    """A recorded failure with phase and context."""

    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[Text] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PromptRecord(Base):
    """Stored AI prompt and response for auditability."""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LogRecord(Base):
    """Mirrored structured log line for the GUI Logs page."""

    __tablename__ = "log_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    logger: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class ExecutionRecord(Base):
    """A full daemon execution cycle."""

    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), default="daily")
    status: Mapped[str] = mapped_column(String(32), default="started")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationRecord(Base):
    """Recent notification events, shown in the GUI Notifications page."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info")
    delivered: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class StatRecord(Base):
    """Aggregated statistics snapshot (computed periodically)."""

    __tablename__ = "stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)  # daily|weekly|monthly
    period_start: Mapped[str] = mapped_column(String(16), nullable=False)
    repo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    ai_success: Mapped[int] = mapped_column(Integer, default=0)
    total_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)