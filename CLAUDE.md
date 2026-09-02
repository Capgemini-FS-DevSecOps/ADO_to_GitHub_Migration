# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Enterprise ADO → GitHub migration platform: Python CLI, FastAPI accelerator, Next.js console, and PEV agent. Risk-based phasing, real git mirroring (+ GEI), pipeline transformation, commit-level validation, profile-based UI workflows.

**Full architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Running

### CLI

```bash
pip install -e ".[api,agent,dev]"   # drop `agent` if you never run the PEV agent
ado2gh <command> [options]
python -m ado2gh <command> [options]
```

### Docker

```bash
docker compose up --build                      # Accelerator :8080 + Agent :8090
docker compose --profile default up --build    # + UI :3000, Redis, worker
```

Local setup: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)

## Environment Variables

```bash
ADO_PAT=<ado-personal-access-token>
ADO_ORG_URL=https://dev.azure.com/YOUR_ORG
GH_TOKEN=<github-token>              # Single token mode

# Multi-token load balancing (recommended at scale)
GH_TOKEN_1=<token1>
GH_TOKEN_2=<token2>

# GitHub App auth (optional, for orgs that mandate it)
GH_APP_ID=<app-id>
GH_APP_INSTALLATION_ID=<install-id>
GH_APP_PRIVATE_KEY_PATH=<path-to-pem>

# Storage + runtime
ADO2GH_STORAGE_BACKEND=sqlite        # or postgres (docker-compose.prod.yml)
ADO2GH_SQLITE_PATH=./migration_state.db
ADO2GH_DATA_DIR=./data
ADO2GH_LIGHTWEIGHT_MODE=true
ADO2GH_AUTH_ENABLED=false
ADO2GH_LOCAL_PROFILE=lightweight
ACCELERATOR_URL=http://localhost:8080

# Agent LLM backend — `stub` runs fully offline
LLM_PROVIDER=stub
ADO2GH_LLM_BACKEND=stub
```

Cloud LLM providers are registered in `ado2gh/api/llm/llm_provider_registry.py` and can
also be configured at runtime via **Settings → LLM models**; `.env.example` carries a
worked example per provider (openai, anthropic, github_copilot, openrouter, azure_openai,
bedrock, vertex, foundry, google_gemini, ollama).

## Dependencies

```bash
pip install -r requirements.txt       # click, requests, pyyaml, rich, urllib3
pip install cryptography PyJWT        # Only if using GitHub App auth
```

Also requires `git` on PATH. For GEI migration strategy: `gh` CLI with `gh-gei` extension.

## Package Structure

```
ado2gh/
├── cli/                   # Click commands (main.py entry)
├── api/                   # Accelerator SDK, pipeline runner, settings, audit history routes, llm/
├── agents/                # migration_agent/ (LangGraph PEV) + metrics
├── auth/                  # Platform users, sessions, RBAC
├── assignments/           # Audit event writer (redaction, audit log)
├── models.py
├── clients/               # ADO + GitHub + TokenManager
├── state/                 # SQLite, Postgres (factory); DynamoDB job store
├── pipelines/             # Extract, transform, inventory
├── phase/                 # Risk, waves, gates, batch executor
├── core/                  # Migration engine, discovery, rollback, cleanup
└── reporting/             # Validator, readiness, manifests

services/
├── accelerator_api/       # FastAPI for UI (:8080)
└── agent/                 # PEV agent (:8090)

apps/migration-ui/         # Next.js console (:3000)
```

## CLI Commands

