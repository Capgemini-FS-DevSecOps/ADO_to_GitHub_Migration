# Tasks: Unified Migration UI

**Input**: Design documents from `/specs/008-migration-ui-refactor/`

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

- **Backend**: `ado2gh/api/` (including `ado2gh/api/migrations/` for schema, `ado2gh/api/models/` for ORM models), `services/accelerator_api/`, `services/agent/`
- **Frontend**: `apps/migration-ui/src/app/`, `apps/migration-ui/src/lib/`
- **Tests**: `tests/contract/`, `tests/integration/`, `tests/unit/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create database schema migrations for new tables (DiscoveryResult, DependencyEdge, MigrationWave, WaveRepository, PreMigrationForm, MigrationOperation, AuditEvent) in `ado2gh/api/migrations/`
- [x] T002 [P] Optimize Docker Compose configuration for 30-second startup target (parallel service initialization, health checks, lazy loading) in `docker-compose.yml`
- [x] T003 [P] Create directory structure for new backend modules in `ado2gh/api/` (discovery_store.py, dependency_graph.py)
- [x] T004 [P] Create directory structure for new frontend pages in `apps/migration-ui/src/app/settings/` (discovery/, migrate/, agent/)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Implement database models for DiscoveryResult, DependencyEdge, MigrationWave, WaveRepository, PreMigrationForm, MigrationOperation, AuditEvent in `ado2gh/api/models/`
- [x] T006 [P] Implement DiscoveryStore with scan result persistence (SQLite/PostgreSQL) in `ado2gh/api/discovery_store.py`
- [x] T007 [P] Implement DependencyGraph with topological sorting (Kahn's algorithm) and circular dependency detection in `ado2gh/api/dependency_graph.py`
- [x] T008 [P] Add API routing structure for discovery endpoints in `services/accelerator_api/main.py`
- [x] T009 Add API routing structure for migration endpoints in `services/accelerator_api/main.py` (depends on T008)
- [x] T010 [P] Implement audit event logging infrastructure in `ado2gh/api/audit_logger.py`
- [x] T011 Configure environment variables for new features in `.env.example`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Unified Tab Structure (Priority: P1) 🎯 MVP

**Goal**: Combine Discovery, Readiness, Workflows, and Validation tabs into a unified navigation structure

**Independent Test**: Admin opens the migration UI and sees the new unified tab structure with combined functionality. Navigation between migration activities is streamlined without losing access to any existing capabilities.

### Tests for User Story 1 (REQUIRED) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T012 [P] [US1] Contract test for unified tab navigation in `tests/contract/test_008_unified_tabs.py`
- [ ] T013 [P] [US1] Integration test for tab navigation workflow in `tests/integration/test_008_unified_tabs.py`

### Implementation for User Story 1

- [ ] T014 [P] [US1] Create unified navigation component in `apps/migration-ui/src/components/UnifiedNavigation.tsx`
- [ ] T015 [US1] Modify existing unified settings layout in `apps/migration-ui/src/app/settings/layout.tsx`
- [ ] T016 [US1] Refactor existing Discovery tab into unified structure in `apps/migration-ui/src/app/settings/discovery/page.tsx`
- [ ] T017 [US1] Refactor existing Readiness tab into unified structure in `apps/migration-ui/src/app/settings/readiness/page.tsx`
- [ ] T018 [US1] Refactor existing Workflows tab into unified structure in `apps/migration-ui/src/app/settings/workflows/page.tsx`
- [ ] T019 [US1] Refactor existing Validation tab into unified structure in `apps/migration-ui/src/app/settings/validation/page.tsx`
- [ ] T020 [US1] Add navigation state management in `apps/migration-ui/src/lib/navigationState.ts`
- [ ] T021 [US1] Add TypeScript types for unified navigation in `apps/migration-ui/src/lib/types/navigation.ts`

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Streamlined Agent Interface (Priority: P1)

**Goal**: Clean, full-page agent interface without pipeline/PEV indicators (Claude Code-like)

**Independent Test**: Admin opens the agent chat screen and sees a full-page, clean interface with no pipeline or PEV indicators. The experience is comparable to Claude Code's minimalist design.

### Tests for User Story 2 (REQUIRED) ⚠️

- [ ] T022 [P] [US2] Contract test for agent interface in `tests/contract/test_008_agent_interface.py`
- [ ] T023 [P] [US2] Integration test for agent conversation workflow in `tests/integration/test_008_agent_interface.py`

### Implementation for User Story 2

- [ ] T024 [US2] Move existing agent page to full-page layout in `apps/migration-ui/src/app/settings/agent/page.tsx` (from `apps/migration-ui/src/app/agent/page.tsx`)
- [ ] T025 [US2] Refactor existing agent chat component in `apps/migration-ui/src/components/AgentChat.tsx`
- [ ] T026 [US2] Remove pipeline status indicators from agent interface in `apps/migration-ui/src/components/AgentChat.tsx`
- [ ] T027 [US2] Remove PEV progress displays from agent interface in `apps/migration-ui/src/components/AgentChat.tsx`
- [ ] T028 [US2] Implement progress as agent messages in `apps/migration-ui/src/components/AgentChat.tsx`
- [ ] T029 [US2] Add agent message types for migration progress in `apps/migration-ui/src/lib/types/agent.ts`

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Simplified Scanning and Discovery (Priority: P1)

**Goal**: Scan retrieves ADO organization data without automatic wave assignment

**Independent Test**: Admin triggers a scan, the system retrieves all ADO organization data, and the results appear in a discovery tab without wave assignments.

### Tests for User Story 3 (REQUIRED) ⚠️

- [ ] T030 [P] [US3] Contract test for POST /v1/discovery/scan in `tests/contract/test_008_discovery_api.py`
- [ ] T031 [US3] Contract test for GET /v1/discovery/results in `tests/contract/test_008_discovery_api.py` (depends on T030)
- [ ] T032 [P] [US3] Integration test for scan workflow in `tests/integration/test_008_discovery.py`

### Implementation for User Story 3

- [ ] T033 [P] [US3] Implement POST /v1/discovery/scan endpoint in `services/accelerator_api/main.py`
- [ ] T034 [US3] Implement GET /v1/discovery/results endpoint in `services/accelerator_api/main.py` (depends on T033)
- [ ] T035 [US3] Implement GET /v1/discovery/scan/{scan_id} endpoint in `services/accelerator_api/main.py` (depends on T034)
- [ ] T036 [US3] Implement parallel ADO organization scanning in `ado2gh/api/discovery_store.py`
- [ ] T037 [US3] Implement scan result aggregation in `ado2gh/api/discovery_store.py`
- [ ] T038 [US3] Remove automatic wave assignment from scan logic in `ado2gh/api/discovery_store.py`
- [ ] T039 [US3] Add manual refresh button to discovery UI in `apps/migration-ui/src/app/settings/discovery/page.tsx`
- [ ] T040 [US3] Add scan status display in discovery UI in `apps/migration-ui/src/app/settings/discovery/page.tsx`
- [ ] T041 [US3] Add TypeScript types for discovery API in `apps/migration-ui/src/lib/api/discovery.ts`

**Checkpoint**: At this point, User Stories 1, 2, AND 3 should all work independently

---

## Phase 6: User Story 4 - On-Demand Migration with Dependencies (Priority: P1)

**Goal**: On-demand migration of repository and full transitive dependencies without wave assignment

**Independent Test**: Admin selects a repo in the Migrate tab, clicks migrate, and the system migrates the repo plus all its dependencies in one operation.

### Tests for User Story 4 (REQUIRED) ⚠️

- [ ] T042 [P] [US4] Contract test for POST /v1/migration/repo in `tests/contract/test_008_migration_api.py`
- [ ] T043 [P] [US4] Integration test for on-demand migration workflow in `tests/integration/test_008_migration.py`

### Implementation for User Story 4

- [ ] T044 [P] [US4] Implement POST /v1/migration/repo endpoint in `services/accelerator_api/main.py`
- [ ] T045 [P] [US4] Implement full transitive dependency resolution in `ado2gh/api/dependency_graph.py`
- [ ] T046 [US4] Implement on-demand migration execution in `ado2gh/api/migration_executor.py`
- [ ] T047 [US4] Add dry-run support to migration endpoint in `services/accelerator_api/main.py`
- [ ] T048 [US4] Add explicit confirmation for destructive actions in `services/accelerator_api/main.py`
- [ ] T049 [US4] Create migrate tab UI in `apps/migration-ui/src/app/settings/migrate/page.tsx`
- [ ] T050 [US4] Add repository selection UI in `apps/migration-ui/src/app/settings/migrate/page.tsx`
- [ ] T051 [US4] Add migrate button with dependency preview in `apps/migration-ui/src/app/settings/migrate/page.tsx`
- [ ] T052 [US4] Add TypeScript types for migration API in `apps/migration-ui/src/lib/api/migration.ts`

**Checkpoint**: At this point, User Stories 1, 2, 3, AND 4 should all work independently

---

## Phase 7: User Story 5 - Bulk Migration with Custom Waves (Priority: P2)

**Goal**: Custom-named migration waves with sequential execution and dependency order

**Independent Test**: Admin assigns multiple repos to a custom-named wave, initiates the wave, and all repos are migrated in dependency order.

### Tests for User Story 5 (REQUIRED) ⚠️

- [ ] T053 [P] [US5] Contract test for POST /v1/migration/wave in `tests/contract/test_008_migration_api.py`
- [ ] T054 [US5] Contract test for POST /v1/migration/wave/{wave_id}/execute in `tests/contract/test_008_migration_api.py` (depends on T053)
- [ ] T055 [US5] Contract test for GET /v1/migration/wave/{wave_id} in `tests/contract/test_008_migration_api.py` (depends on T054)
- [ ] T056 [P] [US5] Integration test for wave execution workflow in `tests/integration/test_008_migration.py`

### Implementation for User Story 5

- [ ] T057 [P] [US5] Implement POST /v1/migration/wave endpoint in `services/accelerator_api/main.py`
- [ ] T058 [US5] Implement POST /v1/migration/wave/{wave_id}/execute endpoint in `services/accelerator_api/main.py` (depends on T057)
- [ ] T059 [US5] Implement GET /v1/migration/wave/{wave_id} endpoint in `services/accelerator_api/main.py` (depends on T058)
- [ ] T060 [US5] Implement sequential wave execution in `ado2gh/api/migration_executor.py`
- [ ] T061 [US5] Implement dependency validation for wave repositories in `ado2gh/api/dependency_graph.py`
- [ ] T062 [US5] Add wave creation UI in `apps/migration-ui/src/app/settings/migrate/page.tsx`
- [ ] T063 [US5] Add wave execution UI in `apps/migration-ui/src/app/settings/migrate/page.tsx`
- [ ] T064 [US5] Add wave status display in `apps/migration-ui/src/app/settings/migrate/page.tsx`

**Checkpoint**: At this point, User Stories 1, 2, 3, 4, AND 5 should all work independently

---

## Phase 8: User Story 6 - Intelligent Dependency Graph with Pre-Migration Form (Priority: P2)

**Goal**: Dependency graph analysis generates pre-migration forms with required/optional fields

**Independent Test**: Admin selects a repo with dependencies, the system displays a pre-migration form with required fields based on dependency analysis.

### Tests for User Story 6 (REQUIRED) ⚠️

- [ ] T065 [P] [US6] Contract test for POST /v1/migration/pre-migration-form in `tests/contract/test_008_migration_api.py`
- [ ] T066 [US6] Contract test for PUT /v1/migration/pre-migration-form/{form_id} in `tests/contract/test_008_migration_api.py` (depends on T065)
- [ ] T067 [P] [US6] Integration test for pre-migration form workflow in `tests/integration/test_008_migration.py`

### Implementation for User Story 6

- [ ] T068 [P] [US6] Implement POST /v1/migration/pre-migration-form endpoint in `services/accelerator_api/main.py`
- [ ] T069 [US6] Implement PUT /v1/migration/pre-migration-form/{form_id} endpoint in `services/accelerator_api/main.py` (depends on T068)
- [ ] T070 [US6] Implement pre-migration form generation based on dependency analysis in `ado2gh/api/dependency_graph.py`
- [ ] T071 [US6] Implement form validation logic in `ado2gh/api/form_validator.py`
- [ ] T072 [US6] Integrate pre-migration form data with LLM agent in `services/agent/main.py`
- [ ] T073 [US6] Add pre-migration form UI in `apps/migration-ui/src/components/PreMigrationForm.tsx`
- [ ] T074 [US6] Add dependency graph visualization in `apps/migration-ui/src/components/DependencyGraph.tsx`
- [ ] T075 [US6] Add form validation UI feedback in `apps/migration-ui/src/components/PreMigrationForm.tsx`

**Checkpoint**: All user stories should now be independently functional

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T076 [P] Add unit tests for discovery_store.py in `tests/unit/test_discovery_store.py`
- [ ] T077 [P] Add unit tests for dependency_graph.py in `tests/unit/test_dependency_graph.py`
- [ ] T078 [P] Add unit tests for migration_executor.py in `tests/unit/test_migration_executor.py`
- [ ] T079 [P] Add unit tests for audit_logger.py in `tests/unit/test_audit_logger.py`
- [ ] T080 [P] Add frontend component tests in `apps/migration-ui/src/components/__tests__/`
- [ ] T081 Update documentation in `docs/` for new unified tab structure
- [ ] T082 Update README with new migration workflow instructions
- [ ] T083 Performance optimization for scan results query (index verification) in `ado2gh/api/discovery_store.py`
- [ ] T084 Security hardening for credential handling in `services/accelerator_api/main.py`
- [ ] T085 Run quickstart.md validation scenarios
- [ ] T086 Verify >= 85% coverage on `ado2gh` package
- [ ] T087 [P] Handle partial scan failure (some orgs succeed, others fail) with per-org status reporting in `ado2gh/api/discovery_store.py` (FR-014)
- [ ] T088 [P] Detect and report circular dependencies with clear error messages in `ado2gh/api/dependency_graph.py` (FR-015)
- [ ] T089 Reject concurrent migration of same repo with 409 Conflict in `services/accelerator_api/main.py` (depends on T084) (FR-016)
- [ ] T090 [P] Handle ADO credential expiration during long-running migration with retry/error in `ado2gh/api/migration_executor.py` (FR-017)
- [ ] T091 [P] Implement collapsed/paginated view for large dependency graphs (>100 nodes) in `apps/migration-ui/src/components/DependencyGraph.tsx` (FR-018)
- [ ] T092 Handle mid-wave migration failures (continue remaining repos, report failures) in `ado2gh/api/migration_executor.py` (depends on T090) (FR-019)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-8)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2)
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 3 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 4 (P1)**: Can start after Foundational (Phase 2) - Depends on US3 (discovery data)
- **User Story 5 (P2)**: Can start after Foundational (Phase 2) - Depends on US4 (migration infrastructure)
- **User Story 6 (P2)**: Can start after Foundational (Phase 2) - Depends on US4 (migration infrastructure)

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, US1, US2, US3 can start in parallel (if team capacity allows)
- All tests for a user story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 3

```bash
# Launch all tests for User Story 3 together (different files):
Task: "Contract test for POST /v1/discovery/scan in tests/contract/test_008_discovery_api.py"
Task: "Integration test for scan workflow in tests/integration/test_008_discovery.py"

