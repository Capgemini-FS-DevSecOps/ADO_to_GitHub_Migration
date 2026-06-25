# API Contracts: LangGraph Agent Refactor

**Feature**: 012-langgraph-agent-refactor
**Date**: 2026-06-24

## Agent Backend API (services/agent/main.py + routes/session_routes.py)

Base URL: `http://localhost:8090` (configurable via `AGENT_URL`)

### Session Management

#### POST /v1/sessions

Create a new agent session.

```json
// Request
{
  "profile_id": "lightweight",
  "prompt": "migrate Project/RepoName to GitHub",
  "dry_run": true,
  "assignment_id": "asg_abc123",
  "model_id": "gpt-4o"
}

// Response (200)
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "profile_id": "lightweight",
  "status": "thinking",
  "dry_run": true,
  "selected_model_id": "gpt-4o",
  "llm_degraded": false,
  "llm_unconfigured": false,
  "capabilities": {
    "supports_tool_calling": true,
    "supports_streaming": true,
    "supports_thinking": false,
    "max_context_tokens": 128000
  },
  "messages": [...],
  "tasks": [...],
  "pending_form": null,
  "migration_plan": null
}
```

#### GET /v1/sessions/{session_id}

Retrieve session state (polled by UI when SSE is disconnected).

```json
// Response (200)
{
  "session_id": "...",
  "status": "executing",
  "subagent": "executor",
  "dry_run": false,
  "messages": [...],
  "tasks": [...],
  "pending_form": null,
  "migration_plan": {...},
  "iteration_count": 5,
  "pev_retry_count": 1,
  "capabilities": {...}
}
```

#### POST /v1/sessions/{session_id}/form-submit

Submit a dynamic form. Resumes the LangGraph graph from checkpoint.

```json
// Request
{
  "form_id": "plan_confirmation",
  "values": {
    "approved": true,
    "dry_run": false
  }
}

// Response (200)
{
  "session_id": "...",
  "status": "planning",
  "messages": [...],
  "pending_form": null
}
```

The server loads the LangGraph checkpoint for `thread_id=session_id`, injects form values into `AgentState`, and resumes the graph from where it left off.

### SSE Streaming

#### POST /v1/sessions/{session_id}/message-stream

Stream agent events in real time via Server-Sent Events. Single long-lived connection for the entire graph execution including PEV cycles. 15-second heartbeat keepalive.

```
// Request (body)
{
  "message": "migrate all repos in wave 1",
  "dry_run": true
}

// SSE Response (text/event-stream)
event: message
data: {"kind": "status", "message": "Orchestrator is classifying intent...", "subagent": "orchestrator", "timestamp": "2026-06-24T20:00:00Z"}

event: message
data: {"kind": "token", "content": "I'll", "subagent": "orchestrator", "timestamp": "2026-06-24T20:00:01Z"}

event: message
data: {"kind": "token", "content": " help", "subagent": "orchestrator", "timestamp": "2026-06-24T20:00:01Z"}

event: message
data: {"kind": "thinking", "content": "Analyzing migration scope...", "subagent": "orchestrator", "timestamp": "2026-06-24T20:00:01Z"}

event: message
data: {"kind": "status", "message": "Planner is generating migration plan...", "subagent": "planner", "timestamp": "2026-06-24T20:00:05Z"}

event: message
data: {"kind": "token", "content": "Planning", "subagent": "planner", "timestamp": "2026-06-24T20:00:05Z"}

event: message
data: {"kind": "tool_call", "tool_name": "fetch_discovery", "args": {"project": "MyProject"}, "subagent": "planner", "timestamp": "2026-06-24T20:00:06Z"}

event: message
data: {"kind": "tool_result", "tool_name": "fetch_discovery", "result": {"repos": 12, "pipelines": 34}, "guardrail_decision": "allow", "subagent": "planner", "timestamp": "2026-06-24T20:00:07Z"}

event: message
data: {"kind": "status", "message": "Executor is running migration...", "subagent": "executor", "timestamp": "2026-06-24T20:00:10Z"}

event: message
data: {"kind": "status", "message": "Validator is checking results...", "subagent": "validator", "timestamp": "2026-06-24T20:00:15Z"}

event: message
data: {"kind": "heartbeat", "timestamp": "2026-06-24T20:00:15Z"}

event: message
data: {"kind": "message", "content": "Migration complete. 12 repos migrated successfully.", "role": "assistant", "timestamp": "2026-06-24T20:00:20Z"}

event: done
data: {"session_id": "...", "status": "completed"}
```

