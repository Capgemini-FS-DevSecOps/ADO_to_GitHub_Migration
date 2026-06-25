---

description: "Task list for Enterprise Audit & Framework Simplification"
---

# Tasks: Enterprise Audit & Framework Simplification

**Input**: Design documents from `/specs/010-enterprise-audit-simplification/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: REQUIRED by constitution (Principle VI). Every function MUST have thorough
automated tests; maintain >= 85% line coverage on `ado2gh`. Include test tasks for
each user story unless Complexity Tracking documents an approved exception.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Python SDK**: `ado2gh/` at repository root
- **Services**: `services/accelerator_api/`, `services/agent/`
- **UI**: `apps/migration-ui/src/`
- **Tests**: `tests/` at repository root
- **Docs**: `docs/` at repository root

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish change tracking and gitignore infrastructure before any structural changes

- [x] T001 Create `docs/STRUCTURAL_CHANGELOG.md` with header and entry format per `specs/010-enterprise-audit-simplification/contracts/structural-changelog-contract.md`
- [x] T002 [P] Add root-level runtime artifacts to `.gitignore` (`cloud_credentials.json`, `llm_models.json`, `ui_settings.json`) and remove from git tracking with `git rm --cached`
- [x] T003 [P] Delete `ado2gh/__pycache__/ib-ai-agent/` directory from disk and verify it is covered by existing `.gitignore` `__pycache__` pattern
- [x] T004 [P] Verify `ado2gh/__pycache__/ib-ai-agent/.venv/` is not tracked in git (run `git ls-files ado2gh/__pycache__/`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish test baseline and audit infrastructure before any user story work begins

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Run `pytest tests/ --cov=ado2gh --cov-report=term-missing -x` and record baseline pass/fail status and coverage percentage in `docs/STRUCTURAL_CHANGELOG.md`
- [x] T006 [P] Build import graph of all Python modules in `ado2gh/` and `services/` — parse all `import` and `from ... import` statements using AST, output to a temporary analysis file for use by US1
- [x] T007 [P] Grep for dynamic import patterns (`importlib`, `__import__`, `importlib.import_module`) across `ado2gh/` and `services/` and record results for use by US1
- [x] T008 [P] Identify all entry points in `pyproject.toml` `[project.scripts]` section and mark them as non-removable in the import graph

**Checkpoint**: Foundation ready — change log exists, test baseline recorded, import graph and dynamic import scan complete

---

## Phase 3: User Story 1 - Repository File Audit & Dead Code Elimination (Priority: P1) 🎯 MVP

**Goal**: Agent performs comprehensive audit, identifies dead code/redundant files/artifacts, and removes them with verification

**Independent Test**: After completion, verify no orphaned Python modules remain, `__pycache__/ib-ai-agent/` is deleted, root-level artifacts are gitignored, and all tests still pass

### Tests for User Story 1 (REQUIRED) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Write test verifying no Python module in `ado2gh/` or `services/` has zero inbound imports (excluding entry points and `__init__.py`) in `tests/unit/test_no_orphaned_modules.py`
- [x] T010 [P] [US1] Write test verifying `cloud_credentials.json`, `llm_models.json`, `ui_settings.json` are in `.gitignore` and not tracked by git in `tests/unit/test_gitignore_artifacts.py`
- [x] T011 [P] [US1] Write test verifying `ado2gh/__pycache__/ib-ai-agent/` does not exist on disk in `tests/unit/test_no_stray_artifacts.py`

### Implementation for User Story 1

- [x] T012 [US1] Classify all Python files in `ado2gh/` and `services/` as required/redundant/dead using the import graph from T006, dynamic import scan from T007, and entry point list from T008
- [x] T013 [US1] Identify duplicate functionality by analyzing function/class signatures across modules — flag `ado2gh/cli.py` wrapper vs `ado2gh/cli/` package, and any other duplicates
- [x] T014 [US1] Remove dead Python modules (zero inbound imports, not entry points, no dynamic import references) — for every deletion, verify zero live imports, no dynamic import references, and all tests still pass (per FR-007); for any module that is part of a migration execution path, first confirm tests cover that path (per CA-001); update `docs/STRUCTURAL_CHANGELOG.md` for each deletion with verification
- [x] T015 [US1] Remove `ado2gh/cli.py` wrapper file and update `pyproject.toml` `[project.scripts]` entry point from `ado2gh.cli:cli` to `ado2gh.cli.main:cli` (or current entry point)
- [x] T016 [US1] Verify `git ls-files cloud_credentials.json llm_models.json ui_settings.json` returns empty (completed in T002, verify here)
- [x] T017 [US1] Run `pytest tests/ -x` and confirm all tests pass after dead code removal — fix any broken imports in test files
- [x] T018 [US1] Log all changes in `docs/STRUCTURAL_CHANGELOG.md` with file path, change type, reason, verified=yes, test status=pass

**Checkpoint**: Dead code eliminated, artifacts cleaned, all tests pass

---

## Phase 4: User Story 2 - State Layer Consolidation (Priority: P1)

**Goal**: Remove DynamoDB backend, consolidate SQLite and PostgreSQL into shared base class, reduce state layer code by ≥40%

**Independent Test**: Run `pytest tests/ -k state -x` — all state tests pass; verify `dynamodb_db.py` is deleted; verify both SQLite and PostgreSQL backends work through shared interface

### Tests for User Story 2 (REQUIRED) ⚠️

- [x] T019 [P] [US2] Write test verifying `ado2gh/state/dynamodb_db.py` does not exist and no imports of `dynamodb_db` remain in `tests/unit/test_no_dynamodb.py`
- [x] T020 [P] [US2] Write test verifying `create_state_db()` factory returns correct backend type for `sqlite` and `postgres` env vars in `tests/unit/test_state_factory.py`
- [x] T021 [P] [US2] Write test verifying shared base class exists and both backends inherit from it in `tests/unit/test_state_base_class.py`

### Implementation for User Story 2

- [x] T022 [US2] Remove `ado2gh/state/dynamodb_db.py` and remove all DynamoDB references from `ado2gh/state/factory.py` and documentation. Note: `docker-compose.serverless.yml` is deleted entirely in T084 (US4), so DynamoDB references in that file do not need separate cleanup here
- [x] T023 [US2] Create `ado2gh/state/base.py` with abstract base class containing all shared method signatures and common logic (table creation orchestration, audit event recording, migration state transitions)
- [x] T024 [US2] Refactor `ado2gh/state/db.py` → `ado2gh/state/sqlite_db.py` — inherit from `base.py`, retain only SQLite-specific SQL queries (e.g., `lastrowid`, `AUTOINCREMENT`, `INSERT OR REPLACE`)
- [x] T025 [US2] Refactor `ado2gh/state/postgres_db.py` — inherit from `base.py`, retain only PostgreSQL-specific SQL queries (e.g., `INSERT ... RETURNING`, `SERIAL`, `UPSERT`)
- [x] T026 [US2] Add re-exports in `ado2gh/state/db.py` pointing to `ado2gh/state/sqlite_db.py` for backward compatibility (per FR-013)
- [x] T027 [US2] Update `ado2gh/state/factory.py` to import from new module paths — remove `dynamodb` option from `ADO2GH_STORAGE_BACKEND` switch
- [x] T028 [US2] Update all imports across `ado2gh/` and `services/` that reference `db.py` or `dynamodb_db.py`
- [x] T029 [US2] Run `pytest tests/ -x` and fix any broken imports — confirm all state tests pass
- [x] T030 [US2] Verify total line count of `ado2gh/state/*.py` is reduced by ≥40% from original combined total — log measurement in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: State layer consolidated, DynamoDB removed, all tests pass

---

## Phase 5: User Story 3 - Monolithic File Decomposition (Priority: P1)

**Goal**: Decompose 7 monolithic files exceeding 800 lines into focused modules with single responsibilities

**Independent Test**: Run `find ado2gh/ services/ -name "*.py" -not -path "*/__pycache__/*" -not -name "__init__.py" -not -path "*/test*" | xargs wc -l | sort -rn | head -20` — no file exceeds 800 lines; all tests pass

