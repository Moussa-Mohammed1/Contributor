"""Retry policy: attempt bounds, exhaustion and exception filtering."""

from __future__ import annotations

import pytest

from keeper.utils.retry import RetryExhaustedError, retry


def test_retry_succeeds_first_try() -> None:
    calls = []

    @retry(attempts=3, base_delay=0, jitter=False)
    def work() -> str:
        calls.append(1)
        return "ok"

    assert work() == "ok"
    assert len(calls) == 1


def test_retry_succeeds_after_failures() -> None:
    calls = []

    @retry(attempts=3, base_delay=0, jitter=False)
    def work() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("boom")
        return "ok"

    assert work() == "ok"
    assert len(calls) == 3


def test_retry_exhausted_raises() -> None:
    @retry(attempts=2, base_delay=0, jitter=False)
    def work() -> str:
        raise ValueError("boom")

    with pytest.raises(RetryExhaustedError):
        work()


def test_retry_reraise_original() -> None:
    @retry(attempts=2, base_delay=0, jitter=False, reraise=True)
    def work() -> str:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        work()


def test_retry_exception_filter() -> None:
    @retry(attempts=3, base_delay=0, jitter=False, exceptions=(KeyError,))
    def work() -> str:
        raise ValueError("not retried")

    with pytest.raises(ValueError):
        work()


def test_retry_on_retry_callback() -> None:
    seen: list[int] = []

    @retry(attempts=2, base_delay=0, jitter=False,
           on_retry=lambda attempt, exc: seen.append(attempt))
    def work() -> str:
        raise ValueError("boom")

    with pytest.raises(RetryExhaustedError):
        work()
    assert seen == [1]
