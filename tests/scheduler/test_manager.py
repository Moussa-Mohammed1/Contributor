"""ScheduleManager: job registration, lifecycle and helpers."""

from __future__ import annotations

from datetime import time

from keeper.scheduler.manager import ScheduleManager, schedule_time_from, today_str


def _callbacks():
    events: list[str] = []
    return {"events": events, "on_daily": lambda: events.append("daily"),
            "on_due": lambda: events.append("due")}


def test_start_registers_both_jobs() -> None:
    callbacks = _callbacks()
    manager = ScheduleManager(
        run_hour=9, run_minute=30,
        on_daily=callbacks["on_daily"], on_due=callbacks["on_due"],
        timezone="UTC",
    )
    manager.start()
    try:
        assert manager.running is True
        jobs = manager.running_jobs()
        ids = {job["id"] for job in jobs}
        assert ids == {"daily_tick", "due_tick"}
    finally:
        manager.stop()
    assert manager.running is False


def test_start_is_idempotent() -> None:
    callbacks = _callbacks()
    manager = ScheduleManager(
        run_hour=9, run_minute=30,
        on_daily=callbacks["on_daily"], on_due=callbacks["on_due"],
        timezone="UTC",
    )
    manager.start()
    manager.start()  # must not raise or duplicate jobs
    try:
        assert len(manager.running_jobs()) == 2
    finally:
        manager.stop()


def test_daily_trigger_hour_and_minute() -> None:
    callbacks = _callbacks()
    manager = ScheduleManager(
        run_hour=9, run_minute=30,
        on_daily=callbacks["on_daily"], on_due=callbacks["on_due"],
        timezone="UTC",
    )
    manager.start()
    try:
        daily = next(j for j in manager.running_jobs() if j["id"] == "daily_tick")
        trigger = daily["trigger"]
        assert "hour='9'" in trigger
        assert "minute='30'" in trigger
    finally:
        manager.stop()


def test_stop_without_start_is_safe() -> None:
    callbacks = _callbacks()
    manager = ScheduleManager(
        run_hour=9, run_minute=30,
        on_daily=callbacks["on_daily"], on_due=callbacks["on_due"],
        timezone="UTC",
    )
    manager.stop()
    assert manager.running is False


def test_schedule_time_from() -> None:
    assert schedule_time_from(time(7, 45)) == (7, 45)
    assert schedule_time_from(time(23, 5, 30)) == (23, 5)


def test_today_str_returns_iso_date() -> None:
    value = today_str("UTC")
    assert len(value) == 10
    assert value.count("-") == 2