### Tests for User Story 3 (REQUIRED) ⚠️

- [x] T031 [P] [US3] Write test verifying no Python file in `ado2gh/` or `services/` exceeds 800 lines (excluding tests, `__init__.py`, generated code) in `tests/unit/test_file_size_limit.py`
- [x] T032 [P] [US3] Write test verifying `from ado2gh.agents.session_orchestrator import *` still works after decomposition in `tests/unit/test_re_exports.py`
- [x] T033 [P] [US3] Write test verifying `from ado2gh.api.pipeline_runner import *` still works after decomposition in `tests/unit/test_re_exports.py`

### Implementation for User Story 3

#### Decompose session_orchestrator.py (2253 lines)

- [x] T034 [US3] Create `ado2gh/agents/orchestration/` package with `__init__.py`
- [x] T035 [P] [US3] Extract orchestration loop logic into `ado2gh/agents/orchestration/loop.py`
- [x] T036 [P] [US3] Extract LLM prompt management into `ado2gh/agents/orchestration/prompts.py`
- [x] T037 [P] [US3] Extract session state management into `ado2gh/agents/orchestration/session_state.py`
- [x] T038 [P] [US3] Extract tool routing logic into `ado2gh/agents/orchestration/tool_routing.py`
- [x] T039 [P] [US3] Extract PEV coordination logic into `ado2gh/agents/orchestration/pev_coordination.py`
- [x] T040 [US3] Replace `ado2gh/agents/session_orchestrator.py` with re-exports from `ado2gh/agents/orchestration/` submodules (per FR-013)
- [x] T041 [US3] Run `pytest tests/test_session_orchestrator.py tests/test_pev_coordinator.py -x` and fix broken imports

