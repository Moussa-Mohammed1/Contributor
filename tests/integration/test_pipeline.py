"""End-to-end pipeline tests with a deterministic mock AI provider.

These tests exercise the real composition root (keeper.app.build_application)
against a real git sandbox: plan -> mutate -> safety -> commit -> record.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import date, timedelta
from pathlib import Path

from keeper.core.types import TaskState
from keeper.database import repo as dataset
from keeper.git.engine import GitEngine
from keeper.utils.time import now_utc


def _commit_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    )
    return int(result.stdout.strip())


def _run_slot(app, *, day: date, dry_run: bool) -> tuple[TaskState, dict]:
    """Create a one-slot plan for ``day`` and execute it; return (state, slot info)."""
    with app.session() as session:
        plan_date = f"{day.isoformat()}-{uuid.uuid4().hex[:8]}"
        schedule = dataset.create_schedule(
            session, plan_date=plan_date, mode="balanced",
            target_commits=1, run_time="09:00", dry_run=dry_run,
        )
        slot = dataset.add_slot(
            session, schedule_id=schedule.id, repo_id=None,
            repo_path=app.config.repositories[0].path,
            repo_name=app.config.repositories[0].name,
            scheduled_at=now_utc() - timedelta(minutes=1),
        )
        slot_id, schedule_id = slot.id, schedule.id
    state = app.executor.execute(slot_id, dry_run=dry_run)
    with app.session() as session:
        record = dataset.get_slot(session, slot_id)
        return state, {"state": record.state, "message": record.attempted_message,
                       "hash": record.commit_hash, "error": record.error,
                       "schedule_id": schedule_id}


def test_dry_run_never_touches_git(app, sandbox_repo: Path) -> None:
    before = _commit_count(sandbox_repo)
    state, info = _run_slot(app, day=date.today(), dry_run=True)

    assert state == TaskState.SUCCEEDED
    assert info["state"] == TaskState.SUCCEEDED.value
    assert "[dry-run]" in (info["message"] or "")
    assert _commit_count(sandbox_repo) == before
    # The working tree must be untouched too.
    status = GitEngine().status(sandbox_repo)
    assert status.dirty is False


def test_full_pipeline_creates_commit(app, sandbox_repo: Path) -> None:
    before = _commit_count(sandbox_repo)
    state, info = _run_slot(app, day=date.today(), dry_run=False)

    assert state in (TaskState.SUCCEEDED, TaskState.COMMITTED)
    assert _commit_count(sandbox_repo) == before + 1
    assert info["hash"]
    assert info["message"]
    assert info["state"] in (TaskState.SUCCEEDED.value, TaskState.COMMITTED.value)

    # Commit is recorded in the database with the message.
    with app.session() as session:
        record = dataset.get_commit_by_hash(session, info["hash"])
        assert record is not None
        assert record.message == info["message"]
        assert record.category == "feat"

    # Working tree is clean after the commit.
    status = GitEngine().status(sandbox_repo)
    assert status.dirty is False


def test_dirty_repo_refuses_to_run(app, sandbox_repo: Path) -> None:
    (sandbox_repo / "user_work.txt").write_text("user data", encoding="utf-8")
    state, info = _run_slot(app, day=date.today(), dry_run=False)
    assert state == TaskState.FAILED
    assert "dirty" in (info["error"] or "").lower()
    assert _commit_count(sandbox_repo) == 1


def test_ai_decline_skips_slot(app, config_manager, sandbox_repo: Path) -> None:
    from tests.helpers.mock_provider import MockProvider

    # Force the mock to decline: return an empty changes list.
    original = MockProvider.complete

    def declining(self, request):  # noqa: ANN001, ANN202
        from keeper.ai.base import AIResponse

        return AIResponse(
            text='{"category": "chore", "message": "chore: no improvement needed",'
                 ' "explanation": "nothing", "changes": []}',
            provider="mock", model="mock-1", latency_ms=1,
        )

    MockProvider.complete = declining
    try:
        state, info = _run_slot(app, day=date.today(), dry_run=False)
    finally:
        MockProvider.complete = original

    assert state == TaskState.SKIPPED
    assert "no genuine improvement" in (info["error"] or "")
    assert _commit_count(sandbox_repo) == 1


def test_exhausted_attempts_mark_failed(app, sandbox_repo: Path) -> None:
    """A slot that already hit the attempt budget must not execute again."""
    with app.session() as session:
        schedule = dataset.create_schedule(
            session, plan_date=f"{date.today().isoformat()}-exhaust",
            mode="balanced", target_commits=1, run_time="09:00", dry_run=False,
        )
        slot = dataset.add_slot(
            session, schedule_id=schedule.id, repo_id=None,
            repo_path=app.config.repositories[0].path,
            repo_name=app.config.repositories[0].name,
            scheduled_at=now_utc() - timedelta(minutes=1),
        )
        slot_id = slot.id
        # Simulate 5 crash-recovered attempts, then a fresh plan reset.
        dataset.set_slot_started(session, slot_id, attempts=app.tracker._max_attempts)
        dataset.set_slot_state(session, slot_id, TaskState.PLANNED)

    state = app.executor.execute(slot_id, dry_run=False)
    assert state == TaskState.FAILED
    with app.session() as session:
        record = dataset.get_slot(session, slot_id)
        assert record.state == TaskState.FAILED.value
        assert "exceeded" in (record.error or "")
    assert _commit_count(sandbox_repo) == 1


def test_provider_failure_is_recorded(app, sandbox_repo: Path) -> None:
    """A failing provider must fail the slot and record the failure."""
    from tests.helpers.mock_provider import MockProvider

    original = MockProvider.complete

    def failing_complete(self, request):  # noqa: ANN001, ANN202
        raise RuntimeError("mock outage")

    MockProvider.complete = failing_complete
    try:
        state, info = _run_slot(app, day=date.today(), dry_run=False)
    finally:
        MockProvider.complete = original

    assert state == TaskState.FAILED
    assert info["error"]
    with app.session() as session:
        from sqlalchemy import select

        from keeper.database.models import FailureRecord

        failures = list(session.scalars(select(FailureRecord)))
    assert len(failures) >= 1
    assert _commit_count(sandbox_repo) == 1


def test_recovery_resets_running_slots(app, sandbox_repo: Path) -> None:
    """StateTracker.recover_interrupted resets RUNNING slots to PLANNED."""
    with app.session() as session:
        schedule = dataset.create_schedule(
            session, plan_date=(date.today() - timedelta(days=1)).isoformat(),
            mode="balanced", target_commits=1, run_time="09:00", dry_run=True,
        )
        slot = dataset.add_slot(
            session, schedule_id=schedule.id, repo_id=None,
            repo_path=app.config.repositories[0].path,
            repo_name=app.config.repositories[0].name,
            scheduled_at=now_utc() - timedelta(hours=1),
        )
        dataset.set_slot_started(session, slot.id, attempts=1)
        slot_id = slot.id

    with app.session() as session:
        summary = app.tracker.recover_interrupted(session)
        assert summary["reset"] == 1
        record = dataset.get_slot(session, slot_id)
        assert record.state == TaskState.PLANNED.value


def test_recovery_detects_landed_commit(app, sandbox_repo: Path) -> None:
    """If a commit with the attempted message exists, recovery marks COMMITTED."""
    from tests.helpers.sandbox import commit_file

    commit_file(sandbox_repo, "app.py", "# new\n", "feat: add multiply helper")

    with app.session() as session:
        schedule = dataset.create_schedule(
            session, plan_date=(date.today() - timedelta(days=2)).isoformat(),
            mode="balanced", target_commits=1, run_time="09:00", dry_run=True,
        )
        slot = dataset.add_slot(
            session, schedule_id=schedule.id, repo_id=None,
            repo_path=app.config.repositories[0].path,
            repo_name=app.config.repositories[0].name,
            scheduled_at=now_utc() - timedelta(hours=1),
        )
        dataset.set_slot_started(session, slot.id, attempts=1)
        dataset.set_slot_metadata(
            session, slot.id, provider="mock", model="m",
            attempted_message="feat: add multiply helper",
        )
        slot_id = slot.id

    with app.session() as session:
        summary = app.tracker.recover_interrupted(session)
        assert summary["recovered_committed"] == 1
        record = dataset.get_slot(session, slot_id)
        assert record.state == TaskState.COMMITTED.value
        assert record.commit_hash is not None
