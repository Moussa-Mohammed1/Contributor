"""Typer CLI for contributor (``keeper`` command)."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from keeper import __version__
from keeper.config.manager import ConfigManager
from keeper.config.models import AppConfig
from keeper.core.exceptions import ConfigError, ContributorError

app = typer.Typer(
    name="keeper",
    help="contributor - AI-powered continuous Git repository improvement.",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

console = Console()
err_console = Console(stderr=True)


def _fail(message: str, code: int = 1) -> None:
    err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code)


def _manager(config_path: Path | None) -> ConfigManager:
    try:
        manager = ConfigManager(config_path, watch=False)
        manager.load(config_path)
        return manager
    except ConfigError as exc:
        _fail(str(exc))


def _load_config(config_path: Path | None) -> AppConfig:
    return _manager(config_path).config


@app.callback()
def main_callback(version: bool = typer.Option(False, "--version", "-v", help="Show version")) -> None:
    if version:
        console.print(f"contributor {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# keeper init
# ---------------------------------------------------------------------------

@app.command()
def init(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    start: str = typer.Option(..., "--start", help="Schedule start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., "--end", help="Schedule end date (YYYY-MM-DD)"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing configuration"),
    repos: list[str] = typer.Option([], "--repo", help="Repository path (repeatable)"),
) -> None:
    """Initialize configuration, directories and the state database."""
    from datetime import timedelta  # noqa: F401

    from keeper.config.loader import generate_default_config
    from keeper.database.engine import Database

    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        _fail(f"invalid date, expected YYYY-MM-DD: {exc}")
    if start_date >= end_date:
        _fail("schedule start must be before end")

    manager = ConfigManager(config_path, watch=False)
    target = manager.resolve_init_path(config_path)
    if target.exists() and not force:
        console.print(
            f"[yellow]Configuration already exists at {target}.[/yellow] "
            "Use --force to overwrite."
        )
        raise typer.Exit()

    text = generate_default_config(start_date.isoformat(), end_date.isoformat())
    if repos:
        repo_lines = "\n".join(f"    - path: {r}" for r in repos)
        text = text.replace("schedule:", "repositories:\n" + repo_lines + "\nschedule:", 1)
    manager.save_text(text)
    manager.reload()

    data_dir = manager.config.resolved_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    manager.config.log_dir.mkdir(parents=True, exist_ok=True)
    Database(manager.config.database_path).connect()

    console.print(Panel.fit(
        f"[bold green]contributor initialized[/bold green]\n\n"
        f"config : [cyan]{manager.config_path}[/cyan]\n"
        f"data   : [cyan]{data_dir}[/cyan]\n"
        f"window : {start_date} .. {end_date}\n\n"
        f"Next steps:\n"
        f"  keeper config edit   - add your repositories\n"
        f"  keeper doctor        - verify the environment\n"
        f"  keeper start         - launch the background daemon",
        title="init",
    ))


# ---------------------------------------------------------------------------
# keeper start / stop / status
# ---------------------------------------------------------------------------

@app.command()
def start(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in the foreground"),
) -> None:
    """Start the background scheduler daemon."""
    from keeper.workers import daemon

    try:
        if foreground:
            from keeper.workers.runner import main as runner_main

            raise SystemExit(runner_main([*(f"--config={config}" if config else []), "--foreground"]))
        pid = daemon.start_daemon(config)
        console.print(f"[green]Daemon started[/green] (pid {pid}). Run `keeper status` to verify.")
    except (daemon.DaemonError, ConfigError) as exc:
        _fail(str(exc))


@app.command()
def stop(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
) -> None:
    """Stop the background scheduler daemon."""
    from keeper.workers import daemon

    pid, message = daemon.stop_daemon(config)
    if pid is None:
        console.print("[yellow]Daemon is not running.[/yellow]")
    else:
        console.print(f"[green]Daemon {message}.[/green]")


@app.command()
def status(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
) -> None:
    """Show daemon status and today's plan summary."""
    from keeper.app import load_cli_app
    from keeper.database import repo as dataset
    from keeper.workers import daemon

    manager = _manager(config)
    daemon_info = daemon.daemon_status(config)

    table = Table(title="contributor status", title_justify="left")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("daemon", "running" if daemon_info["running"] else "not running")
    table.add_row("pid", str(daemon_info["pid"]) if daemon_info["pid"] else "-")
    table.add_row("config", str(manager.config_path))
    table.add_row("data dir", str(manager.config.resolved_data_dir()))
    table.add_row("timezone", manager.config.schedule.timezone)
    table.add_row("window", f"{manager.config.schedule.start_date} .. {manager.config.schedule.end_date}")
    table.add_row("plan mode", manager.config.schedule.plan_mode.value)
    table.add_row("dry run", str(manager.config.schedule.dry_run))

    try:
        app_ctx = load_cli_app(config)
        with app_ctx.session() as session:
            today = manager.config.schedule
            from keeper.utils.time import local_date

            schedule = dataset.get_schedule(session, local_date(today.timezone).isoformat())
            if schedule is not None:
                slots = dataset.list_slots(session, schedule.id)
                done = sum(1 for s in slots if s.state in ("succeeded", "committed"))
                table.add_row(
                    "today's plan",
                    f"[{schedule.status}] {done}/{schedule.target_commits} commits "
                    f"({len(slots)} slots, {schedule.mode})",
                )
        app_ctx.close()
    except ContributorError as exc:
        table.add_row("database", f"error: {exc}")

    console.print(table)