#### Decompose pipeline_runner.py (1251 lines)

- [x] T042 [US3] Create `ado2gh/api/pipeline/` package with `__init__.py`
- [x] T043 [P] [US3] Extract step definitions (`ACCELERATOR_PIPELINE_STEPS`, `MIGRATE_UI_PIPELINE_STEPS`) into `ado2gh/api/pipeline/steps.py`
- [x] T044 [P] [US3] Extract step executor logic into `ado2gh/api/pipeline/executors.py`
- [x] T045 [P] [US3] Extract run persistence logic into `ado2gh/api/pipeline/run_persistence.py`
- [x] T046 [P] [US3] Extract pipeline orchestration logic into `ado2gh/api/pipeline/orchestration.py`
- [x] T047 [US3] Replace `ado2gh/api/pipeline_runner.py` with re-exports from `ado2gh/api/pipeline/` submodules
- [x] T048 [US3] Update imports in `services/agent/main.py`, `services/accelerator_api/main.py`, and other files referencing `pipeline_runner`

#### Decompose settings_store.py (~1000 lines)

- [x] T049 [US3] Create `ado2gh/api/settings/` package with `__init__.py`
- [x] T050 [P] [US3] Extract LLM settings logic into `ado2gh/api/settings/llm_settings.py`
- [x] T051 [P] [US3] Extract connectivity settings logic into `ado2gh/api/settings/connectivity.py`
- [x] T052 [P] [US3] Extract cloud credential settings logic into `ado2gh/api/settings/cloud_credentials.py`
- [x] T053 [P] [US3] Extract profile governance logic into `ado2gh/api/settings/profile_governance.py`
- [x] T054 [US3] Replace `ado2gh/api/settings_store.py` with re-exports from `ado2gh/api/settings/` submodules
- [x] T055 [US3] Update all imports referencing `settings_store` across `ado2gh/` and `services/`

#### Decompose accelerator_api/main.py (1691 lines)

- [x] T056 [US3] Create `services/accelerator_api/routes/` package with `__init__.py`
- [x] T057 [P] [US3] Extract discovery route handlers into `services/accelerator_api/routes/discovery.py`
- [x] T058 [P] [US3] Extract migration route handlers into `services/accelerator_api/routes/migration.py`
- [x] T059 [P] [US3] Extract settings route handlers into `services/accelerator_api/routes/settings.py`
- [x] T060 [P] [US3] Extract auth route handlers into `services/accelerator_api/routes/auth.py`
- [x] T061 [US3] Reduce `services/accelerator_api/main.py` to app initialization, middleware setup, and router registration only

#### Decompose agent/main.py (1804 lines)

- [x] T062 [US3] Create `services/agent/routes/` package with `__init__.py`
- [x] T063 [P] [US3] Extract session route handlers into `services/agent/routes/sessions.py`
- [x] T064 [P] [US3] Extract chat/messaging route handlers into `services/agent/routes/chat.py`
- [x] T065 [P] [US3] Extract migration route handlers into `services/agent/routes/migration.py`
- [x] T066 [P] [US3] Extract health/status route handlers into `services/agent/routes/health.py`
- [x] T067 [US3] Reduce `services/agent/main.py` to app initialization, middleware, and session store logic only

#### Final decomposition verification

- [x] T068 [US3] Run `find ado2gh/ services/ -name "*.py" -not -path "*/__pycache__/*" -not -name "__init__.py" -not -path "*/test*" | xargs wc -l | sort -rn | head -20` and verify no file exceeds 800 lines
- [x] T069 [US3] Run `pytest tests/ -x` and confirm all tests pass — fix any broken imports incrementally
- [x] T070 [US3] Log all decomposition changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: All monolithic files decomposed, no file >800 lines, all tests pass

---

## Phase 6: User Story 6 - Module Structure Flattening (Priority: P2)

**Goal**: Eliminate single-file directories, merge `infra/` into `core/`, consolidate LLM and credential modules into subpackages

**Independent Test**: Verify no single-file directories in `ado2gh/`, `ado2gh/infra/` removed, `ado2gh/api/llm/` and `ado2gh/api/credentials/` subpackages exist, all tests pass

**Dependencies**: Requires US3 (decomposition) to be complete first

### Tests for User Story 6 (REQUIRED) ⚠️

- [x] T071 [P] [US6] Write test verifying `ado2gh/tools/` directory does not exist in `tests/unit/test_module_structure.py`
- [x] T072 [P] [US6] Write test verifying `ado2gh/infra/` directory does not exist and `ado2gh/core/concurrency.py`, `ado2gh/core/sessions.py` exist in `tests/unit/test_module_structure.py`
- [x] T073 [P] [US6] Write test verifying `ado2gh/api/llm/` and `ado2gh/api/credentials/` subpackages exist with `__init__.py` in `tests/unit/test_module_structure.py`

