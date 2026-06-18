# Agent context

<!-- SPECKIT START -->
For feature planning artifacts, see `specs/` and the current plan under `.specify/`.
<!-- SPECKIT END -->

## Repository summary

**ADO2GitHub Migration Accelerator** — enterprise ADO → GitHub migrations with CLI, web console, and PEV agent.

## Architecture (read first)

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — full system design
- **[CLAUDE.md](CLAUDE.md)** — package layout, CLI, env vars

## Stack

| Layer | Technology |
|-------|------------|
| CLI / engine | Python 3.9+, Click, Rich |
| API | FastAPI (`services/accelerator_api`, `services/agent`) |
| UI | Next.js 14 (`apps/migration-ui`) |
| State | SQLite / PostgreSQL / DynamoDB (`ADO2GH_STORAGE_BACKEND`) |
| Agent | Tool orchestrator + LLM provider (`ado2gh/agents/`) |

## Common commands

```bash
pip install -e ".[api,dev]"
ado2gh discover --config migration.yaml
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
pytest tests/
```

## Agent PEV guardrails

Migration agent work must use orchestrator tools (`fetch_profile_discovery` → `build_migration_plan` → `run_migration_pev`). Do not invent repo lists from conversation alone. All PEV phases are reviewed by the LLM (`ado2gh/agents/pev_coordinator.py`); failed migrations may retry up to 3 times when the validator recommends it. Plans include per-repo work items with ready/blocked status. See `ado2gh/agents/session_orchestrator.py`.

## Specs

| Spec | Topic |
|------|-------|
| `specs/001-*` | Agentic platform, assignments |
| `specs/002-*` | Login bootstrap |
| `specs/003-*` | Local agent IDE / MCP |
| `specs/004-*` | Agent PEV RBAC |
| `specs/005-*` | Profile onboarding |
| `specs/006-*` | LLM model catalog |
