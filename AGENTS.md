# Agent context

<!-- SPECKIT START -->
For feature planning artifacts, see `specs/` and the current plan at `specs/012-langgraph-agent-refactor/plan.md`.
<!-- SPECKIT END -->

## Repository summary

**ADO2GitHub Migration Accelerator** — enterprise ADO → GitHub migrations with CLI, web console, and PEV agent.

## Architecture (read first)

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — full system design
- **[CLAUDE.md](CLAUDE.md)** — package layout, CLI, env vars

## Stack

| Layer | Technology |
|-------|------------|
| CLI / engine | Python 3.11+, Click, Rich |
| API | FastAPI (`services/accelerator_api`, `services/agent`) |
| UI | Next.js 14 (`apps/migration-ui`) |
| State | SQLite / PostgreSQL (`ADO2GH_STORAGE_BACKEND`); DynamoDB job store only |
| Agent | LangGraph PEV graph (`ado2gh/agents/migration_agent/`) |

## Common commands

```bash
pip install -e ".[api,agent,dev]"   # agent extra = LangGraph/LangChain
ado2gh discover --config migration.yaml
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
docker compose up --build          # local SQLite stack
pytest tests/
```

Local dev guide: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)

## Agent PEV guardrails

Migration agent work must use orchestrator tools (`fetch_profile_discovery` → `build_migration_plan` → `run_migration_pev`). Do not invent repo lists from conversation alone. Failed migrations may retry up to 3 times when the validator recommends it. Plans include per-repo work items with ready/blocked status. See `ado2gh/agents/migration_agent/guardrails.py` and `nodes/orchestrator_tools.py`.

## Agent PEV architecture (spec 012, LangGraph)

Four-agent LangGraph graph (Orchestrator → Planner → Executor → Validator) with a continuous PEV loop, session checkpointing, SSE streaming, and batch migration support.

**Key modules (`ado2gh/agents/migration_agent/`):**
- `graph/` — LangGraph builder, `AgentState`, conditional edge routing (PEV loop lives in edges/nodes — no separate coordinator class)
- `nodes/` — role nodes: `orchestrator.py`, `planner.py`, `executor/`, `validator.py`
- `runtime/` — LangChain LLM bridge, context window management, tracing
- `session/` — lifecycle, state machine, persistent store (survives restarts)
- `hitl/` — intake, dynamic forms, blockers, operator input, interrupt node
- `guardrails.py` — tool-call interception: plan authorization, deletion confirmation, ADO read-only enforcement

**Resource types:** repos, pipelines→workflows, Bicep→Actions, secrets, service connections, Boards→Issues, Test Plans, Artifacts→Packages, Wiki

**Limits:** Max 20 total iterations, 3 PEV retries per cycle. Dry-run is default; live requires explicit confirmation.

## Specs

| Spec | Topic |
|------|-------|
| `specs/001-*` | Agentic platform, assignments |
| `specs/003-*` | Local agent IDE / MCP |
| `specs/004-*` | Agent PEV RBAC |
| `specs/006-*` | LLM model catalog |
| `specs/007-*` | Cloud LLM credentials |
| `specs/008-*` | Migration UI refactor |
| `specs/009-*` | Pipeline step decoupling & dependency resolution |
| `specs/010-*` | Enterprise audit & simplification |
| `specs/011-*` | Agent PEV architecture rebuild |
| `specs/012-*` | LangGraph agent refactor (current) |

Archived (implemented): `specs/archive/002-*` login bootstrap, `specs/archive/005-*` profile onboarding.

## Pipeline Steps (Accelerator)

The migration pipeline consists of the following steps (defined in `ado2gh/api/pipeline_models.py`):

| Step ID | Description | Prerequisites |
|---------|-------------|---------------|
| `connect` | Connect to ADO and GitHub APIs | None |
| `discover` | Discover repos and pipelines | `connect` |
| `inventory` | Inventory pipelines and dependencies | `discover` |
| `readiness` | Assess pipeline readiness for migration | `inventory` |
| `assign` | Assign repos to migration waves | `readiness` |
| `analyze_deps` | Analyze dependencies (service connections, variable groups, environments) | `inventory` |
| `migrate_repos` | Migrate repository contents (git mirror/GEI) | `analyze_deps` |
| `convert_pipelines` | Convert ADO pipelines to GitHub Actions workflows | `analyze_deps` |
| `convert_metadata` | Convert branch policies, wiki, work items | `migrate_repos` |
| `migrate` | Execute full migration (orchestrates scopes) | `analyze_deps` |
| `validate` | Post-migration validation | `migrate_repos`, `convert_pipelines` |

**Note**: The `map_secrets` step has been removed and its functionality merged into `analyze_deps` (spec 009).
