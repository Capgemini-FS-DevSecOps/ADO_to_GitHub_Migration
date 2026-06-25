# Research: Enterprise Audit & Framework Simplification

**Date**: 2026-06-24 | **Feature**: 010-enterprise-audit-simplification

## R1: State Layer Consolidation Pattern

**Decision**: Shared abstract base class with backend-specific overrides using Python ABC pattern.

**Rationale**: The existing `db.py` (SQLite, 66KB) and `postgres_db.py` (PostgreSQL, 66KB) share ~80% of their method signatures and business logic. A shared base class (`state/base.py`) will contain all common logic (table creation orchestration, audit event recording, migration state transitions), while each backend file retains only the SQL-dialect-specific queries (e.g., `INSERT ... RETURNING` for PostgreSQL vs `lastrowid` for SQLite). The factory pattern in `state/factory.py` remains unchanged — it simply imports from the new module paths.

**Alternatives considered**:
- *SQLAlchemy ORM*: Rejected — adds a heavy dependency, changes query patterns, and the project deliberately uses raw SQL for control over migration state schemas.
- *Protocol class (duck typing)*: Rejected — ABC provides explicit interface enforcement and better IDE support for identifying missing overrides.
- *Keep separate files, extract shared functions*: Rejected — function extraction doesn't solve the duplicated method count or the "change in one place" requirement (FR-008).

## R2: Monolithic File Decomposition Strategy

**Decision**: Decompose by concern, not by size. Each monolithic file is split into modules that group related functions/classes by responsibility. Re-exports in the original module path preserve backward compatibility.

**Rationale**: The 800-line threshold (FR-012) is the constraint, but splitting purely by line count produces arbitrary boundaries. Concern-based decomposition produces cohesive modules that are independently testable and maintainable. The re-export pattern (`from ado2gh.agents.orchestration.loop import *` in `session_orchestrator.py`) ensures existing imports continue to work (FR-013).

**Decomposition targets**:

| File | Lines | Decomposition |
|------|-------|---------------|
| `session_orchestrator.py` | 2253 | `orchestration/loop.py`, `orchestration/prompts.py`, `orchestration/session_state.py`, `orchestration/tool_routing.py`, `orchestration/pev_coordination.py` |
| `pipeline_runner.py` | 1251 | `pipeline/steps.py`, `pipeline/executors.py`, `pipeline/run_persistence.py`, `pipeline/orchestration.py` |
| `settings_store.py` | 38KB (~1000 lines, verify at implementation) | `settings/llm_settings.py`, `settings/connectivity.py`, `settings/cloud_credentials.py`, `settings/profile_governance.py` |
| `db.py` | ~1600 | `state/base.py` (shared), `state/sqlite_db.py` (SQLite-specific) |
| `postgres_db.py` | ~1600 | `state/postgres_db.py` (PostgreSQL-specific, inherits base) |
| `accelerator_api/main.py` | 1691 | `main.py` (init only), `routes/discovery.py`, `routes/migration.py`, `routes/settings.py`, `routes/auth.py` |
| `agent/main.py` | 1804 | `main.py` (init + session store), `routes/sessions.py`, `routes/chat.py`, `routes/migration.py`, `routes/health.py` |

**Alternatives considered**:
- *Split by line count only*: Rejected — produces arbitrary module boundaries that don't align with responsibilities.
- *Move everything into packages without re-exports*: Rejected — breaks all existing imports across the codebase and tests simultaneously.
- *Keep monolithic files, just add section comments*: Rejected — violates FR-012 and doesn't address maintainability.

## R3: Docker Compose Profile Strategy

**Decision**: Use Docker Compose `profiles` key to control which services start. Accelerator and agent have NO `profiles` key (they start by default). Redis, worker, and web have `profiles: ["default"]` so they only start with `--profile default`. The `lightweight` profile is not needed — bare `docker compose up` is the lightweight mode (accelerator + agent only).

**Rationale**: Docker Compose profiles (v2+) are the standard mechanism for conditional service startup. Services without a `profiles` key always start. Services with a `profiles` key only start when that profile is explicitly activated. This means:
- `docker compose up` → accelerator + agent only (lightweight/development mode)
- `docker compose --profile default up` → full stack (accelerator + agent + redis + worker + web)
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile default up` → prod with PostgreSQL

**Implementation**:
```yaml
# docker-compose.yml (base)
services:
  accelerator:  # no profiles key — always starts
  agent:        # no profiles key — always starts
  redis:        # profiles: ["default"] — only with --profile default
  worker:       # profiles: ["default"] — only with --profile default
  web:          # profiles: ["default"] — only with --profile default
