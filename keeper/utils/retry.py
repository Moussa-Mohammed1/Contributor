"""Retry policies and backoff helpers for unreliable operations (AI, network)."""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted."""


def retry(
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None,
    reraise: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that retries a callable with exponential backoff.

    Args:
        attempts: maximum number of attempts (including the first).
        base_delay: initial delay in seconds.
        max_delay: cap for the delay in seconds.
        backoff_factor: multiplier applied to the delay per attempt.
        jitter: add random jitter to avoid thundering herds.
        exceptions: tuple of exception types to retry on.
        on_retry: optional callback invoked before each retry.
        reraise: re-raise the last exception after exhausting attempts.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= attempts:
                        break
                    delay = min(max_delay, base_delay * (backoff_factor ** (attempt - 1)))
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    if logger is not None:
                        logger.warning(
                            "Retry %d/%d for %s after error: %s (sleeping %.1fs)",
                            attempt,
                            attempts,
                            getattr(func, "__qualname__", func),
                            exc,
                            delay,
                        )
                    if callable(on_retry):
                        on_retry(attempt, exc)
                    time.sleep(delay)
            if last_exc is not None:
                if reraise:
                    raise last_exc
                raise RetryExhaustedError(
                    f"{getattr(func, '__qualname__', func)} failed after {attempts} attempts"
                ) from last_exc
            raise RetryExhaustedError("unreachable")

        return wrapper

    return decorator


def backoff_delay(attempt: int, base: float = 1.0, factor: float = 2.0, jitter: bool = True) -> float:
    """Compute the sleep delay (seconds) for a given 0-based attempt index."""
    import math

    delay = base * math.pow(factor, attempt)
    if jitter:
        delay *= 0.5 + random.random()
    return delay