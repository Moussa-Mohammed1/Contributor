# Development

## Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate       # Linux / macOS
python -m pip install -e ".[dev]"
```

## Running the tests

```powershell
python -m pytest -q          # full suite
python -m pytest -x          # stop at the first failure
python -m pytest tests/unit  # unit tests only
```

The suite is hermetic:

- every test uses `tmp_path` — nothing touches your home directory or real repos,
- integration tests build real sandbox git repositories (`tests/helpers/sandbox.py`)
  and run the full pipeline (plan -> mutate -> safety -> commit) against a
  deterministic mock AI provider (`tests/helpers/mock_provider.py`) registered
  under the `ollama` provider slot by `tests/conftest.py`,
- no test performs network I/O.

Test layout:

```
tests/
├── conftest.py            shared fixtures (config, database, app composition root)
├── helpers/
│   ├── sandbox.py         real git repo builder
│   └── mock_provider.py   deterministic AI provider
├── unit/                  planner, distributions, messages, safeguards, config,
│                          prompts, retry, AI parsing, provider factory, analyzer
├── database/              engine + data access
├── git_sandbox/           GitEngine + health scoring
├── scheduler/             APScheduler wiring
└── integration/           end-to-end pipeline + crash recovery
```

## Manual end-to-end check (no AI needed)

```powershell
keeper init --start 2026-08-01 --end 2026-08-31 --repo C:\some\git\repo
keeper doctor
keeper dry-run            # full pipeline simulation, zero side effects
keeper start && keeper status && keeper stop
```

For a real commit test without a cloud key, add an Ollama/LM Studio `base_url`
to `config.yaml` (any local server works).

## Style and tooling

- Python 3.13+, `from __future__ import annotations`, type hints everywhere.
- Ruff with `E/F/I/UP/B` rules; line length 100.
- No placeholder code: every function is implemented and tested.

## Building a standalone executable (Windows)

```powershell
scripts\build.ps1
# produces dist\keeper.exe and dist\keeper-gui.exe
```

## Project layout

```
keeper/
├── core/        types, exceptions, event bus
├── config/      pydantic models, loader, hot-reload manager
├── database/    ORM, engine, data access
├── git/         GitEngine, health checker
├── analysis/    repository analyzer
├── ai/          provider abstraction + 6 providers, prompts, parsing
├── mutation/    apply + safeguards + rollback
├── safety/      formatter/linter/test gate
├── planner/     distributions + DayPlanPlanner
├── scheduler/   APScheduler wrapper
├── state/       tracker + crash recovery
├── services/    executor, orchestrator, doctor
├── workers/     daemon + runner
├── stats/       statistics service
├── notifications/
├── api/         HTTP server
├── gui/         Textual app + pages
├── cli.py       typer entry point
└── app.py       composition root
```
