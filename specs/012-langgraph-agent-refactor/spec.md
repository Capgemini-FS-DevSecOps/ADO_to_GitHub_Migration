# Feature Specification: LangGraph Agent Refactor

**Feature Branch**: `012-langgraph-agent-refactor`

**Created**: 2026-06-24

**Status**: Clarified (Final Pass — 0 ambiguity findings)

**Input**: User description: "Migrate the migration agent to use langchain/langgraph. Some work is already in progress but it must have the following: All requirements in spec011 regarding the agents and subagents but refactored to use langgraph/langchain. The 4 agents should be able to be ran by the same LLM and the LLM thoughts and streams should be displayed in the chat in the UI. It should support any current and future LLM we throw at it."

## Clarifications

### Session 2026-06-24

> **Note**: Answers Q1, Q2, and Q5 from this early session were later superseded by the Clarify Workflow session below (Q25, Q28, Q24 respectively). The Requirements section is authoritative when any clarification answer conflicts with an FR.

- Q: Should the existing langgraph_agent directory (nodes.py, graph.py, orchestrator.py, state.py, llm_bridge.py) be extended or replaced? → A: **Extend and refactor** — the existing scaffolding has the right structure (LangGraph StateGraph, LangChain ChatModel bridge, SSE streaming) but only covers the orchestrator node. The PEV chain (Planner → Executor → Validator) must be added as LangGraph nodes with the same patterns. *(Superseded by Q25: copy to new `migration_agent/` package, delete old.)*
- Q: Should the custom LLMProvider abstraction be replaced entirely with LangChain ChatModels? → A: **Yes, replace with LangChain ChatModel as the primary interface** — the `llm_bridge.py` already shows the pattern: resolve model config from LLMModelStore, return a LangChain `BaseChatModel`. All four agents use the same ChatModel instance, differentiated by system prompts and tool bindings. The legacy `LLMProvider` class remains for backward compatibility during migration but is deprecated. *(Superseded by Q28: delete `llm_provider.py` entirely; all callers migrate to `llm_bridge`.)*
- Q: How should LLM token streaming be surfaced in the UI? → A: **SSE streaming with per-token events** — the existing `/v1/sessions/{id}/message-stream` endpoint already streams events via SSE. The refactor extends this to stream LLM token chunks as `kind: "token"` events with `subagent` labels, so the UI can display thinking/reasoning in real time as each agent processes.
- Q: Should LangGraph checkpointing replace the custom session store? → A: **LangGraph checkpointing for graph state, custom session store for persistence** — LangGraph's `MemorySaver` or `SqliteSaver` handles in-flight graph state (current node, iteration count, accumulated messages). The existing `SessionStore` remains for long-term persistence (session metadata, migration plans, PEV cycle summaries, audit logs). The two layers complement each other: checkpointing enables resume-after-restart for the graph, the session store handles audit and history.
- Q: Should LangChain tool bindings (@tool decorator) replace the custom tool catalog? → A: **Wrap the existing tool catalog as LangChain tools** — the existing `ToolContract` objects in `tool_catalog.py` are wrapped into LangChain `StructuredTool` / `@tool` functions so the LLM can call them natively via LangChain's tool-calling protocol. The guardrail layer remains as a pre-execution validator on each tool. This preserves the existing role-based access control and guardrail logic while gaining LangChain's native tool-calling support. *(Superseded by Q24: eliminate `ToolContract` class; define tools directly as `StructuredTool` instances.)*
- Q: How should the continuous PEV loop be modeled in LangGraph? → A: **Cyclic graph with conditional edges** — the graph cycles through Planner → Executor → Validator with conditional edges that check validation results, retry counts, and iteration limits. The `PevCycleOrchestrator` logic (max 20 iterations, 3 PEV retries) is encoded as conditional edge functions. Terminal states (completed, failed) route to END.

### Session 2026-06-24 (Clarify Workflow)

