"""DayPlanPlanner: window checks, working days, repo caps and gap enforcement."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from keeper.core.exceptions import PlannerError
from keeper.core.types import PlanMode
from keeper.planner.planner import DayPlanPlanner
from tests.conftest import make_planner_config


def _planner(tmp_path, **overrides):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = make_planner_config(tmp_path / "data", repo, **overrides)
    return DayPlanPlanner(cfg, seed=99)


def test_should_run_within_window(tmp_path) -> None:
    planner = _planner(tmp_path)
    should, reason = planner.should_run(date.today())
    assert should is True
    assert reason == "ok"


def test_should_run_outside_window(tmp_path) -> None:
    planner = _planner(tmp_path)
    should, reason = planner.should_run(date.today() + timedelta(days=400))
    assert should is False
    assert "after schedule end" in reason


def test_should_run_working_days(tmp_path) -> None:
    working_days = {d: True for d in
                    ("monday", "tuesday", "wednesday", "thursday", "friday")}
    working_days.update(saturday=False, sunday=False)
    planner = _planner(tmp_path, schedule={"working_days": working_days})
    should, reason = planner.should_run(date(2026, 8, 8))  # Saturday
    assert should is False
    assert "not a working day" in reason


def test_generate_plan_target_override(tmp_path) -> None:
    planner = _planner(tmp_path)
    plan = planner.generate_plan(date.today(), target_override=3)
    assert plan.target_commits == 3
    assert len(plan.slots) == 3
    assert all(s.repository_path.endswith("repo") for s in plan.slots)


def test_generate_plan_respects_repo_cap(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = make_planner_config(
        tmp_path / "data",
        repo,
        repositories=[
            {"path": str(repo), "priority": 1.0, "max_commits_per_day": 2, "enabled": True}
        ],
    )
    planner = DayPlanPlanner(cfg, seed=1)
    plan = planner.generate_plan(date.today(), target_override=5)
    # The cap is a hard daily limit: a single repo capped at 2 must yield
    # exactly 2 planned slots, never more.
    assert len(plan.slots) == 2
    assert plan.target_commits == 5  # target stays as configured; plan is honest


def test_generate_plan_raises_outside_window(tmp_path) -> None:
    planner = _planner(tmp_path)
    with pytest.raises(PlannerError):
        planner.generate_plan(date.today() + timedelta(days=400))


def test_generate_plan_empty_repos(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = make_planner_config(tmp_path / "data", repo, repositories=[])
    planner = DayPlanPlanner(cfg, seed=1)
    with pytest.raises(PlannerError, match="no repositories configured"):
        planner.generate_plan(date.today(), target_override=1)


def test_plan_timestamps_after_run_time(tmp_path) -> None:
    planner = _planner(tmp_path)
    plan = planner.generate_plan(date.today(), target_override=4)
    tz = ZoneInfo("UTC")
    for slot in plan.slots:
        assert slot.scheduled_at.tzinfo is not None
        assert slot.scheduled_at.astimezone(tz).time() >= time(9, 0)


def test_summarize_plan(tmp_path) -> None:
    planner = _planner(tmp_path)
    plan = planner.generate_plan(date.today(), target_override=2)
    from keeper.planner.planner import summarize_plan

    rows = summarize_plan(plan, "UTC")
    assert len(rows) == 2
    assert rows[0]["repository"]
    assert rows[0]["state"] == "planned"
    datetime.fromisoformat(rows[0]["scheduled_at"])  # must parse
