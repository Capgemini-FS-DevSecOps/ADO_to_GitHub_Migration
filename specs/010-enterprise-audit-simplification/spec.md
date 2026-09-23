# Feature Specification: Enterprise Audit & Framework Simplification

**Feature Branch**: `010-enterprise-audit-simplification`

**Created**: 2026-06-24

**Status**: Clarified

**Input**: User description: "The entire repository is too complicated for the job it does. We need to identify all files that are required, simplify workflows and remove any redundancies. Remove technical debt and ensure this is enterprise ready. Create a new feature for a full complete audit. Look through previous specs and understand what can be removed, what technical debts exist and how we can simplify this framework."

## Clarifications

### Session 2026-06-24

- Q: How should the repository audit be invoked? → A: **Agent-driven one-time process** — the agent performs the audit directly during implementation, not as a persistent CLI command or script. No audit tool needs to be built; the agent analyzes the codebase and removes/consolidates directly.
- Q: Should `services/agent/main.py` (1804 lines) be decomposed? → A: **Yes** — decompose into router modules under `services/agent/routes/`, matching the pattern already used by `accelerator_api`.
- Q: How should root-level runtime artifacts (`cloud_credentials.json`, `llm_models.json`, `ui_settings.json`) be handled? → A: **Gitignore them** — they are runtime-generated and should not be tracked in git.
- Q: Should the DynamoDB state backend be kept? → A: **Remove entirely** — SQLite + PostgreSQL only. Remove `dynamodb_db.py` and `docker-compose.serverless.yml`.
- Q: Should the `scripts/` directory be kept or removed? → A: **Keep minimal `scripts/dev/`** — only local-dev startup helpers (2-3 files) for testing without Docker rebuild. Remove all scripts that duplicate CLI functionality.
- Q: Where should the structural change log live? → A: **`docs/STRUCTURAL_CHANGELOG.md`** — markdown file, human-reviewable in PR, simplest and most auditable for a one-time process.
- Q: Should `settings_store.py` (38KB) be decomposed in this feature? → A: **Yes** — decompose into LLM settings, connectivity settings, cloud credential settings, and profile governance modules.
- Q: Should `ado2gh/infra/` be merged or kept? → A: **Merge into `ado2gh/core/`** — runtime infrastructure belongs alongside orchestration and engine; `state/` subdirectory consolidates into `ado2gh/state/`.
- Q: How should tests be handled during structural changes? → A: **Update incrementally** — each structural change includes test import fixes in the same commit, keeping the suite green throughout.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Repository File Audit & Dead Code Elimination (Priority: P1)

The agent performs a comprehensive audit of the entire repository, identifying every file and module, classifying each as required, redundant, or dead code. The audit detects: unused modules, orphaned files not imported by any other module, duplicate implementations of the same functionality, committed artifacts that should be gitignored (e.g., virtual environments in `__pycache__`), and root-level config files that are runtime-generated and should be gitignored. The agent then directly removes dead code and redundant files, verifying no live imports reference the flagged files before deletion. This is a one-time agent-driven process, not a persistent tool.

**Why this priority**: Dead code and unused files are the primary source of repository complexity. Eliminating them is the foundation for all other simplification work.

**Independent Test**: After the agent completes the audit and removal, verify that `ado2gh/__pycache__/ib-ai-agent/` is deleted, no orphaned Python modules remain (all modules have at least one inbound import or are entry points), and root-level runtime artifacts (`cloud_credentials.json`, `llm_models.json`, `ui_settings.json`) are gitignored.

**Acceptance Scenarios**:

1. **Given** the repository in its current state, **When** the agent completes the audit, **Then** dead/orphaned files are removed, redundant files are consolidated, and a change log documents every deletion with rationale.
2. **Given** `ado2gh/__pycache__/ib-ai-agent/.venv/` is a stray virtual environment on disk, **When** the agent processes artifacts, **Then** the directory is deleted and confirmed gitignored.
3. **Given** a Python module with zero inbound imports across the entire codebase (excluding entry points and `__init__.py`), **When** the agent identifies it, **Then** the module is removed after verifying no dynamic imports reference it.
4. **Given** duplicate functionality is detected (e.g., `ado2gh/cli.py` wrapper vs `ado2gh/cli/` package), **When** the agent reviews the duplicate, **Then** the redundant wrapper is removed and entry points are updated to reference the canonical implementation directly.
5. **Given** root-level runtime artifacts (`cloud_credentials.json`, `llm_models.json`, `ui_settings.json`), **When** the agent processes artifacts, **Then** these files are added to `.gitignore` and removed from git tracking.