# ---------------------------------------------------------------------------
# keeper schedule
# ---------------------------------------------------------------------------

@app.command()
def schedule(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    today: bool = typer.Option(False, "--today", help="Show only today's plan"),
) -> None:
    """Show today's and upcoming execution plans."""
    from keeper.app import load_cli_app
    from keeper.database import repo as dataset
    from keeper.utils.time import local_date, utc_to_tz

    manager = _manager(config)
    app_ctx = load_cli_app(config)
    tz = manager.config.schedule.timezone

    with app_ctx.session() as session:
        schedules = dataset.list_schedules(session, limit=14)
        if today and schedules:
            schedules = [s for s in schedules if s.plan_date == local_date(tz).isoformat()]
        if not schedules:
            console.print("[yellow]No schedules yet. The daemon will create one on its first run.[/yellow]")
        for record in schedules:
            plan_table = Table(
                title=f"{record.plan_date}  ({record.status}, target {record.target_commits})",
                title_justify="left",
            )
            plan_table.add_column("Time", style="cyan")
            plan_table.add_column("Repo")
            plan_table.add_column("State", style="bold")
            plan_table.add_column("Commit")
            slots = dataset.list_slots(session, record.id)
            if not slots:
                plan_table.add_row("-", "-", "-", "-")
            for slot in slots:
                local_time = utc_to_tz(slot.scheduled_at, tz).strftime("%H:%M")
                plan_table.add_row(
                    local_time,
                    slot.repo_name,
                    _state_style(slot.state),
                    slot.commit_hash[:10] if slot.commit_hash else "-",
                )
            console.print(plan_table)
            console.print()
    app_ctx.close()


def _state_style(state: str) -> str:
    return {
        "succeeded": "[green]succeeded[/green]",
        "committed": "[green]committed[/green]",
        "failed": "[red]failed[/red]",
        "skipped": "[yellow]skipped[/yellow]",
        "running": "[cyan]running[/cyan]",
        "planned": "[dim]planned[/dim]",
    }.get(state, state)


# ---------------------------------------------------------------------------
# keeper repositories
# ---------------------------------------------------------------------------

@app.command()
def repositories(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    refresh_health: bool = typer.Option(False, "--health", help="Re-check repository health"),
) -> None:
    """List configured repositories and their health."""
    from keeper.app import load_cli_app

    manager = _manager(config)
    app_ctx = load_cli_app(config)

    with app_ctx.session() as session:
        if refresh_health:
            app_ctx.repo_manager.check_health(session, manager.config)
            console.print("[dim]Health refreshed.[/dim]\n")
        rows = app_ctx.repo_manager.snapshots(session, manager.config)

    table = Table(title=f"repositories ({len(rows)})")
    table.add_column("Name", style="bold")
    table.add_column("Path")
    table.add_column("Branch")
    table.add_column("Health")
    table.add_column("Score")
    table.add_column("Last commit")
    for row in rows:
        health_text = {
            "healthy": "[green]healthy[/green]",
            "degraded": "[yellow]degraded[/yellow]",
            "unhealthy": "[red]unhealthy[/red]",
            "unknown": "[dim]unknown[/dim]",
        }.get(row.health, row.health)
        table.add_row(
            row.name,
            row.path,
            row.default_branch,
            health_text,
            f"{row.health_score:.0f}/100",
            (row.last_commit_at or "-")[:19].replace("T", " "),
        )
    console.print(table)
    app_ctx.close()


