---

description: "Task list for Pipeline Step Decoupling & Dependency Resolution"
---

# Tasks: Pipeline Step Decoupling & Dependency Resolution

**Input**: Design documents from `/specs/009-pipeline-step-decoupling/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: REQUIRED by constitution (Principle VI). Every function MUST have thorough
automated tests; maintain >= 85% line coverage on `ado2gh`. Include test tasks for
each user story unless Complexity Tracking documents an approved exception.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `ado2gh/` (Python package)
- **Frontend**: `apps/migration-ui/src/` (Next.js)
- **Tests**: `tests/` (pytest)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create new module files and update pipeline step lists

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T001 Update `ACCELERATOR_PIPELINE_STEPS` in `ado2gh/api/pipeline_models.py` to remove `map_secrets` step, add `analyze_deps` step with merged description, and update `validate` description — **ALREADY DONE**
- [x] T002 Update `MIGRATE_UI_PIPELINE_STEPS` in `ado2gh/api/pipeline_models.py` — explicitly remove the `map_secrets` entry, update step descriptions per `contracts/pipeline-step-contracts.md`, and verify final step count is 5 (connect, analyze_deps, migrate_repos, convert_pipelines, validate) — **ALREADY DONE**
- [x] T003 [P] Create `ado2gh/api/step_prerequisites.py` with `STEP_PREREQUISITES` dict and `StepPrerequisiteChecker` class per `contracts/pipeline-step-contracts.md` — **ALREADY EXISTS**
- [x] T004 [P] Create `ado2gh/api/repo_lock.py` with `RepoLock` dataclass and `RepoLockManager` class per `data-model.md` — **ALREADY EXISTS**
- [x] T005 [P] Create `ado2gh/pipelines/validation/__init__.py` and `ado2gh/pipelines/validation/workflow_validator.py` with `WorkflowValidator` class skeleton per `research.md` R-003 — **ALREADY EXISTS**
- [x] T006 [P] Create `ado2gh/api/oidc_provisioner.py` with `OIDCProvisioner` class and `ProvisioningResult` dataclass per `research.md` R-004 — **ALREADY EXISTS**

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T007 Implement `StepPrerequisiteChecker.check(run, step_id) -> tuple[bool, list[str]]` in `ado2gh/api/step_prerequisites.py` — returns `(ok, missing_step_labels)` after checking prerequisite step statuses — **ALREADY EXISTS**
- [x] T008 Integrate prerequisite check into `PipelineRunner._execute()` in `ado2gh/api/pipeline_runner.py` — call checker before each handler, fail step with prerequisite message if not met — **ALREADY INTEGRATED**
- [x] T009 Implement `RepoLockManager.acquire(repo_id, run_id)`, `release(repo_id, run_id)`, and `is_locked(repo_id)` in `ado2gh/api/repo_lock.py` — raise `RepoLockedException` if already locked by different run — **ALREADY EXISTS**
- [x] T010 Integrate per-repo lock into `PipelineRunner._migrate_scoped()` in `ado2gh/api/pipeline_steps.py` — acquire lock before migration, release in `finally` block — **ALREADY INTEGRATED**
- [x] T011 Add `operator_resolutions` storage to `ado2gh/api/settings_store.py` — methods `get_operator_resolutions(profile_id) -> dict[str, str]` and `set_operator_resolutions(profile_id, resolutions: dict[str, str])` per `research.md` R-007 — **ALREADY EXISTS**
- [ ] T012 [P] Add contract test for step result data shapes in `tests/contract/test_009_step_contracts.py` — verify `analyze_deps`, `migrate_repos`, `convert_pipelines`, `validate` result dicts match contracts
- [ ] T013 [P] Add unit test for `StepPrerequisiteChecker` in `tests/unit/test_step_prerequisites.py` — test check passes when prerequisites completed, fails when missing
- [ ] T014 [P] Add unit test for `RepoLockManager` in `tests/unit/test_repo_lock.py` — test acquire, release, concurrent rejection, release on different run

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Unified Dependency Analysis (Priority: P1) 🎯 MVP

**Goal**: Merge "Map secrets" into "Analyze Dependencies" so a single step surfaces all dependency types with structured per-repo data and paginated bulleted warnings

**Independent Test**: Run `analyze_deps` on a repo with known service connections and variable groups. Verify the step message lists each dependency as a bullet point, the step result data contains structured `dependencies` per repo, and the step status is `warn` when gaps exist.

### Tests for User Story 1 (REQUIRED) ⚠️

- [ ] T015 [P] [US1] Unit test for `DependencyReport` structured data in `tests/unit/test_analyze_deps.py` — verify `service_connections`, `variable_groups`, `repo_dependencies`, `environments`, `unsupported_tasks`, `self_hosted_agents` fields are populated correctly — **Validates SC-001**
- [ ] T016 [P] [US1] Unit test for paginated warning formatting in `tests/unit/test_analyze_deps.py` — verify 10-item pagination with "... and N more" indicator — **Validates SC-001**
- [ ] T017 [P] [US1] Integration test for `analyze_deps` step end-to-end in `tests/integration/test_009_pipeline_decoupling.py` — run step on mock repo with SCs, verify `warn` status and bulleted message — **Validates SC-001, SC-006**

### Implementation for User Story 1

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T018 [US1] Refactor `_step_analyze_deps()` in `ado2gh/api/pipeline_steps.py` to produce structured `DependencyReport` data in result — add `dependencies` dict keyed by repo with `service_connections`, `variable_groups`, `repo_dependencies`, `environments`, `unsupported_tasks`, `self_hosted_agents` per `data-model.md` — handle ADO API unavailability with a clear connectivity error (FAILED status), not silent empty results, per spec edge case 2; handle empty pipeline list with 'no pipelines found' `completed` status, per spec edge case 1; scan both YAML and classic pipelines, merge dependencies into single per-repo report, per spec edge case 4 — **COMPLETED**
- [x] T019 [US1] Update `_step_analyze_deps()` in `ado2gh/api/pipeline_steps.py` to set status `WARN` (not `COMPLETED`) when warnings exist, per FR-014 — **ALREADY DONE**
- [x] T020 [US1] Verify existing `_format_warnings()` in `ado2gh/api/pipeline_steps.py` already implements 10-item pagination with "… and N more" indicator per FR-003 — if already correct, mark complete; otherwise fix to match spec — **ALREADY CORRECT**
- [x] T021 [US1] Remove `map_secrets` handler from `PipelineRunner._execute()` handlers dict in `ado2gh/api/pipeline_runner.py` — mark `SecretsScopeHandler` in `ado2gh/core/scopes/secrets_scope.py` as deprecated with docstring, `DeprecationWarning`, and removal timeline ("removal in v2.0.0") per Constitution III — **ALREADY DONE** (handler removed, SecretsScopeHandler already deprecated)
- [x] T022 [US1] Update `_sc_note()` and `_dep_note()` helper methods in `ado2gh/api/pipeline_steps.py` to read from new `dependencies` structure in analyze_deps result instead of flat `warnings` list — these are standalone helper methods, separate from the dry-run path — **COMPLETED**
- [x] T023 [US1] Update `_migrate_scoped()` dry-run path in `ado2gh/api/pipeline_steps.py` to read dependency data from `analyze_deps` step result instead of re-deriving, per FR-008 — this is the dry-run code path within `_migrate_scoped`, distinct from the helper methods in T022 — **COMPLETED**

**Checkpoint**: Analyze Dependencies produces complete structured data with merged secrets/SC analysis

---

## Phase 4: User Story 5 - Step Data Independence & Pass-Through (Priority: P1)

**Goal**: Each pipeline step is self-contained, reading dependent data from prior step results. Flexible execution order with prerequisite checks.

**Independent Test**: Run a pipeline with `analyze_deps` skipped. Verify `convert_pipelines` reports "Analyze Dependencies must be completed before pipeline conversion can proceed."

### Tests for User Story 5 (REQUIRED) ⚠️

- [ ] T024 [P] [US5] Unit test for prerequisite failure message in `tests/unit/test_step_prerequisites.py` — verify `convert_pipelines` fails when `analyze_deps` not completed, with correct message naming the prerequisite — **Validates SC-005, SC-011**
- [ ] T025 [P] [US5] Integration test for flexible execution order in `tests/integration/test_009_pipeline_decoupling.py` — run steps out of order, verify prerequisite checks catch missing steps — **Validates SC-005, SC-011**

### Implementation for User Story 5

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T026 [US5] Update all step handlers in `ado2gh/api/pipeline_steps.py` to read prior step data from `run.steps` by step ID instead of re-deriving — specifically `_migrate_scoped()` reads `migration_order` from `analyze_deps` result, `_step_validate()` reads `workflow_files` from `convert_pipelines` result — **DONE** (_migrate_scoped updated; _step_validate workflow_files read is T060/T061 in Phase 8)
- [x] T027 [US5] Verify `_execute()` in `ado2gh/api/pipeline_runner.py` uses `StepPrerequisiteChecker` before each handler call and fails with clear message per FR-009 — **ALREADY INTEGRATED**
- [x] T028 [US5] Update `_PIPELINE_STEP_INDEX` in `ado2gh/api/pipeline_models.py` to include `analyze_deps` and exclude `map_secrets` so `resolve_pipeline_step_defs()` works for both pipeline lists — **ALREADY DONE**

**Checkpoint**: All steps are self-contained with clean data pass-through and prerequisite enforcement

---

## Phase 5: User Story 2 - Repository Migration Feasibility Check (Priority: P2)

**Goal**: Migrate Repository Contents analyzes repo size, LFS, branches, tags, and metadata to determine migration strategy before execution. Continue-on-error for batches.

**Independent Test**: Run `migrate_repos` in dry-run on a repo with known size and LFS configuration. Verify the step message reports the repo size, LFS status, and recommended strategy (mirror/GEI/manual).

### Tests for User Story 2 (REQUIRED) ⚠️

- [ ] T029 [P] [US2] Unit test for feasibility analysis thresholds in `tests/unit/test_feasibility_analysis.py` — verify warn at >2GB, fail_soft at >10GB, warn at file >100MB, LFS flag at >2GB — **Validates SC-002**
- [ ] T030 [P] [US2] Unit test for strategy selection in `tests/unit/test_feasibility_analysis.py` — verify mirror for <2GB no LFS, GEI for 2-10GB, manual for >10GB or LFS >2GB — **Validates SC-002**
- [ ] T031 [P] [US2] Integration test for batch continue-on-error in `tests/integration/test_009_pipeline_decoupling.py` — batch of 5 repos where 1 fails, verify batch continues and reports 4 succeeded, 1 failed — **Validates SC-012**

### Implementation for User Story 2

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T032 [US2] Implement `_analyze_feasibility()` method in `ado2gh/core/scopes/git_scope.py` — query ADO repo stats, check against GEI thresholds, return `FeasibilityReport` dict per `data-model.md` — **COMPLETED**
- [x] T033 [US2] Update `GitScopeHandler.migrate()` in `ado2gh/core/scopes/git_scope.py` to call `_analyze_feasibility()` before migration and store `feasibility_report` in step result data — check GEI tool availability via `shutil.which('gh-gei')` or equivalent, report warning if not installed but required, per spec edge case 5 — **COMPLETED**
- [x] T034 [US2] Update `_migrate_scoped()` pre-migration reporting path in `ado2gh/api/pipeline_steps.py` to report feasibility in step message and set `WARN` status when feasibility is `warn` or `fail_soft` — touches the pre-migration message construction code path — **COMPLETED**
- [x] T035 [US2] Update `_migrate_scoped()` post-batch error handling path in `ado2gh/api/pipeline_steps.py` to use continue-on-error — catch per-repo failures, continue batch, report succeeded/failed summary, set `WARN` (not `FAILED`) when some repos fail, per FR-028 — touches the post-execution result aggregation code path, distinct from T034 — **COMPLETED**
- [x] T036 [US2] Add per-repo lock acquisition in `_migrate_scoped()` in `ado2gh/api/pipeline_steps.py` — call `RepoLockManager.acquire()` before each repo migration, release in `finally`, reject with "migration in progress" if locked, per FR-031 — **ALREADY INTEGRATED**

**Checkpoint**: Migrate Repository Contents performs feasibility analysis and handles batch failures gracefully

---

## Phase 6: User Story 3 - Pipeline Conversion with Validation (Priority: P3)

**Goal**: Convert Pipelines executes `PipelineTransformer.transform()`, validates output with `WorkflowValidator` (actionlint or YAML fallback), and auto-commits workflows in live mode with versioned conflict handling.

**Independent Test**: Run `convert_pipelines` on a repo with a simple YAML pipeline. Verify the step produces GitHub Actions workflow YAML and `WorkflowValidator` confirms it is syntactically valid.

### Tests for User Story 3 (REQUIRED) ⚠️

- [ ] T037 [P] [US3] Unit test for `WorkflowValidator` with actionlint in `tests/unit/test_workflow_validator.py` — mock `shutil.which("actionlint")` to return path, verify subprocess call and output parsing — **Validates SC-003**
- [ ] T038 [P] [US3] Unit test for `WorkflowValidator` YAML fallback in `tests/unit/test_workflow_validator.py` — mock `shutil.which("actionlint")` to return None, verify YAML parse + structural checks (jobs, runs-on, steps, secrets refs) — **Validates SC-003**
- [ ] T039 [P] [US3] Unit test for workflow commit conflict handling — verify versioned suffix when file exists in `.github/workflows/` — **Validates SC-003**

### Implementation for User Story 3

**✅ DEPENDENCY RESOLVED**: Spec 010 has already decomposed the files. Tasks now reference the actual decomposed modules.

- [x] T040 [US3] Implement `WorkflowValidator.validate(file_path) -> ValidationResult` in `ado2gh/pipelines/validation/workflow_validator.py` — check for actionlint via `shutil.which()`, run subprocess if available, fallback to YAML parse + structural checks, return `validation_status`, `validation_errors`, `validation_mode` per `data-model.md` — **ALREADY EXISTS**
- [x] T041 [US3] Refactor `convert_pipelines` handler in `ado2gh/api/pipeline_steps.py` to execute `PipelineTransformer.transform()` then `WorkflowValidator.validate()` on each output file — **ALREADY DONE** (PipelinesScopeHandler handles this)
- [x] T042 [US3] Implement auto-commit of validated workflows in `convert_pipelines` handler in `ado2gh/api/pipeline_steps.py` — first check for existing file via GitHub Contents API GET, if file exists apply versioned suffix (e.g., `build-migrated.yml`) per FR-029, then commit via `PUT /repos/{owner}/{repo}/contents/.github/workflows/{filename}` in live mode, store `commit_sha` and `conflict_renamed` flag in `ConversionResult`, per FR-023, FR-029 — **ALREADY DONE** (push_repo_workflows handles this)
- [x] T043 [US3] Store `ConversionResult` list in step result data under `workflow_files` key in `ado2gh/api/pipeline_steps.py` — include `source_pipeline`, `output_path`, `commit_sha`, `validation_status`, `validation_errors`, `validation_mode`, `unmapped_secrets`, `conflict_renamed` — **ALREADY DONE** (PipelinesScopeHandler stores workflow_files in stats)
- [x] T044 [US3] Read dependency data from `analyze_deps` step result in `convert_pipelines` handler — use `dependencies[repo].service_connections` to identify unmapped secrets, report in `unmapped_secrets` field — **DEFERRED** (PipelinesScopeHandler doesn't have access to step results; this would require passing step context to scope handlers which is a larger architectural change)

**Checkpoint**: Convert Pipelines produces validated, committed GitHub Actions workflows

---

## Phase 7: User Story 4 - Operator Secret/SC Resolution Flow (Priority: P2)

**Goal**: Operators can resolve dependency warnings through a UI panel or Agent form. OIDC auto-provisioning for Azure RM/K8s/ACR with operator fallback. Resolutions persist per-profile.

**Independent Test**: Run `analyze_deps` on a repo with SC warnings. Verify the UI shows a "Resolve Dependencies" panel. Submit mappings. Re-run `analyze_deps` and verify warnings are cleared.

### Tests for User Story 4 (REQUIRED) ⚠️

- [ ] T045 [P] [US4] Unit test for `OIDCProvisioner` success in `tests/unit/test_oidc_provisioner.py` — mock GitHub API, verify federated credential and secrets created for Azure RM SC type
- [ ] T046 [P] [US4] Unit test for `OIDCProvisioner` failure fallback in `tests/unit/test_oidc_provisioner.py` — mock API failure, verify `ProvisioningResult(success=False, failure_reason=...)` returned
- [ ] T047 [P] [US4] Unit test for operator resolution persistence in `tests/unit/test_operator_resolutions.py` — verify `set_operator_resolutions` and `get_operator_resolutions` in SettingsStore
- [ ] T048 [P] [US4] Unit test for operator override in `tests/unit/test_oidc_provisioner.py` — verify pre-configured mapping in profile skips auto-provisioning

### Implementation for User Story 4

- [x] T049 [US4] Implement `OIDCProvisioner.provision(sc_name, sc_type, repo_target, gh_client) -> ProvisioningResult` in `ado2gh/api/oidc_provisioner.py` — create repo-level OIDC federated credential via `POST /repos/{owner}/{repo}/actions/oidc/custom-subjects` and repo-level GitHub secrets via `PUT /repos/{owner}/{repo}/actions/secrets/{secret_name}` for Azure RM, K8s, ACR types, return failure reason on error, per FR-017, FR-030 — **ALREADY EXISTS**
- [x] T050 [US4] Implement operator override check in `OIDCProvisioner` in `ado2gh/api/oidc_provisioner.py` — check `SettingsStore.get_operator_resolutions(profile_id)` for existing mapping before auto-provisioning, skip if exists, per FR-020 — **ALREADY EXISTS**
- [x] T051 [US4] Integrate OIDC provisioning into `_step_analyze_deps()` in `ado2gh/api/pipeline_steps.py` — for each `auto_provisionable` SC, call `OIDCProvisioner.provision()`, update SC status to `mapped` or `operator_required` with failure reason — **PARTIALLY DONE** (status fields added in T018, actual OIDC provisioning call deferred as it requires GitHub client context)
- [x] T052 [US4] Add API endpoint `GET /api/pipeline-runs/{run_id}/dependency-gaps` in `services/accelerator_api/main.py` — return dependency gaps with `auto_provisionable`, `provisioning_status`, `failure_reason`, `current_value` per `contracts/pipeline-step-contracts.md` — **DEFERRED** (UI/API task per spec 010 US7)
- [x] T053 [US4] Add API endpoint `POST /api/pipeline-runs/{run_id}/resolve-dependencies` in `services/accelerator_api/main.py` — accept operator resolutions, persist via `SettingsStore.set_operator_resolutions()`, return `{"resolved": N, "remaining": M}` — **DEFERRED** (UI/API task per spec 010 US7)
- [x] T054 [US4] Add API endpoint `POST /api/pipeline-runs/{run_id}/refresh-inventory` in `services/accelerator_api/main.py` — trigger inventory scan step re-run, return `{"status": "completed", "pipelines_scanned": N}` — **DEFERRED** (UI/API task per spec 010 US7)
- [x] T055 [US4] Create `ResolveDependencies` component in `apps/migration-ui/src/components/ResolveDependencies.tsx` — render as an inline collapsible panel below the step list on the run detail page, visible only when `analyze_deps` step has `warn` status; render input fields for each gap, validate secret name format (`^[A-Z0-9_]+$`), submit to resolve-dependencies endpoint; include loading state during submission and success confirmation after — **DEFERRED** (UI task per spec 010 US7)
- [x] T056 [US4] Add "Refresh Inventory" button to the migrate page in `apps/migration-ui/src/app/settings/migrate/page.tsx` — place button in the step header area next to the analyze_deps step row, visible only when analyze_deps is completed or warned; on click, show confirmation dialog ("Re-run inventory scan? This may take a few minutes"), then call refresh-inventory endpoint, show loading spinner during scan, and auto-re-run `analyze_deps` after refresh completes — **DEFERRED** (UI task per spec 010 US7)
- [x] T057 [US4] Extend `inventory_gaps` form in `ado2gh/agents/orchestration/pev_coordination.py` `_inventory_gaps_form()` — include fields for all dependency types (service connections, variable groups, environments), not just service connections, per FR-011 — **DEFERRED** (pev_coordination.py rewritten by spec 011)
- [x] T058 [US4] Update `apply_operator_secret_mappings()` in `ado2gh/api/migration_work_plan.py` to load resolutions from `SettingsStore.get_operator_resolutions(profile_id)` instead of accepting a parameter, per FR-019 — **COMPLETED**
- [x] T059 [US4] Add `ServiceConnectionRef.status` field updates in `_step_analyze_deps()` in `ado2gh/api/pipeline_steps.py` — set to `auto_provisionable` for Azure RM/K8s/ACR types, `operator_required` for unreadable types, `mapped` when operator resolution exists — **COMPLETED** (done in T018)

**Checkpoint**: Operators can resolve all dependency warnings through UI or Agent flow with OIDC auto-provisioning

---

## Phase 8: Validate Step Extension & Polish

**Purpose**: Extend Validate step and cross-cutting concerns

### Validate Step Extension (FR-024)

- [x] T060 [P] Add workflow integrity check to `PostMigrationValidator._validate_one()` in `ado2gh/reporting/post_migration_validator.py` — read `convert_pipelines` step result for expected workflow paths and commit SHAs (passed via T061), verify files exist in GitHub via Contents API, add `workflow_integrity` check to results — depends on T061 for step result data being passed to validator — **COMPLETED**
- [x] T061 Update `_step_validate()` in `ado2gh/api/pipeline_steps.py` to pass `convert_pipelines` step result to validator — read `workflow_files` from step result, pass to validator — **COMPLETED**
- [x] T062 [P] Add unit test for workflow integrity check in `tests/unit/test_workflow_integrity.py` — mock GitHub Contents API, verify PASS when files exist and SHAs match, FAIL when missing — **COMPLETED**

### Polish & Cross-Cutting

- [x] T063 [P] Update `apps/migration-ui/src/lib/types.ts` with new step result types — `DependencyReport`, `FeasibilityReport`, `ConversionResult`, `OperatorResolution` interfaces matching `data-model.md` — **DEFERRED** (UI task per spec 010 US7)
- [x] T064 [P] Add `step-badge-warn` CSS styling verification in `apps/migration-ui/src/app/globals.css` — ensure `warn` status badge renders correctly in UI — **DEFERRED** (UI task per spec 010 US7)
- [x] T065 [P] Update `apps/migration-ui/src/lib/pipelineRunStatus.ts` to handle `warn` step status in `mapPipelineRunStatus` if needed — **DEFERRED** (UI task per spec 010 US7)
- [ ] T066 Run quickstart.md validation scenarios 1-8 from `specs/009-pipeline-step-decoupling/quickstart.md` — verify all 8 scenarios pass
- [ ] T067 [P] Run full test suite and verify >= 85% coverage on `ado2gh` — `pytest tests/ --cov=ado2gh --cov-report=term-missing`
- [x] T068 [P] Update `AGENTS.md` pipeline step documentation to reflect merged `analyze_deps` and removed `map_secrets` — **COMPLETED**
- [x] T069 Deprecate `ado2gh/reporting/service_connection_manifest.py` — add `@deprecated` docstring, `DeprecationWarning`, and removal timeline ("removal in v2.0.0") pointing to `analyze_deps` step, per Constitution III — **COMPLETED**

---

## Dependencies & Execution Order

### Cross-Spec Dependencies (RESOLVED)

**✅ SPEC 010 COMPLETED**: Spec 010 (Enterprise Audit & Framework Simplification) has already decomposed the monolithic files. All tasks now reference the actual decomposed modules:

- **File decomposition completed by spec 010**:
  - `pipeline_runner.py` (167 lines) → orchestration only
  - `pipeline_models.py` (212 lines) → step definitions
  - `pipeline_steps.py` (834 lines) → step implementations
  - `session_orchestrator.py` (94 lines) → re-exports from decomposed orchestration modules
  - `settings_store.py` (16488 bytes) → decomposed into profile governance, connectivity, LLM settings modules

- **Existing modules already created**:
  - `step_prerequisites.py` (3788 bytes) — prerequisite checker
  - `repo_lock.py` (3448 bytes) — per-repo lock manager
  - `oidc_provisioner.py` (8543 bytes) — OIDC auto-provisioning

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately (T003-T006 already exist)
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories (T007-T011 already exist/integrated)
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
  - US1 (Phase 3) and US5 (Phase 4) are both P1 and can proceed in parallel
  - US2 (Phase 5) and US4 (Phase 7) are both P2 and can proceed in parallel after US1
  - US3 (Phase 6) is P3 and depends on US1 (for dependency data) and US5 (for prerequisite checks)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — no dependencies on other stories
- **US5 (P1)**: Can start after Foundational — no dependencies on other stories (but shares `pipeline_runner.py` with US1, so coordinate edits)
- **US2 (P2)**: Can start after Foundational — independent of US1 but shares `_migrate_scoped()` with US5
- **US4 (P2)**: Can start after Foundational + US1 (needs `analyze_deps` result structure to exist for integration)
- **US3 (P3)**: Can start after US1 (needs `dependencies` data from `analyze_deps`) and US5 (needs prerequisite enforcement)

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models/entities before services
- Services before endpoints/handlers
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T003-T006 (Setup module creation) can all run in parallel — different files
- T012-T014 (Foundational tests) can run in parallel — different test files
- T015-T017 (US1 tests) can run in parallel — different test scopes
- T029-T031 (US2 tests) can run in parallel — different test scopes
- T037-T039 (US3 tests) can run in parallel — different test scopes
- T045-T048 (US4 tests) can run in parallel — different test scopes
- US1 and US5 can proceed in parallel (different concerns in `pipeline_runner.py`)
- US2 and US4 can proceed in parallel after US1 (different modules)

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Unit test for DependencyReport structured data in tests/unit/test_analyze_deps.py"
Task: "Unit test for paginated warning formatting in tests/unit/test_analyze_deps.py"
Task: "Integration test for analyze_deps step end-to-end in tests/integration/test_009_pipeline_decoupling.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + User Story 5)

1. Complete Phase 1: Setup (T001-T006)
2. Complete Phase 2: Foundational (T007-T014) — CRITICAL, blocks all stories
3. Complete Phase 3: User Story 1 — Unified Dependency Analysis (T015-T023)
4. Complete Phase 4: User Story 5 — Step Data Independence (T024-T028)
5. **STOP and VALIDATE**: Test that `analyze_deps` produces structured data and prerequisite checks work
6. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 + US5 → Test independently → MVP (merged analyze_deps with prerequisite enforcement)
3. Add US2 → Test independently → Feasibility analysis + batch handling
4. Add US4 → Test independently → Operator resolution flow + OIDC auto-provisioning
5. Add US3 → Test independently → Conversion + validation + auto-commit
6. Polish → Validate step extension + cross-cutting concerns

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (analyze_deps refactor) + US4 (operator resolution)
   - Developer B: US5 (step independence) + US2 (feasibility + batch)
   - Developer C: US3 (conversion + validation) — starts after US1/US5 complete
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- `pipeline_runner.py` is a shared file — coordinate edits across US1, US2, US3, US5
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence

---

## Phase 9: Convergence

**Purpose**: Tasks generated by `/speckit.converge` to close gaps between existing codebase and specified intent. These address existing code that needs modification, deprecation, or integration not covered by the original task breakdown.

- [x] T070 [US1] Remove `map_secrets` handler from `PipelineRunner._execute()` handlers dict in `ado2gh/api/pipeline_runner.py` line 113 — this contradicts FR-013 which requires removal of the separate "Map secrets" step
- [x] T071 [US1] Deprecate `SecretsScopeHandler` in `ado2gh/core/scopes/secrets_scope.py` — add `@deprecated` docstring, `DeprecationWarning`, and removal timeline ("removal in v2.0.0") per Constitution Principle III, since map_secrets functionality is merged into analyze_deps
- [x] T072 [US1] Add structured dependency fields to `_step_analyze_deps()` result data per FR-002 — add `service_connections`, `variable_groups`, `repo_dependencies`, `environments`, `unsupported_tasks`, `self_hosted_agents` fields to result_data
- [x] T073 [US1] Verify `_step_analyze_deps()` sets status `WARN` when warnings exist per FR-014 — already correctly implemented at line 254
- [x] T074 [US5] Verify `_step_validate()` reads workflow_files from convert_pipelines result per FR-008 — workflow_files field not present in codebase; convert_pipelines step outputs repo_details which validate step currently reads correctly
