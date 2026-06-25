# Tasks: Agent PEV Architecture Rebuild

**Input**: Design documents from `/specs/011-agent-pev-rebuild/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: REQUIRED by constitution (Principle VI). Every function MUST have thorough
automated tests; maintain >= 85% line coverage on `ado2gh`. Include test tasks for
each user story unless Complexity Tracking documents an approved exception.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

**Spec 010 Prerequisite**: Spec 010 (Enterprise Audit Simplification) MUST be complete before starting Phase 2. Spec 010 decomposes `session_orchestrator.py` into modules under 800 lines. This spec rewrites those decomposed modules.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `ado2gh/agents/`, `services/agent/`, `ado2gh/api/`
- **Frontend**: `apps/migration-ui/src/`
- **Tests**: `tests/unit/`, `tests/contract/`, `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and new module scaffolding

- [X] T001 Create new module files per plan structure: `ado2gh/agents/session_state_machine.py`, `ado2gh/agents/session_store.py`, `ado2gh/agents/repo_lock_store.py`, `ado2gh/agents/rollback_tracker.py`, `ado2gh/agents/resource_mapping.py`, `ado2gh/agents/context_window.py`, `ado2gh/agents/pev_cycle.py`, `ado2gh/agents/metrics.py` (FR-001)
- [X] T002 [P] Create test file stubs: `tests/unit/test_011_state_machine.py`, `tests/unit/test_011_repo_lock_store.py`, `tests/unit/test_011_context_window.py`, `tests/unit/test_011_resource_mapping.py`, `tests/unit/test_011_metrics.py`, `tests/unit/test_011_rollback_tracker.py`, `tests/unit/test_011_orchestrator.py`, `tests/unit/test_011_guardrails.py`, `tests/unit/test_011_executor.py`, `tests/unit/test_011_validator.py`, `tests/contract/test_011_agent_pev_contracts.py`, `tests/integration/test_011_session_persistence.py`, `tests/integration/test_011_pev_loop.py`, `tests/integration/test_011_rollback.py`
- [X] T003 [P] Create orchestrator skill prompt in `ado2gh/agents/skills/orchestrator.md` (FR-001)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Implement formal session state machine in `ado2gh/agents/session_state_machine.py` — states (idle, thinking, planning, executing, validating, awaiting_input, awaiting_approval, completed, failed), enforced transition table, `transition()` method that validates and rejects invalid transitions, `can_transition()` helper (FR-066)
- [X] T005 Implement persistent session store in `ado2gh/agents/session_store.py` — CRUD for agent_sessions, agent_messages, migration_plans, executor_results, validation_results, pev_cycle_summaries, migration_queues, guardrail_decisions, rollback_records tables per data-model.md schema; all entity IDs generated as UUID v4 (FR-074); uses `create_state_db()` factory for SQLite/PG/DynamoDB selection
- [X] T006 [P] Implement persistent repo lock store in `ado2gh/agents/repo_lock_store.py` — DB-backed `acquire()`, `release()`, `is_locked()`, `holder()` methods; stale lock cleanup on startup with audit logging; extends interface from existing `ado2gh/api/repo_lock.py` (FR-068, FR-072)
- [X] T007 [P] Implement context window manager in `ado2gh/agents/context_window.py` — sliding window strategy: last 2 PEV cycles in full detail + running JSON summary of prior cycles; `build_context()` method that assembles LLM prompt from session history; full message history persisted in session store (FR-061)
- [X] T008 [P] Implement Prometheus-compatible metrics collector in `ado2gh/agents/metrics.py` — in-memory counters/histograms for active_sessions, pev_cycles_total, llm_call_duration_seconds, guardrail_blocks_total, tool_calls_total, session_status_counts; `to_prometheus_text()` method for `/metrics` endpoint (FR-069)
- [X] T009 [P] Implement resource mapping configuration in `ado2gh/agents/resource_mapping.py` — fixed ADO→GitHub field mappings for Boards (work items→Issues CSV), Test Plans (test cases→Issues with labels, test suites→milestones), Artifacts (feeds→GitHub Packages, supported types only), Wiki (pages→GitHub Wiki markdown); `override_allowed` flag per mapping type (FR-078, FR-079, FR-080, FR-081)
- [X] T010 [P] Implement rollback tracker in `ado2gh/agents/rollback_tracker.py` — records every GitHub resource created during a session (repos, workflows, secrets, environments, issues, wiki pages, packages) with correlation IDs; `record_creation()`, `get_eligible()`, `mark_deleted()`, `mark_failed()` methods; guardrail ensures only session-created resources are eligible (FR-083)
- [X] T011 Extend LLM provider with 60s timeout and retry in `ado2gh/agents/llm_provider.py` — add `complete_with_timeout()` method wrapping `complete()` with `asyncio.wait_for(60s)`, retry once on timeout, raise `LLMTimeoutError` on second timeout; degrade gracefully by reporting to orchestrator (FR-067)
- [X] T012 [P] Extend tool catalog with role-based access levels in `ado2gh/agents/local/tool_catalog.py` — add `role_access` field to `ToolContract` (orchestrator: read-only ADO + read-only GitHub + user interaction; planner: read-only ADO + read-only GitHub + discovery; executor: read-only ADO + read/write GitHub + migration; validator: read-only ADO + read-only GitHub + validation); add new tool entries for service connections, Boards, Test Plans, Artifacts, Wiki migration (FR-040)
- [X] T013 [P] Update planner, executor, validator skill prompts in `ado2gh/agents/skills/planner.md`, `ado2gh/agents/skills/executor.md`, `ado2gh/agents/skills/validator.md` — add all ADO resource types (service connections, Boards, Test Plans, Artifacts, Wiki), continuous loop behavior, structured JSON messaging, and guardrail awareness (FR-001)
- [X] T014 Extend audit bridge for agent-to-agent messages in `ado2gh/agents/local/audit_bridge.py` — log inter-agent messages (from_role, to_role, message_type, payload with secrets masked), guardrail decisions, and PEV cycle outcomes with correlation IDs (CA-004)
- [X] T014a [P] Mark in-memory session store and `RepoLockManager` as deprecated in `services/agent/main.py` and `ado2gh/api/repo_lock.py` — add `DeprecationWarning` with removal timeline in docstrings (Constitution Principle III)

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 3 - Orchestrator Agent: User Intent & Dynamic Forms (Priority: P1)

