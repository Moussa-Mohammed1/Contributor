"""Single commit slot execution: the heart of the pipeline.

Pipeline per slot:
  plan -> [guard: clean repo] -> run-mutation -> validate -> safety -> rollback
       -> commit -> record -> push -> notify.

Every step is audited in the database and every failure is recorded with its
phase so crash recovery / retries can continue later.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from keeper.commit.messages import safe_message_from_ai
from keeper.config.models import RepositoryEntry
from keeper.core.events import EventBus
from keeper.core.exceptions import ContributorError, RepositoryError
from keeper.core.types import TaskState, ProviderName
from keeper.database import repo as dataset
from keeper.database.models import SlotRecord
from keeper.git.engine import GitEngine, GitStatus
from keeper.mutation.base import MutationOutcome
from keeper.mutation.engine import MutationEngine
from keeper.notifications.manager import NotificationManager
from keeper.safety.checks import SafetyEngine
from keeper.state.tracker import StateTracker

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class CommitExecutionService:
    """Executes a single planned commit slot end-to-end."""

    def __init__(
        self,
        mutation: MutationEngine,
        safety: SafetyEngine,
        git: GitEngine,
        tracker: StateTracker,
        notifications: NotificationManager,
        events: EventBus,
        session_factory: SessionFactory,
    ) -> None:
        self._mutation = mutation
        self._safety = safety
        self._git = git
        self._tracker = tracker
        self._notifications = notifications
        self._events = events
        self._session_factory = session_factory

    # ------------------------------------------------------------------

    def execute(self, slot_id: int, *, dry_run: bool = False) -> TaskState:
        """Execute one slot and return its terminal state."""
        started = time.perf_counter()
        with self._session_factory() as session:
            slot = dataset.get_slot(session, slot_id)
            if slot is None:
                logger.error("Unknown slot %s", slot_id)
                return TaskState.FAILED
            if not self._tracker.can_run(session, slot):
                logger.warning("Slot %s exhausted attempts; skipped", slot_id)
                return TaskState.FAILED
            self._tracker.begin(session, slot)
            try:
                return self._run_slot(session, slot, dry_run=dry_run)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self._record_failure(session, slot, "slot", str(exc))
                self._tracker.finish(session, slot, state=TaskState.FAILED, error=str(exc))
                logger.exception("Slot %s failed", slot_id)
                return TaskState.FAILED
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000)
                logger.info("Slot %s finished in %dms", slot_id, duration_ms)

    # ------------------------------------------------------------------

    def _run_slot(self, session: Session, slot: SlotRecord, *, dry_run: bool) -> TaskState:
        repo = self._repo_entry(session, slot)
        previous = self._recent_messages(slot.repo_path, limit=8)

        # 1. Repo must be clean (guard against overwriting user work).
        try:
            self._git.require_clean(slot.repo_path)
        except ContributorError as exc:
            self._record_failure(session, slot, "clean", str(exc))
            self._tracker.finish(session, slot, state=TaskState.FAILED, error=str(exc))
            return TaskState.FAILED

        # 2. Analyze + mutate via AI.
        outcome = self._mutation.propose(
            repo,
            dry_run=dry_run,
            previous_messages=previous,
            slot_id=slot.id,
            session=session,
        )
        self._set_metadata(session, slot, outcome)

        if outcome.skipped_reason:
            self._tracker.finish(session, slot, state=TaskState.SKIPPED, error=outcome.skipped_reason)
            logger.info("Slot %s skipped: %s", slot.id, outcome.skipped_reason)
            return TaskState.SKIPPED

        if dry_run:
            message = safe_message_from_ai(outcome.plan.category, outcome.plan.message) if outcome.plan else "dry-run"
            self._tracker.finish(session, slot, state=TaskState.SUCCEEDED, message=f"[dry-run] {message}")
            self._events.publish("dry_run_slot", {"slot_id": slot.id, "repo": slot.repo_name, "message": message})
            logger.info("Dry-run would commit: %s", message)
            return TaskState.SUCCEEDED

        # 3. Safety pipeline; rollback on failure.
        safety_result = self._safety.run(slot.repo_path)
        if not safety_result.passed:
            stage = safety_result.failed_stage or "safety"
            self._rollback(slot, outcome, repo)
            message = f"{stage} check failed: {(safety_result.command or '')[:200]}"
            self._record_failure(session, slot, stage, safety_result.output[:4000])
            self._tracker.finish(session, slot, state=TaskState.FAILED, error=message)
            logger.warning("Safety %s failed for slot %s; rolled back", stage, slot.id)
            return TaskState.FAILED

        # 4. Commit.
        commit_message = safe_message_from_ai(
            outcome.plan.category, outcome.plan.message, changed_files=outcome.applied.changed_paths
        )
        self._tracker.meta(session, slot, provider=outcome.provider, model=outcome.model, message=commit_message)

        try:
            commit_hash = self._git.commit(slot.repo_path, commit_message)
        except Exception as exc:  # noqa: BLE001
            self._rollback(slot, outcome, repo)
            self._record_failure(session, slot, "commit", str(exc))
            self._tracker.finish(session, slot, state=TaskState.FAILED, error=f"commit failed: {exc}")
            logger.exception("Commit failed for slot %s", slot.id)
            return TaskState.FAILED

        # 5. Record commit + push.
        record = dataset.add_commit_record(
            session,
            repo_id=slot.repo_id,
            slot_id=slot.id,
            repo_path=slot.repo_path,
            hash_=commit_hash,
            message=commit_message,
            category=outcome.plan.category.value,
            changed_files=outcome.applied.changed_paths,
            pushed=False,
            provider=outcome.provider,
            model=outcome.model,
            duration_ms=0,
        )
        self._tracker.finish(session, slot, state=TaskState.COMMITTED,
                             commit_hash=commit_hash, message=commit_message)

        pushed, push_error = self._push(slot, session)
        dataset.mark_push_result(session, record.id, pushed=pushed, error=push_error)
        self._tracker.finish(session, slot, state=TaskState.SUCCEEDED if pushed else TaskState.COMMITTED,
                             commit_hash=commit_hash, message=commit_message)
        self._events.publish("commit_completed", {
            "slot_id": slot.id, "repo": slot.repo_name, "hash": commit_hash,
            "message": commit_message, "pushed": pushed,
        })
        return TaskState.SUCCEEDED if pushed else TaskState.COMMITTED

    # ------------------------------------------------------------------

    def _repo_entry(self, session: Session, slot: SlotRecord) -> RepositoryEntry:
        """Build a RepositoryEntry for a slot from the registry + configuration."""
        from keeper.database import repo as dataset

        record = dataset.get_repo_by_path(session, slot.repo_path)
        return RepositoryEntry(
            path=slot.repo_path,
            priority=record.priority if record else 1.0,
            max_commits_per_day=record.max_commits_per_day if record else 8,
            preferred_provider=ProviderName(record.preferred_provider) if record and record.preferred_provider else None,
            ignore_patterns=(record.ignore_patterns or []) if record else [],
            enabled=True,
        )

    def _rollback(self, slot: SlotRecord, outcome: MutationOutcome, repo: RepositoryEntry) -> None:
        if outcome.applied and not outcome.applied.empty:
            try:
                self._mutation.rollback(repo, outcome.applied)
            except Exception:  # noqa: BLE001
                logger.exception("Rollback failed for slot %s", slot.id)
        self._git.reset_index(slot.repo_path)

    def _recent_messages(self, path: str, limit: int) -> list[str]:
        try:
            commits = self._git.recent_commits(path, limit=limit)
            return [c.message for c in commits]
        except Exception:  # noqa: BLE001
            return []

    def _set_metadata(self, session: Session, slot: SlotRecord, outcome: MutationOutcome) -> None:
        self._tracker.meta(session, slot, provider=outcome.provider, model=outcome.model,
                           message=outcome.plan.message if outcome.plan else None)

    def _record_failure(self, session: Session, slot: SlotRecord, phase: str, message: str) -> None:
        dataset.record_failure(
            session, slot_id=slot.id, repo_id=slot.repo_id, repo_path=slot.repo_path,
            phase=phase, message=message[:2000],
        )
        self._events.publish("slot_failed", {
            "slot_id": slot.id, "repo": slot.repo_name, "phase": phase,
        })

    def _push(self, slot: SlotRecord, session: Session) -> tuple[bool, str | None]:
        try:
            self._git.push(slot.repo_path)
            self._notifications.notify("Commit pushed", f"{slot.repo_name}: pushed to remote",
                                       session=session)
            return True, None
        except Exception as exc:  # noqa: BLE001
            logger.error("Push failed for %s: %s", slot.repo_path, exc)
            self._notifications.notify("Push failed", f"{slot.repo_name}: {exc}", level="error",
                                       session=session)
            return False, str(exc)[:2000]