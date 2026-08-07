"""Distribution strategies: deterministic given a seed, and always in-range."""

from __future__ import annotations

import random

import pytest

from keeper.core.exceptions import PlannerError
from keeper.core.types import PlanMode
from keeper.planner.distributions import commit_count, generate_timestamps
from keeper.utils.time import combine_in_tz
from datetime import date


def test_commit_count_reproducible() -> None:
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    values_a = [commit_count(PlanMode.NATURAL, 1, 8, rng_a) for _ in range(50)]
    values_b = [commit_count(PlanMode.NATURAL, 1, 8, rng_b) for _ in range(50)]
    assert values_a == values_b


@pytest.mark.parametrize("mode", [PlanMode.NATURAL, PlanMode.RANDOM, PlanMode.BALANCED,
                                  PlanMode.HEAVY, PlanMode.LIGHT])
def test_commit_count_in_range(mode: PlanMode) -> None:
    rng = random.Random(7)
    for _ in range(200):
        value = commit_count(mode, 1, 10, rng)
        assert 1 <= value <= 10


def test_commit_count_custom_distribution() -> None:
    rng = random.Random(1)
    values = {commit_count(PlanMode.CUSTOM, 1, 10, rng, [0, 3, 7]) for _ in range(100)}
    assert values == {0, 3, 7}


def test_commit_count_custom_requires_distribution() -> None:
    with pytest.raises(PlannerError):
        commit_count(PlanMode.CUSTOM, 1, 10, random.Random(1))


def test_commit_count_invalid_range() -> None:
    with pytest.raises(PlannerError):
        commit_count(PlanMode.NATURAL, 5, 2, random.Random(1))


def test_generate_timestamps_min_gap() -> None:
    day = combine_in_tz(date(2026, 8, 10), __import__("datetime").time(9, 0), "UTC")
    stamps = generate_timestamps(6, day=day, min_gap_minutes=30, rng=random.Random(3))
    assert len(stamps) == 6
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(stamps[:-1], stamps[1:], strict=True)]
    assert all(gap >= 29 for gap in gaps)  # rounding tolerance


def test_generate_timestamps_single() -> None:
    day = combine_in_tz(date(2026, 8, 10), __import__("datetime").time(9, 0), "UTC")
    stamps = generate_timestamps(1, day=day, min_gap_minutes=20, rng=random.Random(1))
    assert len(stamps) == 1


def test_generate_timestamps_zero() -> None:
    day = combine_in_tz(date(2026, 8, 10), __import__("datetime").time(9, 0), "UTC")
    assert generate_timestamps(0, day=day, min_gap_minutes=20, rng=random.Random(1)) == []