```

**Alternatives considered**:
- *Mark all services with `profiles: ["default"]` and accelerator+agent with `profiles: ["default", "lightweight"]`*: Rejected — requires always passing `--profile default` or `--profile lightweight`, bare `docker compose up` starts nothing.
- *Keep 3 compose files*: Rejected — violates FR-017.
- *Use override files instead of profiles*: Rejected — profiles are cleaner and don't require multiple `-f` flags.

## R4: Ruff & Mypy Gradual Enforcement

**Decision**: Use `ruff` with a baseline `per-file-ignores` for existing violations, and `mypy` with `ignore_errors` on specific modules. CI runs both but uses `--exit-zero` initially with a ratchet script that compares violation count to a stored baseline.

**Rationale**: The codebase has no existing linting or type checking. Fixing all violations in one pass is impractical and risks introducing bugs. The gradual approach prevents new violations while allowing incremental cleanup.

**Implementation**:
- `pyproject.toml` `[tool.ruff]` section with rule selection (E, F, W, I, UP)
- `pyproject.toml` `[tool.mypy]` section with `python_version = "3.9"`, `ignore_missing_imports = true`
- CI step: `ruff check ado2gh/ --exit-zero --output-format=json > ruff-baseline.json` then compare to stored baseline
- CI step: `mypy ado2gh/ --ignore-errors --exit-zero` initially
- Pre-commit: `ruff check --fix` and `ruff format` on staged files only

**Alternatives considered**:
- *Fix all violations before adding config*: Rejected — too large a scope change, delays the feature.
- *No baseline, just block on violations*: Rejected — would block all development until everything is fixed.
- *Use flake8 instead of ruff*: Rejected — ruff is faster, covers flake8 + isort + pyupgrade, and is the modern Python standard.

## R5: Dead Code Detection Methodology

**Decision**: Use Python AST analysis combined with grep-based import graph construction. The agent will: (1) parse all Python files to extract import statements; (2) build a directed graph of module dependencies; (3) identify modules with zero inbound edges (excluding entry points); (4) grep for dynamic import patterns (`importlib`, `__import__`); (5) manually verify each candidate before deletion.

**Rationale**: No existing tool perfectly handles the project's structure (mixed SDK + services + tests). AST-based import extraction is reliable for static imports. Dynamic import detection via grep is conservative but safe. Manual verification before deletion is the safety net.

**Alternatives considered**:
- *Use `vulture` or `pyflakes`*: Rejected — these detect unused code within files, not unused modules/files. Useful as a secondary check but not the primary method.
-*Use `pydeps` or `pyan`*: Rejected — these generate dependency graphs but don't directly identify dead modules. Can be used as verification.
- *Manual review only*: Rejected — too error-prone for 120+ files. Automated import graph is more reliable.

## R6: UI Page Consolidation Mapping

**Decision**: Merge pages per spec 008's unified tab structure. The current `apps/migration-ui/src/app/` directory contains 14 page directories: `agent/`, `assignments/`, `dashboard/`, `discovery/`, `history/`, `login/`, `migrate/`, `monitor/`, `onboarding/`, `readiness/`, `runs/`, `settings/`, `validation/`, `workflows/`. Specific mappings:

| Current Page | Merged Into | Rationale |
|-------------|-------------|-----------|
| `readiness/` | `discovery/` | Readiness is a sub-view of discovery results |
| `workflows/` | `migrate/` | Workflow conversion is part of the migration flow |
| `validation/` | `migrate/` | Validation is the final step of migration |
| `monitor/` | `dashboard/` | Monitoring is part of the dashboard overview |
| `runs/` | `dashboard/` | Run history is part of the dashboard |
| `history/` | `dashboard/` | History is part of the dashboard overview |
| `assignments/` | `discovery/` | Assignments are part of the discovery/planning phase |

**Retained pages**: `agent/`, `dashboard/`, `discovery/`, `migrate/`, `settings/`, `login/`, `onboarding/` (7 pages, down from 14 — a 50% reduction, exceeding the 30% SC-006 threshold)

**Alternatives considered**:
- *Keep separate pages, just remove dead routes*: Rejected — doesn't meet the 30% reduction target (SC-006).
- *Merge everything into a single page with tabs*: Rejected — Next.js routing is URL-based; separate pages are better for deep linking and SEO.

## R7: Spec Lifecycle Classification

**Decision**: Classify specs based on their current status field and implementation evidence:

| Spec | Current Status | Classification | Action |
|------|---------------|----------------|--------|
| 001 | Draft | Active (split) | Split concerns, cross-reference to smaller specs |
| 002 | Clarified (implemented) | Archived | Move to `specs/archive/`, pointer to implementation |
| 003 | Clarified | Active | Keep as-is |
| 004 | Draft | Active | Keep as-is |
| 005 | Clarified (implemented) | Archived | Move to `specs/archive/`, pointer to implementation |
| 006 | Draft | Active | Keep as-is |
| 007 | Draft | Active | Keep as-is |
| 008 | (UI refactor) | Active | Keep as-is |
| 009 | (Pipeline decoupling) | Active | Keep as-is |

**Rationale**: Specs 002 and 005 are explicitly marked as implemented. Their requirements are satisfied by existing code. Archiving them with implementation pointers keeps the spec directory focused on active work.

**Alternatives considered**:
- *Archive all specs older than 005*: Rejected — specs 003 and 004 are still active (Clarified/Draft, not implemented).
- *Keep everything, just add README*: Rejected — doesn't reduce confusion about what's implemented vs in-progress.
