"""Timezone-aware time helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from keeper.core.exceptions import ConfigError
from keeper.utils.time import (
    combine_in_tz,
    iter_working_days,
    local_date,
    minutes_between,
    now_utc,
    parse_date,
    parse_time,
    resolve_timezone,
    to_storage,
    utc_to_tz,
)


def test_resolve_timezone_ok() -> None:
    tz = resolve_timezone("Europe/London")
    assert tz is not None


def test_resolve_timezone_invalid() -> None:
    with pytest.raises(ConfigError):
        resolve_timezone("Not/AZone")


def test_parse_date_variants() -> None:
    assert parse_date("2026-08-04") == date(2026, 8, 4)
    assert parse_date(date(2026, 8, 4)) == date(2026, 8, 4)
    assert parse_date(datetime(2026, 8, 4, 12, 0)) == date(2026, 8, 4)
    with pytest.raises(ConfigError):
        parse_date("not-a-date")


def test_parse_time_variants() -> None:
    assert parse_time("08:30") == time(8, 30)
    assert parse_time("08:30:15") == time(8, 30, 15)
    assert parse_time(time(9, 0)) == time(9, 0)
    with pytest.raises(ConfigError):
        parse_time("oops")


def test_now_utc_is_aware() -> None:
    assert now_utc().tzinfo == UTC


def test_utc_to_tz_handles_naive() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    converted = utc_to_tz(naive, "UTC")
    assert converted.tzinfo is not None
    assert converted.hour == 12


def test_to_storage_normalizes() -> None:
    aware = datetime(2026, 1, 1, 12, 0, 15, 123456, tzinfo=UTC)
    stored = to_storage(aware)
    assert stored.microsecond == 0
    assert stored.tzinfo == UTC


def test_combine_in_tz() -> None:
    combined = combine_in_tz(date(2026, 8, 4), time(9, 30), "UTC")
    assert combined.tzinfo is not None
    assert combined.hour == 9
    assert combined.minute == 30


def test_iter_working_days() -> None:
    days = iter_working_days(
        date(2026, 8, 3), date(2026, 8, 9),  # Mon..Sun
        {"monday": True, "tuesday": False, "wednesday": True, "thursday": False,
         "friday": True, "saturday": False, "sunday": False},
    )
    assert days == [date(2026, 8, 3), date(2026, 8, 5), date(2026, 8, 7)]


def test_minutes_between() -> None:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 10, 30, tzinfo=UTC)
    assert minutes_between(start, end) == 30
    assert minutes_between(end, start) == 0


def test_local_date_is_date() -> None:
    assert isinstance(local_date("UTC"), date)
