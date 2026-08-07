"""Application composition root.

Builds the full dependency graph once, wiring configuration, database, git,
AI, planner, scheduler, services, notifications and statistics together with
explicit constructor injection (no globals, no hidden state).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from keeper.analysis.analyzer import RepositoryAnalyzer
from keeper.ai.factory import ProviderFactory
from keeper.config.manager import ConfigManager
from keeper.config.models import AppConfig
from keeper.core.events import EventBus
from keeper.database.engine import Database
from keeper.git.engine import GitEngine
from keeper.mutation.engine import MutationEngine
from keeper.mutation.safeguards import MutationSafeguards
from keeper.notifications.manager import NotificationManager
from keeper.planner.planner import DayPlanPlanner
from keeper.repositories.manager import RepoManager
from keeper.safety.checks import SafetyEngine
from keeper.scheduler.manager import ScheduleManager
from keeper.services.doctor import Doctor
from keeper.services.execution import CommitExecutionService
from keeper.services.orchestrator import Orchestrator
from keeper.state.tracker import StateTracker
from keeper.stats.service import StatsService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Application:
    """Holds every constructed service (the composition root result)."""

    config_manager: ConfigManager
    config: AppConfig
    database: Database | None
    events: EventBus
    git: GitEngine
    analyzer: RepositoryAnalyzer
    providers: ProviderFactory
    safeguards: MutationSafeguards
    mutation: MutationEngine
    safety: SafetyEngine
    tracker: StateTracker
    repo_manager: RepoManager
    stats: StatsService
    notifications: NotificationManager
    planner: DayPlanPlanner
    executor: CommitExecutionService
    orchestrator: Orchestrator
    scheduler: ScheduleManager | None
    doctor: Doctor

    def session(self) -> Session:
        if self.database is None:
            raise RuntimeError("Database is not available in this context")
        return self.database.session()

    def close(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop(wait=False)
        if self.config_manager is not None:
            self.config_manager.stop_watching()
        if self.database is not None:
            self.database.close()


def build_application(
    config_manager: ConfigManager,
    *,
    start_scheduler: bool = False,
    db_path: Path | None = None,
) -> Application:
    """Assemble the application from an already-loaded ConfigManager.

    Args:
        config_manager: loaded configuration manager (owns reload of watch).
        start_scheduler: when True, wire the APScheduler jobs and start them.
        db_path: override the database location (used by tests).
    """
    cfg = config_manager.config
    events = EventBus()
    git = GitEngine()

    database = Database(db_path or cfg.database_path).connect()

    analyzer = RepositoryAnalyzer(git)
    providers = ProviderFactory(cfg.ai)
    safeguards = MutationSafeguards(
        max_change_bytes=cfg.safety.max_change_bytes,
        allow_untracked_cleanup=cfg.guardrails.allow_untracked_cleanup,
    )
    mutation = MutationEngine(analyzer, providers, safeguards)
    safety = SafetyEngine(
        run_formatter=cfg.safety.run_formatter,
        run_linter=cfg.safety.run_linter,
        run_tests=cfg.safety.run_tests,
        test_timeout=cfg.safety.test_timeout,
    )
    tracker = StateTracker(git)
    repo_manager = RepoManager(git)
    stats = StatsService()
    notifications = NotificationManager(
        desktop=cfg.notifications.desktop and cfg.notifications.enabled,
        console=cfg.notifications.console,
    )

    planner = DayPlanPlanner(cfg)

    def session_factory() -> Session:
        return database.session()

    executor = CommitExecutionService(
        mutation=mutation,
        safety=safety,
        git=git,
        tracker=tracker,
        notifications=notifications,
        events=events,
        session_factory=session_factory,
    )
    orchestrator = Orchestrator(
        planner=planner,
        executor=executor,
        tracker=tracker,
        notifications=notifications,
        events=events,
        session_factory=session_factory,
    )

    scheduler: ScheduleManager | None = None
    if start_scheduler:
        from keeper.scheduler.manager import schedule_time_from

        hour, minute = schedule_time_from(planner.run_time)
        scheduler = ScheduleManager(
            run_hour=hour,
            run_minute=minute,
            on_daily=orchestrator.run_now,
            on_due=orchestrator.run_now,
            timezone=planner.timezone,
        )

    doctor = Doctor(cfg, database)

    # Seed the repository registry from configuration.
    with database.session() as session:
        repo_manager.sync_from_config(session, cfg)

    app = Application(
        config_manager=config_manager,
        config=cfg,
        database=database,
        events=events,
        git=git,
        analyzer=analyzer,
        providers=providers,
        safeguards=safeguards,
        mutation=mutation,
        safety=safety,
        tracker=tracker,
        repo_manager=repo_manager,
        stats=stats,
        notifications=notifications,
        planner=planner,
        executor=executor,
        orchestrator=orchestrator,
        scheduler=scheduler,
        doctor=doctor,
    )

    # Reload active references on config hot-reload.
    config_manager.subscribe(lambda new_cfg: _on_config_change(app, new_cfg))
    return app


def load_cli_app(config_path: Path | None = None, *, start_scheduler: bool = False) -> Application:
    """Convenience entry: load config (or discover it) and build the app."""
    manager = ConfigManager(config_path, watch=False)
    manager.load(config_path)
    return build_application(manager, start_scheduler=start_scheduler)


def _on_config_change(app: Application, new_cfg: AppConfig) -> None:
    """Apply a hot-reloaded configuration to the running application.

    The mutation/safety engine internals keep their references but their
    ``config``-backed behaviors are updated via the replacement objects.
    """
    app.config = new_cfg
    app.providers = ProviderFactory(new_cfg.ai)
    app.mutation = MutationEngine(app.analyzer, app.providers, app.safeguards)
    app.safety = SafetyEngine(
        run_formatter=new_cfg.safety.run_formatter,
        run_linter=new_cfg.safety.run_linter,
        run_tests=new_cfg.safety.run_tests,
        test_timeout=new_cfg.safety.test_timeout,
    )
    app.executor = CommitExecutionService(
        mutation=app.mutation,
        safety=app.safety,
        git=app.git,
        tracker=app.tracker,
        notifications=app.notifications,
        events=app.events,
        session_factory=lambda: app.database.session(),
    )
    app.orchestrator = Orchestrator(
        planner=app.planner,
        executor=app.executor,
        tracker=app.tracker,
        notifications=app.notifications,
        events=app.events,
        session_factory=lambda: app.database.session(),
    )
    logger.info("Configuration hot-reloaded (%d repositories)", len(new_cfg.repositories))
    with app.session() as session:
        app.repo_manager.sync_from_config(session, new_cfg)