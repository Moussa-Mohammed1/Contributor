"""Deterministic-but-random helpers with optional seeding.

The planner uses these helpers so plans are reproducible in tests while still
looking natural in production.
"""

from __future__ import annotations

import random
from typing import Sequence, TypeVar

T = TypeVar("T")


def weighted_choice(items: Sequence[T], weights: Sequence[float], rng: random.Random) -> T:
    """Pick one item from ``items`` proportionally to ``weights``."""
    if not items:
        raise ValueError("Cannot pick from an empty sequence")
    if len(items) != len(weights):
        raise ValueError("items and weights must have the same length")
    total = sum(max(0.0, w) for w in weights)
    if total <= 0:
        return items[rng.randrange(len(items))]
    point = rng.uniform(0.0, total)
    acc = 0.0
    for item, weight in zip(items, weights, strict=True):
        acc += max(0.0, weight)
        if acc >= point:
            return item
    return items[-1]


def random_ints(
    count: int, min_value: int, max_value: int, rng: random.Random
) -> list[int]:
    """Generate ``count`` random integers in [min_value, max_value]."""
    if min_value > max_value:
        min_value, max_value = max_value, min_value
    return [rng.randint(min_value, max_value) for _ in range(count)]


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into [low, high]."""
    return max(low, min(high, value))
