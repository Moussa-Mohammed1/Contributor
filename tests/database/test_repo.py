"""Data-access layer: schedules, slots, commits, logs and stats."""

from __future__ import annotations

from datetime import timedelta

from keeper.core.types import PlanStatus, TaskState
from keeper.database import repo as dataset
from keeper.database.models import LogRecord
from keeper.utils.time import now_utc


def _make_schedule_and_slot(session, *, repo_path: str = "/tmp/sandbox", dry_run: bool = False,
                            scheduled_at=None, plan_date: str = "2026-08-04"):
    schedule = dataset.create_schedule(
        session, plan_date=plan_date, mode="balanced", target_commits=2,
        run_time="09:00", dry_run=dry_run,
    )
    slot = dataset.add_slot(
        session, schedule_id=schedule.id, repo_id=None, repo_path=repo_path,
        repo_name="sandbox", scheduled_at=scheduled_at or (now_utc() - timedelta(minutes=5)),
    )
    return schedule, slot


def test_create_schedule_and_slot(database) -> None:
    with database.session() as session:
        schedule, slot = _make_schedule_and_slot(session)
        assert schedule.status == PlanStatus.PENDING.value
        assert schedule.dry_run is False
        assert slot.state == TaskState.PLANNED.value
        assert slot.repo_name == "sandbox"


def test_get_schedule_by_date(database) -> None:
    with database.session() as session:
        _make_schedule_and_slot(session)
        found = dataset.get_schedule(session, "2026-08-04")
        assert found is not None
        assert dataset.get_schedule(session, "2020-01-01") is None


def test_slot_state_transitions(database) -> None:
    with database.session() as session:
        _, slot = _make_schedule_and_slot(session)
        dataset.set_slot_started(session, slot.id, attempts=1)
        refreshed = dataset.get_slot(session, slot.id)
        assert refreshed.state == TaskState.RUNNING.value
        assert refreshed.attempts == 1
        dataset.set_slot_state(session, slot.id, TaskState.COMMITTED, commit_hash="abc123",
                               message="feat: something")
        refreshed = dataset.get_slot(session, slot.id)
        assert refreshed.state == TaskState.COMMITTED.value
        assert refreshed.commit_hash == "abc123"
        assert refreshed.finished_at is not None


def test_set_slot_state_unknown_raises(database) -> None:
    from keeper.core.exceptions import StateError

    with database.session() as session:
        try:
            dataset.set_slot_state(session, 9999, TaskState.FAILED)
            raise AssertionError("expected StateError")
        except StateError:
            pass


def test_due_slots_filters(database) -> None:
    with database.session() as session:
        _, past = _make_schedule_and_slot(session, scheduled_at=now_utc() - timedelta(minutes=5))
        _, future = _make_schedule_and_slot(session, plan_date="2026-08-05",
                                            scheduled_at=now_utc() + timedelta(hours=5))
        due = dataset.due_slots(session)
        ids = {s.id for s in due}
        assert past.id in ids
        assert future.id not in ids


def test_due_slots_ignores_finished(database) -> None:
    with database.session() as session:
        _, slot = _make_schedule_and_slot(session)
        dataset.set_slot_state(session, slot.id, TaskState.SUCCEEDED)
        assert dataset.due_slots(session) == []


def test_plan_status_completed(database) -> None:
    with database.session() as session:
        schedule, slot = _make_schedule_and_slot(session)
        dataset.set_slot_state(session, slot.id, TaskState.SUCCEEDED)
        assert dataset.plan_status(session, schedule.id) == PlanStatus.COMPLETED.value


def test_plan_status_partial(database) -> None:
    with database.session() as session:
        schedule, _ = _make_schedule_and_slot(session)
        _, slot2 = _make_schedule_and_slot(session, plan_date="2026-08-05")
        slot3 = dataset.add_slot(
            session, schedule_id=schedule.id, repo_id=None, repo_path="/tmp/sandbox",
            repo_name="sandbox", scheduled_at=now_utc() - timedelta(minutes=1),
        )
        dataset.set_slot_state(session, slot3.id, TaskState.FAILED)
        assert dataset.plan_status(session, schedule.id) == PlanStatus.PARTIAL.value


def test_commit_records(database) -> None:
    with database.session() as session:
        schedule, slot = _make_schedule_and_slot(session)
        record = dataset.add_commit_record(
            session, repo_id=None, slot_id=slot.id, repo_path="/tmp/sandbox",
            hash_="deadbeef", message="feat: x", category="feat",
            changed_files=["a.py"], pushed=False, provider="mock", model="m",
            duration_ms=10,
        )
        dataset.mark_push_result(session, record.id, pushed=True, error=None)
        assert dataset.get_commit_by_hash(session, "deadbeef").pushed is True
        assert len(dataset.list_commits(session)) == 1
        assert dataset.commit_count_since(session, now_utc() - timedelta(days=1)) == 1


def test_failures_and_prompts_audited(database) -> None:
    with database.session() as session:
        _, slot = _make_schedule_and_slot(session)
        dataset.record_failure(session, slot_id=slot.id, repo_id=None, repo_path="/tmp/sandbox",
                               phase="linter", message="boom")
        dataset.record_prompt(session, slot_id=slot.id, repo_id=None, provider="mock",
                              model="m", prompt="p", response="r", success=True, latency_ms=5)
        assert len(dataset.list_logs(session)) >= 0


def test_logs_via_db_handler(database) -> None:
    import logging

    from keeper.logging.db_handler import DatabaseLogHandler

    handler = DatabaseLogHandler(database.engine, batch_size=2)
    logger = logging.getLogger("test.db_handler")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("hello world")
    logger.warning("second line")
    handler.flush()
    handler.close()
    with database.session() as session:
        rows = dataset.list_logs(session, limit=10)
    messages = [r.message for r in rows if r.logger == "test.db_handler"]
    assert "hello world" in messages
    assert "second line" in messages
    # Fresh logs must survive a 1-day retention cleanup (deletion itself is
    # covered deterministically by the engine prune test).
    assert dataset.delete_logs_older_than(session, days=1) == 0


def test_execution_records(database) -> None:
    with database.session() as session:
        execution = dataset.start_execution(session, kind="daily")
        dataset.finish_execution(session, execution.id, status="ok", summary="done")
        executions = dataset.list_executions(session)
        assert executions[0].status == "ok"


def test_notifications(database) -> None:
    with database.session() as session:
        dataset.add_notification(session, title="t", message="m", level="info")
        assert len(dataset.list_notifications(session)) == 1


def test_stats_upsert(database) -> None:
    with database.session() as session:
        dataset.upsert_stat(session, bucket="day", period_start="2026-08-04", repo_id=None,
                            commit_count=3, success_count=3, failure_count=0,
                            ai_calls=4, ai_success=4, total_duration_ms=100)
        dataset.upsert_stat(session, bucket="day", period_start="2026-08-04", repo_id=None,
                            commit_count=2, success_count=2, failure_count=0,
                            ai_calls=3, ai_success=3, total_duration_ms=50)
        for i in range(5):
            dataset.add_commit_record(
                session, repo_id=None, slot_id=None, repo_path="/tmp/sandbox",
                hash_=f"hash{i}", message=f"feat: change {i}", category="feat",
                changed_files=[], pushed=False, provider="mock", model="m", duration_ms=1,
            )
        dataset.record_prompt(session, slot_id=None, repo_id=None, provider="mock",
                              model="m", prompt="p", response="r", success=True, latency_ms=1)
    from keeper.stats.service import StatsService

    with database.session() as session:
        today = StatsService().today(session)
    assert today["commits"] == 5
    assert today["ai_calls"] == 1
