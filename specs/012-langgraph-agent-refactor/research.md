# Research: LangGraph Agent Refactor

**Feature**: 012-langgraph-agent-refactor
**Date**: 2026-06-24

## Research Tasks

### R1: LangGraph StateGraph Architecture for Multi-Agent Systems

**Decision**: Single unified `StateGraph` with four agent nodes (Orchestrator, Planner, Executor, Validator) connected by conditional edges. The graph is compiled once at module load with a checkpointer attached. Each session uses `thread_id=session_id` to namespace checkpoint state. The graph cycles through Planner → Executor → Validator via conditional edges that check validation results, retry counts, and iteration limits.

**Rationale**: LangGraph's `StateGraph` is the idiomatic pattern for multi-agent systems. A single graph with conditional edges provides: (1) one checkpoint covering the entire loop, (2) SSE streaming that covers all agent activity, (3) no coordination overhead between separate graphs. The existing `langgraph_agent/graph.py` already demonstrates this pattern with orchestrator nodes — extending it to include PEV nodes is a natural progression.

**Alternatives considered**:
- Two-level graph (orchestrator graph + PEV subgraph) — rejected: adds complexity, splits checkpointing, complicates SSE streaming
- Separate background task for PEV — rejected: loses LangGraph's native state management and streaming, reintroduces the `pev_loop()` pattern being eliminated
- Multiple compiled graphs per session — rejected: wasteful, LangGraph's `thread_id` namespace handles per-session isolation natively

### R2: LangChain BaseChatModel as Unified LLM Interface

**Decision**: Use LangChain `BaseChatModel` directly in all nodes. The `llm_bridge.py` resolves model configs from `LLMModelStore` and returns `BaseChatModel` instances (`ChatOpenAI`, `ChatAnthropic`, `AzureChatOpenAI`, etc.). All four agents share the same ChatModel instance, differentiated by system prompts and tool bindings. The legacy `LLMProvider` class is deleted entirely; all callers migrate to `llm_bridge`.

**Rationale**: LangChain's `BaseChatModel` provides a provider-agnostic interface with native streaming (`astream()`), tool-calling (`bind_tools()`), and timeout configuration. The existing `llm_bridge.py` already demonstrates the resolution pattern. Eliminating the `LLMProvider` adapter removes a layer of indirection and makes streaming native.

**Alternatives considered**:
- Keep `LLMProvider` as a wrapper around `BaseChatModel` — rejected: adds indirection, makes streaming secondary, contradicts the goal of using LangChain natively
- Use both interchangeably with runtime detection — rejected: dual-path complexity, fragile `hasattr` checks

### R3: LangChain Native Tool-Calling with bind_tools()

**Decision**: Use LangChain's `bind_tools()` for native tool-calling. The LLM returns structured `AIMessage` objects with `tool_calls` directly. Models without native tool-calling support are rejected at configuration time. The existing `ToolContract` objects are eliminated; tools are defined as LangChain `StructuredTool` instances in per-role modules (`planner_tools.py`, `executor_tools.py`, `validator_tools.py`).

**Rationale**: Native tool-calling leverages provider-specific optimizations (OpenAI function calling, Anthropic tool use) and eliminates fragile text parsing. LangChain's `StructuredTool` provides schema validation and integrates with `bind_tools()` natively. Requiring tool-calling support ensures reliable agent-tool interaction across all supported providers.

**Alternatives considered**:
- Auto-detect and fall back to JSON parsing for models without tool-calling — rejected by user: adds complexity, fragile parsing, inconsistent behavior across models
- Always use JSON parsing — rejected: loses native tool-calling benefits, provider optimizations

### R4: LangGraph Checkpointing Strategy

**Decision**: Use `SqliteSaver` co-located with the existing session DB for LangGraph checkpointing. Swap to `PostgresSaver` when `ADO2GH_STORAGE_BACKEND=postgresql`. The graph is compiled once at module load with the checkpointer attached. Each session passes `thread_id=session_id` to namespace checkpoint state. Form submission resumes the graph via checkpoint — the route handler loads the checkpoint, injects form values into `AgentState`, and resumes from where it left off.

**Rationale**: Aligning the checkpointer with the existing storage backend configuration avoids splitting state across two storage systems. `SqliteSaver` provides out-of-the-box resume-after-restart. LangGraph's `thread_id` namespace handles per-session isolation without compiling separate graphs. Checkpoint-based form resume is the idiomatic LangGraph pattern for human-in-the-loop interactions.

**Alternatives considered**:
- `MemorySaver` only (no persistence) — rejected: sessions don't survive restart
- Separate checkpoint database — rejected: increases operational complexity
- Redis-based checkpointing — rejected: adds a new dependency, not in the current stack

### R5: SSE Streaming for LLM Thoughts

**Decision**: Single long-lived SSE stream for the entire graph execution, including PEV cycles. Raw `astream()` chunks are emitted directly as `kind: "token"` SSE events without server-side batching. 15-second heartbeat keepalive events prevent proxy/browser timeouts. The UI accumulates chunks into a growing text block per agent turn. `session["messages"]` is the event log (source of truth for UI/API); `AgentState.messages` is the LLM context window (trimmed LangChain messages for `astream()`).

**Rationale**: The existing `/v1/sessions/{id}/message-stream` endpoint already streams via SSE. Extending it to cover the entire graph execution (including PEV) provides a unified streaming experience. Raw chunk streaming gives the most responsive "typing" feel and matches Claude Code/Cursor behavior. The browser's EventSource API handles high-frequency events efficiently. The dual-message-store design separates concerns: session messages are the auditable event log, LangGraph messages are the LLM context.