**Goal**: The orchestrator interprets natural language, classifies intent (general chat, migration info, migration action), creates dynamic forms for missing parameters, and routes migration work to the PEV chain

**Independent Test**: A user types "migrate repo to github" (vague request). The orchestrator responds with a dynamic form asking which repository, whether dry-run or live, and whether to include dependencies. After form submission, the orchestrator routes the complete request to the PEV chain.

### Tests for User Story 3 (REQUIRED) ⚠️

- [X] T015 [P] [US3] Unit test for intent classification (general chat, migration info, migration action) with labeled test corpus of 50+ inputs in `tests/unit/test_011_orchestrator.py` (FR-010, SC-003)
- [X] T016 [P] [US3] Contract test for POST /v1/sessions, POST /v1/sessions/{id}/message, POST /v1/sessions/{id}/form-submit, POST /v1/sessions/{id}/form-cancel in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 3

- [X] T017 [US3] Rewrite orchestrator intent classification in `ado2gh/agents/session_orchestrator.py` (post-010 decomposed modules) — replace heuristic `_classify_user_intent` with LLM-driven intent classification; classify as general_chat, migration_info, or migration_action; use ORCHESTRATOR_SYSTEM prompt from `ado2gh/agents/skills/orchestrator.md` (FR-004, FR-010, FR-011, FR-016)
- [X] T018 [US3] Implement dynamic form generation in `ado2gh/agents/session_orchestrator.py` — LLM generates form fields (form_id, title, description, fields with name/label/type/options/required); auto-populate repo options from discovery data; render inline in chat; disable chat input while form pending (FR-013)
- [X] T019 [US3] Implement orchestrator routing logic in `ado2gh/agents/session_orchestrator.py` — general chat and migration info queries respond directly without PEV; migration actions identify required parameters (repo, phase, dry-run/live, scope) and present forms for missing ones before invoking Planner (FR-005, FR-011, FR-012)
- [X] T020 [US3] Implement user message queuing during PEV execution in `ado2gh/agents/session_orchestrator.py` — queue messages while PEV chain is running, process after current cycle completes; interrupt cycle for cancellation requests (FR-017)
- [X] T021 [US3] Implement auto-discovery trigger in `ado2gh/agents/session_orchestrator.py` — when user requests migration and discovery data is empty, auto-trigger profile discovery scan, display "Running discovery scan..." indicator, proceed to planning (FR-077)

