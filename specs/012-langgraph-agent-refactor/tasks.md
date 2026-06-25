# Tasks: LangGraph Agent Refactor

**Input**: Design documents from `/specs/012-langgraph-agent-refactor/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/api-contracts.md, quickstart.md

**Tests**: REQUIRED by constitution (Principle VI). Every function MUST have thorough automated tests; maintain >= 85% line coverage on `ado2gh`. Include test tasks for each user story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Backend: `ado2gh/agents/migration_agent/`
- Routes: `services/agent/routes/`
- UI: `apps/migration-ui/src/`
- Tests: `tests/unit/`, `tests/integration/`, `tests/contract/`

## User Stories (Derived from Spec)

| Story | Priority | Title | Description |
|-------|----------|-------|-------------|
| US1 | P1 | Core LangGraph Infrastructure | Create `migration_agent/` package, AgentState, graph skeleton, LLM bridge, orchestrator node, SSE streaming, session routes |
| US2 | P1 | Model Capabilities & Provider Support | Capability detection, validation, streaming fallback, thinking support, context window adaptation |
| US3 | P2 | Planner Node & Tools | Migration plan generation, LangChain tool bindings, planner tools module |
| US4 | P2 | Executor Node & Guardrails | Migration execution, guardrail wrapper, executor tools, rollback tracking |
| US5 | P2 | Validator Node & Feedback | Validation checks, structured feedback, validator tools |
| US6 | P2 | PEV Loop & Conditional Edges | Graph routing, retry logic, iteration limits, batch queue, inter-agent messaging |
| US7 | P2 | Session Persistence & Resume | Checkpointing, state machine, session store, form checkpoint resume |
| US8 | P3 | UI Streaming Display | AgentChat.tsx, agent.ts, types/agent.ts updates for streaming thinking blocks |
| US9 | P3 | Cross-Cutting & Cleanup | Metrics, health, legacy module deletion, final validation |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the `migration_agent/` package structure and install dependencies

- [x] T001 Create `migration_agent/` package directory structure with `__init__.py` in `ado2gh/agents/migration_agent/` and `ado2gh/agents/migration_agent/tools/`
- [x] T002 Add LangChain/LangGraph dependencies to `requirements.txt` with `>=` version pins: `langchain>=0.2`, `langchain-core>=0.2`, `langgraph>=0.1`, `langchain-openai>=0.1`, `langchain-anthropic>=0.1`
- [x] T003 [P] Copy and refactor `state.py` from `langgraph_agent/state.py` to `ado2gh/agents/migration_agent/state.py` — add `operator.add` reducers for list fields, add PEV-related fields per data-model.md
- [x] T004 [P] Copy and refactor `llm_bridge.py` from `langgraph_agent/llm_bridge.py` to `ado2gh/agents/migration_agent/llm_bridge.py` — add capability detection logic
- [x] T005 [P] Create `ado2gh/agents/migration_agent/constants.py` with `NO_LLM_CONFIGURED_MESSAGE` and shared constants (extracted from `llm_provider.py`)
- [x] T006 [P] Move skill `.md` files from `ado2gh/agents/skills/` to `ado2gh/agents/migration_agent/prompts/` directory
- [x] T007 [P] Create `ado2gh/agents/migration_agent/prompts.py` that loads `.md` files from `prompts/` directory and exposes `ORCHESTRATOR_SYSTEM`, `PLANNER_SYSTEM`, `EXECUTOR_SYSTEM`, `VALIDATOR_SYSTEM` constants

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T008 Create `ado2gh/agents/migration_agent/utils.py` with event appending (`_append_event`), task initialization (`_init_tasks`), and session helper functions extracted from `session_orchestrator.py`
- [x] T009 [P] Create `ado2gh/agents/migration_agent/forms.py` with dynamic form builders extracted from `session_orchestrator.py` (plan confirmation form, parameter collection forms)
- [x] T010 [P] Create `ado2gh/agents/migration_agent/policies.py` consolidating `execution_mode.py`, `live_execution_policy.py`, `session_access.py`, `agent_scope.py` into a single module
- [x] T011 [P] Create `ado2gh/agents/migration_agent/session_state.py` with `SessionStateMachine` (state transitions, `InvalidTransitionError`) and `OrchestratorResult` dataclass, moved from `orchestration/session_state.py`
- [x] T012 [P] Create `ado2gh/agents/migration_agent/session_store.py` with long-term persistence (session metadata, plans, PEV summaries, audit logs), moved from `ado2gh/agents/session_store.py` and refactored for LangGraph state patterns
- [x] T013 Create `ado2gh/agents/migration_agent/guardrails.py` with LangChain tool wrapper for guardrail evaluation (plan authorization, resource validation, deletion confirmation, parameter validation, `GuardrailDecision` logging)
- [x] T014 Create `ado2gh/agents/migration_agent/graph.py` with `StateGraph` definition — define all nodes (classify_intent, orchestrator, planner, executor, validator, execute_tools, finalize), conditional edges, checkpointer attachment, and graph compilation at module load with `thread_id` namespace

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Core LangGraph Infrastructure (Priority: P1) 🎯 MVP

**Goal**: Working LangGraph agent that handles user messages, classifies intent, responds to general chat, and streams LLM output via SSE

**Independent Test**: Send a general chat message via the SSE endpoint and receive streamed LLM token responses with `subagent: "orchestrator"` labels

### Tests for User Story 1 (REQUIRED) ⚠️

- [x] T015 [P] [US1] Unit test for graph structure in `tests/unit/test_graph_structure.py` — verify all nodes present, conditional edges correct, recursion limit >= 40
- [x] T016 [P] [US1] Unit test for state reducers in `tests/unit/test_state_reducers.py` — verify `operator.add` accumulates list fields, scalars overwrite, `add_messages` for messages
- [x] T017 [P] [US1] Unit test for orchestrator node in `tests/unit/test_orchestrator_node.py` — intent classification (general_chat, migration_info, migration_action), form handling, should_return routing
- [x] T018 [P] [US1] Unit test for streaming in `tests/unit/test_streaming.py` — SSE event generation (kind: token, status, message, heartbeat), subagent labels

### Implementation for User Story 1

- [x] T019 [US1] Create `ado2gh/agents/migration_agent/nodes.py` with `classify_intent` node — uses LangChain ChatModel `astream()` to classify user intent, streams thinking tokens, sets `AgentState.intent`
- [x] T020 [US1] Add `orchestrator_node` function to `ado2gh/agents/migration_agent/nodes.py` — handles general_chat (direct LLM response), migration_info (accelerator API queries), migration_action (parameter collection, forms), queues messages during PEV, auto-triggers discovery, highlights destructive operations in plan summary with individual confirmation requirements (FR-092)
- [x] T021 [US1] Add `finalize_node` function to `ado2gh/agents/migration_agent/nodes.py` — prepares response, appends events to session, sets `should_return`
- [x] T022 [US1] Create `ado2gh/agents/migration_agent/orchestrator.py` with `process_user_message()` and `stream_user_message()` wrapper functions — compile graph once at module load, pass `thread_id=session_id`, yield SSE events from graph execution
- [x] T023 [US1] Rewrite `services/agent/routes/session_routes.py` — import from `migration_agent.orchestrator`, remove `_shared.py` imports, use `stream_user_message()` for SSE endpoint, add 15s heartbeat keepalive, add form-submit endpoint with checkpoint resume
- [x] T024 [US1] Update `services/agent/main.py` — remove `_shared.py` import, import from `migration_agent`, update health endpoint

**Checkpoint**: Agent handles general chat and migration info queries with SSE streaming

---

## Phase 4: User Story 2 - Model Capabilities & Provider Support (Priority: P1)

**Goal**: Any LangChain-compatible model with native tool-calling works; capabilities detected and validated at registration; streaming fallback for non-streaming models; thinking support for capable models

**Independent Test**: Register models with different capabilities (OpenAI, Anthropic, stub) and verify correct behavior: tool-calling required, streaming fallback, thinking events, context budget adaptation

### Tests for User Story 2 (REQUIRED) ⚠️

- [x] T025 [P] [US2] Unit test for LLM bridge in `tests/unit/test_llm_bridge.py` — model resolution for OpenAI, Anthropic, Azure OpenAI, OpenAI-compatible, stub; capability reading; streaming vs invoke selection
- [x] T026 [P] [US2] Unit test for model capabilities in `tests/unit/test_model_capabilities.py` — `supports_tool_calling=false` rejected with `ModelCapabilityError`, defaults applied, `max_context_tokens` drives budget

### Implementation for User Story 2

- [x] T027 [US2] Add `ModelCapabilities` dataclass to `ado2gh/agents/migration_agent/llm_bridge.py` with `supports_tool_calling`, `supports_streaming`, `supports_thinking`, `max_context_tokens` fields
- [x] T028 [US2] Update `LLMModelStore` (in `ado2gh/api/llm/`) to accept optional `capabilities` field on model registration — validate `supports_tool_calling=true`, raise `ModelCapabilityError` if false
- [x] T029 [US2] Update `resolve_langchain_llm()` in `ado2gh/agents/migration_agent/llm_bridge.py` — read capabilities, configure `streaming` on ChatModel, set `max_token_budget` from `max_context_tokens`, return capabilities alongside ChatModel
- [x] T030 [US2] Add streaming fallback in `ado2gh/agents/migration_agent/nodes.py` — if `astream()` unavailable or raises, use `invoke()` and emit complete response as single `kind: "token"` SSE event
- [x] T031 [US2] Add thinking extraction in `ado2gh/agents/migration_agent/nodes.py` — for models with `supports_thinking=true`, extract from `AIMessage.additional_kwargs`/`response_metadata` and emit `kind: "thinking"` SSE events
- [x] T032 [US2] Add LangChain timeout configuration in `ado2gh/agents/migration_agent/llm_bridge.py` — use LangChain's built-in `timeout` parameter where supported, fallback to `asyncio.wait_for` for providers without native timeout

**Checkpoint**: System works with any tool-calling-capable LangChain model, adapts to capabilities

---

## Phase 5: User Story 3 - Planner Node & Tools (Priority: P2)

**Goal**: Planner node generates migration plans using LangChain ChatModel with bound tools, streams planning thoughts, creates topological dependency ordering

**Independent Test**: Send a migration action message and verify the Planner node generates a structured plan with repo order, pipeline mappings, and per-repo work items

### Tests for User Story 3 (REQUIRED) ⚠️

- [x] T033 [P] [US3] Unit test for planner node in `tests/unit/test_planner_node.py` — plan generation, revised plans from validator feedback, assumptions documentation, rate-limit awareness
- [x] T034 [P] [US3] Unit test for tool bindings in `tests/unit/test_tool_bindings.py` — planner tools bound correctly, role-based access enforced, `bind_tools()` integration

### Implementation for User Story 3

- [x] T035 [P] [US3] Create `ado2gh/agents/migration_agent/tools/__init__.py` with shared tool utilities
- [x] T036 [P] [US3] Create `ado2gh/agents/migration_agent/tools/planner_tools.py` — LangChain `StructuredTool` instances for planner (read-only ADO, read-only GitHub, discovery); wrap with guardrail validator from `guardrails.py`
- [x] T037 [US3] Add `planner_node` function to `ado2gh/agents/migration_agent/nodes.py` — bind planner tools via `bind_tools()`, use `astream()` for planning thoughts, generate `MigrationPlan` with topological sort, store in `AgentState.migration_plan`, handle `validation_feedback` for revised plans
- [x] T038 [US3] Add `pending_clarification` handling to planner node — set `AgentState.pending_clarification` with `to_role: "orchestrator"` when discovery data is insufficient

**Checkpoint**: Planner generates structured migration plans with streaming thoughts

---

## Phase 6: User Story 4 - Executor Node & Guardrails (Priority: P2)

**Goal**: Executor node performs migration operations using LangChain tools with guardrail enforcement, tracks rollback records, streams execution thoughts

**Independent Test**: Provide a migration plan and verify the Executor executes operations through guardrails, produces `ExecutorResult`, and tracks rollback records

### Tests for User Story 4 (REQUIRED) ⚠️

- [x] T039 [P] [US4] Unit test for executor node in `tests/unit/test_executor_node.py` — execution of all resource types, idempotency, rollback tracking, clarification requests to planner
- [x] T040 [P] [US4] Unit test for guardrails in `tests/unit/test_guardrails.py` — plan authorization, resource validation, deletion confirmation, hallucinated resource rejection, audit logging

### Implementation for User Story 4

- [x] T041 [P] [US4] Create `ado2gh/agents/migration_agent/tools/executor_tools.py` — LangChain `StructuredTool` instances for executor (read-only ADO, read/write GitHub, migration operations: git mirror, pipeline conversion, Bicep/ARM hybrid transform with auto-transform for supported constructs, LLM best-effort with web search for unsupported, gap reporting, secret provisioning, service connections, Boards, Test Plans, Artifacts, Wiki); wrap with guardrail validator
- [x] T042 [US4] Add `executor_node` function to `ado2gh/agents/migration_agent/nodes.py` — bind executor tools, use `astream()` for execution thoughts, execute migration operations deterministically, produce `ExecutorResult`, track `RollbackRecord` entries, handle idempotency, route via accelerator API for existing ops and direct API for new resource types, implement Bicep/ARM three-tier hybrid approach (auto-transform supported constructs, LLM best-effort with web search for unsupported, report remaining gaps per FR-053)
- [x] T043 [US4] Add `execute_tools` hybrid node to `ado2gh/agents/migration_agent/nodes.py` — use LangGraph `ToolNode` for simple read-only tools, custom execution for write tools and complex flows (plan building, PEV start, forms)
- [x] T044 [US4] Integrate guardrail wrapper from `guardrails.py` into executor tool execution — every GitHub write passes through guardrail validation, blocked calls return error to executor without executing

**Checkpoint**: Executor performs migration operations with guardrail enforcement

---

## Phase 7: User Story 5 - Validator Node & Feedback (Priority: P2)

**Goal**: Validator node verifies migration outcomes using API calls and local validation, sends structured feedback to Planner, reports pass/fail per scope

**Independent Test**: Provide an `ExecutorResult` and verify the Validator produces `ValidationResult` with per-scope pass/fail, evidence, and `ValidationFeedback` for failures

### Tests for User Story 5 (REQUIRED) ⚠️

- [x] T045 [P] [US5] Unit test for validator node in `tests/unit/test_validator_node.py` — all validation checks (git SHA parity, workflow YAML, secrets, service connections, dependencies, Boards, Test Plans, Artifacts, Wiki), plan-vs-execution consistency, local workflow simulation only

### Implementation for User Story 5

- [x] T046 [P] [US5] Create `ado2gh/agents/migration_agent/tools/validator_tools.py` — LangChain `StructuredTool` instances for validator (read-only ADO, read-only GitHub, validation checks); wrap with guardrail validator
- [x] T047 [US5] Add `validator_node` function to `ado2gh/agents/migration_agent/nodes.py` — bind validator tools, use `astream()` for validation thoughts, verify migration outcomes via API calls and local validation, produce `ValidationResult` with per-scope pass/fail, set `AgentState.validation_feedback` for failures, validate no operations outside approved plan

**Checkpoint**: Validator checks migration outcomes and provides structured feedback

---

## Phase 8: User Story 6 - PEV Loop & Conditional Edges (Priority: P2)

**Goal**: Continuous PEV loop via LangGraph conditional edges with retry logic, iteration limits, batch queue, and inter-agent messaging

**Independent Test**: Run a migration requiring 2 PEV cycles and verify the graph routes Planner → Executor → Validator → (fail) → Planner → Executor → Validator → (pass) → Orchestrator

### Tests for User Story 6 (REQUIRED) ⚠️

- [x] T048 [P] [US6] Unit test for conditional edges in `tests/unit/test_conditional_edges.py` — all routing scenarios: orchestrator→planner, planner→executor, executor→validator, validator→planner (retry), validator→orchestrator (complete/failed), pending_clarification routing
- [x] T049 [P] [US6] Integration test for PEV loop in `tests/integration/test_pev_loop_integration.py` — full PEV cycle via LangGraph, retry count increments, max iteration enforcement, batch queue processing

### Implementation for User Story 6

- [x] T050 [US6] Add conditional edge functions to `ado2gh/agents/migration_agent/graph.py` — `route_after_classify`, `route_after_orchestrator`, `route_after_planner`, `route_after_executor`, `route_after_validator` — check `should_return`, `pending_clarification`, `validation_feedback`, `pev_retry_count`, `iteration_count`
- [x] T051 [US6] Add PEV cycle logic to graph — `pev_retry_count` increment on validation failure, `iteration_count` increment per cycle, max 3 PEV retries routes to Orchestrator with failure summary, max 20 iterations routes to Orchestrator with partial completion
- [x] T052 [US6] Add inter-agent messaging to nodes — `InterAgentMessage` creation in `AgentState.inter_agent_messages` (via `operator.add` reducer) with `message_type`, `from_role`, `to_role`, `payload`, `correlation_ids`
- [x] T053 [US6] Add batch migration queue to planner node — `MigrationQueue` with per-repo work items ordered by topological dependency, stored in `AgentState.migration_queue`, executor processes sequentially, validator validates each repo before next
- [x] T054 [US6] Add `PevCycleSummary` generation after each PEV cycle — compact JSON with cycle_number, repos_processed/succeeded/failed, failures, next_action; stored in `AgentState.cycle_summaries` via `operator.add` reducer
- [x] T055 [US6] Add context window management to nodes — use LangChain `trim_messages` from `langchain_core.messages`, configure with `max_token_budget` from capabilities, keep last 2 PEV cycles full + JSON summary of prior, verify no token overflow during 50+ repo batch migration (SC-017)

**Checkpoint**: Full PEV loop cycles through agents with retry logic and batch processing

---

## Phase 9: User Story 7 - Session Persistence & Resume (Priority: P2)

**Goal**: Sessions survive server restarts via LangGraph checkpointing; form submission resumes graph from checkpoint; state machine enforces valid transitions

**Independent Test**: Start a migration, kill the server, restart, and verify the session resumes from the last checkpoint. Submit a form and verify the graph resumes via checkpoint.

### Tests for User Story 7 (REQUIRED) ⚠️

- [x] T056 [P] [US7] Unit test for session state in `tests/unit/test_session_state.py` — all valid transitions succeed, invalid transitions raise `InvalidTransitionError`, LangGraph node entry drives transitions
- [x] T057 [P] [US7] Integration test for checkpoint resume in `tests/integration/test_checkpoint_resume.py` — kill server mid-migration, restart, load checkpoint from `SqliteSaver`, resume graph from last node
- [x] T058 [P] [US7] Integration test for graph execution in `tests/integration/test_graph_execution.py` — end-to-end graph execution, form submission via checkpoint resume, 10 concurrent sessions (SC-014), cross-session repo lock detection (SC-020)

### Implementation for User Story 7

- [x] T059 [US7] Configure LangGraph checkpointer in `ado2gh/agents/migration_agent/graph.py` — `SqliteSaver` co-located with session DB (default), `PostgresSaver` when `ADO2GH_STORAGE_BACKEND=postgresql`, attach to compiled graph
- [x] T060 [US7] Add checkpoint-based form resume to `services/agent/routes/session_routes.py` — `POST /v1/sessions/{id}/form-submit` loads checkpoint via `thread_id`, injects form values into `AgentState`, resumes graph from checkpoint
- [x] T061 [US7] Add server restart recovery to `services/agent/main.py` — on startup, load checkpoints from `SqliteSaver`, reconstruct `AgentState` for active sessions, resume graphs from last checkpointed node
- [x] T062 [US7] Integrate `SessionStateMachine` into LangGraph nodes — node entry calls `transition()` to update session status, node exit sets appropriate next state
- [x] T063 [US7] Add repo-level lock enforcement to executor node — acquire lock via `repo_lock_store` before migrating, release on PEV completion/failure/session termination, prevent concurrent migration of same repo across sessions, test cross-session lock detection (SC-020)
- [x] T064 [US7] Add session store persistence — persist session metadata, all messages, migration plans (all revisions), PEV cycle summaries, guardrail decisions, iteration counters, repo locks to `session_store.py`

**Checkpoint**: Sessions persist across restarts, forms resume via checkpoint, state machine enforced

---

## Phase 10: User Story 8 - UI Streaming Display (Priority: P3)

**Goal**: UI displays streaming LLM thinking tokens in real time with per-agent labels, collapsible thinking blocks, distinct visual styling, auto-scroll

**Independent Test**: Open the Agent tab, send a migration message, and verify streaming thinking blocks appear with agent role labels, collapsible blocks, and distinct styling

### Tests for User Story 8 (REQUIRED) ⚠️

- [x] T065 [P] [US8] Contract test for SSE events in `tests/contract/test_langgraph_contracts.py` — verify SSE event schema (token, thinking, tool_call, tool_result, status, heartbeat, message, form_request, done)

### Implementation for User Story 8

- [x] T066 [US8] Update `apps/migration-ui/src/lib/types/agent.ts` — add `ModelCapabilities` type, `StreamingTokenEvent` type, `SSEEventKind` enum (token, thinking, tool_call, tool_result, status, heartbeat), update `AgentMessageType` to include thinking
- [x] T067 [US8] Update `apps/migration-ui/src/lib/agent.ts` — extend `streamAgentMessage` to handle new SSE event types (token, thinking, tool_call, tool_result, status, heartbeat), add heartbeat handling for connection keepalive, add `ModelCapabilities` to session response type
- [x] T068 [US8] Rewrite `apps/migration-ui/src/components/AgentChat.tsx` — render streaming thinking tokens in real time as collapsible blocks per agent role, muted/secondary styling for thinking blocks, primary chat bubble for user-facing messages, auto-scroll with position preservation, working indicator during PEV with expandable agent details, form rendering as inline cards
- [x] T069 [US8] Add UI states to `AgentChat.tsx` — LLM unconfigured (setup prompt + link to settings), empty discovery (scan prompt), error state (actionable summary with retry), non-streaming model (complete block without typing effect)

**Checkpoint**: UI shows real-time streaming thinking blocks with agent labels and distinct styling

---

## Phase 11: User Story 9 - Cross-Cutting & Cleanup (Priority: P3)

**Goal**: Metrics/health endpoints, legacy module deletion, final validation, coverage verification

**Independent Test**: Verify `/metrics` and `/health` endpoints work, all legacy modules deleted, 85% coverage maintained, quickstart validation passes

### Tests for User Story 9 (REQUIRED) ⚠️

- [x] T070 [P] [US9] Additional unit tests in `tests/unit/` for any modules below 85% coverage and cancellation flow test (SC-023: rollback/stop options on cancel) — verify `pytest --cov=ado2gh --cov-report=term-missing` shows >= 85%

### Implementation for User Story 9

- [x] T071 [US9] Add Prometheus-compatible `/metrics` endpoint to `services/agent/main.py` — active_sessions, pev_cycles_total, llm_call_duration_seconds (histogram), guardrail_blocks_total, tool_calls_total, session_status_counts
- [x] T072 [US9] Update `/health` endpoint in `services/agent/main.py` — report LLM provider availability, active session count, storage backend connectivity, checkpointer status, graph compiled status, current configuration
- [x] T073 [US9] Delete legacy modules from `ado2gh/agents/`: `langgraph_agent/`, `orchestration/`, `local/`, `planner.py`, `executor.py`, `validator.py`, `pev_cycle.py`, `pev_coordinator.py`, `session_orchestrator.py`, `llm_provider.py`, `agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`, `session_state_machine.py`, `session_store.py`, `skills/`
- [x] T074 [US9] Delete `services/agent/routes/_shared.py` — ensure all needed functions are in `migration_agent/` or inlined into route handlers
- [x] T075 [US9] Update all imports across codebase — replace `langgraph_agent` imports with `migration_agent`, remove `llm_provider` imports, remove `orchestration` imports, remove `session_orchestrator` imports, remove `_shared` imports
- [x] T076 [US9] Add cancellation and rollback to orchestrator node — user triggers cancellation by sending a "cancel" message or clicking a cancel button in the UI; orchestrator presents rollback/stop options via dynamic form; executor deletes session-created resources via `rollback_records` if rollback chosen (FR-100, FR-101, SC-023)
- [x] T077 [US9] Run quickstart.md validation scenarios — execute all V1-V12 validation scenarios from `specs/012-langgraph-agent-refactor/quickstart.md`
- [x] T078 [US9] Verify 85% coverage on `ado2gh` package — run `pytest --cov=ado2gh --cov-report=term-missing`, add tests for any gaps

**Checkpoint**: All legacy code deleted, metrics/health working, coverage maintained, quickstart passes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — core graph, orchestrator, streaming, routes
- **US2 (Phase 4)**: Depends on US1 — extends LLM bridge and nodes with capabilities
- **US3 (Phase 5)**: Depends on US1 — planner node extends the graph
- **US4 (Phase 6)**: Depends on US3 — executor uses planner's migration plan
- **US5 (Phase 7)**: Depends on US4 — validator checks executor's results
- **US6 (Phase 8)**: Depends on US3, US4, US5 — PEV loop connects all three nodes
- **US7 (Phase 9)**: Depends on US6 — checkpointing persists the full PEV loop
- **US8 (Phase 10)**: Depends on US1 — UI consumes SSE events from the graph
- **US9 (Phase 11)**: Depends on all user stories — cleanup, metrics, final validation

### User Story Dependencies

- **US1 (P1)**: Foundation → core graph infrastructure (MVP)
- **US2 (P1)**: US1 → model capabilities extend the LLM bridge
- **US3 (P2)**: US1 → planner node adds to the graph
- **US4 (P2)**: US3 → executor depends on planner's output
- **US5 (P2)**: US4 → validator depends on executor's output
- **US6 (P2)**: US3 + US4 + US5 → PEV loop connects all three
- **US7 (P2)**: US6 → checkpointing persists the full loop
- **US8 (P3)**: US1 → UI consumes SSE (can start in parallel with US2-US7)
- **US9 (P3)**: All stories → cleanup after everything works

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models/entities before services
- Services before endpoints/nodes
- Node implementation before graph integration
- Story complete before moving to next priority

### Parallel Opportunities

- Setup tasks T003-T007 can run in parallel (different files)
- Foundational tasks T009-T012 can run in parallel (different files)
- US1 tests T015-T018 can run in parallel
- US2 tests T025-T026 can run in parallel
- US3 tool creation T035-T036 can run in parallel with test creation
- US4 tool creation T041 can run in parallel with test creation
- US5 tool creation T046 can run in parallel with test creation
- US6 tests T048-T049 can run in parallel
- US7 tests T056-T058 can run in parallel
- US8 can run in parallel with US2-US7 (different codebase: frontend vs backend)

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Unit test for graph structure in tests/unit/test_graph_structure.py"
Task: "Unit test for state reducers in tests/unit/test_state_reducers.py"
Task: "Unit test for orchestrator node in tests/unit/test_orchestrator_node.py"
Task: "Unit test for streaming in tests/unit/test_streaming.py"

# Launch parallel setup tasks:
Task: "Copy and refactor state.py to migration_agent/state.py"
Task: "Copy and refactor llm_bridge.py to migration_agent/llm_bridge.py"
Task: "Create constants.py"
Task: "Move skill .md files to migration_agent/prompts/"
Task: "Create prompts.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (package structure, deps, copied files)
2. Complete Phase 2: Foundational (utils, forms, policies, state, store, guardrails, graph)
3. Complete Phase 3: User Story 1 (orchestrator node, streaming, routes)
4. **STOP and VALIDATE**: Send a general chat message, verify SSE streaming works
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Test independently → MVP (chat works with streaming)
3. Add US2 → Test independently → Any model works with capabilities
4. Add US3 → Test independently → Planner generates plans
5. Add US4 → Test independently → Executor runs migrations
6. Add US5 → Test independently → Validator checks results
7. Add US6 → Test independently → Full PEV loop cycles
8. Add US7 → Test independently → Sessions survive restarts
9. Add US8 → Test independently → UI shows streaming thinking
10. Add US9 → Final cleanup, metrics, validation

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 → US2 → US3 (backend core)
   - Developer B: US8 (frontend, parallel with backend)
3. After US3:
   - Developer A: US4 → US5 → US6 (PEV chain)
   - Developer C: US7 (persistence, parallel with PEV)
4. Final: US9 (cleanup, all together)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- The `langgraph_agent/` package stays until US9 deletes it — do NOT delete during earlier phases
- All new tests use descriptive names (no `test_012_` prefix)
- Legacy modules are deleted in US9 (T073-T075) — not during earlier phases

---

## Phase 12: Convergence

**Purpose**: Close gaps found during `/speckit.converge` assessment — legacy module deletion, import migration, and metrics completion.

- [x] T079 [US9] Delete legacy agent modules from `ado2gh/agents/`: `orchestration/` (entire directory), `local/` (entire directory), `planner.py`, `executor.py`, `validator.py`, `pev_cycle.py`, `pev_coordinator.py`, `session_orchestrator.py`, `llm_provider.py`, `agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`, `session_state_machine.py`, `session_store.py`, `skills/` (entire directory). The `langgraph_agent/` package is retained as a compatibility shim (per T078). Verify no production code imports from deleted modules after migration.
- [x] T080 [US9] Delete `services/agent/routes/_shared.py` — ensure all needed functions are either in `migration_agent/` submodules or inlined into route handlers. Before deletion, audit all imports from `_shared.py` across `main.py`, `session_routes.py`, `run_routes.py`, and `pev_engine.py`.
- [x] T081 [US9] Delete `services/agent/routes/pev_engine.py` — this legacy PEV engine (452 lines) imports from `_shared.py`, `llm_provider`, and `pev_coordinator`. PEV logic is now handled by the LangGraph graph in `migration_agent/`. Remove any route registrations that reference it.
- [x] T082 [US9] Update `services/agent/main.py` imports — replace all `_shared.py` imports with equivalents from `migration_agent/` submodules or inline. Remove imports of `pev_loop`, `_build_migration_plan_impl`, `_check_accelerator_impl`, `_start_pev_run_impl`, etc. from `_shared.py`. The `/metrics` endpoint should use `migration_agent.session_store` for session counts.
- [x] T083 [US9] Update `services/agent/routes/session_routes.py` imports — replace all `_shared.py` imports with `migration_agent/` equivalents. Remove imports from `session_orchestrator.py`, `llm_provider.py`, `pev_coordinator.py`. Ensure the SSE streaming endpoint uses `migration_agent.orchestrator.stream_user_message()`.
- [x] T084 [US9] Update `ado2gh/agents/__init__.py` — remove imports of `AgentExecutor`, `AgentPlanner`, `AgentValidator` from legacy modules. Either remove these exports entirely (if no external consumers) or re-export from `migration_agent` equivalents.
- [x] T085 [US9] Add missing Prometheus metrics to `/metrics` endpoint in `services/agent/main.py` — add `pev_cycles_total` (counter), `llm_call_duration_seconds` (histogram), `guardrail_blocks_total` (counter), `tool_calls_total` (counter) per FR-102. Wire metrics collection into `migration_agent` nodes and guardrails.
- [x] T086 [US9] Run full test suite after legacy deletion — execute `pytest tests/` and verify all tests pass. Fix any broken imports or references to deleted modules. Ensure coverage remains >= 85% on `ado2gh` package.

---

## Phase 13: Convergence (Round 2)

**Purpose**: Close remaining gaps found during second `/speckit.converge` assessment — remaining legacy modules and import cleanup.

- [x] T087 [US9] Delete remaining legacy agent modules from `ado2gh/agents/`: `pev_coordinator.py`, `llm_provider.py`, `failure_analysis.py` (used only by pev_engine.py). Verify no production code imports from these modules.
- [x] T088 [US9] Delete `services/agent/routes/_shared.py` — audit and migrate all functions used by `main.py`, `session_routes.py`, `run_routes.py`, `mcp_routes.py` to `migration_agent/` submodules or inline into route handlers.
- [x] T089 [US9] Delete `services/agent/routes/pev_engine.py` — remove route registrations that reference it. PEV logic is now handled by the LangGraph graph in `migration_agent/`.
- [x] T090 [US9] Update `services/agent/main.py` imports — replace all `_shared.py` imports with equivalents from `migration_agent/` submodules or inline. Remove imports of legacy functions.
- [x] T091 [US9] Update `services/agent/routes/session_routes.py` imports — replace all `_shared.py` imports with `migration_agent/` equivalents. Remove imports from `llm_provider.py`, `pev_coordinator.py`.
- [x] T092 [US9] Update `services/agent/routes/run_routes.py` imports — replace all `_shared.py` imports with `migration_agent/` equivalents.
- [x] T093 [US9] Update `services/agent/routes/mcp_routes.py` imports — replace all `_shared.py` imports with `migration_agent/` equivalents.
- [x] T094 [US9] Update `ado2gh/agents/__init__.py` — verify no exports of legacy AgentExecutor/AgentPlanner/AgentValidator classes remain.
- [x] T095 [US9] Run full test suite after legacy deletion — execute `pytest tests/` and verify all tests pass. Fix any broken imports or references to deleted modules. Ensure coverage remains >= 85% on `ado2gh` package.

---

## Phase 14: Convergence (Round 3)

**Purpose**: Close remaining gap found during third `/speckit.converge` assessment — replace custom context window implementation with LangChain's built-in `trim_messages`.

- [x] T096 [US6] Replace custom context window implementation in `ado2gh/agents/migration_agent/context_window.py` with LangChain's built-in `trim_messages` from `langchain_core.messages` per FR-076, FR-077, T055, and Q49. The custom `trim_context()`, `_estimate_tokens()`, and `build_context_with_cycle_summaries()` functions should be replaced with LangChain's `trim_messages()` configured with appropriate strategy (keep last 2 PEV cycles full + JSON summary of prior cycles). Update `nodes.py` to import and use the LangChain function instead of the custom implementation.

---

## Phase 15: Convergence (Round 4)

**Purpose**: Close remaining gaps found during fourth `/speckit.converge` assessment — integrate context window trimming, repo locking, and sequential batch processing.

- [x] T097 [US6] Integrate context window trimming in agent nodes per FR-076, FR-077. In each agent node (orchestrator, planner, executor, validator) before LLM calls, retrieve `state.get("messages")` and `state.get("cycle_summaries")`, call `build_context_with_cycle_summaries(messages, cycle_summaries, state.get("max_token_budget"))` to trim context within token budget, and use the trimmed messages for the LLM invocation. Ensure the sliding window strategy (last 2 PEV cycles full + JSON summary of prior cycles) is applied.
- [x] T098 [US9] Integrate repo-level locking in executor node per FR-075. Before migrating each repo in the executor node, call `session_store.acquire_repo_lock(session_id, repository_id)`. If the lock acquisition fails (returns False), skip the repo and log a clear message that the repo is locked by another session. Release locks via `session_store.release_repo_lock(session_id, repository_id)` on PEV cycle completion, failure, or session termination. Call `session_store.release_all_locks(session_id)` in the finalize node or orchestrator on session end.
- [x] T099 [US6] Implement sequential batch processing in executor node per FR-080. Modify the executor to process repos sequentially from the migration queue (one repo at a time), call the validator node after each repo completion before proceeding to the next repo. The queue state (`current_index`, `completed_items`, `failed_items`) should be updated after each repo and persisted in LangGraph checkpoints.

---

## Phase 16: Convergence (Round 5)

**Purpose**: Close remaining gaps found during fifth `/speckit.converge` assessment — fix sequential validation routing, implement rollback execution, complete metrics, verify UI states.

- [x] T100 [US6] Fix sequential batch processing routing per FR-080. The current executor processes one repo at a time but advances the queue index and returns, expecting validator to run. However, the graph should route executor → validator → executor repeatedly for each repo. Modify executor to NOT advance queue index after processing a repo, return with executor_result for that single repo, let validator run, then executor resumes with next repo. Update `_route_after_executor` to route back to executor when migration_queue has remaining items and validation passed.
- [x] T101 [US9] Implement rollback execution per FR-100, FR-101. When user selects rollback in the cancellation form, the executor should iterate through `state.get("rollback_records")` and delete each GitHub resource via the accelerator API. Add a rollback tool or executor function that calls the accelerator's rollback endpoint or direct GitHub API deletion. Ensure guardrail layer verifies only session-created resources are deleted. Update orchestrator to handle rollback form submission and trigger executor with rollback flag.
- [x] T102 [US6] Complete metrics endpoint per FR-102. Add missing metrics to the metrics collector in `ado2gh/agents/metrics.py`: `guardrail_blocks_total` (counter), `tool_calls_total` (counter), `llm_call_duration_seconds` (histogram). Update guardrails.py to increment guardrail_blocks_total on each block decision. Update nodes.py or tool catalog to increment tool_calls_total on each tool invocation. Add timing around LLM calls for the histogram.
- [x] T103 [US6] Verify and complete UI states per FR-081-FR-088. Check AgentChat.tsx for: model selector dropdown, LLM unconfigured state display, empty discovery state with scan prompt, error state with retry/contact-admin options. Add any missing UI states or ensure they are properly rendered.

---

## Phase 17: Convergence (Round 6)

**Purpose**: Close remaining gap found during sixth `/speckit.converge` assessment — validator node tool binding.

- [x] T104 [US5] Bind validator tools to LLM in validator node per FR-057, FR-027. The validator node currently does not bind its tools to the LLM via `bind_tools()`. Import `get_validator_tools` from `migration_agent.tools`, call it with `accel_get` and `session_token`, and bind the tools to the LLM using `llm.bind_tools(validator_tools)` when `capabilities.supports_tool_calling` is true, similar to the planner node implementation. This enables the validator to use LangChain native tool-calling for validation checks.

---

## Phase 18: Convergence (Round 7)

**Purpose**: Close remaining gap found during seventh `/speckit.converge` assessment — intent classification streaming.

- [x] T105 [US1] Stream thinking tokens during intent classification per FR-032. The `classify_intent_node` currently uses `llm.ainvoke()` for LLM-enhanced classification, but FR-032 requires using `astream()` to stream thinking tokens during classification. Replace the `llm.ainvoke(trimmed_messages)` call with `_stream_llm_response(llm, trimmed_messages, state, subagent="orchestrator", capabilities=capabilities)` to enable streaming thinking tokens during intent classification.

---

## Phase 19: Convergence (Round 8)

**Purpose**: Close remaining gap found during eighth `/speckit.converge` assessment — executor node LLM reasoning and tool binding.

- [x] T106 [US4] Refactor executor node to use LLM reasoning and tool binding per FR-047, FR-027. The current executor node directly calls `_execute_scope()` for deterministic execution without using the LLM for reasoning. FR-047 requires the executor to use the LangChain ChatModel for reasoning with tool-calling and stream execution thoughts via `astream()`. Import `get_executor_tools` from `migration_agent.tools`, bind tools to the LLM using `llm.bind_tools(executor_tools)` when `capabilities.supports_tool_calling` is true, and use `_stream_llm_response()` to stream execution thoughts. The LLM should determine which tools to call based on the migration plan, and tool execution should be handled via the LangChain tool-calling protocol.

---

## Phase 20: Convergence (Round 9)

**Purpose**: Close remaining gap found during ninth `/speckit.converge` assessment — executor node LLM-driven execution.

- [x] T107 [US4] Implement LLM-driven execution in executor node per FR-047. The executor node currently binds tools to the LLM but still directly calls `_execute_scope()` in a loop for deterministic execution. FR-047 requires the executor to use the LangChain ChatModel for reasoning with tool-calling and stream execution thoughts via `astream()`. Build an execution prompt that includes the migration plan and current work items, use `_stream_llm_response(llm_with_tools, messages, state, subagent="executor", capabilities=capabilities)` to let the LLM determine which tools to call, and process tool calls via LangChain's tool-calling protocol. The executor should stream execution thoughts as the LLM reasons about which operations to perform.

---

## Phase 21: Convergence (Round 10)

**Purpose**: Close remaining gaps found during tenth `/speckit.converge` assessment — SSE event emission for tool_result and status, executor→planner clarification routing, dry-run toggle UI, SSE type completeness, and dead code cleanup.

- [x] T108 [US1] Emit `kind: "tool_result"` SSE events from `stream_user_message()` in `ado2gh/agents/migration_agent/orchestrator.py` per FR-024. Tool results are currently appended to `session["messages"]` via `_append_event()` with `kind="tool_result"` but are never extracted from graph state updates and yielded via SSE. Modify `stream_user_message()` to detect tool_result events in node state updates (e.g., by checking for a `_tool_results` key in the update dict or by diffing session messages before/after each node execution) and yield them as SSE events with `kind: "tool_result"`, `content: <result summary>`, `subagent: <agent_role>`, and `meta: {tool_name, guardrail_decision}`. Ensure secrets are masked in result summaries. Also add `'tool_result'` to the `SSEEventKind` union type in `apps/migration-ui/src/lib/types/agent.ts`.

- [x] T109 [US1] Emit `kind: "status"` SSE events from `stream_user_message()` in `ado2gh/agents/migration_agent/orchestrator.py` per FR-025. Status messages (e.g., "Planner is generating migration plan...", "Executor is running migration...", "Validator is checking results...") are currently appended to `session["messages"]` via `_append_status_message()` but are never extracted from graph state updates and yielded via SSE. Modify `stream_user_message()` to detect status events in node state updates and yield them as SSE events with `kind: "status"`, `content: <status text>`, and `subagent: <agent_role>`. Also add `'status'` to the `SSEEventKind` union type in `apps/migration-ui/src/lib/types/agent.ts`.

- [x] T110 [US6] Fix executor→planner clarification routing in `ado2gh/agents/migration_agent/graph.py` per FR-006, FR-070. The `_route_after_executor` function currently always routes to `"orchestrator"` when `pending_clarification` is set, regardless of the `to_role` field. FR-006 requires that when `to_role == "planner"`, the graph routes back to the Planner node without involving the user. Modify `_route_after_executor` to check `pending_clarification.get("to_role")` and return `"planner"` when `to_role == "planner"`. Add `"planner": NODE_PLANNER` to the conditional edges mapping for the executor node in `_build_graph()`.

- [x] T111 [US8] Render dry-run toggle UI element in `apps/migration-ui/src/components/AgentChat.tsx` per FR-081. The `handleDryRunModeChange` function is defined (line 401) and `dryRunMode` state is used when creating sessions, but no visible toggle is rendered in the chat interface. Add a dry-run toggle (checkbox or switch) in the chat composer area alongside the model selector, labeled clearly (e.g., "Dry run" / "Live"), that calls `handleDryRunModeChange` on change. The toggle should be disabled when a session is in an active state (planning, executing, validating).

- [x] T112 [US8] Add `'done'` to `SSEEventKind` union type in `apps/migration-ui/src/lib/types/agent.ts` per FR-022, SSE contract. The `orchestrator.py:stream_user_message()` emits `kind: "done"` events (line 217) but `SSEEventKind` only includes `'__done__'` (a boolean flag on the event object, not a kind value). Add `'done'` to the `SSEEventKind` union to resolve the type mismatch.

- [x] T113 [US9] Remove `pev_loop` stub from `ado2gh/agents/migration_agent/route_helpers.py` and its call site in `services/agent/routes/run_routes.py` per spec requirement "remove `pev_loop()` entirely". The no-op stub `pev_loop()` at `route_helpers.py:404` is still called from `run_routes.py:129` via `asyncio.create_task(pev_loop(run_id, req))`. Remove the function definition, the import in `run_routes.py`, and the `asyncio.create_task` call. Also remove the `pev_loop` import from `services/agent/main.py`.

- [x] T114 [US9] Remove dead code from `ado2gh/agents/migration_agent/context_window.py` per T096. The `trim_context()` function (lines 70-90) and `_estimate_tokens()` function (lines 64-68) are legacy custom implementations that were supposed to be replaced by LangChain's `trim_messages`. They are no longer used in production code (only in tests). Remove both functions and update any test references to use `build_context_with_cycle_summaries()` or `trim_messages()` directly.
