"""Commit count and timestamp distribution strategies.

The goal is human-looking activity: no rigid 1/10/1/10 patterns and no
clustered commits. All distributions are reproducible given a fixed seed.
"""

from __future__ import annotations

import random
from datetime import datetime

from keeper.core.exceptions import PlannerError
from keeper.core.types import PlanMode
from keeper.utils.rand import clamp

_DAY_START_HOUR = 8.0  # earliest commit (wall clock, local tz)
_DAY_END_HOUR = 22.0  # latest commit (wall clock, local tz)


def commit_count(
    mode: PlanMode,
    min_count: int,
    max_count: int,
    rng: random.Random,
    custom_distribution: list[int] | None = None,
) -> int:
    """Generate a daily commit count for the given mode.

    Each mode uses a different shape:
      * NATURAL:  triangular distribution peaking between min and max.
      * RANDOM:   uniform in [min, max].
      * BALANCED: close to the midpoint with small jitter.
      * HEAVY:    biased to the top third of the range.
      * LIGHT:    biased to the bottom third of the range.
      * CUSTOM:   uniform pick from the provided distribution.
    """
    if min_count > max_count or min_count < 1:
        raise PlannerError(f"Invalid commit range: [{min_count}, {max_count}]")

    if mode == PlanMode.CUSTOM:
        if not custom_distribution:
            raise PlannerError("custom plan_mode requires custom_distribution")
        return rng.choice([c for c in custom_distribution if c >= 0])

    span = max_count - min_count

    if mode == PlanMode.RANDOM:
        return rng.randint(min_count, max_count)

    if mode == PlanMode.BALANCED:
        mid = (min_count + max_count) / 2
        jitter = max(1.0, span / 6.0)
        value = int(round(rng.gauss(mid, jitter)))
        return int(clamp(value, min_count, max_count))

    if mode == PlanMode.LIGHT:
        if span == 0:
            return min_count
        peak = min_count + span * 0.15
        value = rng.triangular(min_count, peak, min_count + span * 0.4)
        return int(round(clamp(value, min_count, max_count)))

    if mode == PlanMode.HEAVY:
        if span == 0:
            return min_count
        peak = min_count + span * 0.85
        value = rng.triangular(min_count + span * 0.6, max_count, peak)
        return int(round(clamp(value, min_count, max_count)))

    # NATURAL: beta-shaped probability over the range, so daily totals vary
    # without ever looking mechanical.
    probs = [_density(i, min_count, max_count) for i in range(min_count, max_count + 1)]
    total = sum(probs)
    if total <= 0:
        return rng.randint(min_count, max_count)
    point = rng.uniform(0.0, total)
    acc = 0.0
    for count, prob in zip(range(min_count, max_count + 1), probs, strict=True):
        acc += prob
        if acc >= point:
            return count
    return max_count


def _density(count: int, min_count: int, max_count: int) -> float:
    """Quadratic bell density peaking between the bounds."""
    x = (count - min_count) / max(1, max_count - min_count)  # 0..1
    return 4.0 * x * (1.0 - x) + 0.2


def generate_timestamps(
    count: int,
    *,
    day: datetime,
    min_gap_minutes: int,
    rng: random.Random,
    start_hour: float = _DAY_START_HOUR,
    end_hour: float = _DAY_END_HOUR,
) -> list[datetime]:
    """Generate ``count`` naturally-spread commit timestamps within a day.

    Times are drawn from a Beta(2,2)-shaped distribution (quiet mornings and
    late evenings, busy mid-day), sorted, then pushed apart until every gap
    respects ``min_gap_minutes``. Seconds are randomized for a human feel.
    """
    if count < 1:
        return []
    if count == 1:
        return [_day_at(day, _sample_point(rng, start_hour, end_hour))]

    gap_hours = min_gap_minutes / 60.0
    required = (count - 1) * gap_hours
    span_hours = end_hour - start_hour

    if required > span_hours:
        # The day is too short for the configured minimum gap; extend the
        # working window (bounded) rather than clustering commits.
        start_hour = 7.0
        end_hour = 23.0
        span_hours = end_hour - start_hour
    if required > span_hours:
        # Still impossible: relax the gap to fit.
        gap_hours = span_hours / max(1, count - 1)

    points = sorted(_sample_point(rng, start_hour, end_hour) for _ in range(count))

    for _ in range(40):
        points.sort()
        moved = False
        for i in range(1, len(points)):
            gap = points[i] - points[i - 1]
            if gap < gap_hours:
                delta = (gap_hours - gap) / 2.0
                points[i - 1] = max(start_hour + 0.15, points[i - 1] - delta)
                points[i] = min(end_hour - 0.15, points[i] + delta)
                moved = True
        if not moved:
            break

    return [_day_at(day, h) for h in sorted(points)]


def _sample_point(rng: random.Random, start_hour: float, end_hour: float) -> float:
    """Sample a wall-clock hour in [start_hour, end_hour] with mid-day bias."""
    x = rng.betavariate(2.0, 2.0)  # peaked at 0.5
    return start_hour + x * (end_hour - start_hour)


def _day_at(day: datetime, hours: float) -> datetime:
    hour = int(hours)
    minute = int(round((hours - hour) * 60))
    if minute >= 60:
        minute = 59
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)