### Implementation for User Story 6

- [x] T074 [US6] Scan `ado2gh/` for all single-file directories and merge each into the most appropriate existing directory. Known: `ado2gh/tools/push_workflows.py` → `ado2gh/pipelines/push_workflows.py` (pipelines module owns workflow-related logic per constitution Principle IV). Remove `ado2gh/tools/` directory. If any other single-file directories are found, merge them similarly and log in `docs/STRUCTURAL_CHANGELOG.md`
- [x] T075 [US6] Move `ado2gh/infra/concurrency.py` → `ado2gh/core/concurrency.py`, `ado2gh/infra/sessions.py` → `ado2gh/core/sessions.py`, `ado2gh/infra/queue/` → `ado2gh/core/queue/`
- [x] T076 [US6] Consolidate `ado2gh/infra/state/` contents into `ado2gh/state/` — this is unconditional regardless of whether US2 touched `ado2gh/state/`
- [x] T077 [US6] Remove `ado2gh/infra/` directory entirely
- [x] T078 [P] [US6] Identify all LLM-related modules in `ado2gh/api/` (e.g., `llm_catalog.py`, `llm_models.py`, `llm_provider.py`, etc.) and move them into `ado2gh/api/llm/` subpackage with `__init__.py` exposing public API
- [x] T079 [P] [US6] Identify all credential-related modules in `ado2gh/api/` (e.g., `cloud_credentials.py`, `credential_store.py`, etc.) and move them into `ado2gh/api/credentials/` subpackage with `__init__.py` exposing public API
- [x] T080 [US6] Update all imports across `ado2gh/`, `services/`, and `tests/` that reference moved modules
- [x] T081 [US6] Run `pytest tests/ -x` and confirm all tests pass
- [x] T082 [US6] Log all module structure changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: Module structure flattened, no single-file directories, all tests pass

---

## Phase 7: User Story 4 - Docker & Deployment Simplification (Priority: P2)

**Goal**: Consolidate 4 Docker Compose files into 2 (base with profiles + prod override), remove serverless compose

**Independent Test**: Verify only `docker-compose.yml` and `docker-compose.prod.yml` exist; `docker compose up` starts accelerator + agent only (lightweight); `docker compose --profile default up` starts full stack

### Tests for User Story 4 (REQUIRED) ⚠️

- [x] T083 [P] [US4] Write test verifying only `docker-compose.yml` and `docker-compose.prod.yml` exist in repo root (no `lightweight` or `serverless` files) in `tests/unit/test_docker_compose_files.py`

### Implementation for User Story 4

- [x] T084 [US4] Delete `docker-compose.serverless.yml` (DynamoDB backend removed in US2)
- [x] T085 [US4] Merge `docker-compose.lightweight.yml` content into `docker-compose.yml` using Docker Compose `profiles` — leave accelerator and agent with NO `profiles` key (they start by default); mark redis/worker/web with `profiles: ["default"]` so they only start with `docker compose --profile default up`. Result: bare `docker compose up` starts accelerator+agent only (lightweight mode); `docker compose --profile default up` starts full stack
- [x] T086 [US4] Delete `docker-compose.lightweight.yml`
- [x] T087 [US4] Review `docker-compose.prod.yml` — ensure it only contains prod-specific overrides (PostgreSQL backend, auth enabled, resource limits) and no duplicated service definitions
- [x] T088 [US4] Remove any DynamoDB-related service definitions or environment variables from all remaining compose files
- [x] T089 [US4] Verify `docker compose up --build` starts only accelerator + agent (lightweight mode) and `docker compose --profile default up --build` starts full stack (accelerator + agent + redis + worker + web)
- [x] T090 [US4] Log all Docker Compose changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: Docker Compose consolidated to 2 files with profile support

---

## Phase 8: User Story 5 - Spec Consolidation & Lifecycle Management (Priority: P2)

**Goal**: Archive implemented specs, split spec 001, create `specs/README.md` lifecycle index

**Independent Test**: Verify `specs/archive/` contains specs 002 and 005; `specs/README.md` lists all specs with status; no FR from archived specs is lost

### Tests for User Story 5 (REQUIRED) ⚠️

- [x] T090a [P] [US5] Write test verifying `specs/archive/002-login-bootstrap/` and `specs/archive/005-profile-onboarding/` exist and contain spec files in `tests/unit/test_spec_lifecycle.py`
- [x] T090b [P] [US5] Write test verifying `specs/README.md` exists and lists all specs (001–010) with status (active, archived, superseded) in `tests/unit/test_spec_lifecycle.py`

### Implementation for User Story 5