- Q: Should the system use LangChain native tool-calling (`bind_tools()`) or keep the existing JSON parsing approach for extracting tool calls from LLM output? → A: **Use LangChain native tool-calling via `bind_tools()` as primary** — the LLM returns structured `AIMessage` objects with `tool_calls` directly, leveraging provider-native tool-calling (OpenAI function calling, Anthropic tool use). The existing JSON-parsing fallback is kept for stub/degraded mode only. This eliminates fragile text parsing for production LLM providers.
- Q: Which LangGraph checkpointer storage backend should be used for in-flight graph state persistence? → A: **`SqliteSaver` co-located with the existing session DB** — swap to `PostgresSaver` when `ADO2GH_STORAGE_BACKEND=postgresql`. This aligns with the current storage backend configuration, provides out-of-the-box resume-after-restart, and avoids splitting state across two storage systems.
- Q: Should the PEV chain (Planner → Executor → Validator) be nodes within the same LangGraph graph as the Orchestrator, or a separate subgraph/background task? → A: **Single unified `StateGraph`** — all 4 agent nodes (Orchestrator, Planner, Executor, Validator) exist in one compiled graph with conditional edges routing between them. One checkpoint covers the entire loop. This is the idiomatic LangGraph pattern for multi-agent systems and ensures the SSE stream covers all agent activity including PEV execution.
- Q: Should nodes use LangChain `BaseChatModel` directly or through an adapter wrapper? → A: **Use LangChain `BaseChatModel` directly in all nodes** — `llm_bridge.resolve_langchain_llm()` returns a `BaseChatModel` or `None`. The temporary `llm_adapter.py` wrapper is a stopgap during development and will be eliminated. Nodes call `astream()` directly on the ChatModel. The legacy `LLMProvider` class remains only for backward compat in non-LangGraph code paths. *(Superseded by Q28: delete `llm_provider.py` entirely; all callers migrate to `llm_bridge`.)*
- Q: How should the existing `pev_loop()` background task be migrated when the unified LangGraph graph includes PEV nodes? → A: **Full replacement** — remove `pev_loop()` entirely; the LangGraph graph handles PEV cycles via conditional edges. The `start_pev` flag from the orchestrator node triggers the conditional edge into the Planner node instead of launching a background task. Clean cutover with no feature flags or dual-path maintenance. The old `pev_loop()` code is removed.
- Q: How should the SSE stream handle long-running PEV execution (minutes for real migrations)? → A: **Single long-lived SSE stream for entire graph execution** — 15-second heartbeat keepalive events prevent proxy/browser timeouts. The UI stays connected throughout the PEV loop, showing streaming thinking tokens from each agent as they execute. For extremely long migrations (50+ repos), the stream stays open but the UI can optionally disconnect and reconnect via polling if the user navigates away.
- Q: Should `AgentState.messages` (LangChain message list) or `session["messages"]` (session dict event log) be the source of truth? → A: **`session["messages"]` is the event log (source of truth for UI/API)** — it contains all events (user messages, assistant messages, thinking, tool calls, status, etc.) and is what the API and UI consume. `AgentState.messages` is the LLM context window — it holds the trimmed LangChain message history (SystemMessage + HumanMessage + AIMessage) passed to `astream()`. Nodes sync both: session messages are the full auditable event log; LangGraph messages are the LLM context.
- Q: Where should agent system prompts live? → A: **New `migration_agent/prompts.py` loads skill `.md` files from `migration_agent/prompts/` directory** and exposes constants (`ORCHESTRATOR_SYSTEM`, `PLANNER_SYSTEM`, `EXECUTOR_SYSTEM`, `VALIDATOR_SYSTEM`). LangGraph nodes import from there. The old `orchestration/prompts.py` is deleted. The `.md` skill files are moved from `ado2gh/agents/skills/` to `migration_agent/prompts/`.
- Q: Should the package be named `langgraph_agent` or something less generic? → A: **`migration_agent`** — descriptive of the domain, not the framework. The package `ado2gh/agents/migration_agent/` contains all agent modules.
- Q: Should tool execution use LangGraph's built-in `ToolNode` or custom execution? → A: **Hybrid** — use `ToolNode` for simple read-only tools (discovery, status); custom execution for write tools and complex flows (plan building, PEV start, forms). Keeps it simple while retaining control over guardrails and session mutations.
- Q: What happens to the `orchestration/` module package? → A: **Move all needed utilities from `orchestration/` into `migration_agent/` upfront; delete `orchestration/` as part of this spec.** No legacy custom logic remains. Everything is refactored to use LangGraph/LangChain patterns.
- Q: How should LLM timeout and retry work with LangChain `astream()`? → A: **Use LangChain's built-in `timeout` parameter on ChatModel where supported; fallback to `asyncio.wait_for` for providers without native timeout.** On timeout, retry once; on second timeout, route to Orchestrator with degraded state.
- Q: How granular should thinking token SSE events be? → A: **Stream raw `astream()` chunks directly as SSE events without server-side batching.** The browser's EventSource API handles high-frequency events efficiently. The UI accumulates chunks into a single growing text block per agent turn. Providers that chunk by word (Anthropic) or by token (OpenAI) naturally produce different cadences.
- Q: Which LangGraph state reducer strategy for accumulating list fields? → A: **Use `operator.add` (list concatenation) as the reducer** for all accumulating list fields (`inter_agent_messages`, `cycle_summaries`, `rollback_records`, `streaming_tokens`, `message_queue`). Scalar fields overwrite normally. Standard LangGraph pattern.
- Q: How should `pev_coordinator.py` and `pev_cycle.py` logic be handled? → A: **Fold PEV cycle logic into graph edges/nodes; eliminate `PevCycleOrchestrator` class.** Iteration counters, retry limits, batch queue, repo locks, and inter-agent message creation become graph conditional edge functions and node state. No separate PEV coordinator module survives.
- Q: What is the testing strategy? → A: **New test suite for LangGraph nodes/graph; keep existing tests during transition; delete tests for removed code after refactor.** Tests use descriptive names (not numbered prefixes like `test_011_` or `test_012_`).
- Q: How should LangChain/LangGraph dependencies be pinned? → A: **Pin minimum versions (`>=`) in `requirements.txt`** for LangChain ecosystem packages (`langchain>=0.2`, `langchain-core>=0.2`, `langgraph>=0.1`, `langchain-openai>=0.1`, `langchain-anthropic>=0.1`). Group deps with a comment block.
- Q: How should the graph be compiled and sessions managed? → A: **Compile graph once at module load with checkpointer attached.** Each session invocation passes `thread_id=session_id` to the compiled graph's `.ainvoke()` or `.astream()` call. LangGraph uses `thread_id` to namespace checkpoint state per session. Route handlers call wrapper functions in `migration_agent.orchestrator`.
- Q: What happens to the existing `langgraph_agent/` directory? → A: **Create `migration_agent/` as new package; copy and refactor files from `langgraph_agent/`; keep old package until tests pass; delete old after.**
- Q: How should dynamic form submission re-enter the graph? → A: **Checkpoint on `pending_form`; form submission resumes graph via `thread_id` checkpoint.** The route handler loads the checkpoint, injects form values into `AgentState`, and resumes the graph from where it left off.
- Q: What happens to `session_orchestrator.py` (2281 lines)? → A: **Extract needed functions into `migration_agent/` submodules (`forms.py`, `utils.py`, etc.); delete `session_orchestrator.py`; refactor to LangGraph state patterns.**
- Q: What happens to `planner.py`, `executor.py`, `validator.py`? → A: **Fold all logic from these modules directly into LangGraph node functions and as LangChain tools for the PEV agents; delete the standalone agent modules.**
- Q: Where do LangChain tool definitions live? → A: **Multiple tool modules: `migration_agent/tools/planner_tools.py`, `executor_tools.py`, `validator_tools.py`** — one file per agent role. Guardrail validation applied via a tool wrapper in `migration_agent/guardrails.py`.
- Q: How should context window management work? → A: **Use LangChain's built-in `trim_messages` from `langchain_core.messages`**; configure per-node via state reducer. No custom context window module needed.
- Q: What happens to `session_store.py` and `session_state_machine.py`? → A: **Move both into `migration_agent/` as submodules; refactor to work with LangGraph state patterns; delete originals.**
- Q: What happens to the `skills/` directory? → A: **Move `skills/` to `migration_agent/prompts/`; `prompts.py` loads `.md` files from sibling directory at import time.**
- Q: What happens to `local/tool_catalog.py` and the `local/` directory? → A: **Extract metadata/roles/guardrails into `migration_agent/tools/`; eliminate `ToolContract` class; delete `local/` directory.** Guardrails become a LangChain tool wrapper in `migration_agent/guardrails.py`.
- Q: What happens to `llm_provider.py`? → A: **Delete `llm_provider.py` entirely; migrate all callers to LangChain ChatModel via `llm_bridge`; move shared constants to `migration_agent/constants.py`.**
- Q: What happens to remaining top-level agent modules (`agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`)? → A: **Move remaining utils into `migration_agent/` submodules; `context_window.py` replaced by `trim_messages`; policy modules consolidated into `migration_agent/policies.py`; delete originals.**
- Q: What happens to all `orchestration/` subdirectory modules? → A: **Delete all of `orchestration/`; extract `OrchestratorResult` and any still-needed types into `migration_agent/`; nothing survives.**
- Q: What happens to `services/agent/routes/_shared.py`? → A: **Delete `_shared.py` entirely; move all needed functions into `migration_agent/` or inline into route handlers.**
- Q: What is the final package structure for `migration_agent/`? → A: **Confirmed structure:**
  ```
  ado2gh/agents/migration_agent/
  ├── __init__.py
  ├── nodes.py              # LangGraph node functions
  ├── graph.py              # StateGraph definition, conditional edges, compiled graph
  ├── state.py              # AgentState TypedDict with reducers
  ├── orchestrator.py       # process_user_message(), stream_user_message() wrappers
  ├── llm_bridge.py         # resolve_langchain_llm() → BaseChatModel or None
  ├── prompts.py            # Loads .md files from prompts/ directory
  ├── constants.py          # Shared constants (NO_LLM_CONFIGURED_MESSAGE, etc.)
  ├── forms.py              # Dynamic form builders
  ├── utils.py              # Event appending, task init, session helpers
  ├── policies.py           # execution_mode, live_execution_policy, session_access, agent_scope (consolidated)
  ├── session_state.py      # State machine + OrchestratorResult
  ├── session_store.py      # Long-term persistence
  ├── guardrails.py         # Guardrail evaluation as LangChain tool wrapper
  ├── prompts/              # .md skill files
  │   ├── orchestrator.md
  │   ├── planner.md
  │   ├── executor.md
  │   └── validator.md
  └── tools/
      ├── __init__.py
      ├── planner_tools.py    # LangChain StructuredTools for Planner node
      ├── executor_tools.py   # LangChain StructuredTools for Executor node
      └── validator_tools.py  # LangChain StructuredTools for Validator node
  ```
  **Modules to delete from `ado2gh/agents/`:** `langgraph_agent/`, `orchestration/`, `local/`, `planner.py`, `executor.py`, `validator.py`, `pev_cycle.py`, `pev_coordinator.py`, `session_orchestrator.py`, `llm_provider.py`, `agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`, `session_state_machine.py`, `session_store.py`, `skills/`. **Also delete:** `services/agent/routes/_shared.py`.
