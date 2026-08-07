"""DayPlanPlanner: turns configuration into a concrete daily execution plan.

Responsibilities:
  * decide the commit count for today (distribution strategy),
  * generate natural timestamps respecting the minimum gap,
  * assign repositories per slot using weighted probabilities and per-repo
    daily caps,
  * produce the ordered plan consumed by the scheduler.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime

from keeper.config.models import AppConfig, RepositoryEntry
from keeper.core.exceptions import PlannerError
from keeper.core.types import DayPlan, PlanMode, PlannedCommit
from keeper.planner.distributions import commit_count, generate_timestamps
from keeper.utils.rand import weighted_choice
from keeper.utils.time import combine_in_tz, local_now, parse_date, parse_time, resolve_timezone

logger = logging.getLogger(__name__)


class DayPlanPlanner:
    """Generates execution plans for a given calendar day."""

    def __init__(self, config: AppConfig, seed: int | None = None) -> None:
        self._config = config
        self._seed = seed

    @property
    def timezone(self) -> str:
        return self._config.schedule.timezone

    @property
    def run_time(self) -> time:
        return parse_time(self._config.schedule.run_time)

    @property
    def dry_run(self) -> bool:
        return self._config.schedule.dry_run

    # ------------------------------------------------------------------

    def should_run(self, day: date | None = None) -> tuple[bool, str]:
        """Check whether the scheduler should execute on ``day`` (default: today).

        Returns (should_run, reason) where reason explains a negative answer.
        """
        schedule = self._config.schedule
        local = local_now(schedule.timezone)
        day = day or local.date()
        start = parse_date(schedule.start_date)
        end = parse_date(schedule.end_date)

        if day < start:
            return False, "before schedule start date"
        if day > end:
            return False, "after schedule end date"
        if not schedule.working_days.is_enabled(day.strftime("%A")):
            return False, f"{day:%A} is not a working day"
        if not self._config.repositories:
            return False, "no repositories configured"
        return True, "ok"

    # ------------------------------------------------------------------

    def generate_plan(self, day: date, *, rng: random.Random | None = None,
                      target_override: int | None = None) -> DayPlan:
        """Build the DayPlan for ``day``.

        Raises PlannerError when the day is outside the configured period or
        not a working day.
        """
        schedule = self._config.schedule
        should, reason = self.should_run(day)
        if not should:
            raise PlannerError(f"Cannot plan {day}: {reason}")

        rng = rng or random.Random(self._seed or random.randrange(1 << 30))
        mode = schedule.plan_mode
        target = (
            target_override
            if target_override is not None
            else commit_count(
                mode,
                schedule.commit_count.min,
                schedule.commit_count.max,
                rng,
                schedule.custom_distribution,
            )
        )

        run_hour = parse_time(schedule.run_time)
        plan_start = combine_in_tz(day, run_hour, schedule.timezone)

        timestamps = generate_timestamps(
            target,
            day=plan_start,
            min_gap_minutes=schedule.commit_interval.min_minutes,
            rng=rng,
        )

        # Enforce the maximum gap as an upper bound where possible.
        max_gap = schedule.commit_interval.max_minutes / 60.0
        timestamps = self._respect_max_gap(timestamps, max_gap, rng)

        slots: list[PlannedCommit] = []
        repo_pool = [r for r in self._config.repositories if r.enabled]
        if not repo_pool:
            raise PlannerError("No enabled repositories to schedule")

        daily_counts = {r.path: 0 for r in repo_pool}
        available = list(repo_pool)

        for ts in timestamps:
            repo = self._select_repo(available, daily_counts, rng)
            if repo is None:
                # Every repository has hit its daily cap; leave the rest of
                # the slots unplanned rather than exceeding the cap.
                break
            daily_counts[repo.path] += 1
            if daily_counts[repo.path] >= repo.max_commits_per_day:
                available.remove(repo)
            slots.append(
                PlannedCommit(
                    scheduled_at=ts,
                    repository_path=repo.path,
                    repository_name=repo.name,
                )
            )

        plan = DayPlan(plan_date=plan_start, target_commits=target, mode=mode, slots=slots)
        logger.info(
            "Planned %d commits for %s (mode=%s) across %d repositories",
            target, day, mode.value, len({s.repository_path for s in slots}),
        )
        return plan

    # ------------------------------------------------------------------

    def _respect_max_gap(self, timestamps: list[datetime], max_gap_hours: float,
                         rng: random.Random) -> list[datetime]:
        """Relax timestamps so no two commits are absurdly far apart.

        When the max gap would be exceeded we nudge later commits closer to
        the previous one within the working window.
        """
        if len(timestamps) < 2:
            return timestamps
        result = list(timestamps)
        tz = resolve_timezone(self._config.schedule.timezone)
        for i in range(1, len(result)):
            gap = (result[i] - result[i - 1]).total_seconds() / 3600.0
            if gap > max_gap_hours:
                target = result[i - 1].timestamp() + max_gap_hours * 3600 * (0.85 + 0.25 * rng.random())
                result[i] = datetime.fromtimestamp(target, tz=tz)
        return result

    def _select_repo(self, pool: list[RepositoryEntry], daily_counts: dict[str, int],
                     rng: random.Random) -> RepositoryEntry | None:
        """Weighted repository selection with health-neutral priority weighting."""
        weights = []
        for repo in pool:
            priority = max(0.1, repo.priority)
            cap_factor = 1.0
            if daily_counts[repo.path] >= repo.max_commits_per_day:
                cap_factor = 0.0
            jitter = 0.6 + 0.8 * rng.random()
            weights.append(priority * cap_factor * jitter)
        if sum(weights) <= 0:
            return None
        return weighted_choice(pool, weights, rng)


def summarize_plan(plan: DayPlan, timezone: str) -> list[dict]:
    """Convert a plan to plain dicts for CLI / GUI / API rendering."""
    rows = []
    for slot in plan.slots:
        rows.append(
            {
                "scheduled_at": slot.scheduled_at.astimezone(resolve_timezone(timezone)).isoformat(),
                "repository": slot.repository_name,
                "state": slot.state.value,
                "message": slot.message,
                "hash": slot.commit_hash,
                "error": slot.error,
            }
        )
    return rows