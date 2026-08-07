"""Execution state tracker: crash recovery and task lifecycle.

The tracker owns the recovery protocol:
  * on startup, ``recover_interrupted`` resets RUNNING slots to PLANNED,
  * slots whose commit may have landed during the crash window are inspected
    through git reflog/log and marked COMMITTED (never re-committed),
  * slot attempts are bounded so a permanently failing slot cannot spin
    forever.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from keeper.core.types import PlanStatus, TaskState
from keeper.database import repo as dataset
from keeper.database.models import ScheduleRecord, SlotRecord
from keeper.git.engine import GitEngine
from keeper.utils.time import now_utc, to_storage

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RESUME_WINDOW_HOURS = 12


class StateTracker:
    """Persistent task state management with crash recovery."""

    def __init__(self, git: GitEngine | None = None, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._git = git or GitEngine()
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------

    def recover_interrupted(self, session: Session) -> dict[str, int]:
        """Run the startup recovery protocol.

        Returns a summary dict: {"reset": n, "recovered_committed": n,
        "noop": n}.
        """
        summary = {"reset": 0, "recovered_committed": 0, "noop": 0}

        running = list(
            session.scalars(
                select(SlotRecord).where(
                    SlotRecord.state == TaskState.RUNNING.value,
                    SlotRecord.started_at >= to_storage(now_utc() - timedelta(hours=RESUME_WINDOW_HOURS)),
                )
            )
        )
        for slot in running:
            recovered = self._recover_slot(session, slot)
            if recovered:
                summary["recovered_committed"] += 1
            else:
                summary["reset"] += 1

        stale = list(
            session.scalars(
                select(SlotRecord).where(
                    SlotRecord.state == TaskState.RUNNING.value,
                    SlotRecord.started_at < to_storage(now_utc() - timedelta(hours=RESUME_WINDOW_HOURS)),
                )
            )
        )
        for slot in stale:
            dataset.set_slot_state(session, slot.id, TaskState.FAILED, error="interrupted >12h ago, marked failed")
            summary["noop"] += 1

        logger.info(
            "Crash recovery: %d commits recovered, %d slots reset, %d stale failed",
            summary["recovered_committed"], summary["reset"], summary["noop"],
        )
        return summary

    def _recover_slot(self, session: Session, slot: SlotRecord) -> bool:
        """Check whether the interrupted commit actually landed; recover it."""
        if not slot.attempted_message:
            dataset.set_slot_state(
                session, slot.id, TaskState.PLANNED, error="reset after restart"
            )
            return False
        try:
            landed = self._git.find_commit_by_message(
                slot.repo_path,
                slot.attempted_message,
                since=to_storage(slot.started_at) if slot.started_at else None,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not inspect %s during recovery", slot.repo_path, exc_info=True)
            landed = None
        if landed:
            dataset.set_slot_state(
                session, slot.id, TaskState.COMMITTED,
                commit_hash=landed, message=slot.attempted_message,
            )
            logger.warning(
                "Recovered committed slot #%d (%s) hash=%s", slot.id, slot.repo_name, landed[:10]
            )
            return True
        dataset.set_slot_state(
            session, slot.id, TaskState.PLANNED, error="reset after restart"
        )
        return False

    # ------------------------------------------------------------------

    def can_run(self, session: Session, slot: SlotRecord) -> bool:
        """True when the slot is still planned and below the attempt budget."""
        if slot.state != TaskState.PLANNED.value:
            return False
        if slot.attempts >= self._max_attempts:
            dataset.set_slot_state(session, slot.id, TaskState.FAILED,
                                   error=f"exceeded {self._max_attempts} attempts")
            return False
        return True

    def begin(self, session: Session, slot: SlotRecord) -> None:
        """Mark a slot as RUNNING (attempts incremented)."""
        dataset.set_slot_started(session, slot.id, slot.attempts + 1)

    def finish(self, session: Session, slot: SlotRecord, *, state: TaskState,
               error: str | None = None, commit_hash: str | None = None,
               message: str | None = None) -> None:
        """Mark a slot with a terminal state."""
        dataset.set_slot_state(session, slot.id, state, error=error,
                               commit_hash=commit_hash, message=message)

    def meta(self, session: Session, slot: SlotRecord, *, provider: str | None,
             model: str | None, message: str | None) -> None:
        """Persist provider/model/message metadata for the slot."""
        dataset.set_slot_metadata(session, slot.id, provider=provider, model=model,
                                  attempted_message=message)

    # ------------------------------------------------------------------

    def close_plan(self, session: Session, schedule_id: int) -> None:
        """Recompute and persist the plan status when all slots are final."""
        status = dataset.plan_status(session, schedule_id)
        if status in (PlanStatus.COMPLETED.value, PlanStatus.FAILED.value, PlanStatus.PARTIAL.value):
            dataset.update_schedule_status(session, schedule_id, status)

    def due_now(self, session: Session, *, scope: int | None = None) -> list[SlotRecord]:
        """Slots ready to execute right now."""
        return dataset.due_slots(session, scope=scope)

    def next_planned(self, session: Session) -> SlotRecord | None:
        """Earliest future planned slot (for the dashboard)."""
        return session.scalar(
            select(SlotRecord)
            .where(SlotRecord.state == TaskState.PLANNED.value)
            .order_by(SlotRecord.scheduled_at)
            .limit(1)
        )