**Alternatives considered**:
- Two-phase streaming (SSE for orchestrator, polling for PEV) — rejected: inconsistent UX, complexity
- Server-side batching (100ms or word-boundary) — rejected: adds latency, unnecessary complexity
- `AgentState.messages` as single source of truth — rejected: loses the session dict's rich event types (tool_call, status, form_request)

### R6: Model Capability Detection and Validation

**Decision**: Add optional `capabilities` field to model config in `LLMModelStore` with: `supports_tool_calling` (boolean, required: true), `supports_streaming` (boolean, default true), `supports_thinking` (boolean, default false), `max_context_tokens` (integer, default 32000). The store validates `supports_tool_calling=true` at registration — models without it are rejected with a clear error. `llm_bridge.py` reads capabilities to configure behavior: streaming vs invoke, thinking event extraction, and `max_token_budget` per session. For models without streaming, the system falls back to synchronous `invoke()` and emits the complete response as a single SSE event. For models with thinking support, the node extracts thinking content from `AIMessage.additional_kwargs`/`response_metadata`.

**Rationale**: Different LLM providers have vastly different capabilities. Some models lack tool-calling (rejected), some lack streaming (fallback to invoke), some produce structured thinking (emit as separate events), and context windows range from 8K to 1M tokens. Explicit capability metadata enables validation at configuration time rather than discovering failures at runtime. The `max_context_tokens` field drives the context window budget per session, allowing the trimming strategy to adapt.

**Alternatives considered**:
- Runtime detection only (no config field) — rejected: discovers failures too late, poor UX
- Auto-detect via test API call at registration — rejected: adds latency, external dependency during config
- Fixed capabilities per provider — rejected: provider offerings change, new models may differ

### R7: LangGraph State Reducers

**Decision**: Use `operator.add` (list concatenation) as the reducer for all accumulating list fields (`inter_agent_messages`, `cycle_summaries`, `rollback_records`, `streaming_tokens`, `message_queue`). Scalar fields (`migration_plan`, `executor_result`, `validation_result`, etc.) overwrite normally. The `messages` field uses LangGraph's `add_messages` reducer.

**Rationale**: `operator.add` is the standard LangGraph pattern for list accumulation — each node returns new items and the reducer appends them. This avoids the complexity of custom reducers while ensuring accumulated state persists across graph iterations. Scalars overwrite because they represent current state (latest plan, latest result), not history.

**Alternatives considered**:
- Custom reducers per field (dedup, cap) — rejected: over-engineered for current needs
- No reducers (manual full-list returns) — rejected: error-prone, nodes must read-then-append

### R8: Context Window Management with trim_messages

**Decision**: Use LangChain's built-in `trim_messages` from `langchain_core.messages` for context window management. Configure per-node via state reducer. The `capabilities.max_context_tokens` drives `max_token_budget` per session. After each PEV cycle, a compact JSON cycle summary is generated and stored in `AgentState.cycle_summaries`. Full message history remains in `session["messages"]` for audit.

**Rationale**: LangChain's `trim_messages` handles the common patterns (token counting, message selection, system message preservation) without custom code. The sliding window strategy (last 2 PEV cycles full + JSON summary of prior) is configured via `trim_messages` parameters. Using `max_context_tokens` from capabilities ensures the trimming adapts to different models (aggressive for 8K models, lenient for 200K models).

**Alternatives considered**:
- Custom `trim_context()` function — rejected: reinvents `trim_messages`, adds maintenance burden
- LangGraph pre-node for automatic trimming — rejected: less explicit control, harder to debug

### R9: Hybrid Tool Execution

**Decision**: Use LangGraph's built-in `ToolNode` for simple read-only tools (discovery, status checks); custom execution node for write tools and complex flows (plan building, PEV start, form handling). The custom execution wraps the existing `_run_orchestrator_tool()` and `execute_tool()` patterns as LangChain tools with guardrail validation.

**Rationale**: Simple read-only tools fit `ToolNode`'s automatic execution pattern perfectly. Complex tools with side effects on session state, conditional form handling, and auto-chaining logic don't fit the standard `ToolNode` pattern. The hybrid approach keeps simple tools simple while retaining full control over complex flows.

**Alternatives considered**:
- Full custom execution for all tools — rejected: unnecessary for simple read-only tools
- Full `ToolNode` for all tools — rejected: too rigid for complex flows with session mutations

### R10: Module Consolidation Strategy

**Decision**: Create `migration_agent/` as a new package. Copy and refactor files from `langgraph_agent/`. Move all needed utilities from `orchestration/`, `session_orchestrator.py`, `session_state_machine.py`, `session_store.py`, `local/tool_catalog.py`, `skills/`, and policy modules (`agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`) into `migration_agent/` submodules. Delete all originals after tests pass. Delete `llm_provider.py` entirely — migrate all callers to `llm_bridge`. Delete `services/agent/routes/_shared.py` — inline needed functions into route handlers or `migration_agent/`.

**Rationale**: The user explicitly wants no legacy custom logic remaining. Consolidating everything into `migration_agent/` creates a single, self-contained package with clear domain naming. The copy-then-delete approach preserves git history while ensuring a clean cutover. No feature flags or dual-path maintenance.

**Alternatives considered**:
- Keep `orchestration/` as shared utilities — rejected by user: everything should be moved
- Rename `langgraph_agent/` in-place via git mv — rejected by user: prefer new package with refactoring
- Phase the deletion over multiple PRs — rejected: clean cutover, no parallel packages