- Q: What happens when a production model doesn't support native tool-calling? → A: **Require native tool-calling support; models without it are rejected at configuration time with a clear error message.** The `LLMModelStore` validates `supports_tool_calling=true` when a model is registered. Models that lack tool-calling cannot be used with the agent.
- Q: What happens when a model doesn't support streaming? → A: **Auto-detect streaming support; fall back to `invoke()` with single SSE event for non-streaming models.** If `astream()` is not available or raises, the system uses synchronous `invoke()` and emits the complete response as a single `kind: "token"` event. The UI renders it as a complete block instead of a typing effect.
- Q: Should the model config include capability metadata for validation? → A: **Add optional `capabilities` field to model config** with boolean flags: `supports_tool_calling` (required: true), `supports_streaming` (optional, default true), `supports_thinking` (optional, default false), `max_context_tokens` (optional, default 32000). The store validates `supports_tool_calling=true` at registration. `llm_bridge.py` reads capabilities to configure behavior. Models without explicit capabilities default to the LangChain provider's detected capabilities.
- Q: How should thinking/reasoning tokens be handled across models with different capabilities? → A: **`capabilities.supports_thinking` flag** — for models with `supports_thinking=true`, the node extracts thinking content from LangChain `AIMessage`'s `additional_kwargs` or `response_metadata` (provider-specific fields like `thinking` for Anthropic, `reasoning_content` for OpenAI o-series). Thinking content is emitted as `kind: "thinking"` SSE events. For models without thinking support, no thinking events are emitted — only `kind: "token"` events.
- Q: How should the context window budget adapt to different models? → A: **`capabilities.max_context_tokens` drives `max_token_budget` per session.** When a session is created, `llm_bridge.py` resolves the model and reads `max_context_tokens` (defaulting to 32000 if unspecified). The context window manager uses this budget to determine how many PEV cycles to keep in full detail vs. summarize.

### Session 2026-06-24 (Final Clarify Pass)

> **Result**: 0 clarification questions needed. All 12 ambiguity categories scanned and marked Clear. Fixed 5 residual inconsistencies across 3 passes: (1) SC-008 still referenced "existing tool catalog is wrapped" → updated to "Tools are defined as LangChain `StructuredTool` instances"; (2) `StreamingTokenEvent` entity still referenced `call_llm_node` → updated to "agent nodes"; (3) Added superseded notes to early clarification answers Q1, Q2, Q5 for historical traceability; (4) Added `OrchestratorResult` to Key Entities (plan referenced 14 entities but spec listed 13); (5) Added superseded note to Q29 (`LLMProvider` backward compat) referencing Q28 (delete entirely). Spec, plan, and tasks are fully aligned with zero findings and ready for implementation.

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

- **CA-001**: The agent MUST provide a dry-run mode for all migration operations — no irreversible GitHub changes occur without explicit user confirmation and approver sign-off where required. This is preserved from spec 011 and enforced via LangGraph tool validators.
- **CA-002**: Destructive actions (repo deletion, workflow deletion, secret deletion, ADO pipeline disabling) MUST require explicit user confirmation via dynamic forms AND approver sign-off where policy requires it. Guardrail interceptors on LangChain tools enforce this.
- **CA-003**: Secrets and credential values MUST NOT appear in chat messages, agent transcripts, audit logs, LangGraph state, or UI — only secret names and masked indicators are visible.
- **CA-004**: All agent decisions, tool calls, inter-agent communications, user interactions, guardrail evaluations, and PEV cycle outcomes MUST be auditable with correlation to the session ID, plan ID, and migration run ID. LangGraph state transitions are logged as audit events.
- **CA-005**: The agent MUST NOT modify or delete GitHub resources that are not in the approved migration plan — the guardrail layer enforces plan-vs-execution consistency on every LangChain tool call.

### Functional Requirements

#### LangGraph architecture: four-agent graph with continuous loop

- **FR-001**: The system MUST implement the four agent roles (Orchestrator, Planner, Executor, Validator) as LangGraph nodes within a single `StateGraph` — each node receives the shared `AgentState` and uses the same LangChain `BaseChatModel` instance, differentiated by system prompts and tool bindings.
- **FR-002**: The agent loop MUST be a LangGraph cyclic graph — the graph cycles through Orchestrator → Planner → Executor → Validator repeatedly via conditional edges until a terminal state is reached (job complete, needs user input, unrecoverable error, or max iterations exceeded).
- **FR-003**: Each agent node MUST pass inter-agent messages via LangGraph state updates — messages are structured JSON with `message_type` (instruction|clarification_request|feedback|result), `from_role`, `to_role`, `payload`, `correlation_ids`, and `timestamp`, stored in the `AgentState.inter_agent_messages` field.
- **FR-004**: The Orchestrator node MUST be the only node that produces user-facing messages — internal agent-to-agent messages in LangGraph state are not emitted to the SSE stream as user-visible messages. The orchestrator node sets `AgentState.reply` for user-facing output.
- **FR-005**: The Planner node MUST be able to request information from the Orchestrator by setting `AgentState.pending_clarification` — the graph routes back to the Orchestrator node, which generates a dynamic form for the user.
- **FR-006**: The Executor node MUST be able to request clarification from the Planner by setting `AgentState.pending_clarification` with `to_role: "planner"` — the graph routes back to the Planner node without involving the user.
- **FR-007**: The Validator node MUST send structured feedback to the Planner via `AgentState.validation_feedback` — the graph conditionally routes back to the Planner node when validation fails, including expected state, observed state, specific failure, and recommended remediation.
- **FR-008**: The maximum PEV retry loop MUST be configurable (default: 3 cycles) — the conditional edge function checks `AgentState.pev_retry_count` against the limit before routing back to the Planner. Exceeding the limit routes to the Orchestrator with a failure summary.
- **FR-009**: The maximum total agent loop iterations MUST be configurable (default: 20) — the conditional edge function checks `AgentState.iteration_count` against the limit before continuing the loop. Exceeding the limit routes to the Orchestrator with a partial completion summary.
- **FR-010**: The LangGraph `StateGraph` MUST be compiled with a recursion limit of at least 40 (2x the max iterations) to accommodate the cyclic graph structure without premature termination.