### SSE Event Schema

| Event | kind | Fields | Description |
|-------|------|--------|-------------|
| Token stream | `token` | content, subagent, timestamp | Raw LLM token chunk from `astream()` |
| Thinking | `thinking` | content, subagent, timestamp | Structured thinking output (models with `supports_thinking=true` only) |
| Tool call | `tool_call` | tool_name, args (masked), subagent, timestamp | Agent invoked a tool |
| Tool result | `tool_result` | tool_name, result (masked), guardrail_decision, subagent, timestamp | Tool completed |
| Status | `status` | message, subagent, timestamp | Graph node transition |
| Heartbeat | `heartbeat` | timestamp | 15s keepalive |
| User message | `message` | content, role, timestamp | User-facing message from Orchestrator |
| Form request | `form_request` | form_id, title, description, fields, timestamp | Dynamic form for user input |
| Done | (done event) | session_id, status | Graph execution complete |

### Model Registration

#### POST /v1/models

Register a new LLM model. Validates capabilities.

```json
// Request
{
  "model_id": "claude-3-5-sonnet",
  "provider": "anthropic",
  "api_key_env": "ANTHROPIC_API_KEY",
  "model_name": "claude-3-5-sonnet-20241022",
  "capabilities": {
    "supports_tool_calling": true,
    "supports_streaming": true,
    "supports_thinking": true,
    "max_context_tokens": 200000
  }
}

// Response (200)
{
  "model_id": "claude-3-5-sonnet",
  "status": "registered",
  "capabilities": {...}
}

// Response (422) — model lacks tool-calling
{
  "error": "model_capability_error",
  "message": "This model does not support tool-calling, which is required for the migration agent. Please select a model that supports function/tool calling."
}
```

### Health and Metrics

#### GET /health

```json
{
  "status": "healthy",
  "llm_provider": "available",
  "active_sessions": 3,
  "storage_backend": "sqlite",
  "checkpointer": "SqliteSaver",
  "graph_compiled": true
}
```

#### GET /metrics

Prometheus-format metrics:

```
# HELP active_sessions Number of active agent sessions
# TYPE active_sessions gauge
active_sessions 3

# HELP pev_cycles_total Total PEV cycles completed
# TYPE pev_cycles_total counter
pev_cycles_total 42

# HELP llm_call_duration_seconds LLM call duration
# TYPE llm_call_duration_seconds histogram
llm_call_duration_seconds_bucket{le="1"} 15
llm_call_duration_seconds_bucket{le="5"} 38

# HELP guardrail_blocks_total Total guardrail blocks
# TYPE guardrail_blocks_total counter
guardrail_blocks_total 2

# HELP tool_calls_total Total tool calls
# TYPE tool_calls_total counter
tool_calls_total 156
```

## LangGraph Graph Structure Contract

The compiled graph MUST contain the following nodes and edges:

**Nodes**:
- `classify_intent` — entry point, classifies user message
- `orchestrator` — handles user interaction, forms, routing
- `planner` — generates migration plans
- `executor` — executes migration operations
- `validator` — validates migration results
- `execute_tools` — executes tool calls (hybrid: ToolNode for simple, custom for complex)
- `finalize` — prepares response and routes to END

**Conditional edges**:
- `classify_intent` → `orchestrator` (migration_action) | `finalize` (general_chat, migration_info)
- `orchestrator` → `planner` (start_pev=true, pending_form=null) | `finalize` (pending_form set, should_return=true)
- `planner` → `executor` (plan complete, no pending_clarification) | `orchestrator` (pending_clarification to orchestrator)
- `executor` → `validator` (execution complete, no pending_clarification) | `planner` (pending_clarification to planner)
- `validator` → `orchestrator` (validation passed OR retries exceeded) | `planner` (validation failed, retries < max)
- Any node → `orchestrator` (pending_clarification with to_role=orchestrator) | `planner` (pending_clarification with to_role=planner)

**Checkpointer**: `SqliteSaver` (default) or `PostgresSaver` (PostgreSQL backend)
**Recursion limit**: >= 40 (2x max iterations)
**thread_id**: `session_id`
