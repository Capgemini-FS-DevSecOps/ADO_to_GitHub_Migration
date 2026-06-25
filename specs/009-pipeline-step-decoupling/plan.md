# Implementation Plan: Pipeline Step Decoupling & Dependency Resolution

**Branch**: `009-pipeline-step-decoupling` | **Date**: 2026-06-24 | **Spec**: `specs/009-pipeline-step-decoupling/spec.md`

**Input**: Feature specification from `/specs/009-pipeline-step-decoupling/spec.md`

## Summary

Refactor the migration pipeline so every step is a self-contained entity with clean data pass-through. Merge "Map secrets & service connections" into "Analyze Dependencies." Add feasibility analysis to Migrate Repository Contents. Execute conversion + `actionlint` validation + auto-commit in Convert Pipelines. Extend Validate to verify committed workflows. Implement operator resolution flow with OIDC auto-provisioning. Add per-repo locking and continue-on-error batch handling. Update both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS`.

## Technical Context

**Language/Version**: Python 3.9+ (see `pyproject.toml`)

**Primary Dependencies**: Click, Rich, FastAPI, PyYAML, PyGithub, azure-devops; Next.js 14 (UI); `actionlint` (optional binary on PATH)

**Storage**: SQLite (StateDB) via `ado2gh/state/db.py`; SettingsStore for per-profile operator resolutions

**Testing**: pytest with pytest-cov; 85% line coverage gate on `ado2gh` package; contract tests in `tests/contract/`

**Target Platform**: Cross-platform (Windows/Linux/macOS); Docker for deployed stacks

**Project Type**: CLI + web-service (FastAPI backend, Next.js frontend)

**Performance Goals**: Batch migration of 50+ repos per phase; per-repo analysis < 30s; conversion < 10s per pipeline

**Constraints**: Dry-run default; secrets never logged; human-in-the-loop for live mode; per-repo lock for concurrency

**Scale/Scope**: 11 pipeline steps in `ACCELERATOR_PIPELINE_STEPS`, 5 in `MIGRATE_UI_PIPELINE_STEPS` (after removing `map_secrets`); ~1200 lines in `pipeline_runner.py` to refactor

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Each step handler is a focused function with clear inputs/outputs; step result data is structured and typed |
| II. Documentation | New step handlers, helpers, and entities will include docstrings describing purpose, inputs, outputs |
| III. Deprecation | `map_secrets` step ID is explicitly deprecated in both pipeline lists; `SecretsScopeHandler` is marked deprecated with removal timeline |
| IV. Architecture & Naming | New modules: `ado2gh/api/step_prerequisites.py`, `ado2gh/api/repo_lock.py`, `ado2gh/pipelines/validation/`; names match domain (dependency, feasibility, conversion, validation) |
| V. Enterprise Safeguards | Dry-run supported on all steps; live mode requires explicit confirmation; per-repo lock prevents concurrent writes; secrets never stored; OIDC auto-provisioning with operator fallback; all step results auditable in PipelineRun |
| VI. Testing (85%+) | Unit tests for each step handler, prerequisite checker, repo lock, feasibility analyzer, workflow validator, OIDC provisioner; contract tests for step result data shapes; coverage maintained ≥ 85% |

**Result**: [x] PASS — all gates satisfied

## Project Structure

### Documentation (this feature)

```text
specs/009-pipeline-step-decoupling/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── step-result-contracts.md
│   └── pipeline-step-contracts.md
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
ado2gh/
├── api/
│   ├── pipeline_runner.py          # Orchestration only (decomposed by spec 010)
│   ├── pipeline_models.py         # Step definitions (decomposed by spec 010) — update to remove map_secrets, add analyze_deps
│   ├── pipeline_steps.py          # Step implementations (decomposed by spec 010) — refactor _step_analyze_deps, _migrate_scoped, _step_validate
│   ├── step_prerequisites.py       # Prerequisite checker (already exists from spec 010)
│   ├── repo_lock.py                # Per-repo lock manager (already exists from spec 010)
│   └── oidc_provisioner.py         # OIDC auto-provisioning (already exists from spec 010)
├── pipelines/
│   ├── transform/
│   │   └── transformer.py          # Existing: ADO → GHA YAML conversion
│   └── validation/
│       └── workflow_validator.py   # NEW: actionlint wrapper + YAML fallback validation
├── core/
│   └── scopes/
│       └── git_scope.py            # Extended: feasibility analysis before migration
├── agents/
│   └── orchestration/
│       └── pev_coordination.py     # Extended: inventory_gaps form for all dependency types
├── reporting/
│   ├── post_migration_validator.py # Extended: workflow integrity verification
│   └── service_connection_manifest.py  # Deprecated: functionality merged into analyze_deps
└── state/
    └── db.py                       # Extended: repo_locks table, operator_resolutions table (schema only; API layer in settings_store.py)

apps/migration-ui/src/
├── components/
│   └── ResolveDependencies.tsx     # NEW: operator resolution panel
├── app/
│   └── runs/
│       └── MonitorClient.tsx       # Extended: refresh inventory button
└── lib/
    └── types.ts                    # Extended: new step result types

tests/
├── unit/
│   ├── test_step_prerequisites.py
│   ├── test_repo_lock.py
│   ├── test_oidc_provisioner.py
│   ├── test_workflow_validator.py
│   ├── test_feasibility_analysis.py
│   ├── test_analyze_deps.py
│   ├── test_operator_resolutions.py
│   └── test_workflow_integrity.py
├── contract/
│   └── test_009_step_contracts.py
└── integration/
    └── test_009_pipeline_decoupling.py
```

**Structure Decision**: Extends existing `ado2gh/` package layout. New modules are placed by domain: API orchestration in `api/`, pipeline validation in `pipelines/validation/`, locking in `api/`. UI components follow existing Next.js `apps/migration-ui/src/` structure. Tests mirror source layout in `tests/`.

## Complexity Tracking

> No Constitution Check violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