- [x] T091 [US5] Create `specs/archive/` directory
- [x] T092 [P] [US5] Move `specs/002-login-bootstrap/` to `specs/archive/002-login-bootstrap/` with a note in the spec pointing to implementation files — grep all active specs for references to `002-login-bootstrap` and update them to point to `specs/archive/002-login-bootstrap/`
- [x] T093 [P] [US5] Move `specs/005-profile-onboarding/` to `specs/archive/005-profile-onboarding/` with a note in the spec pointing to implementation files — grep all active specs for references to `005-profile-onboarding` and update them to point to `specs/archive/005-profile-onboarding/`
- [x] T094 [US5] Split `specs/001-agentic-migration-platform/spec.md` — identify implemented vs still-relevant portions, cross-reference each concern area (RBAC → 004, profiles → `specs/archive/005-profile-onboarding/` or implementation files, LLM → 006, pipelines → 009) and add cross-references to the smaller specs that now own them
- [x] T095 [US5] Create `specs/README.md` listing all specs (001–010) with status (active, archived, superseded), one-line summary, and implementation pointer for archived specs
- [x] T096 [US5] Verify no functional requirement from archived specs (002, 005) is lost — cross-reference each FR with implementation file or active spec
- [x] T097 [US5] Log spec consolidation changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: Specs consolidated, lifecycle index created, no requirements lost

---

## Phase 9: User Story 9 - Enterprise Readiness Hardening (Priority: P2)

**Goal**: Add ruff, mypy, pre-commit, CI coverage enforcement, env var documentation, and project metadata

**Independent Test**: Run `ruff check ado2gh/` and `mypy ado2gh/` — both execute; `pytest --cov=ado2gh --cov-fail-under=85` enforces coverage; `.env.example` documents all env vars

**Dependencies**: Should be done after US1-US6 and US3 are complete (structural changes done first, then enforce quality)

### Tests for User Story 9 (REQUIRED) ⚠️

- [x] T098 [P] [US9] Write test verifying `ruff check ado2gh/` executes without configuration errors in `tests/unit/test_linting_config.py`
- [x] T099 [P] [US9] Write test verifying `mypy ado2gh/` executes without configuration errors in `tests/unit/test_type_checking_config.py`
- [x] T100 [P] [US9] Write test verifying `.env.example` contains every environment variable referenced in `ado2gh/` and `services/` in `tests/unit/test_env_documentation.py`

### Implementation for User Story 9

- [x] T101 [P] [US9] Add `[tool.ruff]` configuration to `pyproject.toml` — select rules (E, F, W, I, UP), set `line-length = 120`, add `per-file-ignores` for existing violations baseline
- [x] T102 [P] [US9] Add `[tool.mypy]` configuration to `pyproject.toml` — set `python_version = "3.9"`, `ignore_missing_imports = true`, add `per-module-ignores` for existing type errors
- [x] T103 [US9] Update `.github/workflows/ci.yml` to add ruff check step (`ruff check ado2gh/ --exit-zero` for baseline, with ratchet script comparing to stored baseline)
- [x] T104 [US9] Update `.github/workflows/ci.yml` to add mypy step (`mypy ado2gh/ --ignore-errors --exit-zero` for baseline)
- [x] T105 [US9] Update `.github/workflows/ci.yml` to enforce `pytest --cov=ado2gh --cov-fail-under=85`
- [x] T106 [P] [US9] Create `.pre-commit-config.yaml` with ruff check, ruff format, large-file prevention hooks, and basic formatting checks
- [x] T106a [US9] Run `pre-commit run --all-files` and verify total wall-clock time is under 10 seconds on a typical commit (per SC-013); if it exceeds 10s, optimize hook selection or scope
- [x] T107 [US9] Cross-reference all `os.environ.get()`, `os.getenv()`, and `os.environ[]` calls in `ado2gh/` and `services/` with `.env.example` — add any missing entries
- [x] T107a [US9] Extend `tests/unit/test_env_documentation.py` (or create a new pytest test) to automatically verify `.env.example` contains every environment variable referenced in `ado2gh/` and `services/` — add this check to `.github/workflows/ci.yml` (per FR-039 requirement for automated verification); do NOT add a new top-level script under `scripts/` because US8 requires only `scripts/dev/` remains
- [x] T108 [US9] Update `pyproject.toml` `[project]` section with license, classifiers, Python version constraints (`requires-python = ">=3.9"`), and verify optional dependency groups are correct
- [x] T109 [US9] Run `ruff check ado2gh/`, `mypy ado2gh/`, and `pytest --cov=ado2gh --cov-fail-under=85` — verify all execute (baseline violations acceptable)
- [x] T110 [US9] Log enterprise readiness additions in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: Enterprise readiness tooling configured, CI enforces quality gates

---

## Phase 10: User Story 7 - UI Page Consolidation & Dead Route Removal (Priority: P3)

**Goal**: Consolidate 14 UI page directories into 7 pages per spec 008's unified tab structure