---

### User Story 2 - State Layer Consolidation (Priority: P1)

A platform maintainer reviews the state persistence layer and finds two near-identical database implementations (`db.py` at 66KB for SQLite, `postgres_db.py` at 66KB for PostgreSQL) with massive code duplication. The DynamoDB backend (`dynamodb_db.py` at 14KB) is removed entirely as part of this simplification — SQLite and PostgreSQL are the only supported backends going forward. The maintainer consolidates the two remaining implementations into a shared base class with backend-specific overrides, reducing total state layer code by at least 40% while preserving all existing functionality and the `ADO2GH_STORAGE_BACKEND` factory pattern.

**Why this priority**: The state layer is the largest source of code duplication in the repository. Consolidating it eliminates ~60KB of redundant code and makes future schema changes single-file. Removing DynamoDB eliminates a third backend that adds maintenance burden without sufficient deployment demand.

**Independent Test**: Run the existing test suite against the consolidated state layer. All tests pass. Verify that SQLite and PostgreSQL backends both work through the shared interface. Confirm the total line count of the state layer is reduced by at least 40%. Verify that `dynamodb_db.py` is deleted and no references to DynamoDB remain in the codebase.

**Acceptance Scenarios**:

1. **Given** two separate database implementation files with duplicated method signatures, **When** the maintainer consolidates them into a shared base class with backend-specific overrides, **Then** all existing tests pass without modification.
2. **Given** the consolidated state layer, **When** a new column or table is added, **Then** the change is made in one location (the shared base) rather than two separate files.
3. **Given** the `ADO2GH_STORAGE_BACKEND` environment variable is set to `sqlite` or `postgres`, **When** the factory creates a state DB instance, **Then** the correct backend is selected and all CRUD operations work as before.
4. **Given** `dynamodb_db.py` and `docker-compose.serverless.yml` exist, **When** the maintainer removes the DynamoDB backend, **Then** all references to DynamoDB are removed from the codebase, factory, and documentation.

---

### User Story 3 - Monolithic File Decomposition (Priority: P1)

A platform maintainer identifies and decomposes monolithic source files that exceed reasonable size thresholds. The following files are flagged for decomposition: `session_orchestrator.py` (91KB, 2253 lines), `pipeline_runner.py` (58KB, 1251 lines), `settings_store.py` (38KB), `db.py` (66KB), `postgres_db.py` (66KB), `accelerator_api/main.py` (1691 lines), `agent/main.py` (1804 lines). Each file is split into focused modules with clear single responsibilities, and imports are updated across the codebase. The 800-line threshold applies to both `ado2gh/` package files and `services/` entry points. Note: `db.py` and `postgres_db.py` decomposition is handled by US2 (state layer consolidation) — US3 enforces the 800-line threshold on the resulting modules.

**Why this priority**: Monolithic files are the primary barrier to maintainability. A 91KB Python file cannot be effectively reviewed, tested in isolation, or navigated by new contributors.

**Independent Test**: After decomposition, run the full test suite. All tests pass. Verify that no single Python file in the `ado2gh/` package or `services/` directory exceeds 800 lines (excluding generated code and test fixtures). Confirm that imports across the codebase resolve correctly.

**Acceptance Scenarios**:

1. **Given** `session_orchestrator.py` at 91KB with mixed concerns (orchestration, LLM prompting, session state, tool routing, PEV coordination), **When** the maintainer decomposes it, **Then** the resulting modules each have a single responsibility and no module exceeds 800 lines.
2. **Given** `pipeline_runner.py` at 58KB with step definitions, execution logic, and result persistence mixed together, **When** the maintainer decomposes it, **Then** step definitions, step executors, and run persistence are in separate modules.
3. **Given** `accelerator_api/main.py` at 1691 lines with route handlers, middleware, and startup logic mixed together, **When** the maintainer decomposes it, **Then** routes are organized into focused router modules and the main file contains only app initialization.
4. **Given** `agent/main.py` at 1804 lines with session, chat, migration, and health route handlers mixed together, **When** the maintainer decomposes it, **Then** routes are organized into `services/agent/routes/` modules and the main file retains only app initialization, middleware, and session store logic.
5. **Given** `settings_store.py` at 38KB with LLM, connectivity, cloud credential, and profile governance settings mixed together, **When** the maintainer decomposes it, **Then** each settings domain is in a separate module under `ado2gh/api/settings/` and re-exports preserve the original import path.
6. **Given** the decomposed modules, **When** the test suite runs, **Then** all existing tests pass without modification to test assertions (only import paths may change).

