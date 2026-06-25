# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Enterprise ADO → GitHub migration platform: Python CLI, FastAPI accelerator, Next.js console, and PEV agent. Risk-based phasing, real git mirroring (+ GEI), pipeline transformation, commit-level validation, profile-based UI workflows.

**Full architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Running

### CLI

```bash
pip install -e ".[api,dev]"
ado2gh <command> [options]
python -m ado2gh <command> [options]
```

### Docker (UI + API + agent)

```bash
docker compose up --build
# UI :3000 · Accelerator :8080 · Agent :8090
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
```

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
├── api/                   # Accelerator SDK, pipeline runner, settings, auth, agentic routes
├── agents/                # LLM provider, session orchestrator, skills
├── models.py
├── clients/               # ADO + GitHub + TokenManager
├── state/                 # SQLite, Postgres, DynamoDB (factory)
├── pipelines/             # Extract, transform, inventory
├── phase/                 # Risk, waves, gates, batch executor
├── core/                  # Migration engine, discovery, rollback, cleanup
└── reporting/             # Validator, readiness, manifests

services/
├── accelerator_api/       # FastAPI for UI (:8080)
└── agent/                 # PEV agent + MCP (:8090)

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
├── pipelines/
│   ├── inventory         Scan ADO pipelines into StateDB
│   ├── plan              Pipeline breakdown per wave
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

`create_state_db()` via `ado2gh/state/factory.py`. Backends: **SQLite** (local), **PostgreSQL** (prod compose), **DynamoDB** (serverless). Set `ADO2GH_STORAGE_BACKEND`.

Core tables: `migrations`, `wave_runs`, `pipeline_inventory`, `pipeline_migrations`, `repo_risk_scores`, `phase_gates`, `batch_checkpoints`

## Agent PEV

Continuous PEV loop: Orchestrator → Planner → Executor → Validator cycle with retry logic, session persistence, and batch migration support.

**Architecture:**
- `ado2gh/agents/session_orchestrator.py` — re-exports from `orchestration/` modules
- `ado2gh/agents/planner.py` — LLM-driven migration plan generation with dependency ordering
- `ado2gh/agents/executor.py` — deterministic execution with guardrail integration, hybrid API routing
- `ado2gh/agents/validator.py` — evidence-based validation with structured feedback to planner
- `ado2gh/agents/pev_cycle.py` — continuous loop orchestrator with inter-agent messaging, batch queue, repo locking
- `ado2gh/agents/session_store.py` — persistent session state (plans, messages, executor results, validation results)
- `ado2gh/agents/session_state_machine.py` — state transitions (idle→thinking→planning→executing→validating→completed|failed)
- `ado2gh/agents/rollback_tracker.py` — tracks GitHub resources created during session for rollback on cancellation
- `ado2gh/agents/repo_lock_store.py` — repo-level locks preventing concurrent migration
- `ado2gh/agents/metrics.py` — Prometheus-compatible metrics collector
- `ado2gh/agents/context_window.py` — context window management for LLM sessions
- `ado2gh/agents/resource_mapping.py` — ADO→GitHub resource type mapping
- `ado2gh/agents/local/tool_catalog.py` — tool registry with role-based access and guardrails

**Resource types supported:** repos, pipelines→workflows, Bicep→Actions, secrets, service connections, Boards→Issues, Test Plans, Artifacts→Packages, Wiki

**Key behaviors:**
- Max 20 total iterations, 3 PEV retries per cycle
- Dry-run is default; live execution requires explicit user confirmation (CA-001)
- Secret values masked in all messages, logs, and audit records (CA-003)
- Destructive operations highlighted in plan summary with individual confirmation (CA-002)
- Session state persists across server restarts; resume from last persisted state
- Batch migration queue for 50+ repos with sequential processing and per-repo validation
- Inter-agent communication via structured JSON messages (instruction, clarification_request, feedback, result)

**Endpoints:** `/health`, `/metrics`, `/v1/sessions/{id}/plan-summary`, `/v1/sessions/{id}/confirm-live`, `/v1/sessions/{id}/cancel`

UI chat in `apps/migration-ui` — no manual plan/execute buttons.

## Key Patterns

- `TokenManager` rotates tokens per API call; updates rate limits from response headers
- Interrupted runs auto-resume from last SQLite checkpoint
- Gate checks enforce success thresholds; `--override --reason` for operator escalation
- All parallelism via `ThreadPoolExecutor` with separate repo-level and pipeline-level knobs
- Failed repos auto-exported to `failed_repos_{phase}.txt` after each phase run