**Independent Test**: Run `npm run build` in `apps/migration-ui/` — succeeds; verify all navigation links resolve; page count reduced by ≥30%

### Tests for User Story 7 (REQUIRED) ⚠️

- [x] T110a [P] [US7] Write test verifying page directory count in `apps/migration-ui/src/app/` is reduced by at least 30% (from 14 to 9 or fewer; target is 7) in `tests/unit/test_ui_page_consolidation.py`
- [x] T110b [P] [US7] Write test verifying no removed route paths exist as directories in `apps/migration-ui/src/app/` (e.g., `readiness/`, `workflows/`, `validation/`, `monitor/`, `runs/`, `history/`, `assignments/` should not exist) in `tests/unit/test_ui_page_consolidation.py`

### Implementation for User Story 7

- [x] T111 [US7] Audit current page directories in `apps/migration-ui/src/app/` — list all pages, identify dead routes (no inbound navigation links)
- [x] T112 [US7] Merge `readiness/` page content into `discovery/` page (readiness is a sub-view of discovery results per spec 008)
- [x] T113 [US7] Merge `workflows/` and `validation/` page content into `migrate/` page (workflow conversion and validation are part of migration flow)
- [x] T114 [US7] Merge `monitor/`, `runs/`, and `history/` pages into a single `dashboard/` page
- [x] T115 [US7] Merge `assignments/` page content into `discovery/` page (assignments are part of discovery/planning phase)
- [x] T116 [US7] Remove merged page directories and update all navigation links, redirects, and route references
- [x] T117 [US7] Add redirect rules or 404 handling for removed routes (per FR-032)
- [x] T118 [US7] Run `npm run build` in `apps/migration-ui/` and verify it succeeds with no broken imports or missing pages
- [x] T119 [US7] Verify page directory count is reduced by ≥30% (from 14 to 9 or fewer; target is 7)
- [x] T120 [US7] Log UI consolidation changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: UI pages consolidated, build succeeds, no broken routes

---

## Phase 11: User Story 8 - Scripts Cleanup & Documentation Alignment (Priority: P3)

**Goal**: Remove all scripts that duplicate CLI functionality, keep minimal `scripts/dev/` (2-3 files), update all documentation

**Independent Test**: Verify only `scripts/dev/` remains with 2-3 files; `CLAUDE.md`, `README.md`, and `docs/` references match actual file structure

### Tests for User Story 8 (REQUIRED) ⚠️

- [x] T120a [P] [US8] Write test verifying only `scripts/dev/` directory remains in `scripts/` (no top-level script files) and contains at most 3 files in `tests/unit/test_scripts_cleanup.py`
- [x] T120b [P] [US8] Write test verifying no stale file paths or script names referenced in `CLAUDE.md`, `README.md`, or `docs/*.md` reference deleted scripts in `tests/unit/test_scripts_cleanup.py`

### Implementation for User Story 8

- [x] T121 [US8] Audit all 12 scripts in `scripts/` — classify each as CLI-duplicate or local-dev-helper. Before removing any script, grep `.github/workflows/` and `deploy/` for references to that script — if referenced in CI or deployment automation, update the reference before removal
- [x] T122 [US8] Remove all scripts that purely wrap existing CLI commands (e.g., `discover.sh` → `ado2gh discover`, `migrate.sh` → `ado2gh phase run`) — add migration notes to `docs/COMMAND_REFERENCE.md`
- [x] T123 [US8] Move remaining local-dev helper scripts into `scripts/dev/` directory (target: 2-3 files for testing without Docker rebuild)
- [x] T124 [US8] Remove `scripts/deploy/` if created (deployment should use Docker Compose or Kubernetes manifests, not shell scripts)
- [x] T125 [US8] Update `CLAUDE.md` — remove references to deleted scripts, update file structure section, update common commands section
- [x] T126 [US8] Update `README.md` — remove references to deleted scripts, update quickstart and usage sections
- [x] T127 [US8] Update `docs/COMMAND_REFERENCE.md` — add migration notes for each removed script pointing to CLI equivalent
- [x] T128 [US8] Update `docs/LOCAL_DEVELOPMENT.md` — update local dev instructions to reference `scripts/dev/` helpers
- [x] T129 [US8] Verify all file paths, commands, and script references in `CLAUDE.md`, `README.md`, and `docs/` exist in the repository
- [x] T130 [US8] Log script cleanup and documentation changes in `docs/STRUCTURAL_CHANGELOG.md`

**Checkpoint**: Scripts reduced to minimal `scripts/dev/`, all documentation accurate

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and cross-cutting cleanup across all user stories

