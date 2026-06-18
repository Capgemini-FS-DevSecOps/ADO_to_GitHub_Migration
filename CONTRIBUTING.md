# Contributing

## Development setup

```bash
git clone <this-repo>
cd ADO_to_GitHub_Migration
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[api,postgres,dev]"
```

**UI:**

```bash
cd apps/migration-ui && npm install && npm run dev
```

**Full stack:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build`

See [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project structure

| Path | Role |
|------|------|
| `ado2gh/cli/` | Click CLI commands (`ado2gh/cli/main.py` entry) |
| `ado2gh/core/` | Migration engine, config, discovery, rollback |
| `ado2gh/api/` | Accelerator SDK, pipeline runner, settings, auth |
| `ado2gh/agents/` | LLM provider, session orchestrator, skills |
| `ado2gh/state/` | SQLite / Postgres / DynamoDB state stores |
| `services/accelerator_api/` | FastAPI service for the UI |
| `services/agent/` | PEV agent + MCP |
| `apps/migration-ui/` | Next.js console |

Conventions:

- Dataclasses and enums in `ado2gh/models.py`
- Logging via `ado2gh/logging_config.py`
- State access through `create_state_db()` — not raw SQLite paths in new code
- API clients in `ado2gh/clients/` with retry and rate-limit handling

## Adding a CLI command

Add a Click command under `ado2gh/cli/` (e.g. `misc.py`, `phase.py`) and register it in `ado2gh/cli/main.py`. Use lazy imports inside the handler:

```python
@cli.command()
@click.option("--config", "-c", required=True)
def my_command(config):
    from ado2gh.core.config_loader import ConfigLoader
    ...
```

## Adding an ADO task mapping

Edit `ado2gh/pipelines/transformer.py` — `ADO_TASK_MAP`:

```python
"YourTask@1": "owner/action@vN",
```

## Adding a migration scope

1. Add to `MigrationScope` in `ado2gh/models.py`
2. Implement `_migrate_<scope>` in `ado2gh/core/migration_engine.py`
3. Register in `scope_handlers` inside `migrate_repo()`

## Tests

```bash
pytest tests/
```

Coverage gates apply to selected `ado2gh.api.*` and `ado2gh.auth.*` modules (see `pyproject.toml`). Use `pytest --no-cov` for a quick local run without the coverage threshold.

## Scripts (`scripts/`)

| Script | Purpose |
|--------|---------|
| `discover.sh` | ADO org scan → discovery output |
| `migrate.sh` | Minimal phased migration from repo map |
| `migrate-full.sh` | Full workflow including pipeline inventory |
| `run-local-agent.ps1` / `.sh` | Lightweight accelerator + agent for IDE dev |
| `run-local.ps1` | Full Docker stack (deprecated for agent-only; see `run-local-agent`) |
| `run-ui.ps1` / `stop-ui.ps1` | Start/stop Next.js UI only |
| `auth-smoke.ps1` | Auth bootstrap smoke checks |
| `ide-check.ps1` | Agent health, MCP tools, dry-run session smoke |

Ad-hoc E2E harnesses belong in `tests/`, not `scripts/`.

## Code style

- Minimal scope — avoid drive-by refactors
- Rich for CLI output; structured logging elsewhere
- No secrets in code or commits — use `.env` (gitignored)