**Checkpoint**: Orchestrator correctly interprets user intent, generates dynamic forms, and routes to PEV chain

---

## Phase 4: User Story 4 - Planner Agent: Migration Planning (Priority: P1)

**Goal**: The planner creates detailed migration plans from orchestrator instructions using discovery data, maps dependencies via topological sort, includes all ADO resource types, and creates revised plans from validator feedback

**Independent Test**: The planner receives "migrate Project/RepoA" where RepoA depends on RepoB and RepoC. The planner produces a plan with topological order [RepoB, RepoC, RepoA], pipeline conversion mappings, secret/service connection mappings, Boards/Test Plans/Artifacts/Wiki work items, and a dry-run flag.

### Tests for User Story 4 (REQUIRED) ⚠️

- [X] T022 [P] [US4] Unit test for plan generation with all resource types in `tests/unit/test_011_resource_mapping.py`
- [X] T023 [P] [US4] Contract test for planner→executor message format (instruction payload) in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 4

- [X] T024 [US4] Rewrite planner agent in `ado2gh/agents/planner.py` — LLM-driven planning using discovery data and ADO API calls (rate-limit aware); generate structured JSON plan with: repo migration order (topological sort), pipeline-to-workflow conversion mappings, Bicep/ARM transformation steps, secret/service connection mapping, Boards (work items→Issues) mapping, Test Plans mapping, Artifacts (feeds→Packages) mapping, Wiki migration steps, dry-run/live flag, per-repo work items with ready/blocked status (FR-018, FR-019, FR-020, FR-023)
- [X] T025 [US4] Implement planner-orchestrator coordination in `ado2gh/agents/planner.py` — when planner needs information not available from discovery (e.g., secret values, service connection migration), send clarification request to orchestrator which asks user via dynamic form (FR-005, FR-021)
- [X] T026 [US4] Implement revised plan generation from validator feedback in `ado2gh/agents/planner.py` — revised plan targets only failed scopes, includes remediation steps, increments revision number (FR-022)
- [X] T027 [US4] Implement planner assumptions documentation in `ado2gh/agents/planner.py` — document all assumptions when discovery data is incomplete, flag for orchestrator confirmation; rate-limit aware — work with available data and flag gaps (FR-024, FR-025)
- [X] T028 [US4] Implement planner override for resource mappings in `ado2gh/agents/planner.py` — planner may override fixed ADO→GitHub mappings per-migration with documented justification for edge cases (FR-079, FR-080)

**Checkpoint**: Planner generates complete migration plans with all resource types and creates revised plans from validator feedback

---

## Phase 5: User Story 7 - Shared Tool Access with Guardrails (Priority: P2)

**Goal**: All agents share a common tool catalog with role-based access levels, guardrails intercept all GitHub write operations, and all tool executions are logged for audit

**Independent Test**: The executor attempts to delete a GitHub repo not in the migration plan. The guardrail layer blocks the operation, logs the attempt, and returns an error. No GitHub resource is modified.

**Note**: P2 priority but MUST be implemented before US5 (Executor) since the executor depends on guardrails for write operations.

### Tests for User Story 7 (REQUIRED) ⚠️

- [X] T029 [P] [US7] Unit test for guardrail decision logic (allow/block) in `tests/unit/test_011_guardrails.py`
- [X] T030 [P] [US7] Contract test for guardrail interception of write operations in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 7

- [X] T031 [US7] Implement guardrail layer for GitHub write operations in `ado2gh/agents/local/tool_catalog.py` — intercept all write operations: validate operation is in approved plan, validate target resource exists (for modifications), block deletions without explicit authorization, validate all parameters reference valid resources; return `GuardrailDecision` with allow/block + reason (FR-030, FR-042, FR-051, FR-053, CA-005)
- [X] T032 [US7] Implement tool parameter validation in `ado2gh/agents/local/tool_catalog.py` — validate all parameters against known resources before execution; reject hallucinated resource names, invalid paths, non-existent repos with descriptive errors (FR-043)
- [X] T033 [US7] Implement audit logging for all tool executions in `ado2gh/agents/local/audit_bridge.py` — log timestamp, agent role, tool name, parameters (secrets masked), result, guardrail decision, correlation IDs (session_id, plan_id, run_id) (FR-044, FR-055, CA-004)
- [X] T034 [US7] Implement ADO read-only enforcement in `ado2gh/agents/local/tool_catalog.py` — no agent can modify, create, or delete ADO resources except ADO pipeline disabling when explicitly approved as part of migration cleanup (FR-041)