# ---------------------------------------------------------------------------
# keeper logs
# ---------------------------------------------------------------------------

@app.command()
def logs(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    tail: int = typer.Option(50, "--tail", "-n", help="Number of lines"),
    level: str = typer.Option(None, "--level", "-l", help="Filter by level (DEBUG/INFO/WARNING/ERROR)"),
    raw: bool = typer.Option(False, "--raw", help="Read the raw log file instead of the database"),
) -> None:
    """Tail structured logs."""
    from keeper.app import load_cli_app
    from keeper.database import repo as dataset

    manager = _manager(config)
    if raw:
        log_file = manager.config.log_dir / "contributor.log"
        if not log_file.exists():
            _fail(f"no log file at {log_file}")
        lines = log_file.read_text(encoding="utf-8").splitlines()[-tail:]
        for line in lines:
            console.print(line)
        return

    app_ctx = load_cli_app(config)
    with app_ctx.session() as session:
        rows = dataset.list_logs(session, limit=tail, level=level)
    for row in rows:
        color = {"ERROR": "red", "WARNING": "yellow", "DEBUG": "dim"}.get(row.level, "white")
        console.print(
            f"[dim]{row.ts.strftime('%H:%M:%S')}[/dim] "
            f"[{color}]{row.level:<8}[/{color}] {row.logger}: {row.message}"
        )
    app_ctx.close()


