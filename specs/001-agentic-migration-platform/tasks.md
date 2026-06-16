# Tasks: Agentic ADO-to-GitHub Migration Platform

**Input**: Design documents from `specs/001-agentic-migration-platform/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on `ado2gh`)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Plan sync**: 2026-06-16 — gates (FR-034), policy rules (FR-019), symmetric rollback (FR-026a), audit Postgres+S3 (FR-037), history UI+API (FR-039).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dev tooling, CI coverage gate, package scaffolding

- [x] T001 Add pytest-cov and 85% coverage gate to CI workflow in `.github/workflows/` (or caller workflow per `integrate-ci-cd`)
- [x] T002 [P] Add `pytest-cov` configuration in `pyproject.toml` (`--cov=ado2gh`, `--cov-fail-under=85`)
- [x] T003 [P] Create `ado2gh/assignments/` package with `__init__.py` per plan.md structure
- [x] T004 [P] Create `ado2gh/agents/` package with `skills/` subdirectory for subagent skill markdown
- [x] T005 [P] Create `ado2gh/pipelines/dependency_graph.py` module stub with package exports
- [x] T006 Document storage env vars (SQLite vs Postgres) in `README` aligned with `quickstart.md` §1

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: State schema, RBAC, audit, dependency graph, assignments, gates, policy rules — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [x] T007 [P] Extend `tests/test_storage_config.py` for Postgres parity on new tables
- [x] T008 [P] Add `tests/test_dependency_graph.py` for topo sort, cycle detection, empty graph
- [x] T009 [P] Add `tests/test_audit_redaction.py` for secret masking in audit payloads
- [x] T010 [P] Add `tests/test_gate_assignment.py` for assignment-scoped gate pass/fail/override (FR-034)
- [x] T011 [P] Add `tests/test_policy_rules.py` for FR-019 profile policy evaluation

### Implementation (Foundational)

- [x] T012 Extend SQLite schema in `ado2gh/state/db.py` for assignments, cohort membership, repo_dependency_edges, audit_events, workflow_dependency_checks, remediation_loops
- [x] T013 Mirror new tables in `ado2gh/state/postgres_db.py` with migration/init SQL
- [x] T014 Implement `ado2gh/pipelines/dependency_graph.py` — build edges, Kahn sort, cycle report
- [x] T015 Implement `ado2gh/assignments/models.py` dataclasses per `data-model.md`
- [x] T016 Implement `ado2gh/assignments/store.py` CRUD and assignment→execution phase resolution
- [x] T017 Implement RBAC helpers in `ado2gh/assignments/rbac.py` (Coordinator, Operator, Approver)
- [x] T018 Implement `ado2gh/assignments/audit.py` immutable append-only AuditEvent writer with redaction
- [x] T019 Implement workflow readiness checker in `ado2gh/api/workflow_readiness.py` with structured log lines (FR-051)
- [x] T020 Wire assignment-scoped `PhaseGateChecker` in `ado2gh/phase/gate_checker.py` using cohort repo list (FR-034)
- [x] T021 Implement `ado2gh/phase/policy_rules.py` and profile policy settings in `ado2gh/api/settings_store.py` (FR-019)
- [x] T022 Add assignments REST routes in `services/accelerator_api/main.py` per `contracts/assignments-api.md`
- [x] T023 Add dependency-graph endpoint in `services/accelerator_api/main.py` per `contracts/assignments-api.md`
- [x] T024 Add workflow-readiness endpoint returning `log_lines[]` per `contracts/dependency-logging-provisioning.md`
- [x] T025 Add gate-status and gate-override routes in `services/accelerator_api/main.py` per `contracts/rollback-gates-api.md`
- [x] T026 [P] Add `tests/test_assignments.py` for CRUD, single active membership, phase link
- [x] T027 Wire topo-sorted `repo_order` into `ado2gh/phase/batch_executor.py` plan output (read-only)

**Checkpoint**: Foundation ready — gates, assignments API, policy rules scaffolded

---

## Phase 3: User Story 1 — Manual Migration Accelerator (Priority: P1) 🎯 MVP

**Goal**: Operator completes discover → dry-run → live run → validate via CLI/UI/API without agent.

**Independent Test**: `quickstart.md` §2–3, §8 (gate), §9 (rollback dry-run) — dry-run/live with gate + Approver; dependency logs when missing.

### Tests for User Story 1

- [x] T028 [P] [US1] Golden tests for consolidated vs modular layout in `tests/test_pipeline_layout_policy.py`
- [x] T029 [P] [US1] Integration test for `push_workflows` dry-run in `tests/test_push_workflows.py`
- [x] T030 [P] [US1] Test topo order enforced in batch execution in `tests/test_batch_executor_topo.py`
- [x] T031 [P] [US1] Add `tests/test_rollback.py` for scope-targeted rollback and symmetric ADO re-enable (FR-026a, SC-016)

### Implementation for User Story 1

- [x] T032 [US1] Integrate dependency order into `ado2gh/phase/batch_executor.py` live execution path
- [x] T033 [US1] Enforce one active live run per repo in `ado2gh/core/migration_engine.py` (FR-036)
- [x] T034 [US1] Block live migrate/jobs when assignment gate `can_advance` is false in `services/accelerator_api/main.py` (FR-034)
- [x] T035 [US1] Evaluate `policy_rules` after base gate before live execution in `ado2gh/phase/batch_executor.py`
- [x] T036 [US1] Add `workflow_layout_policy` to profile settings in `ado2gh/api/settings_store.py`
- [x] T037 [US1] Extend `ado2gh/pipelines/transform/transformer.py` for consolidated vs modular layout
- [x] T038 [US1] Ensure `ado2gh/core/scopes/pipelines_scope.py` runs for all repos in cohort scope
- [x] T039 [US1] Gate live `push_workflows` on readiness + Approver in `ado2gh/tools/push_workflows.py`
- [x] T040 [US1] Add ADO pipeline disable post-push in `ado2gh/core/ado_cleanup.py` (FR-048)
- [x] T041 [US1] Implement rollback API `POST /v1/rollback` and approval flow in `services/accelerator_api/main.py` per `contracts/rollback-gates-api.md`
- [x] T042 [US1] Extend `ado2gh/core/rollback.py` for assignment-scoped scope-targeted rollback
- [x] T043 [US1] Wire symmetric ADO pipeline re-enable on `pipelines` rollback in `ado2gh/core/ado_cleanup.py` (FR-026a)
- [x] T044 [US1] Extend `ado2gh/api/accelerator.py` migrate endpoints for assignment-scoped runs
- [x] T045 [US1] Log missing dependencies to Rich console in `ado2gh/cli/` readiness wrappers
- [x] T046 [US1] Extend `ado2gh/api/pipeline_runner.py` for readiness + gate checks before migrate step
- [x] T047 [US1] Update `apps/migration-ui/src/app/migrate/page.tsx` for dry-run/live, gate badge, Approver request
- [x] T048 [US1] Add workflow branch/PR status on `apps/migration-ui/src/app/runs/MonitorClient.tsx`

**Checkpoint**: US1 — manual migrate, gates, rollback dry-run API path

---

## Phase 4: User Story 5 — Team Repository Assignments (Priority: P2)

**Goal**: Coordinator assigns repos to POC/pilot/Wave N; operators scope runs to cohort.

**Independent Test**: `quickstart.md` §5 — create Wave assignment; scoped dry-run only on cohort repos.

### Tests for User Story 5

- [x] T049 [P] [US5] API contract tests for assignments CRUD in `tests/test_assignments_api.py`
- [x] T050 [P] [US5] Test cohort resolution in `tests/test_assignment_resolver.py`

### Implementation for User Story 5

- [x] T051 [US5] Implement assignment resolver in `ado2gh/assignments/resolver.py`
- [x] T052 [US5] Add assignment list/create UI in `apps/migration-ui/src/app/assignments/page.tsx`
- [x] T053 [US5] Add cohort picker to `apps/migration-ui/src/app/migrate/page.tsx` and `apps/migration-ui/src/lib/api.ts`
- [x] T054 [US5] Record `assignment_id` on migration runs in `ado2gh/state/db.py`
- [x] T055 [US5] Block cross-cohort mutations in `ado2gh/core/migration_engine.py`
- [x] T056 [US5] Gate status badge on assignment detail UI components
- [x] T057 [US5] UI reassignment flow with audit trail in assignment management components

**Checkpoint**: US5 + US1 — scoped migration by cohort with gate visibility

---

## Phase 5: User Story 2 — Natural Language Migration Agent (Priority: P2)

**Goal**: Planner → Executor → Validator; natural language; assignment-aware; tool whitelist.

**Independent Test**: `quickstart.md` §6 — agent PEV dry-run; plan scoped to assignment.

### Tests for User Story 2

- [x] T058 [P] [US2] Contract tests for agent session API in `tests/test_agent_pev.py`
- [x] T059 [P] [US2] Test executor tool whitelist in `tests/test_agent_executor_guard.py`
- [x] T060 [P] [US2] Test tiered provision auth in `tests/test_agent_provision_auth.py`
- [x] T061 [P] [US2] Test `ado2gh_rollback` tool dry-run default in `tests/test_agent_rollback_tool.py`

### Implementation for User Story 2

- [x] T062 [US2] Add versioned skill markdown in `ado2gh/agents/skills/` (planner, executor, validator; all assignment types; FR-012)
- [x] T063 [US2] Implement `ado2gh/agents/llm_provider.py` hyperscaler-agnostic interface
- [x] T064 [US2] Implement `ado2gh/agents/planner.py` — plan, topo order, layout policy, workflow branch strategy
- [x] T065 [US2] Implement `ado2gh/agents/executor.py` — whitelisted Accelerator API calls only
- [x] T066 [US2] Implement `ado2gh/agents/validator.py` — git/pipeline/access scopes (boards after T087)
- [x] T067 [US2] Extend `services/agent/main.py` with `/v1/sessions` per `contracts/agent-pev-api.md`
- [x] T068 [US2] Persist immutable session transcripts via audit store (FR-037)
- [x] T069 [US2] Implement `POST /v1/sessions/{id}/provision` with tiered auth (FR-054a/b)
- [x] T070 [US2] Add secure provision modal in `apps/migration-ui/src/components/AgentChat.tsx`
- [x] T071 [US2] Wire assignment context from picker into `services/agent/main.py`
- [x] T072 [US2] Register `ado2gh_gate_check` and `ado2gh_rollback` in `services/agent/mcp_server.py`

**Checkpoint**: US2 dry-run agent path with gates and rollback tools

---

## Phase 6: User Story 3 — Validation Loop & Human Escalation (Priority: P3)

**Goal**: Validator→executor retry loop with limits; UI escalation when exhausted.

**Independent Test**: Simulated validation failure → retry or escalation UI.

### Tests for User Story 3

- [x] T073 [P] [US3] Test remediation loop retry limit in `tests/test_remediation_loop.py`
- [x] T074 [P] [US3] Test escalated failure blocks executor in `tests/test_escalation_gate.py`

### Implementation for User Story 3

- [x] T075 [US3] Implement `RemediationLoop` tracking in `ado2gh/state/db.py`
- [x] T076 [US3] Add validator→executor routing in `ado2gh/agents/validator.py` and `services/agent/main.py`
- [x] T077 [US3] Implement `POST /v1/sessions/{id}/remediate` in `services/agent/main.py`
- [x] T078 [US3] Add escalation UI in `apps/migration-ui/src/components/AgentChat.tsx` and runs monitor
- [x] T079 [US3] Record remedial Approver approvals in `ado2gh/assignments/audit.py`
- [x] T080 [US3] Configurable auto-retry limit (default 3) in profile policy settings

**Checkpoint**: US3 retry/escalation in agent session

---

## Phase 7: User Story 4 — OrchestrateAI-Compliant Frontend (Priority: P4)

**Goal**: OAI styling, navigation, approvals, history browser, workflow views.

**Independent Test**: OAI theme; context persists; history browser lists sessions (FR-039).

### Tests for User Story 4

- [x] T081 [P] [US4] Playwright/design smoke for OAI tokens on primary routes in `apps/migration-ui/` (if harness exists)

### Implementation for User Story 4

- [x] T082 [US4] Audit primary pages against `apps/migration-ui/src/styles/oai-styles.css` — fix `globals.css`
- [x] T083 [US4] Ensure `apps/migration-ui/src/components/NavTabs.tsx` covers migrate, agent, runs, assignments, history, settings
- [x] T084 [US4] Role-attributed message styling in `apps/migration-ui/src/components/AgentChat.tsx`
- [x] T085 [US4] Approver approval queue view in `apps/migration-ui/src/app/` (live, rollback, provision)
- [x] T086 [US4] Dependency readiness panel with log lines in migrate/agent flows
- [x] T087 [US4] Rollback panel with scope picker on runs/assignment views
- [x] T088 [US4] History browser UI in `apps/migration-ui/src/app/history/page.tsx` (sessions, runs, approvals; FR-039)
- [x] T089 [US4] Boards gaps report viewer component linked to validation results

**Checkpoint**: US4 — OAI surface including audit/history review

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Boards parity, audit export, history API, contracts, coverage

- [x] T090 Implement Boards gaps report in `ado2gh/reporting/boards_gaps.py` per FR-040–042
- [x] T091 Extend `ado2gh/core/scopes/work_items_scope.py` and related for boards parity
- [x] T092 Extend `ado2gh/agents/validator.py` for boards gaps verification (after T090–T091)
- [x] T093 Add audit query/export REST endpoints in `services/accelerator_api/main.py` for FR-039
- [x] T094 Implement optional S3 WORM audit export job in `ado2gh/assignments/audit_export.py` (FR-037, profile-configurable)
- [x] T095 [P] Contract tests for `contracts/pipeline-workflow-branch.md` in `tests/contract/test_pipeline_workflow_contract.py`
- [x] T096 [P] Contract tests for `contracts/dependency-logging-provisioning.md` in `tests/contract/test_dependency_contract.py`
- [x] T097 [P] Contract tests for `contracts/rollback-gates-api.md` in `tests/contract/test_rollback_gates_contract.py`
- [x] T098 Agent LLM-unavailable degradation path in `services/agent/main.py` and UI indicator (Edge Cases)
- [x] T099 Run full `quickstart.md` validation or document sign-off checklist
- [x] T100 Verify `docker-compose.prod.yml` Postgres wiring in README
- [x] T101 Final pytest-cov run; add tests to reach ≥85% on `ado2gh` if below threshold
- [x] T102 [P] Add SSO stretch-goal extension points doc in `docs/` (FR-022a — no implementation)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1** → **Phase 2** → user stories
- **US1 (P1)** after Phase 2 — MVP manual accelerator + gates + rollback API
- **US5 (P2)** after Phase 2; pairs with US1 for cohort scoping
- **US2 (P2)** after US5 resolver; uses gates/rollback tools from Phase 2/US1
- **US3 (P3)** after US2 session infrastructure
- **US4 (P4)** overlaps US1–US3; history browser (T088) after audit API (T093) or stub data
- **Phase 8** boards (T090–T092) before full validator boards checks (T092); polish last

### User Story Dependency Graph

```text
Phase 2 (Foundation + gates + policy)
       │
       ├──────────────┐
       ▼              ▼
     US1 (P1)       US5 (P2)
       │              │
       └──────┬───────┘
              ▼
          US2 (P2)
              ▼
          US3 (P3)
              ▼
          US4 (P4)
              ▼
    Phase 8 (boards, audit export, contracts)