```
ado2gh
├── discover              Scan ADO org, generate wave config
├── plan                  Preview migration plan
├── run --wave N          Execute wave(s)
├── status                Migration status
├── report --format html|json|csv
├── validate              Commit-SHA-level source vs target verification
├── rollback --wave N [--scopes branch_policies,pipelines]
├── export-failed         Failed repos → text file for retries
├── token-status          GitHub token rate limits
├── pipeline-readiness    Auto/assisted/manual assessment + effort estimate
├── service-connections   ADO service connections → GitHub secrets manifest
├── ado-cleanup           Disable ADO pipelines, add redirect, archive repos
├── push-workflows        Push locally generated workflow YAML to GitHub
├── pipelines/
│   ├── inventory         Scan ADO pipelines into StateDB
│   ├── status            Pipeline migration status
│   └── retry-failed      Re-attempt failed pipelines
└── phase/
    ├── assign            Risk-score + auto-assign to phases
    ├── plan              Phase breakdown with gates
    ├── run --phase poc   Execute with batching + gate enforcement
    ├── gate-check        Validate thresholds (--override --reason)
    └── dashboard         Live progress dashboard
```

## Migration Strategies

**`migration_strategy: gei`** (default) — Uses `gh ado2gh migrate-repo` (GitHub Enterprise Importer for Azure DevOps). Handles PRs and branch policies natively. Requires `gh` CLI with `gh-ado2gh` extension (`gh extension install github/gh-ado2gh`).

**`migration_strategy: mirror`** — `git clone --mirror` + `git push --mirror`. Handles all branches, tags, LFS objects. Requires `git` on PATH.

## ADO-Specific Design Decisions

- **Git migration actually executes**: `_migrate_git` runs `git clone --mirror && git push --mirror` via subprocess, including LFS push. Not just metadata recording.
- **Post-migration validation is content-level**: Compares HEAD commit SHA between ADO and GitHub (not just branch counts). Proves code actually transferred.
- **Service connections can't be migrated**: Only names are readable via API. The `service-connections` command generates a manifest with GitHub secrets names + OIDC setup instructions for ops teams.
- **Pipeline readiness is assessed before migration**: `pipeline-readiness` classifies each pipeline as auto/assisted/manual with estimated hours — lets teams plan before committing.
- **ADO cleanup is a separate post-migration step**: `ado-cleanup` disables pipelines, pushes a MIGRATION_NOTICE.md redirect, and optionally archives the ADO repo.
- **Rollback is scope-targeted**: `rollback --scopes branch_policies,pipelines` only undoes those scopes without deleting the repo.

## Execution Workflow

```
0. bootstrap admin → profile onboarding (`/onboarding/profile`) → operator Create account on login (optional)
1. discover           → discovered_repos.yaml
2. pipelines inventory → populate StateDB pipeline_inventory
3. pipeline-readiness  → assess conversion effort
4. service-connections → generate ops manifest
5. phase assign        → migration_phase.yaml (risk-scored)
6. phase run --phase poc [--dry-run]
7. validate            → commit SHA verification
8. phase gate-check --phase poc
9. phase run --phase pilot → wave1 → wave2 → wave3
10. ado-cleanup        → disable pipelines, add redirect, archive
```

## State Persistence

`create_state_db()` via `ado2gh/state/factory.py`. Backends: **SQLite** (local), **PostgreSQL** (prod compose). Set `ADO2GH_STORAGE_BACKEND`. DynamoDB is job-store only (`state/job_store.py`), not a state-DB backend.

Core tables: `migrations`, `wave_runs`, `pipeline_inventory`, `pipeline_migrations`, `repo_risk_scores`, `phase_gates`, `batch_checkpoints`

## Agent PEV (LangGraph)

Four-agent LangGraph graph (Orchestrator → Planner → Executor → Validator) with a continuous PEV loop, LangChain provider-agnostic LLMs, SSE streaming of agent thinking, session checkpointing, and batch migration support. See `specs/012-langgraph-agent-refactor/`.

