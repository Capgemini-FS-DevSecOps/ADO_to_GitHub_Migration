# Quickstart Validation: LangGraph Agent Refactor

**Feature**: 012-langgraph-agent-refactor
**Date**: 2026-06-24

## Prerequisites

- Python 3.9+ with `pip install -e ".[api,dev]"` and LangChain/LangGraph deps installed
- Node.js 18+ for the migration UI
- At least one LLM model configured in Settings (OpenAI, Anthropic, or stub)
- `ADO2GH_STORAGE_BACKEND` set (default: sqlite)
- Agent API running: `.\scripts\dev\run-local-agent.ps1`
- UI running: `.\scripts\dev\run-ui.ps1`

## Validation Scenarios

### V1: Graph Structure Verification

**Validates**: FR-001, FR-002, SC-001 — Four agent nodes in single StateGraph with conditional edges.

```bash
# Run the graph structure test
pytest tests/unit/test_graph_structure.py -v
```

**Expected**: Test confirms the compiled graph contains nodes: `classify_intent`, `orchestrator`, `planner`, `executor`, `validator`, `execute_tools`, `finalize`. Conditional edges are present between the correct nodes. Recursion limit >= 40.

### V2: Model Capability Validation

**Validates**: FR-016a, FR-016b, FR-016c, SC-006a — Tool-calling required, capabilities detected.

```bash
# Run capability validation tests
pytest tests/unit/test_model_capabilities.py -v
```

**Expected**: Tests confirm: (1) registering a model with `supports_tool_calling=false` raises `ModelCapabilityError`, (2) `llm_bridge` reads capabilities and configures streaming/thinking/budget, (3) models without explicit capabilities default correctly.

### V3: SSE Streaming Verification

**Validates**: FR-017, FR-022, SC-004 — Token streaming and thinking events via SSE.

```bash
# Start the agent API
.\scripts\dev\run-local-agent.ps1

# In another terminal, connect to SSE endpoint
# (requires a session to be created first)
curl -N -X POST http://localhost:8090/v1/sessions/{session_id}/message-stream \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "dry_run": true}'
```

**Expected**: SSE events arrive with `kind: "token"` containing incremental content chunks and `subagent` labels. Heartbeat events arrive every 15 seconds. For models with `supports_thinking=true`, `kind: "thinking"` events appear.

### V4: PEV Loop Execution

**Validates**: FR-002, FR-008, FR-009, SC-002 — Continuous PEV loop with retry logic.

```bash
# Run the PEV loop integration test
pytest tests/integration/test_pev_loop_integration.py -v
```

**Expected**: Test runs a migration that requires 2 PEV cycles. Graph routes: Planner → Executor → Validator → (fail) → Planner → Executor → Validator → (pass) → Orchestrator. `pev_retry_count` increments correctly. Exceeding 3 retries routes to Orchestrator with failure summary.

### V5: Checkpoint and Resume

**Validates**: FR-071, FR-072, SC-013 — Session survives restart via LangGraph checkpoint.

```bash
# Run checkpoint resume test
pytest tests/integration/test_checkpoint_resume.py -v
```

**Expected**: Test starts a migration, simulates server restart (clears in-memory state), loads checkpoint from `SqliteSaver`, and resumes the graph from the last checkpointed node. Session state is fully reconstructed.

### V6: Dynamic Form Checkpoint Resume

**Validates**: FR-034, FR-035 — Form submission resumes graph via checkpoint.

```bash
# Run via the UI or API:
# 1. Send a migration action message
# 2. Receive a form_request SSE event
# 3. Submit the form via POST /v1/sessions/{id}/form-submit
# 4. Confirm graph resumes from checkpoint
pytest tests/integration/test_graph_execution.py -v
```

**Expected**: Form submission loads the LangGraph checkpoint, injects form values into `AgentState`, and resumes the graph. The graph routes from Orchestrator to Planner (if form was plan confirmation).

### V7: Multi-Provider Support

**Validates**: FR-011, FR-012, FR-013, SC-006 — Any LangChain-compatible provider works.

```bash
# Configure different models in Settings and run:
pytest tests/unit/test_llm_bridge.py -v
```

**Expected**: Tests confirm `llm_bridge.resolve_langchain_llm()` returns correct `BaseChatModel` instances for OpenAI, Anthropic, Azure OpenAI, OpenAI-compatible endpoints, and stub models. All four agents share the same ChatModel instance.

### V8: Guardrail Enforcement

**Validates**: FR-028, FR-089, SC-009 — Guardrails intercept tool calls.

```bash
pytest tests/unit/test_guardrails.py -v
```

**Expected**: Tests confirm guardrail wrapper blocks unauthorized writes, deletes not in plan, and hallucinated resource names. Blocked calls return error to calling agent without executing. Audit log entries are created.

### V9: State Machine Transitions

**Validates**: FR-073, SessionStateMachine — Formal state transitions enforced.

```bash
pytest tests/unit/test_session_state.py -v
```

**Expected**: Tests confirm all valid transitions succeed and all invalid transitions raise `InvalidTransitionError`. LangGraph node entry/exit drives transitions correctly.

### V10: UI Streaming Display

**Validates**: FR-019, FR-020, FR-082, SC-005 — UI shows streaming thinking blocks.

1. Open the migration UI at `http://localhost:3000`
2. Navigate to the Agent tab
3. Send a migration message
4. Observe streaming token blocks appear in real time
5. Confirm each block is labeled with agent role (Orchestrator, Planner, Executor, Validator)
6. Confirm thinking blocks use muted/secondary styling and are collapsible
7. Confirm user-facing messages use primary chat bubble style

**Expected**: Streaming tokens render incrementally with typing indicator. Agent labels are visible. Thinking blocks are visually distinct and collapsible. Auto-scroll works but preserves position when scrolled up.

### V11: Non-Streaming Model Fallback

**Validates**: FR-015, SC-006b — Non-streaming models fall back to invoke().

```bash
# Configure a model with supports_streaming=false
# Send a message and observe the response
pytest tests/unit/test_llm_bridge.py::test_non_streaming_fallback -v
```

**Expected**: System uses `invoke()` instead of `astream()`. Complete response emitted as single `kind: "token"` SSE event. UI renders as complete block without typing effect.

### V12: Context Window Adaptation

**Validates**: FR-076, FR-077, SC-006d — Context budget adapts to model capabilities.

```bash
pytest tests/unit/test_llm_bridge.py::test_context_budget_adaptation -v
```

**Expected**: Model with `max_context_tokens=8000` gets aggressive trimming (more summaries, fewer full cycles). Model with `max_context_tokens=200000` gets lenient trimming (more full cycles). `max_token_budget` in `AgentState` matches `capabilities.max_context_tokens`.