**Checkpoint**: Shared tools with role-based access and guardrails are operational

---

## Phase 6: User Story 5 - Executor Agent: Deterministic Execution (Priority: P1)

**Goal**: The executor interprets planner instructions deterministically, performs all migration operations (repos, pipelines, secrets, service connections, Boards, Test Plans, Artifacts, Wiki), routes through guardrails, and sends exact output to the validator

**Independent Test**: The executor receives a plan to migrate RepoA, convert 3 pipelines, provision 2 secrets, migrate 15 work items, and migrate wiki. The executor performs exactly those operations — no more, no less. Output lists: repo mirror status, 3 workflow files created, 2 secrets provisioned (names only), 15 issues created, wiki enabled.

### Tests for User Story 5 (REQUIRED) ⚠️

- [X] T035 [P] [US5] Unit test for executor result structure in `tests/unit/test_011_executor.py`
- [X] T036 [P] [US5] Integration test for executor with guardrails in `tests/integration/test_011_pev_loop.py`

### Implementation for User Story 5

- [X] T037 [US5] Rewrite executor agent in `ado2gh/agents/executor.py` — LLM-driven deterministic execution; interpret planner instructions, perform operations through guardrail layer, send exact output to validator (what was created, what failed, what was skipped with reasons) (FR-026, FR-027, FR-028, FR-031, FR-033, FR-065); includes Bicep/ARM transformation via hybrid approach (FR-032, FR-087): supported constructs auto-transformed, unsupported attempted via LLM best-effort with web search, gaps reported with construct name and manual conversion steps
- [X] T038 [US5] Implement hybrid API routing in `ado2gh/agents/executor.py` — existing operations (discovery, GEI git mirror, pipeline conversion, readiness checks) route through accelerator service API; new resource types (service connections, Boards, Test Plans, Artifacts, Wiki) use direct ADO/GitHub API calls with profile PATs (FR-073)
- [X] T039 [US5] Implement ADO Boards migration via CSV import in `ado2gh/agents/executor.py` — export ADO work items via ADO REST API, transform to GitHub Issues CSV format using fixed field mapping from `resource_mapping.py`, import via GitHub Issues Import API (FR-078)
- [X] T040 [US5] Implement ADO Test Plans migration in `ado2gh/agents/executor.py` — map test suites to GitHub milestones, test cases to GitHub Issues with `test-case`/`test-suite` labels; include test steps as structured checklist in issue body (FR-079)
- [X] T041 [US5] Implement ADO Artifacts migration in `ado2gh/agents/executor.py` — read ADO Artifacts feed data via ADO REST API, publish supported types (npm, NuGet, Docker, Maven, PyPI) to GitHub Packages, document unsupported types as gaps (FR-080)
- [X] T042 [US5] Implement ADO Wiki migration in `ado2gh/agents/executor.py` — enable GitHub Wiki per-repo via GitHub API, clone ADO Wiki git repo, transform pages to markdown, push to GitHub Wiki git remote (FR-081)
- [X] T043 [US5] Implement executor clarification requests in `ado2gh/agents/executor.py` — when instructions are ambiguous, send clarification request to planner (not user); executor does not guess or default (FR-006, FR-029)
- [X] T044 [US5] Implement rollback tracking in `ado2gh/agents/executor.py` — call `rollback_tracker.record_creation()` for every GitHub resource created during the session; enables rollback on cancellation (FR-083)
- [X] T044a [US5] Implement idempotency detect-and-prompt in `ado2gh/agents/executor.py` — when executor detects a repo was already migrated (via API checks), ask orchestrator to prompt user with overwrite/skip/abort options (FR-086)
- [X] T044b [US5] Implement service connection migration in `ado2gh/agents/executor.py` — map simple credential-based connections to GitHub secrets, deployment-scoped connections to GitHub environments with protection rules (FR-085)

**Checkpoint**: Executor performs all migration operations deterministically with guardrails and rollback tracking

---

## Phase 7: User Story 6 - Validator Agent: Evidence-Based Validation (Priority: P1)

