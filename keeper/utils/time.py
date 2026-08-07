"""Timezone-aware date and time helpers.

All timestamps stored in the database are timezone-aware UTC datetimes.
Helpers in this module convert between the configured local timezone and UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from keeper.core.exceptions import ConfigError

_MINUTE = timedelta(minutes=1)


def resolve_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone name, raising a friendly ConfigError if invalid."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone: {name!r}") from exc


def parse_date(value: str | date | datetime) -> date:
    """Parse a date from string (YYYY-MM-DD), date or datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid date {value!r}, expected YYYY-MM-DD") from exc


def parse_time(value: str | time) -> time:
    """Parse a time from string (HH:MM or HH:MM:SS) or time object."""
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid time {value!r}, expected HH:MM") from exc


def now_utc() -> datetime:
    """Current time as timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_to_tz(dt: datetime, tz_name: str) -> datetime:
    """Convert a timezone-aware (or naive-UTC) datetime into the given timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(resolve_timezone(tz_name))


def tz_to_utc(dt: datetime, tz_name: str) -> datetime:
    """Convert a timezone-aware datetime expressed in ``tz_name`` into UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=resolve_timezone(tz_name))
    return dt.astimezone(UTC)


def local_now(tz_name: str) -> datetime:
    """Current wall-clock time in the configured timezone (aware)."""
    return now_utc().astimezone(resolve_timezone(tz_name))


def local_date(tz_name: str) -> date:
    """Current calendar date in the configured timezone."""
    return local_now(tz_name).date()


def combine_in_tz(day: date, at_time: time, tz_name: str) -> datetime:
    """Combine a date and time in the configured timezone into a tz-aware datetime."""
    tz = resolve_timezone(tz_name)
    return datetime.combine(day, at_time, tzinfo=tz)


def to_storage(dt: datetime) -> datetime:
    """Normalize a datetime for storage (UTC, aware, second precision)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)


def from_storage(dt: datetime) -> datetime:
    """Normalize a datetime read from storage (UTC, aware)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iter_working_days(start: date, end: date, working_days: dict[str, bool]) -> list[date]:
    """Return the list of dates in [start, end] whose weekday is enabled."""
    enabled = {k.capitalize(): v for k, v in working_days.items()}
    days: list[date] = []
    day = start
    while day <= end:
        if enabled.get(day.strftime("%A"), False):
            days.append(day)
        day += timedelta(days=1)
    return days


def minutes_between(start: datetime, end: datetime) -> int:
    """Whole minutes between two datetimes (positive)."""
    delta = end - start
    return max(0, int(delta.total_seconds() // 60))