# ---------------------------------------------------------------------------
# keeper doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
) -> None:
    """Run a full environment / repository diagnostic."""
    from keeper.app import load_cli_app

    manager = _manager(config)
    app_ctx = load_cli_app(config)
    report = app_ctx.doctor.run()
    app_ctx.close()

    if json_output:
        console.print(json.dumps(report.to_dict(), indent=2))
        return

    table = Table(title="keeper doctor", title_justify="left")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail")
    for check in report.checks:
        table.add_row(
            check.name,
            "[green]OK[/green]" if check.ok else "[red]FAIL[/red]",
            check.message,
        )
    console.print(table)
    for check in report.checks:
        for warning in check.warnings:
            console.print(f"[yellow]warning ({check.name}):[/yellow] {warning}")
    if not report.passed:
        console.print("\n[red]Some checks failed. See the details above.[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# keeper dry-run
# ---------------------------------------------------------------------------

@app.command()
def dry_run(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    day: str = typer.Option(None, "--day", help="Simulate a specific day (default: today)"),
) -> None:
    """Simulate a full execution day with no side effects on disk or git."""
    from keeper.app import load_cli_app

    manager = _manager(config)
    app_ctx = load_cli_app(config)
    tz = manager.config.schedule.timezone

    try:
        target_day = date.fromisoformat(day) if day else date.today()
    except ValueError as exc:
        app_ctx.close()
        _fail(f"invalid date, expected YYYY-MM-DD: {exc}")
    console.print(
        Panel.fit(
            f"[bold]Dry run for {target_day}[/bold] (timezone {tz}, mode "
            f"{manager.config.schedule.plan_mode.value})",
            title="dry-run",
        )
    )

    plan = app_ctx.planner.generate_plan(target_day)
    console.print(f"Target commits: [bold]{plan.target_commits}[/bold]\n")

    plan_table = Table()
    plan_table.add_column("Time")
    plan_table.add_column("Repository")
    for slot in plan.slots:
        plan_table.add_row(slot.scheduled_at.astimezone(
            __import__("zoneinfo").ZoneInfo(tz)).strftime("%H:%M"), slot.repository_name)
    console.print(plan_table)

    if plan.target_commits == 0:
        console.print("\n[yellow]Nothing to simulate.[/yellow]")
        return

    # Execute the pipeline without applying changes or creating commits.
    executed = 0
    committed = 0
    with app_ctx.session() as session:
        from keeper.database import repo as dataset

        schedule_record = dataset.get_schedule(session, target_day.isoformat())
        if schedule_record is None:
            schedule_record = dataset.create_schedule(
                session,
                plan_date=target_day.isoformat(),
                mode=plan.mode.value,
                target_commits=plan.target_commits,
                run_time=str(manager.config.schedule.run_time),
                dry_run=True,
            )
            for slot in plan.slots:
                dataset.add_slot(
                    session,
                    schedule_id=schedule_record.id,
                    repo_id=None,
                    repo_path=slot.repository_path,
                    repo_name=slot.repository_name,
                    scheduled_at=slot.scheduled_at,
                )
        else:
            console.print(
                f"\n[yellow]A schedule already exists for {target_day}; "
                f"simulating its {len(dataset.list_slots(session, schedule_record.id))} slots.[/yellow]"
            )
        slot_records = dataset.list_slots(session, schedule_record.id)
    for record in slot_records:
        state = app_ctx.executor.execute(record.id, dry_run=True)
        executed += 1
        if state.value in ("succeeded", "committed"):
            committed += 1
    console.print(f"\n[dim]Simulated {executed} slots; {committed} would have produced commits.[/dim]")
    app_ctx.close()


# ---------------------------------------------------------------------------
# keeper config
# ---------------------------------------------------------------------------

@app.command()
def config(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    action: str = typer.Argument("show", help="show | edit | validate"),
) -> None:
    """Show, edit or validate the configuration."""
    manager = _manager(config)
    path = manager.config_path

    if action == "validate":
        from keeper.config.loader import validate_config

        warnings = validate_config(manager.config)
        console.print(f"[green]Configuration valid:[/green] {path}")
        for warning in warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")
        return

    if action == "edit":
        console.print(f"Opening {path} in your default editor...")
        import subprocess
        import sys as _sys

        try:
            if _sys.platform == "win32":
                subprocess.run(["notepad", str(path)], check=False)
            elif _sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError as exc:
            _fail(f"Could not open editor: {exc}")
        return

    # show
    console.print(Panel.fit(f"config file: {path}", title="config"))
    console.print(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# keeper stats
# ---------------------------------------------------------------------------

@app.command()
def stats(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
    days: int = typer.Option(30, "--days", help="Window in days"),
) -> None:
    """Print statistics."""
    from keeper.app import load_cli_app

    manager = _manager(config)
    app_ctx = load_cli_app(config)

    with app_ctx.session() as session:
        daily = app_ctx.stats.daily_series(session, days)
        weekly = app_ctx.stats.weekly_series(session, 12)
        repos = app_ctx.stats.repo_activity(session, days)
        ai = app_ctx.stats.ai_success_rate(session, days)
        failures = app_ctx.stats.failure_rate(session, days)

    console.print(Panel.fit(
        f"[bold]Last {days} days[/bold]\n"
        f"commits: {sum(d['commits'] for d in daily)}\n"
        f"AI success rate: {ai['rate']}% ({ai['successful']}/{ai['calls']} calls)\n"
        f"failure rate: {failures['rate']}%",
        title="stats",
    ))

    if any(d["commits"] for d in daily):
        spark = _sparkline([d["commits"] for d in daily])
        console.print(f"daily sparkline: [cyan]{spark}[/cyan]")
    if repos:
        repo_table = Table(title="repository activity")
        repo_table.add_column("Repository")
        repo_table.add_column("Commits", justify="right")
        for row in repos:
            repo_table.add_row(Path(row["repo"]).name, str(row["commits"]))
        console.print(repo_table)
    app_ctx.close()


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    max_value = max(values) or 1
    blocks = "▁▂▃▄▅▆▇█"
    return "".join(blocks[min(7, int(v / max_value * 8))] for v in values)


# ---------------------------------------------------------------------------
# keeper api
# ---------------------------------------------------------------------------

@app.command()
def api(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
) -> None:
    """Start the local HTTP API (blocking)."""
    from keeper.api.server import api_serve_blocking
    from keeper.app import load_cli_app

    manager = _manager(config)
    app_ctx = load_cli_app(config)
    console.print(
        f"[green]API listening on[/green] http://{manager.config.api.host}:{manager.config.api.port}"
    )
    try:
        api_serve_blocking(app_ctx, manager.config.api.host, manager.config.api.port)
    except KeyboardInterrupt:
        pass
    finally:
        app_ctx.close()


# ---------------------------------------------------------------------------
# keeper gui
# ---------------------------------------------------------------------------

@app.command()
def gui(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Custom config file path"),
) -> None:
    """Launch the Textual desktop dashboard."""
    from keeper.gui.app import run_gui

    run_gui(config)


def main() -> None:
    app()


if __name__ == "__main__":
    main()