**Architecture (`ado2gh/agents/migration_agent/`):**
- `agent.py` — top-level entry; wires graph, runtime, and session layers
- `graph/` — LangGraph builder, `AgentState` schema, conditional edge routing
- `nodes/` — role nodes: `orchestrator.py`, `planner.py`, `executor/` (node, plan, scope, pipeline), `validator.py`, `finalize.py`, plus `intent`, `streaming`, `messaging`, `read_tools`, and the `planner_research` / `planner_plan_builders` / `validator_investigation` helpers
- `runtime/` — LLM bridge (LangChain), orchestrator runtime, context window management, tracing, dependency injection
- `session/` — session lifecycle, state machine, persistent store (plans, messages, rollback records, checkpoints)
- `hitl/` — human-in-the-loop: intake, dynamic forms, blockers, operator input, interrupt node
- `tools/` — LangChain tools per role (orchestrator, planner, executor, validator, shared)
- `guardrails.py` — tool-call interception: plan authorization, deletion confirmation, ADO read-only enforcement
- `policies.py` — scope guardrails, live execution policy, session access control
- `prompts/` — per-role system prompts (markdown)
- `ado2gh/agents/metrics.py` — Prometheus-compatible metrics collector

**Resource types supported:** repos, pipelines→workflows, Bicep→Actions, secrets, service connections, Boards→Issues, Test Plans, Artifacts→Packages, Wiki

**Key behaviors:**
- Max 20 total iterations, 3 PEV retries per cycle
- Dry-run is default; live execution requires explicit user confirmation (CA-001)
- Secret values masked in all messages, logs, and audit records (CA-003)
- Destructive operations highlighted in plan summary with individual confirmation (CA-002)
- Session state persists across server restarts; resume from last persisted state
- Batch migration queue for 50+ repos with sequential processing and per-repo validation
- Inter-agent communication via structured JSON messages (instruction, clarification_request, feedback, result)

**Endpoints (`services/agent`, :8090)** — routes live in `routes/session_routes.py` and `routes/run_routes.py`:

```
GET    /health · /metrics · /v1/llm/status · /v1/agent/models
POST   /v1/sessions                      GET /v1/sessions
GET    /v1/sessions/{id}                 DELETE /v1/sessions/{id}
POST   /v1/sessions/{id}/message · message-stream · plan · run-pev
POST   /v1/sessions/{id}/approve · remediate · provision · cancel
POST   /v1/sessions/{id}/request-live · confirm-live
PATCH  /v1/sessions/{id}/execution-mode
GET    /v1/sessions/{id}/plan-summary
POST   /v1/sessions/{id}/form-submit · form-submit-stream · form-cancel
POST   /v1/internal/sessions/{id}/resume-live · deny-live
GET    /v1/history/sessions · /v1/history/event-types · /v1/history/sessions/export
```

UI chat in `apps/migration-ui` — no manual plan/execute buttons.

## Key Patterns

- `TokenManager` rotates tokens per API call; updates rate limits from response headers
- Interrupted runs auto-resume from last SQLite checkpoint
- Gate checks enforce success thresholds; `--override --reason` for operator escalation
- All parallelism via `ThreadPoolExecutor` with separate repo-level and pipeline-level knobs
- Failed repos auto-exported to `failed_repos_{phase}.txt` after each phase run

## Testing

```bash
pytest                    # full suite, ~970 tests in ~105s
pytest tests/unit         # or agent/ integration/ contract/ feature/ eval/ + domain dirs
```

`addopts` is deliberately empty in `pyproject.toml` so bare `pytest` works without
`pytest-cov` installed. Coverage lives only in CI: `pytest --cov=ado2gh --cov-fail-under=85`,
alongside `ruff check ado2gh/ services/` and `mypy`. None of `pytest-cov`, `ruff`, `vulture`
are preinstalled in the local venv.

- If pytest appears to hang *after* the last test, suspect a leaked non-daemon aiosqlite
  checkpointer thread (`graph/builder.py` `_close_checkpointer` + session-scoped conftest
  teardown). Diagnose with `py-spy dump`.
- `tests/unit/test_no_orphaned_modules.py` guards against orphan modules — update its
  allowlist when adding or deleting dynamically imported modules.

## Repo Conventions

- `specs/` (spec-kit) is the source of truth for behaviour; `012-langgraph-agent-refactor`
  is the active spec, `011-agent-pev-rebuild` and `010-enterprise-audit-simplification`
  the most recent completed ones.
- `docs/STRUCTURAL_CHANGELOG.md` is the append-only ledger for file moves and deletions —
  record structural changes there.