---

### User Story 4 - Docker & Deployment Simplification (Priority: P2)

A platform operator reviews the Docker Compose files (`docker-compose.yml`, `docker-compose.lightweight.yml`, `docker-compose.prod.yml`, `docker-compose.serverless.yml`) and consolidates them. The `docker-compose.serverless.yml` is removed entirely (DynamoDB backend dropped). The `docker-compose.lightweight.yml` is replaced by profile support in the base `docker-compose.yml` — accelerator and agent have no `profiles` key (start by default), while redis/worker/web have `profiles: ["default"]` (only start with `--profile default`). The final result is two files: `docker-compose.yml` (base with profile support) and `docker-compose.prod.yml` (prod overrides). Environment variable duplication across compose files is eliminated.

**Why this priority**: Four compose files with duplicated service definitions create maintenance burden and configuration drift. Docker Compose profiles are the standard mechanism for this.

**Independent Test**: Run `docker compose --profile default up --build` (full stack) and `docker compose up --build` (lightweight — accelerator + agent only). Both produce working stacks with the correct services. Verify the prod override file applies PostgreSQL backend when used with the base file.

**Acceptance Scenarios**:

1. **Given** four Docker Compose files with overlapping service definitions, **When** the operator consolidates them, **Then** the repository has at most two compose files: `docker-compose.yml` (base with profiles) and `docker-compose.prod.yml` (prod overrides).
2. **Given** the consolidated `docker-compose.yml` with profile support, **When** the operator runs `docker compose up` (bare, no profile), **Then** only the accelerator and agent services start (no Redis, worker, or web) — this is the lightweight mode.
3. **Given** the consolidated `docker-compose.yml`, **When** the operator runs `docker compose --profile default up`, **Then** the full stack starts (accelerator, agent, Redis, worker, web). Bare `docker compose up` starts only accelerator + agent (lightweight mode).
4. **Given** the prod override file, **When** the operator runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile default up`, **Then** PostgreSQL backend is used with authentication enabled.

---

### User Story 5 - Spec Consolidation & Lifecycle Management (Priority: P2)

A platform maintainer reviews the nine existing specs (001–009) and consolidates them into a clear lifecycle: implemented specs are archived to `specs/archive/`, superseded specs are marked with a deprecation notice, and the active specs are reorganized to eliminate overlap. Spec 001 (616 lines) is split — its implemented portions are archived, and its still-relevant requirements are cross-referenced from the smaller specs that now own them. A `specs/README.md` is created to document the spec lifecycle and current active features.

**Why this priority**: Nine specs with significant overlap create confusion about what is implemented, what is in progress, and what is planned. A clear lifecycle makes the project navigable.

**Independent Test**: Review the `specs/` directory after consolidation. Verify implemented specs (002, 005) are in `specs/archive/`, the `specs/README.md` accurately lists active vs archived specs, and no requirement is lost (all FRs from archived specs are traceable to either implementation or an active spec).

**Acceptance Scenarios**:

1. **Given** nine specs with varying statuses (Draft, Clarified, implemented), **When** the maintainer consolidates them, **Then** implemented specs are moved to `specs/archive/` with a note pointing to the implementation.
2. **Given** spec 001 at 616 lines covering platform, RBAC, audit, boards, pipelines, and assignments, **When** the maintainer splits it, **Then** each concern area is cross-referenced from the smaller spec that owns it (e.g., RBAC → 004, profiles → `specs/archive/005-profile-onboarding/`, LLM → 006).
3. **Given** the consolidated spec directory, **When** a new contributor reads `specs/README.md`, **Then** they can identify which features are implemented, in progress, and planned without reading every spec.
4. **Given** an archived spec, **When** a requirement from that spec is referenced, **Then** the archive entry includes a pointer to where the requirement now lives (implementation file or active spec).

---

### User Story 6 - Module Structure Flattening & Redundant Directory Elimination (Priority: P2)

A platform maintainer reviews the module structure and eliminates redundant directories and wrappers. Specifically: merges `ado2gh/tools/push_workflows.py` into `ado2gh/pipelines/`; merges `ado2gh/infra/` (4 items: `concurrency.py`, `sessions.py`, `queue/`, and `state/`) into `ado2gh/core/` — `concurrency.py`, `sessions.py`, and `queue/` relocate to `ado2gh/core/`, and `infra/state/` consolidates into `ado2gh/state/`; verifies the `ado2gh/cli.py` wrapper has been removed (done in US1) and entry points reference `ado2gh/cli/` package directly; consolidates the LLM-related modules scattered in `ado2gh/api/` into a focused `ado2gh/api/llm/` subpackage; and consolidates the credential-related modules scattered in `ado2gh/api/` into `ado2gh/api/credentials/` subpackage.

**Why this priority**: A flatter, more intuitive module structure reduces cognitive load and makes the codebase navigable — a core constitution principle (Principle IV: Intuitive Architecture & Naming).

**Independent Test**: After restructuring, run the full test suite. All tests pass. Verify that `import ado2gh` and all CLI commands work. Confirm the module tree has no single-file directories and no wrapper files that merely re-export from a package.

**Acceptance Scenarios**:

1. **Given** `ado2gh/tools/` contains a single file (`push_workflows.py`), **When** the maintainer relocates it, **Then** the file is moved to `ado2gh/pipelines/` and `ado2gh/tools/` is removed.
2. **Given** `ado2gh/cli.py` was a 96-byte wrapper that imports from `ado2gh/cli/` (removed in US1), **When** the maintainer verifies the wrapper has been removed, **Then** entry points in `pyproject.toml` reference `ado2gh.cli.main:cli` directly and CLI commands work unchanged.
3. **Given** LLM-related modules scattered in `ado2gh/api/`, **When** the maintainer consolidates them into `ado2gh/api/llm/`, **Then** all imports are updated and the subpackage has a clear `__init__.py` exposing the public API.
4. **Given** credential-related modules scattered in `ado2gh/api/`, **When** the maintainer consolidates them into `ado2gh/api/credentials/`, **Then** all imports are updated and the subpackage has a clear `__init__.py` exposing the public API.

---

### User Story 7 - UI Page Consolidation & Dead Route Removal (Priority: P3)

A frontend developer reviews the 14 page directories in `apps/migration-ui/src/app/` (`agent/`, `assignments/`, `dashboard/`, `discovery/`, `history/`, `login/`, `migrate/`, `monitor/`, `onboarding/`, `readiness/`, `runs/`, `settings/`, `validation/`, `workflows/`) and consolidates them per spec 008's unified tab structure. Redundant pages (e.g., separate `readiness/`, `workflows/`, `validation/` pages that spec 008 merges into Discovery + Migrate) are removed. Dead routes are identified by checking which pages have no navigation entry point. The `monitor/`, `runs/`, and `history/` pages are merged into a single dashboard.

**Why this priority**: UI page sprawl mirrors the backend module sprawl. Consolidating pages improves user experience and reduces frontend maintenance burden.

**Independent Test**: After consolidation, run the Next.js build. It succeeds. Verify that all navigation links resolve to existing pages and no 404 routes exist for linked pages. Confirm the page count is reduced by at least 30% (from 14 to 9 or fewer; target is 7).

**Acceptance Scenarios**:

1. **Given** 14 page directories including separate `readiness/`, `workflows/`, `validation/`, `monitor/`, `runs/`, `history/`, `assignments/`, **When** the developer consolidates per spec 008, **Then** these 7 are merged into `discovery/`, `migrate/`, and `dashboard/` respectively, leaving 7 retained pages with no loss of functionality.
2. **Given** pages with no inbound navigation links (dead routes), **When** the developer runs a dead-route audit, **Then** each dead route is listed and removed or marked as intentionally unlinked.
3. **Given** the consolidated UI, **When** the Next.js build runs, **Then** it succeeds with no broken imports or missing pages.

---

### User Story 8 - Scripts Cleanup & Documentation Alignment (Priority: P3)

A platform maintainer reviews the 12 scripts in `scripts/` and removes all that duplicate CLI functionality (e.g., `discover.sh` vs `ado2gh discover`, `migrate.sh` vs `ado2gh phase run`). A minimal `scripts/dev/` directory is kept with only local-dev startup helpers (2-3 files) for testing without Docker rebuild. Removed scripts are documented with migration notes in `docs/COMMAND_REFERENCE.md`. Documentation (`CLAUDE.md`, `README.md`, `docs/`) is updated to reflect the simplified structure.

**Why this priority**: Scripts that duplicate CLI commands create confusion about the canonical way to perform operations. Documentation that references removed scripts or files is misleading.

**Independent Test**: After cleanup, verify each remaining script has a clear purpose not covered by the CLI. Run any remaining scripts to confirm they work. Check that `CLAUDE.md` and `README.md` references match the actual file structure.

**Acceptance Scenarios**:

1. **Given** 12 scripts including `discover.sh` and `migrate.sh` that wrap CLI commands, **When** the maintainer reviews them, **Then** scripts that purely wrap existing CLI commands are removed with a migration note pointing to the CLI equivalent.
2. **Given** remaining scripts after cleanup, **When** the maintainer organizes them, **Then** only a minimal `scripts/dev/` directory (2-3 files) remains for local development without Docker.
3. **Given** updated documentation, **When** a new contributor follows `README.md` or `CLAUDE.md`, **Then** all referenced files, commands, and paths exist in the repository.

---

### User Story 9 - Enterprise Readiness Hardening (Priority: P2)

A platform maintainer ensures the repository meets enterprise readiness standards: adds linting/type-checking configuration (ruff, mypy) with CI enforcement; adds a `pre-commit` configuration for automated formatting and lint checks; ensures `pyproject.toml` has proper metadata (license, classifiers, Python version constraints); verifies all environment variables are documented in `.env.example`; and confirms the 85% test coverage gate from the constitution is enforced in CI.

**Why this priority**: Enterprise readiness requires automated code quality enforcement, not just manual review. The constitution mandates 85% coverage but CI may not enforce it.

**Independent Test**: Run `ruff check ado2gh/` and `mypy ado2gh/` — both execute without configuration errors. Run `pytest --cov=ado2gh --cov-fail-under=85` and verify the coverage gate is enforced. Check `.env.example` contains every environment variable referenced in the codebase.

**Acceptance Scenarios**:

1. **Given** no linting configuration in the repository, **When** the maintainer adds `ruff` configuration to `pyproject.toml`, **Then** `ruff check ado2gh/` runs and reports violations (existing code may have a baseline exclusion list).
2. **Given** no type-checking configuration, **When** the maintainer adds `mypy` configuration, **Then** `mypy ado2gh/` runs and reports type errors (existing code may have a baseline exclusion list).
3. **Given** the CI workflow in `.github/workflows/ci.yml`, **When** the maintainer reviews it, **Then** it includes ruff, mypy, and pytest with `--cov-fail-under=85`.
4. **Given** environment variables referenced in the codebase, **When** the maintainer cross-references with `.env.example`, **Then** every environment variable has a documented entry in `.env.example`.
5. **Given** a `pre-commit` configuration, **When** a developer runs `git commit`, **Then** ruff and basic checks run automatically on staged files.

---

### Edge Cases

- What happens when a "dead" module is actually imported dynamically (e.g., via `importlib` or `__import__`)? The audit MUST flag dynamic imports and exclude them from the dead-code list.
- What happens when consolidating state DB implementations introduces a regression in a backend-specific query? Each backend MUST retain its specific SQL query logic in override methods.
- What happens when decomposing a monolithic file breaks a public API that external consumers depend on? The decomposition MUST preserve public API surface via re-exports in the original module path.
- What happens when a removed script is referenced in CI or deployment automation? The audit MUST grep CI workflows and deployment manifests for script references before removal.
- What happens when consolidating UI pages breaks a bookmarked URL? Removed routes MUST have a redirect or a 404 page that guides users to the new location.
- What happens when an archived spec is referenced by an active spec? The archive entry MUST include a stable link and the active spec MUST update its reference to point to the archive location.
- What happens when the ruff/mypy baseline has too many violations to fix immediately? The configuration MUST support a gradual enforcement strategy (e.g., `ruff check --exit-zero` baseline in CI initially, with a ratchet that prevents new violations).

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

When this feature touches migration run, cleanup, rollback, or gate override:

- **CA-001**: The audit and simplification MUST NOT remove or alter any migration execution path without verifying test coverage of the affected path
- **CA-002**: State layer consolidation MUST NOT change the `ADO2GH_STORAGE_BACKEND` factory contract or break existing migration state persistence
- **CA-003**: No secrets or credentials MUST be exposed during the audit process (the audit scans file structure, not file contents)
- **CA-004**: All structural changes (file moves, module renames, decompositions) MUST be tracked in `docs/STRUCTURAL_CHANGELOG.md` with before/after paths for auditability

### Functional Requirements

#### Repository audit (agent-driven one-time process)

- **FR-001**: The agent MUST perform a comprehensive repository audit that scans every file and module, classifying each as: required (imported by live code), redundant (duplicate of another file's functionality), dead (not imported anywhere), or artifact (runtime-generated, should be gitignored)
- **FR-002**: The audit MUST detect committed virtual environments and large binary artifacts in gitignored directories and delete them
- **FR-003**: The audit MUST detect duplicate functionality by analyzing function/class signatures and module responsibilities, not just file names
- **FR-004**: The audit MUST detect dynamic imports (`importlib`, `__import__`, `importlib.import_module`) and exclude those modules from the dead-code list
- **FR-005**: The audit MUST identify root-level runtime artifacts (`cloud_credentials.json`, `llm_models.json`, `ui_settings.json`) and add them to `.gitignore`, removing them from git tracking
- **FR-006**: All deletions and removals MUST be documented in `docs/STRUCTURAL_CHANGELOG.md` with file path, rationale, and verification that no live imports reference the removed file
- **FR-007**: The agent MUST verify every deletion through automated checks (zero live imports, no dynamic import references, and all existing tests still pass) before removing a file; for this one-time codebase cleanup, automated verification replaces operator confirmation and the override rationale is documented in `docs/STRUCTURAL_CHANGELOG.md` per the constitution's HITL safeguard

#### State layer consolidation

- **FR-008**: State layer MUST be consolidated into a shared base class with backend-specific overrides, eliminating duplicated method implementations between SQLite and PostgreSQL
- **FR-009**: The `create_state_db()` factory in `ado2gh/state/factory.py` MUST remain the single entry point and its signature MUST NOT change
- **FR-010**: The consolidated state layer MUST preserve all existing table schemas, query semantics, and transaction behavior for each backend
- **FR-011**: The total line count of the state layer (`ado2gh/state/`) MUST be reduced by at least 40% from the current combined total
- **FR-011a**: The DynamoDB backend (`dynamodb_db.py`) and `docker-compose.serverless.yml` MUST be removed entirely — SQLite and PostgreSQL are the only supported backends going forward

#### Monolithic file decomposition

- **FR-012**: No Python file in the `ado2gh/` package or `services/` directory (excluding test files, generated code, and `__init__.py`) MUST exceed 800 lines after decomposition
- **FR-013**: Decomposition MUST preserve public API surface via re-exports in the original module path (e.g., `from ado2gh.api.pipeline_runner import PipelineRunner` continues to work after `PipelineRunner` is moved to a submodule)
- **FR-014**: The `session_orchestrator.py` file MUST be decomposed into at least: orchestration loop, LLM prompt management, session state management, tool routing, and PEV coordination modules
- **FR-015**: The `pipeline_runner.py` file MUST be decomposed into at least: step definitions, step executors, run persistence, and pipeline orchestration modules
- **FR-016**: The `accelerator_api/main.py` file MUST be reduced to app initialization, middleware setup, and router registration only — all route handlers MUST be in router modules
- **FR-016a**: The `agent/main.py` file MUST be decomposed into router modules under `services/agent/routes/` — the main file retains only app initialization, middleware, and session store logic
- **FR-016b**: The `settings_store.py` file MUST be decomposed into at least: LLM settings, connectivity settings, cloud credential settings, and profile governance modules

#### Docker & deployment simplification

- **FR-017**: The repository MUST have at most two Docker Compose files: `docker-compose.yml` (base with profile support) and `docker-compose.prod.yml` (prod overrides)
- **FR-018**: The `docker-compose.lightweight.yml` file MUST be replaced by profile support in the base `docker-compose.yml` — accelerator and agent have no `profiles` key (start by default for lightweight mode), redis/worker/web have `profiles: ["default"]` (only start with `--profile default` for full stack)
- **FR-019**: The `docker-compose.serverless.yml` file MUST be deleted entirely — DynamoDB backend is removed and serverless deployment is out of scope
- **FR-020**: Environment variable definitions MUST NOT be duplicated across compose files — shared defaults live in the base file, overrides in the prod file

#### Spec consolidation

- **FR-021**: Implemented specs (status "implemented" or "Clarified (implemented)") MUST be moved to `specs/archive/` with a pointer to the implementation
- **FR-022**: A `specs/README.md` MUST be created listing all specs with their status (active, archived, superseded) and a one-line summary
- **FR-023**: Spec 001 MUST be split — each major concern area (platform, RBAC, audit, boards, pipelines, assignments) is cross-referenced from the smaller spec that owns it
- **FR-024**: No functional requirement from an archived spec MUST be lost — each FR is either implemented (pointer to code) or transferred to an active spec

#### Module structure

- **FR-025**: Single-file directories in `ado2gh/` MUST be eliminated by merging the file into the most appropriate existing directory
- **FR-026**: Wrapper files that only re-export from a package (e.g., `ado2gh/cli.py`) MUST be removed with entry points updated to reference the package directly
- **FR-027**: LLM-related modules in `ado2gh/api/` MUST be consolidated into an `ado2gh/api/llm/` subpackage with a clear public API in `__init__.py`
- **FR-028**: Credential-related modules in `ado2gh/api/` MUST be consolidated into an `ado2gh/api/credentials/` subpackage with a clear public API in `__init__.py`
- **FR-029**: The `ado2gh/infra/` directory MUST be merged into `ado2gh/core/` — `concurrency.py`, `sessions.py`, and `queue/` relocate to `ado2gh/core/`; the `state/` subdirectory consolidates into `ado2gh/state/`. The `ado2gh/infra/` directory is removed

#### UI consolidation

- **FR-030**: UI pages MUST be consolidated per spec 008's unified tab structure (Discovery + Migrate + Agent + Settings)
- **FR-031**: Dead UI routes (pages with no inbound navigation links) MUST be identified and either removed or marked as intentionally unlinked
- **FR-032**: Removed UI routes MUST have redirect rules or a 404 page guiding users to the new location

#### Scripts & documentation

- **FR-033**: Scripts that purely wrap existing CLI commands MUST be removed with a migration note in `docs/COMMAND_REFERENCE.md`
- **FR-034**: Only a minimal `scripts/dev/` directory (2-3 files) MUST remain, containing only local-dev startup helpers for testing without Docker rebuild
- **FR-035**: `CLAUDE.md`, `README.md`, and all `docs/` files MUST be updated to reflect the simplified structure — no referenced file, command, or path may be stale

#### Enterprise readiness

- **FR-036**: `ruff` configuration MUST be added to `pyproject.toml` with a baseline enforcement strategy (existing violations grandfathered, new violations blocked)
- **FR-037**: `mypy` configuration MUST be added to `pyproject.toml` with a gradual enforcement strategy (existing type errors grandfathered, new errors blocked)
- **FR-038**: CI workflow (`.github/workflows/ci.yml`) MUST enforce ruff, mypy, and `pytest --cov=ado2gh --cov-fail-under=85`
- **FR-039**: `.env.example` MUST document every environment variable referenced in the codebase — a script or CI check MUST verify completeness
- **FR-040**: A `pre-commit` configuration MUST be added with ruff, basic formatting checks, and large-file prevention hooks
- **FR-041**: `pyproject.toml` MUST include proper project metadata: license, classifiers, Python version constraints, and optional dependency groups

### Key Entities *(include if feature involves data)*

- **AuditReport**: Conceptual entity (not a buildable artifact) representing the categorized file inventory produced by the agent-driven audit. Contains: `required_files` (with import evidence), `redundant_files` (with duplicate rationale), `dead_files` (with zero-import evidence), `artifacts` (with gitignore status), and `summary` (counts by category). The agent uses this classification internally during implementation; no persistent data structure is created.
- **ChangeRecord**: Entry in `docs/STRUCTURAL_CHANGELOG.md`. Contains: `file_path` (before), `new_path` (after, if moved), `change_type` (moved, renamed, split, merged, deleted), `reason`, `timestamp`, `verified` (whether live import verification was performed), and `test_status` (pass/fail/skipped).
- **SpecLifecycleEntry**: Entry in `specs/README.md`. Contains: `spec_id`, `title`, `status` (active, archived, superseded), `implementation_pointer` (for archived specs), and `one_line_summary`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The agent-driven audit identifies 100% of files in the repository and correctly classifies each as required, redundant, dead, or artifact
- **SC-002**: Total repository file count (excluding `.git/`, `node_modules/`, `__pycache__/`, `.venv/`) is reduced by at least 20% after dead code and redundancy elimination
- **SC-003**: State layer code is reduced by at least 40% (measured by total line count in `ado2gh/state/`)
- **SC-004**: No Python file in `ado2gh/` or `services/` exceeds 800 lines after decomposition (excluding tests, generated code, `__init__.py`)
- **SC-005**: Docker Compose files are reduced from 4 to 2 (serverless removed entirely, lightweight merged as profile)
- **SC-006**: UI page directories are reduced by at least 30% (from 14 to 9 or fewer; target is 7)
- **SC-007**: All existing tests pass after every structural change — zero test regressions
- **SC-008**: `ruff check ado2gh/` and `mypy ado2gh/` both execute successfully (with baseline exclusions for existing violations)
- **SC-009**: CI enforces ruff, mypy, and 85% coverage gate — a PR that introduces a new lint violation, type error, or coverage drop is blocked
- **SC-010**: `.env.example` documents 100% of environment variables referenced in the codebase (verified by automated check)
- **SC-011**: `specs/README.md` provides a complete and accurate index of all specs with their lifecycle status
- **SC-012**: No stale references in documentation — every file path, command, and script referenced in `CLAUDE.md`, `README.md`, and `docs/` exists in the repository
- **SC-013**: The `pre-commit` hooks run in under 10 seconds on a typical commit (defined as a commit touching ≤10 staged files; ruff + formatting + large-file check)
- **SC-014**: Repository clone size (excluding `.git/`) is reduced by removing the committed `.venv` and other large artifacts from disk

## Assumptions

- The existing test suite provides sufficient coverage to catch regressions from structural changes (if gaps exist, tests are added before restructuring)
- Dynamic imports are limited and can be detected by grepping for `importlib`, `__import__`, and `importlib.import_module` patterns
- The `ado2gh/__pycache__/ib-ai-agent/` directory is a stray artifact from a previous experiment and is safe to delete (it is already gitignored)
- Docker Compose profiles are supported by the target deployment environments (Docker Compose v2+)
- The `ado2gh/cli.py` wrapper exists only for backward compatibility with entry points and can be replaced by updating `pyproject.toml`
- Spec 001's implemented portions can be identified by cross-referencing spec status with code existence
- The 85% coverage threshold from the constitution is achievable with the existing test suite or by adding targeted tests for uncovered paths
- `ruff` and `mypy` baseline exclusions are acceptable as a gradual enforcement strategy — the goal is to prevent new violations, not fix all existing ones in one pass
- Root-level files like `cloud_credentials.json`, `llm_models.json`, and `ui_settings.json` are runtime artifacts that MUST be gitignored, not tracked in git
- The consolidation work is done incrementally with tests passing after each step, not as a single large change
- The DynamoDB backend has insufficient deployment demand to justify maintenance overhead — SQLite (local) and PostgreSQL (prod) cover all target deployment scenarios
- The `scripts/` directory is reduced to a minimal `scripts/dev/` (2-3 files) for local development without Docker; all CLI-wrapping scripts are removed
- The structural change log lives in `docs/STRUCTURAL_CHANGELOG.md` as a markdown file — simple, PR-reviewable, and sufficient for a one-time process
- The `settings_store.py` file is decomposed alongside other monolithic files — it contains mixed settings concerns (LLM, connectivity, cloud credentials, profile governance) that should be separated
- The `ado2gh/infra/` directory is merged into `ado2gh/core/` — runtime infrastructure belongs with orchestration and engine logic; `infra/state/` consolidates into `ado2gh/state/`
- Tests are updated incrementally alongside each structural change — each commit includes both the structural change and the corresponding test import fixes, keeping the suite green throughout
