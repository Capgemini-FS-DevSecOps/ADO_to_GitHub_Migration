# Implementation Plan: Enterprise Audit & Framework Simplification

**Branch**: `010-enterprise-audit-simplification` | **Date**: 2026-06-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-enterprise-audit-simplification/spec.md`

## Summary

Agent-driven one-time audit and simplification of the ADO2GitHub migration repository. The agent will: (1) audit and remove dead code, redundant files, and stray artifacts; (2) consolidate the state persistence layer from 3 backends to 2 (remove DynamoDB) with a shared base class; (3) decompose 7 monolithic files exceeding 800 lines into focused modules; (4) consolidate 4 Docker Compose files into 2 with profile support; (5) archive implemented specs and create a spec lifecycle index; (6) flatten the module structure by eliminating single-file directories, wrapper files, and scattered LLM/credential modules; (7) consolidate UI pages per spec 008; (8) reduce scripts to a minimal `scripts/dev/`; (9) add ruff, mypy, pre-commit, and CI coverage enforcement for enterprise readiness. All changes are tracked in `docs/STRUCTURAL_CHANGELOG.md` and tests are updated incrementally per commit.

## Technical Context

**Language/Version**: Python 3.9+ (see `pyproject.toml`)

**Primary Dependencies**: Click, Rich (CLI); FastAPI, Uvicorn (API); Next.js 14 (UI); Redis (job queue); pytest, pytest-cov (testing); ruff (linting — to be added); mypy (type-checking — to be added)

**Storage**: SQLite (local/default), PostgreSQL (prod) — DynamoDB to be removed

**Testing**: pytest with pytest-cov; coverage gate at 85% on `ado2gh` package (constitution mandate)

**Target Platform**: Docker Compose (local + prod), Kubernetes (enterprise deploy)

**Project Type**: CLI + web-service (FastAPI backend + Next.js frontend + agent service)

**Performance Goals**: N/A — this is a structural simplification feature, not a runtime performance feature

**Constraints**: Zero test regressions; public API surface preserved via re-exports; all structural changes documented in `docs/STRUCTURAL_CHANGELOG.md`; no secrets exposed during audit

**Scale/Scope**: ~120 Python files in `ado2gh/`, ~100 test files, ~22 UI components, 14 UI page directories, 12 scripts, 9 specs, 4 Docker Compose files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Plan describes readable structure; no unjustified complexity |
| II. Documentation | New modules/functions will include purpose/inputs/outputs docstrings |
| III. Deprecation | No silent legacy paths; deprecations marked or removed |
| IV. Architecture & Naming | Folder/module names match domain (migration, phase, pipeline, validate) |
| V. Enterprise Safeguards | Dry-run/HITL/audit/secrets handling addressed for destructive scope |
| VI. Testing (85%+) | Test strategy defined; coverage gate will not regress below 85% on `ado2gh` |

**Result**: [x] PASS — all gates satisfied

## Project Structure

### Documentation (this feature)

```text
specs/010-enterprise-audit-simplification/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── structural-changelog-contract.md
└── tasks.md             # Phase 2 output (created by /speckit-tasks)
```

### Source Code (repository root — target state after simplification)

```text
ado2gh/
├── agents/
│   ├── orchestration/        # Decomposed from session_orchestrator.py
│   │   ├── loop.py           # Orchestration loop
│   │   ├── prompts.py        # LLM prompt management
│   │   ├── session_state.py  # Session state management
│   │   ├── tool_routing.py   # Tool routing
│   │   └── pev_coordination.py # PEV coordination
│   ├── local/
│   ├── skills/
│   ├── executor.py
│   ├── failure_analysis.py
│   ├── live_execution_policy.py
│   ├── llm_provider.py
│   ├── pev_coordinator.py
│   ├── planner.py
│   ├── session_access.py
│   └── validator.py
├── api/
│   ├── llm/                  # Consolidated LLM modules
│   ├── credentials/          # Consolidated credential modules
│   ├── pipeline/             # Decomposed from pipeline_runner.py
│   │   ├── steps.py          # Step definitions
│   │   ├── executors.py      # Step executors
│   │   ├── run_persistence.py # Run persistence
│   │   └── orchestration.py  # Pipeline orchestration
│   ├── settings/             # Decomposed from settings_store.py
│   │   ├── llm_settings.py
│   │   ├── connectivity.py
│   │   ├── cloud_credentials.py
│   │   └── profile_governance.py
│   └── [existing modules]
├── assignments/
├── auth/
├── cli/                     # Direct entry point (cli.py wrapper removed)
├── clients/
├── core/
│   ├── concurrency.py       # Merged from infra/
│   ├── sessions.py          # Merged from infra/
│   ├── queue/               # Merged from infra/
│   ├── orchestration/
│   ├── scopes/
│   ├── ado_cleanup.py
│   ├── config_loader.py
│   ├── discovery.py
│   ├── gei_runtime.py
│   ├── migration_engine.py
│   ├── rollback.py
│   └── wave_runner.py
├── phase/
├── pipelines/
│   └── push_workflows.py    # Merged from tools/
├── reporting/
├── state/
│   ├── base.py              # Shared base class (new)
│   ├── sqlite_db.py         # Renamed from db.py
│   ├── postgres_db.py
│   ├── audit_query.py
│   └── factory.py
└── [root modules]

services/
├── accelerator_api/
│   ├── main.py              # App init + middleware only
│   ├── routes/              # Decomposed route handlers
│   └── Dockerfile
├── agent/
│   ├── main.py              # App init + middleware + session store only
│   ├── routes/              # Decomposed route handlers
│   ├── mcp_server.py
│   └── Dockerfile

apps/migration-ui/src/
├── app/
│   ├── agent/               # Agent tab
│   ├── dashboard/           # Merged monitor + runs + history
│   ├── discovery/           # Merged readiness + assignments
│   ├── migrate/             # Merged workflows + validation
│   ├── settings/            # Settings tab
│   ├── login/
│   └── onboarding/
├── components/
└── lib/

scripts/
└── dev/                    # Minimal local-dev helpers (2-3 files)

docs/
├── STRUCTURAL_CHANGELOG.md  # New — tracks all structural changes
└── [existing docs]

docker-compose.yml           # Base with profiles (bare = lightweight, --profile default = full stack)
docker-compose.prod.yml      # Prod overrides (PostgreSQL)
```

**Structure Decision**: The existing multi-package structure (`ado2gh/` SDK + `services/` API + `apps/` UI) is preserved. The simplification focuses on: (1) decomposing monolithic files into focused submodules within their existing packages; (2) merging `infra/` into `core/`; (3) consolidating scattered LLM and credential modules into subpackages; (4) removing `tools/`, `cli.py` wrapper, and DynamoDB backend; (5) reducing UI pages and scripts. No new top-level directories are created.

## Complexity Tracking

> No constitution violations — all gates pass. The feature itself *reduces* complexity.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