#### LangChain LLM abstraction: provider-agnostic model support

- **FR-011**: The system MUST use LangChain `BaseChatModel` as the unified LLM interface for all four agents — the `llm_bridge.py` module resolves model configurations from the existing `LLMModelStore` and returns LangChain ChatModel instances (`ChatOpenAI`, `ChatAnthropic`, etc.).
- **FR-012**: The system MUST support any LangChain-compatible chat model provider — adding a new LLM provider requires only registering it in the `LLMModelStore` and ensuring the corresponding LangChain integration package is installed. No agent code changes are needed.
- **FR-013**: The system MUST support the following provider types via LangChain integrations: OpenAI (`langchain_openai.ChatOpenAI`), Anthropic (`langchain_anthropic.ChatAnthropic`), Azure OpenAI (`langchain_openai.AzureChatOpenAI`), OpenAI-compatible endpoints (OpenRouter, GitHub Models, GitHub Copilot — via `ChatOpenAI` with custom `base_url`), and stub/offline models (via `GenericFakeChatModel` for testing).
- **FR-014**: All four agents MUST share the same LangChain ChatModel instance within a session — agents are differentiated by system prompts and tool bindings, not by model. This ensures consistent reasoning quality and simplifies configuration.
- **FR-015**: The LangChain ChatModel MUST be configured with `streaming=True` when the model supports it (per `capabilities.supports_streaming`) to enable token-by-token streaming via `astream()` — each agent node uses `astream()` to receive incremental token chunks and emits them as SSE events. For models without streaming support, the system falls back to synchronous `invoke()` and emits the complete response as a single `kind: "token"` SSE event.
- **FR-016**: The system MUST degrade gracefully when no LLM is configured — `resolve_langchain_llm()` returns `(None, True, True)` and the orchestrator node returns a setup prompt directing the user to configure a model in Settings.
- **FR-016a**: The `LLMModelStore` MUST include an optional `capabilities` field per model config with: `supports_tool_calling` (boolean, required: true), `supports_streaming` (boolean, default true), `supports_thinking` (boolean, default false), and `max_context_tokens` (integer, default 32000). When a model is registered, the store validates that `supports_tool_calling` is true — models without native tool-calling support are rejected with a clear error message explaining the requirement.
- **FR-016b**: The `llm_bridge.py` MUST read the `capabilities` field from the model config to determine: whether to enable streaming (`astream()` vs `invoke()`), whether to extract thinking content from `AIMessage.additional_kwargs`/`response_metadata`, and the `max_token_budget` for the session. Models without explicit capabilities default to the LangChain provider's detected capabilities where available, otherwise to the defaults (streaming=true, thinking=false, max_context_tokens=32000).
- **FR-016c**: The system MUST reject any model that lacks native tool-calling support at configuration time — the `LLMModelStore` returns a validation error with a message like "This model does not support tool-calling, which is required for the migration agent. Please select a model that supports function/tool calling."

#### LLM streaming: real-time thoughts in the UI

- **FR-017**: Each agent node (orchestrator, planner, executor, validator) MUST stream LLM token chunks as they arrive from `astream()` — each chunk is appended to `AgentState.streaming_tokens` and emitted as an SSE event with `kind: "token"`, `content: <chunk>`, `subagent: <agent_role>`, and `timestamp`.
- **FR-018**: The SSE streaming endpoint (`/v1/sessions/{id}/message-stream`) MUST emit token events in real time as the LangGraph graph executes — the `stream_user_message()` async generator polls session messages and yields new events as they appear.
- **FR-019**: The UI MUST display LLM thinking/reasoning tokens in real time as a streaming text block within the chat interface — tokens are rendered incrementally with a typing indicator, and the `subagent` field labels which agent is currently thinking (orchestrator, planner, executor, validator).
- **FR-020**: The UI MUST render thinking tokens with distinct visual styling from user-facing messages — thinking blocks use a muted/secondary style with the agent label (e.g., "Planner is thinking...") and are collapsible. User-facing messages use the primary chat bubble style.
- **FR-021**: The UI MUST auto-scroll to the latest token as it arrives, but preserve scroll position if the user has scrolled up to read earlier messages.
- **FR-022**: The SSE stream MUST include `kind: "thinking"` events for structured LLM thinking output when the model supports thinking (`capabilities.supports_thinking=true`) and the LLM produces thinking content in `AIMessage.additional_kwargs` or `response_metadata` (provider-specific fields like `thinking` for Anthropic, `reasoning_content` for OpenAI o-series). For models without thinking support, no thinking events are emitted — only `kind: "token"` events for regular content. The UI shows thinking blocks only when they arrive.
- **FR-023**: The SSE stream MUST include `kind: "tool_call"` events when an agent invokes a tool — the event includes the tool name, arguments (with secrets masked), and the calling agent's role.
- **FR-024**: The SSE stream MUST include `kind: "tool_result"` events when a tool completes — the event includes the tool name, result summary (with secrets masked), guardrail decision, and the calling agent's role.
- **FR-025**: The SSE stream MUST include `kind: "status"` events when the graph transitions between agent nodes — e.g., "Planner is generating migration plan...", "Executor is running migration...", "Validator is checking results...".

#### LangChain tool integration: native tool calling

- **FR-026**: Tools MUST be defined directly as LangChain `StructuredTool` instances in per-role modules (`planner_tools.py`, `executor_tools.py`, `validator_tools.py`) — each tool's `name`, `description`, and `parameters` schema are defined as LangChain tool metadata. The `ToolContract` class is eliminated. The LLM calls tools natively via LangChain's tool-calling protocol (`bind_tools()`).
- **FR-027**: Each agent node MUST bind only the tools accessible to its role — the Planner node binds planner-allowlisted tools, the Executor node binds executor-allowlisted tools, etc. Role-based access control is enforced at the LangChain tool-binding level.
- **FR-028**: The guardrail layer MUST intercept all tool calls before execution — a LangChain tool validator checks plan authorization, resource existence, deletion confirmation, and parameter validation. Blocked calls return an error to the calling agent without executing.
- **FR-029**: All tool executions MUST be logged for audit with: timestamp, agent role, tool name, parameters (with secrets masked), result, guardrail decision, and correlation IDs (session_id, plan_id, run_id). Audit events are emitted as LangGraph state updates and persisted to the session store.
- **FR-030**: ADO tools MUST remain read-only for all agents — the LangChain tool wrappers enforce read-only access to ADO resources. No agent can modify, create, or delete ADO resources except ADO pipeline disabling when explicitly approved.
- **FR-031**: The tool parameter validation MUST reject hallucinated resource names, invalid paths, and non-existent repos — the LangChain tool validator checks all parameters against known resources before execution and returns descriptive errors.

