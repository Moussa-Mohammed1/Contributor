"""Orchestrator: coordinates planning, slot execution and plan completion.

The orchestrator is invoked by the scheduler (daily + due ticks) and at
startup. It is idempotent: a day plan is generated once, persisted, and each
invocation only executes the slots that are due and still planned.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date

from sqlalchemy.orm import Session

from keeper.core.events import EventBus
from keeper.core.exceptions import PlannerError
from keeper.core.types import PlanStatus, TaskState
from keeper.database import repo as dataset
from keeper.database.models import ScheduleRecord
from keeper.notifications.manager import NotificationManager
from keeper.planner.planner import DayPlanPlanner
from keeper.services.execution import CommitExecutionService
from keeper.state.tracker import StateTracker
from keeper.utils.time import combine_in_tz, local_date, parse_time, to_storage

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class Orchestrator:
    """Turns a schedule into executed commits."""

    def __init__(
        self,
        planner: DayPlanPlanner,
        executor: CommitExecutionService,
        tracker: StateTracker,
        notifications: NotificationManager,
        events: EventBus,
        session_factory: SessionFactory,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._tracker = tracker
        self._notifications = notifications
        self._events = events
        self._session_factory = session_factory
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def run_now(self) -> dict:
        """Execute today's due work. Returns a summary dict.

        Safe to call repeatedly (idempotent, guarded by an in-process lock).
        """
        if not self._lock.acquire(blocking=False):
            logger.info("Orchestrator already running; skipping concurrent invocation")
            return {"status": "skipped", "reason": "already running"}

        summary = {"status": "idle", "slots_executed": 0, "slots_failed": 0, "commits": 0,
                   "skipped": 0, "plan": None}
        try:
            with self._session_factory() as session:
                schedule = self._ensure_today_plan(session)
                if schedule is None:
                    summary["status"] = "no_plan"
                    return summary

                summary["plan"] = {
                    "id": schedule.id,
                    "date": schedule.plan_date,
                    "target": schedule.target_commits,
                    "dry_run": schedule.dry_run,
                }
                dataset.update_schedule_status(session, schedule.id, PlanStatus.RUNNING.value)

                due = self._tracker.due_now(session, scope=schedule.id)
                if not due:
                    self._close_if_final(session, schedule.id)
                    summary["status"] = "idle"
                    return summary

                summary["status"] = "running"
                for slot in due:
                    state = self._executor.execute(slot.id, dry_run=bool(schedule.dry_run))
                    if state in (TaskState.SUCCEEDED, TaskState.COMMITTED):
                        summary["slots_executed"] += 1
                        if state == TaskState.SUCCEEDED:
                            summary["commits"] += 1
                    elif state == TaskState.SKIPPED:
                        summary["skipped"] += 1
                    else:
                        summary["slots_failed"] += 1

                closed = self._close_if_final(session, schedule.id)
                if closed:
                    self._notify_schedule_end(session, schedule)
                return summary
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Orchestrator run failed")
            summary["status"] = "error"
            return summary
        finally:
            self._lock.release()

    # ------------------------------------------------------------------

    def _ensure_today_plan(self, session: Session) -> ScheduleRecord | None:
        """Create today's plan when missing; otherwise return the existing one."""
        should, reason = self._planner.should_run()
        today = local_date(self._planner.timezone)
        if not should:
            logger.info("Scheduler idle: %s", reason)
            return None

        schedule = dataset.get_schedule(session, today.isoformat())
        if schedule is not None:
            return schedule

        try:
            plan = self._planner.generate_plan(today)
        except PlannerError as exc:
            logger.warning("Plan generation failed: %s", exc)
            return None

        run_time = self._planner.run_time
        schedule = dataset.create_schedule(
            session,
            plan_date=today.isoformat(),
            mode=plan.mode.value,
            target_commits=plan.target_commits,
            run_time=run_time.isoformat(),
            dry_run=self._planner.dry_run,
        )
        for slot in plan.slots:
            dataset.add_slot(
                session,
                schedule_id=schedule.id,
                repo_id=self._repo_id(session, slot.repository_path),
                repo_path=slot.repository_path,
                repo_name=slot.repository_name,
                scheduled_at=slot.scheduled_at,
            )
        self._events.publish("schedule_started", {
            "date": today.isoformat(),
            "target": plan.target_commits,
            "slots": len(plan.slots),
        })
        self._notifications.notify(
            "Today's schedule started",
            f"{plan.target_commits} commits planned across {len({s.repository_path for s in plan.slots})} repos",
            session=session,
        )
        logger.info(
            "Plan created for %s: %d commits (%d slots)",
            today, plan.target_commits, len(plan.slots),
        )
        return schedule

    def _repo_id(self, session: Session, path: str) -> int | None:
        record = dataset.get_repo_by_path(session, path)
        return record.id if record else None

    def _close_if_final(self, session: Session, schedule_id: int) -> bool:
        slots = dataset.list_slots(session, schedule_id)
        if not slots:
            return False
        states = {s.state for s in slots}
        if states <= {TaskState.SUCCEEDED.value, TaskState.COMMITTED.value,
                      TaskState.FAILED.value, TaskState.SKIPPED.value}:
            self._tracker.close_plan(session, schedule_id)
            return True
        return False

    def _notify_schedule_end(self, session: Session, schedule: ScheduleRecord) -> None:
        status = dataset.get_schedule(session, schedule.plan_date)
        if status is None:
            return
        slots = dataset.list_slots(session, status.id)
        done = sum(1 for s in slots if s.state == TaskState.SUCCEEDED.value)
        self._notifications.notify(
            "Schedule completed",
            f"{done}/{status.target_commits} commits completed for {status.plan_date}",
            session=session,
        )
        self._events.publish("schedule_completed", {
            "date": status.plan_date, "done": done, "target": status.target_commits,
        })


def plan_window_days(config) -> list[str]:
    """ISO dates of all working days in the configured window (for the GUI)."""
    from keeper.utils.time import iter_working_days, parse_date

    start = parse_date(config.schedule.start_date)
    end = parse_date(config.schedule.end_date)
    days = iter_working_days(start, end, config.schedule.working_days.as_dict())
    return [d.isoformat() for d in days]