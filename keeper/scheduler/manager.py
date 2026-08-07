"""Scheduler: APScheduler integration with crash-safe day planning.

The manager owns two recurring jobs:
  * ``daily_tick``  - fires at the configured run time every day,
  * ``due_tick``    - fires every 5 minutes to execute any slot that became
                      due (also covers restarts after the PC was off).

State is kept in the database, so the scheduler survives restarts: on startup
the runner calls :meth:`recover` which resets interrupted slots and re-plans
today when needed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

DailyCallback = Callable[[], None]


class ScheduleManager:
    """Wraps APScheduler and coordinates plan generation + slot execution."""

    def __init__(
        self,
        *,
        run_hour: int,
        run_minute: int,
        on_daily: DailyCallback,
        on_due: DailyCallback,
        timezone: str = "UTC",
    ) -> None:
        self._run_hour = run_hour
        self._run_minute = run_minute
        self._on_daily = on_daily
        self._on_due = on_due
        self._timezone = timezone
        self._scheduler: BackgroundScheduler | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background scheduler with daily and due jobs."""
        with self._lock:
            if self._scheduler is not None and self._scheduler.running:
                return
            self._scheduler = BackgroundScheduler(
                timezone=self._timezone,
                job_defaults={
                    "coalesce": True,
                    "max_instances": 1,
                    "misfire_grace_time": 3600,
                },
            )
            self._scheduler.add_job(
                self._on_daily,
                CronTrigger(hour=self._run_hour, minute=self._run_minute, timezone=self._timezone),
                id="daily_tick",
                replace_existing=True,
            )
            self._scheduler.add_job(
                self._on_due,
                CronTrigger(minute="*/5", timezone=self._timezone),
                id="due_tick",
                replace_existing=True,
            )
            self._scheduler.start()
            logger.info(
                "Scheduler started (daily %02d:%02d %s, due every 5m)",
                self._run_hour, self._run_minute, self._timezone,
            )

    def stop(self, wait: bool = True) -> None:
        with self._lock:
            if self._scheduler is None:
                return
            if self._scheduler.running:
                self._scheduler.shutdown(wait=wait)
            self._scheduler = None
            logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def running_jobs(self) -> list[dict[str, Any]]:
        """Describe the running jobs (for ``keeper status``)."""
        if self._scheduler is None or not self._scheduler.running:
            return []
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append(
                {
                    "id": job.id,
                    "next_run": next_run.isoformat() if next_run else None,
                    "trigger": str(job.trigger)[:80],
                }
            )
        return jobs


def schedule_time_from(run_time: time) -> tuple[int, int]:
    return run_time.hour, run_time.minute


def today_str(tz: str) -> str:
    from keeper.utils.time import local_date

    return local_date(tz).isoformat()