**Goal**: The validator receives both the planner's plan and the executor's output, verifies migration outcomes using API calls, sends structured feedback to the planner on failures, and validates all resource types

**Independent Test**: The executor reports it created a workflow file `ci.yml`. The validator queries the GitHub API to confirm the file exists, checks YAML syntax, verifies secret references resolve, and runs local workflow simulation (e.g., `act`) — the validator MUST NOT dispatch the actual workflow on GitHub (per FR-088). If any check fails, the validator sends structured feedback to the planner.

### Tests for User Story 6 (REQUIRED) ⚠️

- [X] T045 [P] [US6] Unit test for validation result structure in `tests/unit/test_011_validator.py`
- [X] T046 [P] [US6] Contract test for validator→planner feedback message format in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 6

- [X] T047 [US6] Rewrite validator agent in `ado2gh/agents/validator.py` — LLM-driven validation; receive planner's plan and executor's output as context; verify migration outcomes using API calls and testing frameworks (FR-034, FR-035)
- [X] T048 [US6] Implement validation checks for all resource types in `ado2gh/agents/validator.py` — git HEAD SHA parity, workflow file existence and YAML syntax, secret reference resolution, service connection migration verification (secrets exist, environments exist with correct protection rules per FR-085), dependency order compliance, local workflow simulation validation (e.g., `act`, MUST NOT dispatch actual workflow on GitHub, FR-088), Bicep/ARM transformation validation (FR-038), Boards (work items→Issues) migration verification, Test Plans migration verification, Artifacts (feeds→Packages) migration verification, Wiki migration verification (FR-035)
- [X] T049 [US6] Implement structured feedback to planner in `ado2gh/agents/validator.py` — send expected state, observed state, specific failure, file path, and recommended remediation (FR-007, FR-036)
- [X] T050 [US6] Implement plan-vs-execution consistency check in `ado2gh/agents/validator.py` — validate executor did not perform operations outside approved plan; any deviation reported as validation failure (FR-037)
- [X] T051 [US6] Implement per-scope pass/fail reporting in `ado2gh/agents/validator.py` — report pass/fail per scope (repo content, pipelines/workflows, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki) with evidence suitable for audit review (FR-039)

**Checkpoint**: Validator verifies all resource types and sends structured feedback for re-planning

---

## Phase 8: User Story 2 - Continuous Agent Loop (Priority: P1)

**Goal**: The agent loop cycles through Orchestrator → Planner → Executor → Validator repeatedly until a terminal state is reached. The loop is event-driven, async, and handles retries, user input requests, and max iteration limits.

**Independent Test**: A user submits a migration request for a repo with a missing secret. The planner identifies the missing secret, the orchestrator asks the user, the user provides it, the planner revises the plan, the executor provisions the secret and completes the migration, the validator confirms — all within a single chat session without re-submitting the original request.

### Tests for User Story 2 (REQUIRED) ⚠️

- [X] T052 [P] [US2] Integration test for continuous PEV loop with retry in `tests/integration/test_011_pev_loop.py`
- [X] T053 [P] [US2] Integration test for session persistence and resume after restart in `tests/integration/test_011_session_persistence.py`

### Implementation for User Story 2

- [X] T054 [US2] Implement PEV cycle orchestrator in `ado2gh/agents/pev_cycle.py` — continuous loop that cycles through Planner → Executor → Validator; manages cycle state, iteration counters (max 20 total, 3 PEV retries), and context window updates; generates `PevCycleSummary` after each cycle (FR-002, FR-003, FR-008, FR-009, FR-056, FR-062)
- [X] T055 [US2] Implement inter-agent communication in `ado2gh/agents/pev_cycle.py` — messages with `message_type` (instruction|clarification_request|feedback|result), `from_role`, `to_role`, `payload` (typed per message_type), `correlation_ids`, `timestamp` (FR-003, FR-059)
- [X] T056 [US2] Implement session state machine integration in `ado2gh/agents/pev_cycle.py` — drive state transitions through the loop (idle→thinking→planning→executing→validating→completed|failed|planning(retry)); pause to awaiting_input when user input needed; pause to awaiting_approval for live execution (FR-066)
- [X] T057 [US2] Implement session persistence and resume in `ado2gh/agents/session_store.py` — persist current state machine state, current PEV cycle, current repo in queue, executor progress; on server restart, resume from last persisted state (FR-057, FR-064, FR-071)
- [X] T058 [US2] Implement repo-level lock acquisition in `ado2gh/agents/pev_cycle.py` — acquire lock via `repo_lock_store` when executor begins migrating a repo; release on PEV cycle complete, fail, or session termination (FR-068, FR-072)
- [X] T059 [US2] Implement batch migration queue in `ado2gh/agents/pev_cycle.py` — for 50+ repo batch migrations, process repos sequentially from `MigrationQueue`; validator validates each repo before next begins (FR-060, FR-063)
- [X] T059a [US2] Implement concurrent session support in `services/agent/main.py` — asyncio-based session management supporting up to 10 concurrent active sessions without state corruption or cross-session interference (FR-058, SC-014)