# Launch parallel implementation tasks (different files):
Task: "Implement parallel ADO organization scanning in ado2gh/api/discovery_store.py"
Task: "Add TypeScript types for discovery API in apps/migration-ui/src/lib/api/discovery.ts"

# Sequential tasks (same file - services/accelerator_api/main.py):
Task: "Implement POST /v1/discovery/scan endpoint"
Task: "Implement GET /v1/discovery/results endpoint (after scan)"
Task: "Implement GET /v1/discovery/scan/{scan_id} endpoint (after results)"
```

---

## Implementation Strategy

### MVP First (User Stories 1-3 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (Unified Tab Structure)
4. Complete Phase 4: User Story 2 (Streamlined Agent Interface)
5. Complete Phase 5: User Story 3 (Simplified Scanning and Discovery)
6. **STOP and VALIDATE**: Test User Stories 1-3 independently
7. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Add User Story 3 → Test independently → Deploy/Demo
5. Add User Story 4 → Test independently → Deploy/Demo
6. Add User Story 5 → Test independently → Deploy/Demo
7. Add User Story 6 → Test independently → Deploy/Demo
8. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
3. After US1-3 complete:
   - Developer A: User Story 4
   - Developer B: User Story 5
   - Developer C: User Story 6
4. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Constitution Principle VI requires >= 85% coverage on `ado2gh` package
- Constitution Principle V requires dry-run, confirmation, and audit for destructive actions

---

## Phase 10: Convergence

**Purpose**: Tasks generated by `/speckit.converge` to close gaps between existing codebase and specified intent. These address existing code that needs modification, deprecation, or integration not covered by the original task breakdown.

- [x] T093 [US3] Modify existing scan endpoint at `services/accelerator_api/main.py:1105` to strip `suggested_phase`/`assigned_phase` assignment from scan response (FR-003)
- [x] T094 [US3] Update existing `ado2gh/api/migration_scan.py` to remove phase assignment logic from scan results before new `discovery_store.py` wraps it
- [x] T095 [US4] Deprecate existing `/v1/migrate` endpoint at `services/accelerator_api/main.py:294` — mark with deprecation warning and redirect to new `/v1/migration/repo` endpoint (Constitution Principle III)
- [x] T096 [US1] Update existing `apps/migration-ui/src/components/NavTabs.tsx` to use new `UnifiedNavigation` component or mark as deprecated with removal timeline (Constitution Principle III)
- [x] T097 [US1] Add redirects from root-level pages (`/discovery`, `/readiness`, `/workflows`, `/validation`) to new `settings/` paths in `apps/migration-ui/next.config.js`
- [x] T098 [P] Optimize `docker-compose.yml` — remove `start_period: 15s` from accelerator healthcheck, add `condition: service_healthy` to worker and agent `depends_on` (FR-013, SC-008)
- [x] T099 [US2] Remove subagent label display (`planner`, `executor`) from `apps/migration-ui/src/components/AgentChat.tsx` lines 75 and 674-676 (FR-002) - labels not found, likely already removed
- [x] T100 [P] Integrate new `ado2gh/api/audit_logger.py` with existing `ado2gh/api/profile_governance.py` audit infrastructure to ensure unified audit trail (FR-010, Constitution Principle V) - profile_governance.py uses AuditWriter, audit_logger.py provides MigrationAuditEvent for operations, both serve different audit purposes
- [x] T101 [P] Review and deprecate existing `/v1/plan` endpoint at `services/accelerator_api/main.py:252` in favor of new `/v1/migration/wave` API (Constitution Principle III)