#### Orchestrator node (LangGraph)

- **FR-032**: The Orchestrator node MUST classify user intent using the LangChain ChatModel — intent is classified as `general_chat`, `migration_info`, or `migration_action`. The node uses `astream()` to stream thinking tokens during classification.
- **FR-033**: For general chat and migration info queries, the Orchestrator node MUST respond directly without routing to the PEV chain — the graph routes to the finalize node.
- **FR-034**: For migration actions, the Orchestrator node MUST identify required parameters (repo, phase, dry-run/live, scope) and present dynamic forms for missing parameters — the node sets `AgentState.pending_form` and the graph routes to the finalize node until the form is submitted.
- **FR-035**: The Orchestrator node MUST create dynamic forms with: form_id, title, description, and fields (name, label, type: select/checkbox/text/textarea, options, required) — forms are rendered inline in the chat interface via the SSE stream.
- **FR-036**: The Orchestrator node MUST enforce dry-run by default — live execution requires explicit user confirmation via a dynamic form. The node checks `AgentState.dry_run` and `AgentState.live_approved` before allowing the Executor to proceed with live operations.
- **FR-037**: The Orchestrator node MUST present plan summaries to the user for confirmation before live execution — the summary includes repos, scopes, order, and risks. The node sets `AgentState.pending_form` with a plan confirmation form.
- **FR-038**: The Orchestrator node MUST handle user messages received while the PEV chain is running by queuing them — the node checks `AgentState.pev_active` and queues messages in `AgentState.message_queue`. Cancellation requests interrupt the cycle.
- **FR-039**: The Orchestrator node MUST auto-trigger discovery when the user requests migration and discovery data is empty — the node calls the discovery tool, displays a "Running discovery scan..." status event, and proceeds to planning.

#### Planner node (LangGraph)

- **FR-040**: The Planner node MUST create detailed migration plans using the LangChain ChatModel with discovery data — the node binds planner-allowlisted tools (read-only ADO + read-only GitHub + discovery) and uses `astream()` to stream planning thoughts.
- **FR-041**: The Planner node MUST map repository dependencies using topological sort and include the migration order in the plan — the plan is stored in `AgentState.migration_plan`.
- **FR-042**: The Planner node MUST include in every plan: repo migration order, pipeline-to-workflow conversion mappings, Bicep/ARM template transformation steps, secret/service connection mapping, Boards mapping, Test Plans mapping, Artifacts mapping, Wiki migration steps, dry-run/live flag, and per-repo work items with ready/blocked status.
- **FR-043**: The Planner node MUST coordinate with the Orchestrator when it needs information not available from discovery — the node sets `AgentState.pending_clarification` with `to_role: "orchestrator"` and the graph routes back to the Orchestrator.
- **FR-044**: The Planner node MUST create revised plans from Validator feedback — when `AgentState.validation_feedback` is present, the Planner generates a revised plan targeting only failed scopes with remediation steps and an incremented revision number.
- **FR-045**: The Planner node MUST document all assumptions when discovery data is incomplete and flag them for Orchestrator confirmation — assumptions are stored in `AgentState.migration_plan.assumptions`.
- **FR-046**: The Planner node MUST be rate-limit aware — when ADO API rate limits are hit, it works with available data and flags gaps rather than blocking indefinitely.

#### Executor node (LangGraph)

- **FR-047**: The Executor node MUST interpret and execute instructions from the Planner using the LangChain ChatModel for reasoning and deterministic tool calls for execution — the node binds executor-allowlisted tools (read-only ADO + read/write GitHub + migration) and uses `astream()` to stream execution thoughts. The LLM determines which tools to call; tool execution is deterministic.
- **FR-048**: The Executor node MUST perform all migration operations: git mirror, pipeline → workflow conversion, Bicep/ARM transformation, secret provisioning, service connection migration, Boards migration, Test Plans migration, Artifacts migration, Wiki migration, and workflow branch push.
- **FR-049**: The Executor node MUST send exact output to the Validator via `AgentState.executor_result` — the result includes what was created (resource names, paths, SHAs), what failed (error details, error codes), and what was skipped (with reasons).
- **FR-050**: The Executor node MUST request clarification from the Planner when instructions are ambiguous — the node sets `AgentState.pending_clarification` with `to_role: "planner"` and the graph routes back to the Planner.
- **FR-051**: The Executor node MUST operate through the guardrail layer for all GitHub write operations — every tool call passes through the LangChain tool validator which checks plan authorization, resource existence, deletion confirmation, and parameter validation.
- **FR-052**: The Executor node MUST NOT delete any GitHub resource unless the deletion is explicitly in the approved plan, the user has confirmed via a dynamic form, and the approver policy is satisfied.
- **FR-053**: The Executor node MUST support Bicep/ARM template transformation using the hybrid approach — supported constructs are auto-transformed, unsupported constructs are attempted via LLM best-effort with web search, and remaining gaps are reported.
- **FR-054**: The Executor node MUST track all GitHub resources created during the session for rollback — each creation is recorded via the rollback tracker and stored in `AgentState.rollback_records`.
- **FR-055**: The Executor node MUST implement idempotency — when it detects a repo was already migrated, it asks the Orchestrator to prompt the user with overwrite/skip/abort options.
- **FR-056**: The Executor node MUST route existing operations through the accelerator service API and new resource types (service connections, Boards, Test Plans, Artifacts, Wiki) via direct ADO/GitHub API calls using profile PATs.

#### Validator node (LangGraph)

- **FR-057**: The Validator node MUST receive both the Planner's plan (`AgentState.migration_plan`) and the Executor's output (`AgentState.executor_result`) as context — the node uses the LangChain ChatModel with validator-allowlisted tools (read-only ADO + read-only GitHub + validation).
- **FR-058**: The Validator node MUST verify migration outcomes using API calls and local validation frameworks: git HEAD SHA parity, workflow file existence and YAML syntax, secret reference resolution, service connection verification, dependency order compliance, local workflow simulation (MUST NOT dispatch actual workflow on GitHub), Bicep/ARM transformation validation, Boards verification, Test Plans verification, Artifacts verification, and Wiki verification.
- **FR-059**: The Validator node MUST send structured feedback to the Planner via `AgentState.validation_feedback` when validation fails — the feedback includes expected state, observed state, specific failure, file path, and recommended remediation.
- **FR-060**: The Validator node MUST validate that the Executor did not perform any operations outside the approved plan — any deviation is reported as a validation failure in `AgentState.validation_feedback`.
- **FR-061**: The Validator node MUST report pass/fail per scope (repo, pipelines, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki) with evidence suitable for audit review — results are stored in `AgentState.validation_result`.
- **FR-062**: The Validator node MUST validate workflows locally only — YAML syntax validation plus local workflow simulation (e.g., `act`). The validator MUST NOT dispatch or run the actual workflow on GitHub.