**Checkpoint**: Continuous PEV loop operates autonomously with persistence, locks, and batch support

---

## Phase 9: User Story 1 - Hands-Off Single Repo Migration (Priority: P1) 🎯 MVP

**Goal**: A user types "migrate Project/RepoName to GitHub" and the agent completes the full migration (repos, pipelines→workflows, Bicep→Actions, secrets, service connections, Boards→Issues, Test Plans, Artifacts→Packages, Wiki) without any intermediate user interaction for repos with complete discovery data.

**Independent Test**: A user submits "migrate Project/RepoName" for a repo with 2 dependencies and 3 ADO pipelines. The agent completes the full migration without any intermediate user interaction. The migrated repos' GitHub Actions workflows run successfully on the first push.

**Dependencies**: Requires US2-US6 and US7 to be complete (orchestrator, planner, executor, validator, continuous loop, guardrails)

### Tests for User Story 1 (REQUIRED) ⚠️

- [X] T060 [P] [US1] Integration test for end-to-end hands-off migration in `tests/integration/test_011_pev_loop.py`
- [X] T061 [P] [US1] Contract test for full migration flow (session create → plan → execute → validate → complete) in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 1

- [X] T062 [US1] Integrate orchestrator → PEV chain → completion flow in `services/agent/main.py` — wire together orchestrator intent classification, PEV cycle, state machine transitions, and session persistence for the complete "migrate X" → completed flow
- [X] T063 [US1] Implement plan summary presentation and confirmation in `services/agent/main.py` — present plan summary (repos, scopes, order, risks) to user before live execution; enforce dry-run default; require explicit confirmation for live (FR-014, FR-015, CA-001)
- [X] T064 [US1] Implement cancellation with rollback option in `services/agent/main.py` — POST /v1/sessions/{id}/cancel endpoint; orchestrator presents rollback (delete all session-created resources) or stop (complete current operation) options; if rollback chosen, executor deletes tracked resources via guardrail (FR-082, FR-083)

**Checkpoint**: Hands-off single repo migration works end-to-end — MVP complete

---

## Phase 10: User Story 8 - Claude Code/Cursor-Like Chat Interface (Priority: P2)

**Goal**: The agent tab presents a simplified chat interface with message list, input, model selector, dry-run toggle, session sidebar, working indicator, and inline dynamic forms. Internal agent-to-agent messages are hidden.

**Independent Test**: A user opens the Agent tab, sees a clean chat interface, types a migration request, sees a "Working..." indicator while PEV runs, and receives only orchestrator user-facing messages. Internal agent messages are not visible.

### Tests for User Story 8 (REQUIRED) ⚠️

- [X] T065 [P] [US8] Contract test for session listing and polling in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 8

- [X] T066 [US8] Rewrite AgentChat component in `apps/migration-ui/src/components/AgentChat.tsx` — simplified Claude Code/Cursor-like chat: message list (user + assistant only), message input, model selector, dry-run toggle, session sidebar; hide internal agent-to-agent messages; show only orchestrator user-facing messages (FR-045, FR-046)
- [X] T067 [US8] Implement working indicator in `apps/migration-ui/src/components/AgentChat.tsx` — show "Working..." indicator when PEV chain is running with optional expandable details showing which agent is currently active (e.g., "Planner is building migration plan...") (FR-047)
- [X] T068 [US8] Implement inline dynamic form rendering in `apps/migration-ui/src/components/AgentChat.tsx` — render forms as cards with fields, submit button, cancel button; disable chat input while form is pending (FR-048)
- [X] T069 [US8] Implement session sidebar and persistence in `apps/migration-ui/src/components/AgentChat.tsx` — support multiple sessions per profile, session persistence, sidebar for switching between sessions; auto-scroll to latest message, preserve scroll position when loading history (FR-049, FR-050)
- [X] T070 [US8] Implement distinct UI states in `apps/migration-ui/src/components/AgentChat.tsx` — LLM unconfigured (setup prompt with link to settings), empty discovery (scan prompt with button), error state (actionable summary with retry and contact-admin options) (FR-076)
- [X] T071 [US8] Update frontend types in `apps/migration-ui/src/lib/types/agent.ts` — new message types, state machine states (idle/thinking/planning/executing/validating/awaiting_input/awaiting_approval/completed/failed), session interface with iteration counters and PEV retry count
- [X] T072 [US8] Update frontend API client in `apps/migration-ui/src/lib/agent.ts` — add cancel with rollback endpoint, metrics endpoint, health endpoint; update session polling to handle new states; add session status counts display