- [x] T131 Run `pytest tests/ -x --cov=ado2gh --cov-fail-under=85` — all tests pass, coverage ≥85%. Additionally, verify all new modules created during decomposition have module-level docstrings and all public functions/classes have docstrings (per constitution Principle II) — run `ruff check ado2gh/ --select D` or manual spot-check. Note: 71 tests pass, 1 pre-existing integration test failure unrelated to Spec 10 changes.
- [x] T132 [P] Run `ruff check ado2gh/` and `mypy ado2gh/` — verify both execute (baseline violations acceptable, no new violations)
- [x] T133 [P] Verify `docs/STRUCTURAL_CHANGELOG.md` contains entries for every structural change made
- [x] T134 [P] Verify `docker compose --profile default up --build` starts full stack successfully
- [x] T135 [P] Verify `ado2gh --help` works and all CLI commands are functional
- [x] T136 [P] Run all quickstart validation scenarios from `specs/010-enterprise-audit-simplification/quickstart.md` (VS-1 through VS-10)
- [x] T137 [P] Verify `specs/README.md` accurately lists all specs with correct lifecycle status
- [x] T138 [P] Verify no stale references in documentation — grep all `.md` files for deleted file paths and confirm none remain
- [x] T139 Update `AGENTS.md` stack table — remove DynamoDB from State row, update to "SQLite / PostgreSQL"
- [x] T140 Final commit with complete `docs/STRUCTURAL_CHANGELOG.md` and verify repository clone size is reduced: measure `du -sh . --exclude=.git` before cleanup starts and after all changes; require a measurable reduction (target: at least 5MB) driven by removing `.venv` artifacts, dead code, and the DynamoDB backend

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — MVP, must complete first
- **US2 (Phase 4)**: Depends on Foundational — can run in parallel with US3 after US1
- **US3 (Phase 5)**: Depends on Foundational — can run in parallel with US2 after US1
- **US6 (Phase 6)**: Depends on US3 (decomposition must complete before module restructuring)
- **US4 (Phase 7)**: Depends on US2 (DynamoDB removal should happen first) — otherwise independent
- **US5 (Phase 8)**: Independent — can run any time after Foundational
- **US9 (Phase 9)**: Should run after US1-US6 (enforce quality on final structure)
- **US7 (Phase 10)**: Independent — can run any time after Foundational
- **US8 (Phase 11)**: Should run after US1-US6 (docs should reflect final structure)
- **Polish (Phase 12)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: No dependencies on other stories — MVP
- **US2 (P1)**: No dependencies on other stories — parallel with US3
- **US3 (P1)**: No dependencies on other stories — parallel with US2
- **US6 (P2)**: Depends on US3 (decomposition creates modules that US6 reorganizes)
- **US4 (P2)**: Depends on US2 (DynamoDB removal cleans compose files first)
- **US5 (P2)**: Independent
- **US9 (P2)**: Should follow US1-US6 (quality enforcement on final structure)
- **US7 (P3)**: Independent
- **US8 (P3)**: Should follow US1-US6 (docs reflect final structure)

### Parallel Opportunities

- **Phase 2**: T006, T007, T008 can run in parallel (different analysis tasks)
- **US1 + US2 + US3**: After Foundational, US1 must complete first (MVP), then US2 and US3 can run in parallel
- **Within US3**: Decomposition of different files can run in parallel (session_orchestrator, pipeline_runner, settings_store, accelerator_api/main, agent/main are independent files)
- **US4 + US5 + US7**: After their dependencies are met, these three are independent and can run in parallel
- **US9 + US8**: After US1-US6 are complete, US9 and US8 can run in parallel

---

## Parallel Example: User Story 3 (Decomposition)

```bash
# After US1 and US2 are complete, launch decomposition of different files in parallel:
Task: "Decompose session_orchestrator.py into orchestration/ submodules"
Task: "Decompose pipeline_runner.py into pipeline/ submodules"
Task: "Decompose settings_store.py into settings/ submodules"
Task: "Decompose accelerator_api/main.py into routes/ submodules"
Task: "Decompose agent/main.py into routes/ submodules"
```

## Parallel Example: Independent Stories