#### Graph routing and conditional edges

- **FR-063**: The LangGraph `StateGraph` MUST use conditional edges to route between agent nodes — the routing functions check `AgentState.should_return`, `AgentState.pending_clarification`, `AgentState.validation_feedback`, `AgentState.pev_retry_count`, and `AgentState.iteration_count` to determine the next node.
- **FR-064**: The graph MUST route from the Orchestrator to the Planner when the user's intent is a migration action and all required parameters are collected — the conditional edge checks `AgentState.intent == "migration_action"` and `AgentState.pending_form is None`.
- **FR-065**: The graph MUST route from the Planner to the Executor when the plan is complete — the conditional edge checks `AgentState.migration_plan is not None` and `AgentState.pending_clarification is None`.
- **FR-066**: The graph MUST route from the Executor to the Validator when execution is complete — the conditional edge checks `AgentState.executor_result is not None` and `AgentState.pending_clarification is None`.
- **FR-067**: The graph MUST route from the Validator to the Planner (retry) when validation fails and the retry count is within limits — the conditional edge checks `AgentState.validation_result.passed == False` and `AgentState.pev_retry_count < MAX_PEV_RETRIES`.
- **FR-068**: The graph MUST route from the Validator to the Orchestrator (complete) when validation passes — the conditional edge checks `AgentState.validation_result.passed == True` and routes to the Orchestrator for user-facing completion message.
- **FR-069**: The graph MUST route from the Validator to the Orchestrator (failed) when validation fails and the retry count is exceeded — the conditional edge checks `AgentState.pev_retry_count >= MAX_PEV_RETRIES` and routes to the Orchestrator for a failure summary.
- **FR-070**: The graph MUST route from any node to the Orchestrator when `AgentState.pending_clarification` is set — the conditional edge checks the `to_role` field and routes to the appropriate agent (Orchestrator for user input, Planner for executor clarification).

#### Session persistence and resume

- **FR-071**: The system MUST use LangGraph checkpointing for in-flight graph state — the compiled graph uses a checkpointer (`SqliteSaver` co-located with the session DB by default, `PostgresSaver` when `ADO2GH_STORAGE_BACKEND=postgresql`) to persist the current node, iteration count, and accumulated state.
- **FR-072**: Agent sessions MUST survive backend server restarts — on restart, the system loads the checkpoint from the SqliteSaver, reconstructs the `AgentState`, and resumes the graph from the last checkpointed node.
- **FR-073**: The session store MUST persist: session metadata, all agent messages (inter-agent and user-facing), migration plans (all revisions), PEV cycle summaries, guardrail decisions, iteration counters, and repo-level locks — enabling full session reconstruction after server restart.
- **FR-074**: The system MUST support up to 10 concurrent active agent sessions — the graph is compiled once at module load and each session uses a `thread_id=session_id` namespace on the shared compiled graph for state isolation, managed via asyncio.
- **FR-075**: Repo-level locks MUST prevent concurrent migration of the same repo across sessions — the Executor node acquires a lock via `repo_lock_store` before migrating a repo and releases it on PEV cycle completion, failure, or session termination.

#### Context window management

- **FR-076**: The system MUST manage LLM context windows using a sliding window strategy — the LangGraph state accumulates messages, and a context window manager trims the message history to the last 2 PEV cycles in full detail plus a running JSON summary of prior cycles.
- **FR-077**: After each PEV cycle, the system MUST generate a compact JSON cycle summary containing: cycle number, repos processed, repos succeeded, repos failed, failures summary, and next action — the summary is stored in `AgentState.cycle_summaries`.
- **FR-078**: Full message history MUST be persisted in the session store for audit — the LangGraph state holds the trimmed context, but the session store retains the complete history.

#### Batch migration

- **FR-079**: The system MUST support batch phase migration — the Planner node generates a migration queue with per-repo work items ordered by topological dependency, stored in `AgentState.migration_queue`.
- **FR-080**: The Executor node MUST process repos sequentially from the migration queue — the Validator node validates each repo before the next begins. The queue state is persisted in LangGraph checkpoints.

#### Chat interface

- **FR-081**: The Agent tab MUST present a Claude Code/Cursor-like chat interface with: message list (user and assistant messages), streaming thinking blocks, message input, model selector, dry-run toggle, and session sidebar.
- **FR-082**: The chat interface MUST show LLM thinking tokens in real time as streaming text blocks — each block is labeled with the active agent role (Orchestrator, Planner, Executor, Validator) and uses distinct visual styling from user-facing messages.
- **FR-083**: The chat interface MUST NOT show internal agent-to-agent messages (planner → executor, executor → validator) as user-facing messages — only the Orchestrator's user-facing messages are visible. Thinking tokens from all agents are visible in collapsible blocks.
- **FR-084**: The chat interface MUST show a working indicator when the PEV chain is running, with expandable details showing which agent is currently active and what tools are being called.
- **FR-085**: Dynamic forms MUST render inline in the chat as cards with fields, submit button, and cancel button — the chat input is disabled while a form is pending.
- **FR-086**: The chat interface MUST support multiple sessions per profile, with session persistence and a sidebar for switching between sessions.
- **FR-087**: The chat interface MUST auto-scroll to the latest message/token and preserve scroll position when loading history.
- **FR-088**: The chat interface MUST display distinct UI states: (a) LLM unconfigured — shows a setup prompt with a link to settings; (b) empty discovery — shows a scan prompt; (c) error state — shows an actionable summary with retry and contact-admin options.

#### Enterprise guardrails (preserved from spec 011)

- **FR-089**: A guardrail layer MUST intercept all GitHub write operations via the LangChain tool wrapper defined in FR-028. This is the cross-cutting enforcement of the same guardrail logic for all agent roles, not a separate implementation.
- **FR-090**: Secret values MUST be masked in all agent messages, LangGraph state, logs, audit records, SSE events, and UI — only secret names are visible, never values.
- **FR-091**: The agent MUST NOT perform any GitHub write operation that is not in the approved migration plan — this is the cross-cutting enforcement of the executor-specific guardrail defined in FR-051, applied to all agent roles. Deviations are blocked and logged for audit.
- **FR-092**: Destructive operations MUST be highlighted in the plan summary with individual confirmation requirements.
- **FR-093**: All agent operations MUST be auditable with: timestamp, agent role, tool name, parameters (masked), result, guardrail decision, session ID, plan ID, and run ID.

#### Resource type migrations (preserved from spec 011)

