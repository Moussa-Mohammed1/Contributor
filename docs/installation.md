# Installation

contributor requires **Python 3.13+** (3.14 supported), **git 2.40+** on PATH and a working AI provider (cloud API key or a local server such as Ollama / LM Studio).

## 1. Install from source

```powershell
git clone <your-repo-url> contributor
cd contributor
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate       # Linux / macOS
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

The `keeper` console script is now on PATH. Verify:

```powershell
keeper --help
python -m keeper doctor --json   # optional structural report
```

## 2. Configure

```powershell
keeper init --start 2026-08-01 --end 2026-08-31 --repo C:\path\to\your\repo
```

This creates `config.yaml` in the current directory (or `~/.contributor/config.yaml`), creates the data directory and initializes the SQLite database. Edit the file (`keeper config edit`) to add more repositories, choose a provider and tune the schedule. See `examples/config.example.yaml` for the full annotated schema.

### AI providers

| Provider  | Config key | Requirement |
|-----------|------------|-------------|
| Ollama    | `ai.providers.ollama` | `base_url` of a running server |
| LM Studio | `ai.providers.lmstudio` | `base_url` of a running server |
| OpenAI    | `ai.providers.openai` | `api_key` |
| Gemini    | `ai.providers.gemini` | `api_key` |
| Claude    | `ai.providers.claude` | `api_key` |
| OpenRouter| `ai.providers.openrouter` | `api_key` |

With `provider: auto` (default) contributor picks the first configured provider, local servers first.

## 3. Verify and start

```powershell
keeper doctor          # environment, config, repos, database, providers
keeper start           # launches the background daemon
keeper status          # confirm the daemon is running
keeper dry-run         # simulate today's plan with zero side effects
keeper gui             # optional interactive dashboard
```

## 4. Optional extras

```powershell
python -m pip install -e ".[notify]"   # desktop notifications (Windows)
python -m pip install -e ".[gui]"      # Textual GUI dependencies
```

## Troubleshooting

- `No configuration file found` — run `keeper init` or pass `--config <path>`.
- `No AI provider is configured` — set an API key or an Ollama/LM Studio `base_url`, then `keeper doctor`.
- Daemon stuck — `keeper stop`, then `keeper start`; crash recovery resets interrupted slots automatically.