**Checkpoint**: Chat interface is simplified, shows only orchestrator messages, and handles all UI states

---

## Phase 11: User Story 9 - Enterprise Guardrails & Safety (Priority: P2)

**Goal**: Enterprise-level guardrails prevent accidental deletions, information leakage, and unauthorized operations. Guardrails operate at tool parameter validation, plan-vs-execution consistency, deletion confirmation, secret masking, and audit logging layers.

**Independent Test**: The executor generates a tool call to delete a GitHub repo. The guardrail blocks it unless: it's in the approved plan, user confirmed via dynamic form, and approver policy is satisfied. Secret values are masked in all outputs. All operations are auditable.

### Tests for User Story 9 (REQUIRED) ⚠️

- [X] T073 [P] [US9] Integration test for rollback on cancellation in `tests/integration/test_011_rollback.py`
- [X] T074 [P] [US9] Contract test for /metrics and /health endpoints in `tests/contract/test_011_agent_pev_contracts.py`

### Implementation for User Story 9

- [X] T075 [US9] Implement secret value masking across all channels in `ado2gh/agents/local/audit_bridge.py` and `ado2gh/agents/session_orchestrator.py` — mask secret values in agent messages, logs, audit records, and UI; only secret names visible, never values (FR-052, CA-003)
- [X] T076 [US9] Implement destructive operation highlighting in `services/agent/main.py` — destructive operations (repo deletion, workflow deletion, secret deletion, ADO pipeline disabling) highlighted in plan summary with individual confirmation requirements (FR-054, CA-002)
- [X] T077 [US9] Implement /metrics endpoint in `services/agent/main.py` — expose Prometheus-compatible metrics from `metrics.py` at GET /metrics with text/plain content type (FR-069)
- [X] T078 [US9] Implement /health endpoint in `services/agent/main.py` — agent-specific health check at GET /health reporting LLM provider availability, active session count, storage backend connectivity, configuration status (FR-070)
- [X] T079 [US9] Implement session data retention cleanup in `ado2gh/agents/session_store.py` — scheduled backend task that purges session data older than 90 days from last activity while preserving audit log entries indefinitely (FR-075)

**Checkpoint**: Enterprise guardrails, metrics, health, and retention are operational

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T080 [P] Update MCP server tool catalog in `services/agent/mcp_server.py` — expose new tools for service connections, Boards, Test Plans, Artifacts, Wiki migration; update tool descriptions and role-based access (FR-040)
- [X] T081 [P] Update docker-compose.yml — AGENT_METRICS_ENABLED, AGENT_SESSION_RETENTION_DAYS (default 90), AGENT_MAX_ITERATIONS (default 20), AGENT_MAX_PEV_RETRIES (default 3)
- [X] T082 Update pyproject.toml coverage config: `--cov=ado2gh.agents.session_state_machine`, `--cov=ado2gh.agents.session_store`, `--cov=ado2gh.agents.repo_lock_store`, `--cov=ado2gh.agents.rollback_tracker`, `--cov=ado2gh.agents.resource_mapping`, `--cov=ado2gh.agents.context_window`, `--cov=ado2gh.agents.pev_cycle`, `--cov=ado2gh.agents.metrics` — then run full test suite and verify >= 85% coverage on `ado2gh`: `pytest tests/ --cov=ado2gh.agents --cov-report=term-missing --cov-fail-under=85`
- [X] T083 [P] Run quickstart.md validation scenarios — execute all 13 validation scenarios from `specs/011-agent-pev-rebuild/quickstart.md` and confirm pass criteria
- [X] T084 Update CLAUDE.md and AGENTS.md — document continuous PEV loop, state machine, persistent sessions, hybrid API routing, and all ADO resource type support (FR-084)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion AND Spec 010 completion — BLOCKS all user stories
- **US3 (Phase 3)**: Depends on Foundational — Orchestrator is the entry point
- **US4 (Phase 4)**: Depends on Foundational — Planner can be tested independently with mock data
- **US7 (Phase 5)**: Depends on Foundational — Guardrails must exist before Executor
- **US5 (Phase 6)**: Depends on US7 (guardrails) and US4 (planner instructions) — Executor needs both
- **US6 (Phase 7)**: Depends on US5 (executor output) — Validator needs executor results
- **US2 (Phase 8)**: Depends on US3-US6 — Continuous loop ties all agents together
- **US1 (Phase 9)**: Depends on US2-US6 and US7 — MVP integration story
- **US8 (Phase 10)**: Depends on US1 (backend API stable) — UI needs working backend
- **US9 (Phase 11)**: Depends on US1 — Guardrail hardening on working system
- **Polish (Phase 12)**: Depends on all user stories being complete