- **FR-094**: The Executor node MUST migrate all ADO resource types: repos (git mirror), pipelines (→ GitHub Actions workflows), secrets (→ GitHub secrets), service connections (→ GitHub secrets + environments), Boards (work items → GitHub Issues via CSV import), Test Plans (→ GitHub Issues with labels + milestones), Artifacts (feeds → GitHub Packages), and Wiki (→ GitHub Wiki).
- **FR-095**: ADO Boards work items MUST be migrated to GitHub Issues via the GitHub Issues Import API using CSV export with fixed field mapping.
- **FR-096**: ADO Test Plans MUST be migrated to GitHub Issues with test-case and test-suite labels, with test suites mapped to milestones.
- **FR-097**: ADO Artifacts MUST be migrated to GitHub Packages for supported types (npm, NuGet, Docker, Maven, PyPI). Unsupported types are documented as gaps.
- **FR-098**: ADO Wiki MUST be migrated to GitHub Wiki per-repo — the executor enables the wiki via the GitHub API and pushes wiki content as markdown files.
- **FR-099**: ADO service connections MUST be mapped to GitHub secrets (simple credential-based) or GitHub environments with protection rules (deployment-scoped) based on connection type.

#### Cancellation and rollback

- **FR-100**: When a user cancels a migration, the Orchestrator node MUST present two options: rollback (delete all GitHub resources created during this session) or stop (complete the current operation and halt).
- **FR-101**: If the user opts into rollback, the Executor node MUST delete all GitHub resources created during the session as tracked by `AgentState.rollback_records` — the guardrail layer ensures only session-created resources are eligible for deletion.

#### Metrics and health

- **FR-102**: The agent backend MUST expose a Prometheus-compatible `/metrics` endpoint with: active_sessions, pev_cycles_total, llm_call_duration_seconds (histogram), guardrail_blocks_total, tool_calls_total, and session_status_counts.
- **FR-103**: The agent backend MUST expose an agent-specific `/health` endpoint reporting: LLM provider availability, active session count, storage backend connectivity, and current configuration status.

### Key Entities *(include if feature involves data)*

- **AgentState**: The LangGraph `TypedDict` state that flows through the graph. Contains: `messages` (LangChain message history with `add_messages` reducer), `user_message`, `session` (reference to the live session dict), `llm` (LangChain ChatModel), `intent`, `thinking`, `reply`, `tool_calls`, `parsed`, `iteration`, `max_iterations`, `accumulated_tokens`, `max_token_budget`, `start_pev`, `pending_form`, `should_return`, `error`, `inter_agent_messages` (list of structured JSON messages), `migration_plan`, `executor_result`, `validation_result`, `validation_feedback`, `pending_clarification`, `pev_retry_count`, `cycle_summaries`, `migration_queue`, `rollback_records`, `streaming_tokens`, `pev_active`, `message_queue`.
- **LangChainChatModelConfig**: Configuration for building a LangChain ChatModel from the existing LLMModelStore. Contains: model_id, provider (openai|anthropic|azure_openai|openai_compatible|stub), api_key, base_url, model_name, streaming (per capabilities), max_tokens, capabilities (ModelCapabilities), and extra_headers.
- **ModelCapabilities**: Optional metadata describing what a model supports. Contains: `supports_tool_calling` (boolean, required: true — models without tool-calling are rejected), `supports_streaming` (boolean, default true — when false, system uses `invoke()` instead of `astream()`), `supports_thinking` (boolean, default false — when true, system extracts thinking content from `AIMessage.additional_kwargs`/`response_metadata`), `max_context_tokens` (integer, default 32000 — drives `max_token_budget` per session and context window trimming aggressiveness).
- **InterAgentMessage**: Structured JSON message passed between agent nodes via LangGraph state. Contains: message_id, session_id, message_type (instruction|clarification_request|feedback|result), from_role, to_role, payload (typed per message_type), correlation_ids, timestamp.
- **MigrationPlan**: Structured output from the Planner node. Contains: plan_id, repos (in topological order), per-repo work items, dry_run flag, assumptions, blocked items, revision number, pipeline mappings, secret mappings, resource mappings.
- **ExecutorResult**: Output from the Executor node. Contains: plan_id, per-repo results (git mirror status, workflows created, secrets provisioned, service connections migrated, Bicep transformations), failures, skipped items, rollback records.
- **ValidationResult**: Output from the Validator node. Contains: plan_id, per-scope pass/fail, evidence, failures (expected vs observed), recommended remediation.
- **MigrationQueue**: Ordered queue of per-repo work items for batch migrations. Contains: queue_id, plan_id, items (ordered by topological dependency), current_index, completed_items, failed_items.
- **RollbackRecord**: Record of resources created during a session eligible for rollback. Contains: session_id, resource_type, resource_name, github_org, created_at, correlation_id, rollback_status.
- **GuardrailDecision**: Record of a guardrail evaluation on a LangChain tool call. Contains: timestamp, agent role, tool name, operation type, target resource, decision (allow/block), reason, plan reference, session correlation.
- **PevCycleSummary**: Compact JSON summary generated after each PEV cycle. Contains: cycle_number, repos_processed, repos_succeeded, repos_failed, failures, next_action, timestamp.
- **SessionStateMachine**: Formal state machine for session status (preserved from spec 011). States: idle, thinking, planning, executing, validating, awaiting_input, awaiting_approval, completed, failed. Transitions are driven by LangGraph node entry/exit.
- **StreamingTokenEvent**: SSE event for real-time LLM token streaming. Contains: kind ("token"), content (chunk text), subagent (agent role), timestamp. Emitted by agent nodes during `astream()`.
- **OrchestratorResult**: Return value from the orchestrator node wrapping the user-facing reply, pending form (if any), and session event updates. Contains: reply (string or None), pending_form (DynamicForm or None), events (list of session events to append), should_return (boolean).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The migration agent runs as a LangGraph `StateGraph` with four agent nodes (Orchestrator, Planner, Executor, Validator) — verified by inspecting the compiled graph structure and confirming all four nodes are present with correct conditional edges.
- **SC-002**: The continuous PEV loop cycles through Planner → Executor → Validator repeatedly via LangGraph conditional edges until a terminal state is reached — verified by running a migration that requires 2 PEV cycles and confirming the graph routes correctly.
- **SC-003**: All four agents use the same LangChain ChatModel instance within a session — verified by inspecting the graph state and confirming the same `BaseChatModel` object is passed to all agent nodes.
- **SC-004**: LLM thinking tokens are streamed to the UI in real time via SSE — verified by connecting to the `/v1/sessions/{id}/message-stream` endpoint and confirming `kind: "token"` events arrive incrementally with `subagent` labels.
- **SC-005**: The UI displays streaming thinking blocks for each agent role with distinct visual styling — verified by loading the Agent tab and confirming thinking tokens render incrementally with agent labels (Orchestrator, Planner, Executor, Validator).
- **SC-006**: The system supports any LangChain-compatible chat model provider that supports native tool-calling — verified by configuring an OpenAI model, an Anthropic model, and a stub model, and confirming all four agents function correctly with each.
- **SC-006a**: Models without native tool-calling support are rejected at configuration time with a clear error message — verified by attempting to register a non-tool-calling model and confirming the validation error.
- **SC-006b**: Models without streaming support fall back to `invoke()` with a single SSE event — verified by configuring a non-streaming model and confirming the response appears as a complete block in the UI.
- **SC-006c**: Models with thinking support emit `kind: "thinking"` SSE events — verified by configuring a model with `supports_thinking=true` and confirming thinking events appear in the SSE stream.
- **SC-006d**: The context window budget adapts to `capabilities.max_context_tokens` — verified by configuring models with different context windows and confirming the trimming strategy adjusts accordingly.
- **SC-007**: Adding a new LLM provider requires only registering it in the `LLMModelStore` and installing the LangChain integration package — no agent code changes are needed. Verified by adding a hypothetical provider config and confirming the agents work.
- **SC-008**: Tools are defined as LangChain `StructuredTool` instances with role-based tool binding — verified by inspecting the tool bindings on each agent node and confirming only role-appropriate tools are accessible.
- **SC-009**: The guardrail layer intercepts all GitHub write operations via LangChain tool validators — verified by attempting an unauthorized write and confirming it is blocked with an audit log entry.
- **SC-010**: No secret values appear in chat messages, LangGraph state, SSE events, audit logs, or UI — verified by scanning all output channels during a migration that provisions secrets.
- **SC-011**: The chat interface shows only orchestrator user-facing messages as primary chat bubbles — internal agent-to-agent messages are not visible. Thinking tokens from all agents are visible in collapsible blocks.
- **SC-012**: The agent gracefully handles LLM unavailability, ADO rate limits, and GitHub permission errors without crashing or leaving the session in an unrecoverable state.
- **SC-013**: Agent sessions survive a backend server restart — verified by killing the server mid-migration and confirming the session resumes from the last LangGraph checkpoint on restart.
- **SC-014**: The backend supports up to 10 concurrent agent sessions — verified by running 10 simultaneous migration sessions and confirming each completes independently without state corruption.
- **SC-015**: The agent loop respects configurable iteration limits (default: 20 total, 3 PEV retries) — verified by running a migration that exceeds the retry limit and confirming the Orchestrator presents a failure summary.
- **SC-016**: A user can request "migrate all repos in wave 1" (50+ repos) and the agent processes all repos sequentially through the PEV loop with a migration queue.
- **SC-017**: LLM context window usage stays within model limits during a 50+ repo batch migration — the sliding window with structured summaries prevents token overflow.
- **SC-018**: The executor migrates all ADO resource types: repos, pipelines, secrets, service connections, Boards, Test Plans, Artifacts, and Wiki — each with dedicated work items and validation.
- **SC-019**: The session state machine rejects all invalid state transitions — verified by attempting disallowed transitions and confirming they are rejected.
- **SC-020**: Two concurrent sessions attempting to migrate the same repo are detected — the second session receives a clear message that the repo is locked.
- **SC-021**: The `/metrics` endpoint exposes Prometheus-compatible metrics and the `/health` endpoint reports agent-specific status.
- **SC-022**: The executor routes existing operations through the accelerator and new resource types via direct API calls — verified by inspecting tool call logs.
- **SC-023**: When a user cancels a migration, the orchestrator presents rollback and stop options — verified by cancelling mid-migration and confirming both options are presented.
- **SC-024**: The UI shows distinct states for LLM unconfigured, empty discovery, and errors — verified by simulating each state.
- **SC-025**: The LangGraph graph is compiled with a recursion limit of at least 40 — verified by inspecting the compiled graph configuration.

