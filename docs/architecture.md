# Architecture

contributor is a layered Python application: **CLI / GUI / API** front ends talk
to a **composition root** that wires clean-architecture services around a
**SQLite state database** and a **git engine**. Everything is explicitly
dependency-injected; there are no globals and no hidden state.

```
┌───────────────┬───────────────┬──────────────┐
│ CLI (typer)   │ GUI (textual) │ HTTP API     │   front ends
└───────┬───────┴───────┬───────┴──────┬───────┘
        └───────────────┼──────────────┘
                        ▼
              keeper.app.build_application      composition root
                        ▼
 ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐
 │ Planner │ │ Scheduler│ │Execution  │ │ Orchestrator │   services
 └────┬────┘ └────┬─────┘ └─────┬─────┘ └──────┬───────┘
      ▼           │             ▼              │
 ┌─────────┐      │   ┌─────────────────┐      │
 │  AI     │◄─────┴───┤ MutationEngine  │◄─────┘
 │ factory │          │  + safeguards   │
 └─────────┘          └───────┬─────────┘
                              ▼
 ┌─────────┐ ┌─────────┐ ┌────────┐ ┌──────────────┐
 │ GitEngine│ │ Safety  │ │ State  │ │ Notifications│
 │  + health│ │ checks  │ │ tracker│ │             │
 └────┬────┘ └────┬────┘ └───┬────┘ └──────────────┘
      └───────────┼──────────┼──────────────────────┘
                  ▼          ▼
           SQLite (WAL)   git repos on disk
```

## Modules

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Core | `keeper/core` | shared types (`TaskState`, `PlanMode`, `ProviderName`), domain exceptions, event bus |
| Config | `keeper/config` | strict Pydantic models, YAML loader, `ConfigManager` with watchdog hot-reload |
| Persistence | `keeper/database` | SQLAlchemy ORM, engine (WAL, busy timeout, schema versioning), data-access functions |
| Git | `keeper/git` | `GitEngine` (status, commit, push, restore, crash-recovery lookup), `RepoHealthChecker` scoring |
| Analysis | `keeper/analysis` | local repository analysis (languages, tooling, cleanliness) feeding the AI prompt |
| AI | `keeper/ai` | provider abstraction + 6 providers, prompt builders, JSON plan parsing |
| Mutation | `keeper/mutation` | applies AI plans with safeguards (path safety, protected files, no-ops, deterministic rollback) |
| Safety | `keeper/safety` | formatter/linter/test gate with hard timeouts |
| Planner | `keeper/planner` | daily commit count distribution + natural timestamps + repo assignment with per-repo caps |
| Scheduler | `keeper/scheduler` | APScheduler wrapper: `daily_tick` at run time, `due_tick` every 5 minutes |
| State | `keeper/state` | task lifecycle + crash recovery (reset interrupted slots, detect landed commits) |
| Services | `keeper/services` | `CommitExecutionService` (one slot), `Orchestrator` (a day), `Doctor` (diagnostics) |
| Workers | `keeper/workers` | background daemon (spawn, stop file, pid) |
| Stats | `keeper/stats` | daily/weekly/monthly aggregates from the audit tables |
| Notifications | `keeper/notifications` | console/desktop notifications, persisted in DB |
| API | `keeper/api` | local HTTP server (read-only status/statistics endpoints) |
| GUI | `keeper/gui` | Textual dashboard with 9 pages |

## Execution pipeline (one slot)

```
require_clean (never overwrite user work)
  -> analyze repository
  -> provider call (retry x3, audited in DB)
  -> parse + validate the AI plan (strict JSON schema + path safety)
  -> apply changes (safeguards, full-file writes, rollback metadata)
  -> safety gate: formatter -> linter -> tests (timeout-bounded)
  -> commit (conventional message, staged via `git add -A`)
  -> record commit + push (best effort)
  -> notify
```

Every step is audited (`failures`, `prompts`, `commits`, `log_records`), so the
GUI, CLI and API can all reconstruct what happened.

## Scheduling and crash recovery

- The planner persists a `schedule` row per day plus `slot` rows per commit.
- `daily_tick` fires at `schedule.run_time` and `due_tick` every 5 minutes picks
  up any planned-but-due slot (covers laptop-awake restarts and clock changes).
- On daemon startup `StateTracker.recover_interrupted` resets `RUNNING` slots;
  slots whose commit may have landed are checked via `git log --grep` and marked
  `committed` — they are never re-committed.
- Attempts are bounded (`MAX_ATTEMPTS = 5`, resume window 12 h).

## Safety guarantees

- Repos are only touched when the working tree is clean and no merge/rebase is
  in progress.
- The AI plan is schema-validated; absolute paths, `.git/`, lock files, secrets
  and vendored dirs are rejected before touching disk.
- Whitespace-only / no-op edits are rejected; every applied change is
  revertible (full previous content, not diffs).
- The safety gate (formatter/linter/tests) runs with hard timeouts and rolls
  the working tree back on failure.

## Concurrency and idempotency

The daemon, GUI and CLI share the SQLite database (WAL mode + 30 s busy
timeout). The orchestrator uses a non-blocking lock so two ticks can never
execute the same slot twice; slot states (`planned -> running -> terminal`)
make execution idempotent even across process restarts.

## Configuration

The full configuration schema is documented in `examples/config.example.yaml`.
Configuration files are watched (`watchdog`); a hot reload rebuilds the
provider factory, safety engine, executor and orchestrator in place.