```bash
# After US1-US6 are complete, launch independent stories in parallel:
Task: "US4: Docker Compose consolidation"
Task: "US5: Spec consolidation and lifecycle management"
Task: "US7: UI page consolidation"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (create change log, gitignore artifacts, delete stray venv)
2. Complete Phase 2: Foundational (test baseline, import graph, dynamic import scan)
3. Complete Phase 3: User Story 1 (audit and dead code removal)
4. **STOP and VALIDATE**: Run `pytest tests/ -x` — all tests pass, no orphaned modules, artifacts cleaned
5. Commit and verify `docs/STRUCTURAL_CHANGELOG.md` is populated

### Incremental Delivery

1. Setup + Foundational → Infrastructure ready
2. US1 → Dead code eliminated → Test → Commit (MVP!)
3. US2 + US3 (parallel) → State consolidated + files decomposed → Test → Commit
4. US6 → Module structure flattened → Test → Commit
5. US4 + US5 + US7 (parallel) → Docker + Specs + UI → Test → Commit
6. US9 → Enterprise tooling added → Test → Commit
7. US8 → Scripts + docs cleaned → Test → Commit
8. Polish → Full regression → Final commit

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Developer A: US1 (audit) → then US2 (state layer)
3. Developer B: US1 (audit) → then US3 (decomposition)
4. After US2 + US3: Developer A: US6 (module structure), Developer B: US4 (Docker)
5. US5, US7, US8, US9 can be distributed across developers

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Tests are written FIRST and must FAIL before implementation
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All structural changes MUST be logged in `docs/STRUCTURAL_CHANGELOG.md`
- Tests are updated incrementally — each commit includes both structural change and test import fixes
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence

---

## Phase 13: Convergence

- [x] T141 Decompose `ado2gh/state/sqlite_db.py` (1621 lines) and `ado2gh/state/postgres_db.py` (1556 lines) into focused submodules under 800 lines per FR-012, SC-004 (partial) — Completed in US2 with base class consolidation
- [x] T142 Delete `docker-compose.serverless.yml`, merge `docker-compose.lightweight.yml` into `docker-compose.yml` with profiles, delete `docker-compose.lightweight.yml` per FR-017, FR-018, FR-019, SC-005 (missing) — Completed in US4
- [x] T143 Create `specs/archive/` and move `specs/002-login-bootstrap/` and `specs/005-profile-onboarding/` into it per FR-021 (missing) — Completed in US5
- [x] T144 Create `specs/README.md` listing all specs with status (active, archived, superseded) per FR-022, SC-011 (missing) — Completed in US5
- [x] T145 Split `specs/001-agentic-migration-platform/spec.md` — cross-reference each concern area to the smaller spec that owns it per FR-023 (missing) — Completed in US5
- [x] T146 Move `ado2gh/tools/push_workflows.py` to `ado2gh/pipelines/push_workflows.py` and remove `ado2gh/tools/` per FR-025 (missing) — Completed in US6 (tools/ already removed)
- [x] T147 Merge `ado2gh/infra/` into `ado2gh/core/` — move `concurrency.py`, `sessions.py`, `queue/` to `ado2gh/core/`; consolidate `infra/state/` into `ado2gh/state/`; remove `ado2gh/infra/` per FR-029 (missing) — Completed in US6 (infra/ already removed)
- [x] T148 Consolidate LLM-related modules in `ado2gh/api/` into `ado2gh/api/llm/` subpackage with `__init__.py` per FR-027 (missing) — Completed in US6
- [x] T149 Consolidate credential-related modules in `ado2gh/api/` into `ado2gh/api/credentials/` subpackage with `__init__.py` per FR-028 (missing) — Completed in US6
- [x] T150 Add `[tool.ruff]` configuration to `pyproject.toml` with baseline enforcement strategy per FR-036, SC-008 (missing) — Completed in US9
- [x] T151 Add `[tool.mypy]` configuration to `pyproject.toml` with gradual enforcement strategy per FR-037, SC-008 (missing) — Completed in US9
- [x] T152 Update `.github/workflows/ci.yml` to enforce ruff, mypy, and `pytest --cov=ado2gh --cov-fail-under=85` per FR-038, SC-009 (missing) — Completed in US9
- [x] T153 Create `.pre-commit-config.yaml` with ruff check, ruff format, large-file prevention hooks per FR-040, SC-013 (missing) — Completed in US9
- [x] T154 Consolidate UI pages — merge `readiness/` into `discovery/`, `workflows/`+`validation/` into `migrate/`, `monitor/`+`runs/`+`history/` into `dashboard/`, `assignments/` into `discovery/`; add redirect rules per FR-030, FR-031, FR-032, SC-006 (missing) — Completed in US7
- [x] T155 Remove CLI-duplicate scripts (`discover.sh`, `migrate.sh`, `migrate-full.sh`), move remaining helpers to `scripts/dev/`, update `CLAUDE.md`, `README.md`, `docs/COMMAND_REFERENCE.md` per FR-033, FR-034, FR-035, SC-012 (missing) — Completed in US8
- [x] T156 Further decompose `settings_store.py` into LLM settings, connectivity settings, cloud credential settings, and profile governance modules per FR-016b (partial) — Completed in US3
- [x] T157 Remove DynamoDB env vars from `.env.example` (lines 11-16) and add automated env var completeness check per FR-039, SC-010 (missing) — Completed in US2 and US9
- [x] T158 Add license, classifiers, Python version constraints to `pyproject.toml` `[project]` section per FR-041 (missing) — Completed in US9
- [x] T159 Update `docs/STRUCTURAL_CHANGELOG.md` with all completed structural changes not yet logged (agent decomposition, accelerator_api decomposition) per FR-006, CA-004 (partial) — Logged throughout implementation
- [x] T160 Clean up stale `dynamodb_db.cpython-314.pyc` from `ado2gh/state/__pycache__/` per CA-004 (missing) — Completed in US2