```

### Parallel Opportunities

- Phase 1: T002–T005
- Phase 2: T007–T011 tests parallel; T026 after T012–T018
- US1: T028–T031 tests parallel; T036–T038 after T032–T034
- US5: T049–T050 parallel
- US2: T058–T061 parallel
- Phase 8: T095–T097 contract tests parallel

---

## Parallel Example: User Story 1

```bash
pytest tests/test_pipeline_layout_policy.py tests/test_push_workflows.py \
  tests/test_batch_executor_topo.py tests/test_rollback.py

# After T032–T034 gate wiring:
# T036 settings_store.py | T037 transformer.py | T038 pipelines_scope.py
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 + Phase 2 (include T020–T025 gates/policy)
2. Phase 3 US1 through T048 (defer rollback live UI to T087 if needed)
3. Validate `quickstart.md` §2–3, §8 gate, §9 rollback dry-run
4. Demo without agent

### Incremental Delivery

1. Foundation → **US1 MVP**
2. **US5** cohort scoping
3. **US2** agent PEV
4. **US3** escalation
5. **US4** + **Phase 8** boards, audit export, coverage

### Suggested MVP Scope

**Phase 1 + Phase 2 + US1** — manual accelerator, assignment-linked gates, pipeline→GHA on `ado2gh/migrated-workflows`, topo order, dependency logging, Approver live gate, rollback dry-run API.

---

## Notes

- Docstrings on all new public functions/classes (constitution II)
- Live mutations require Approver (FR-020); symmetric `pipelines` rollback re-enables ADO (FR-026a)
- Do not log secret values in readiness, audit, or transcripts (CA-003)
- Per-assignment phase gate only by default; program-order gates via FR-019 policy rules
- Task IDs T001–T102