### User Story Dependencies

- **US3 (P1)**: Foundational → US3 (no other story dependencies)
- **US4 (P1)**: Foundational → US4 (no other story dependencies)
- **US7 (P2)**: Foundational → US7 (no other story dependencies, but BLOCKS US5)
- **US5 (P1)**: Foundational + US7 + US4 → US5
- **US6 (P1)**: Foundational + US5 → US6
- **US2 (P1)**: Foundational + US3 + US4 + US5 + US6 → US2
- **US1 (P1)**: US2 + US3 + US4 + US5 + US6 + US7 → US1 (MVP integration)
- **US8 (P2)**: US1 → US8
- **US9 (P2)**: US1 → US9

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- Phase 2: T006, T007, T008, T009, T010, T012, T013 can all run in parallel (different files)
- Phase 3 (US3) and Phase 4 (US4) can run in parallel after Foundational
- Phase 5 (US7) can run in parallel with US3 and US4 after Foundational
- Tests within each story marked [P] can run in parallel

---

## Parallel Example: Foundational Phase

```bash
# Launch all independent foundational tasks together:
Task: "Implement persistent repo lock store in ado2gh/agents/repo_lock_store.py"
Task: "Implement context window manager in ado2gh/agents/context_window.py"
Task: "Implement Prometheus metrics in ado2gh/agents/metrics.py"
Task: "Implement resource mapping in ado2gh/agents/resource_mapping.py"
Task: "Implement rollback tracker in ado2gh/agents/rollback_tracker.py"
Task: "Extend tool catalog with role-based access in ado2gh/agents/local/tool_catalog.py"
Task: "Update skill prompts in ado2gh/agents/skills/"
```

## Parallel Example: US3 + US4 + US7

```bash
# After Foundational completes, these three stories can proceed in parallel:
Developer A: US3 (Orchestrator) — ado2gh/agents/session_orchestrator.py
Developer B: US4 (Planner) — ado2gh/agents/planner.py
Developer C: US7 (Guardrails) — ado2gh/agents/local/tool_catalog.py
```

---

## Implementation Strategy

### MVP First (User Story 1 — requires US2-US7)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete US3 (Orchestrator) + US4 (Planner) + US7 (Guardrails) in parallel
4. Complete US5 (Executor) — depends on US7 + US4
5. Complete US6 (Validator) — depends on US5
6. Complete US2 (Continuous Loop) — ties PEV together
7. Complete US1 (Hands-Off Migration) — MVP integration
8. **STOP and VALIDATE**: Test end-to-end hands-off migration

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US3 + US4 + US7 → Orchestrator, Planner, Guardrails working
3. Add US5 + US6 → Executor and Validator working
4. Add US2 → Continuous PEV loop operational
5. Add US1 → MVP: hands-off migration works end-to-end
6. Add US8 → Simplified chat UI
7. Add US9 → Enterprise hardening, metrics, health, retention
8. Polish → Documentation, coverage, quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Spec 010 MUST be complete before starting Phase 2 (Foundational)
- US7 is P2 but is implemented before US5 (P1) because the executor depends on guardrails