## Assumptions

- The existing `langgraph_agent/` directory (nodes.py, graph.py, orchestrator.py, state.py, llm_bridge.py) provides the correct scaffolding and is copied and refactored into the new `migration_agent/` package. The old `langgraph_agent/` package is deleted after tests pass.
- LangChain and LangGraph Python packages (`langchain`, `langchain-core`, `langgraph`, `langchain-openai`, `langchain-anthropic`) are installed and compatible with the existing Python 3.9+ environment.
- The existing `LLMModelStore` provides sufficient model configuration data (provider, api_key, base_url, model_id) to build LangChain ChatModel instances. The `LLMProvider` class is deleted entirely; all callers migrate to `llm_bridge`.
- Tools are defined directly as LangChain `StructuredTool` instances in per-role modules (`planner_tools.py`, `executor_tools.py`, `validator_tools.py`). The `ToolContract` class is eliminated; the `local/` directory is deleted. Guardrails become a LangChain tool wrapper in `guardrails.py`.
- The existing SSE streaming endpoint (`/v1/sessions/{id}/message-stream`) and the `stream_user_message()` async generator provide the correct transport layer for real-time event streaming — the refactor extends the event types to include `kind: "token"` for incremental LLM token streaming.
- The existing `AgentChat.tsx` component and `streamAgentMessage()` client function provide the correct UI-side SSE consumption pattern — the refactor extends the UI to render streaming thinking tokens with agent role labels.
- The same LLM model is used for all four agents — no model routing or per-agent model selection is needed. Agents are differentiated by system prompts and tool bindings.
- LangGraph checkpointing (`SqliteSaver` or equivalent) provides reliable in-flight graph state persistence for resume-after-restart. The existing `SessionStore` handles long-term persistence (audit, history, plans).
- The existing guardrail logic (evaluate_guardrail, GuardrailDecision) is refactored into a LangChain tool wrapper in `migration_agent/guardrails.py`. The `tool_catalog.py` module is deleted.
- The existing PEV cycle logic in `pev_cycle.py` (max iterations, retry counts, batch queue, repo locks) is encoded as LangGraph conditional edge functions and state fields. The `PevCycleOrchestrator` class is eliminated; `pev_cycle.py` and `pev_coordinator.py` are deleted.
- The existing skill prompt files (`ado2gh/agents/skills/*.md`) are reused as system prompts for the LangChain ChatModel in each agent node.
- The existing dynamic form system (form_id, fields, types) is sufficient for all user interaction needs — no new form types are required.
- The existing session management infrastructure in `services/agent/routes/session_routes.py` is rewritten to use the LangGraph-based orchestrator (`migration_agent.orchestrator`) as the sole entry point. The legacy `process_user_message` and all custom orchestration logic are removed; no fallback path is retained.
- The existing inter-agent message format from spec 011 (structured JSON with message_type, from_role, to_role, payload, correlation_ids, timestamp) is preserved — messages are passed via LangGraph state updates instead of direct function calls.
- The UI polling interval (800ms-1s) for session status is sufficient for non-streaming updates; the SSE stream provides real-time updates for active sessions.
- All entity IDs use UUID v4, consistent with the existing codebase.
