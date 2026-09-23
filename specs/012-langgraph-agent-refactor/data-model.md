# Data Model: LangGraph Agent Refactor

**Feature**: 012-langgraph-agent-refactor
**Date**: 2026-06-24

## Entities

### AgentState

The LangGraph `TypedDict` state that flows through the graph. Uses reducers for accumulating fields.

| Field | Type | Reducer | Description |
|-------|------|---------|-------------|
| messages | list[BaseMessage] | `add_messages` | LangChain message history (LLM context window, trimmed) |
| user_message | str | overwrite | Current user message |
| session | dict | overwrite | Reference to the live session dict (event log) |
| llm | BaseChatModel | overwrite | LangChain ChatModel instance (shared by all agents) |
| intent | str | overwrite | Classified intent: general_chat, migration_info, migration_action |
| thinking | str | overwrite | Accumulated thinking text from current agent |
| reply | str | overwrite | User-facing reply (set by Orchestrator only) |
| tool_calls | list | overwrite | Pending tool calls from LLM |
| parsed | dict | overwrite | Parsed LLM output (orchestration plan, migration plan, etc.) |
| iteration | int | overwrite | Current loop iteration count |
| max_iterations | int | overwrite | Maximum iterations (default: 20) |
| accumulated_tokens | int | overwrite | Token count accumulated in session |
| max_token_budget | int | overwrite | Max tokens for context window (from capabilities.max_context_tokens) |
| start_pev | bool | overwrite | Flag to start PEV cycle (routes to Planner) |
| pending_form | dict \| None | overwrite | Active dynamic form awaiting user input |
| should_return | bool | overwrite | Flag to route to finalize/END |
| error | str \| None | overwrite | Error message if agent encountered failure |
| inter_agent_messages | list[InterAgentMessage] | `operator.add` | Messages between agent nodes |
| migration_plan | MigrationPlan \| None | overwrite | Current migration plan |
| executor_result | ExecutorResult \| None | overwrite | Output from Executor |
| validation_result | ValidationResult \| None | overwrite | Output from Validator |
| validation_feedback | ValidationFeedback \| None | overwrite | Feedback from Validator to Planner |
| pending_clarification | ClarificationRequest \| None | overwrite | Request for clarification between agents |
| pev_retry_count | int | overwrite | PEV retry cycles in current migration |
| cycle_summaries | list[PevCycleSummary] | `operator.add` | Compact summaries of each PEV cycle |
| migration_queue | MigrationQueue \| None | overwrite | Ordered queue for batch migration |
| rollback_records | list[RollbackRecord] | `operator.add` | Resources created for rollback |
| streaming_tokens | list[StreamingTokenEvent] | `operator.add` | Token chunks for SSE streaming |
| pev_active | bool | overwrite | Whether PEV chain is currently running |
| message_queue | list[str] | `operator.add` | Queued user messages during PEV |

**Relationships**: Contains MigrationPlan, ExecutorResult, ValidationResult, MigrationQueue, and lists of InterAgentMessage, PevCycleSummary, RollbackRecord, StreamingTokenEvent.

### LangChainChatModelConfig

Configuration for building a LangChain ChatModel from the existing LLMModelStore.

| Field | Type | Description |
|-------|------|-------------|
| model_id | str | Unique model identifier |
| provider | enum | openai, anthropic, azure_openai, openai_compatible, stub |
| api_key | str | API key (from env, never logged) |
| base_url | str \| None | Custom base URL for OpenAI-compatible endpoints |
| model_name | str | Provider-specific model name (e.g., gpt-4o, claude-3-5-sonnet) |
| streaming | bool | Whether to enable streaming (from capabilities.supports_streaming) |
| max_tokens | int \| None | Max output tokens per call |
| capabilities | ModelCapabilities | Model capability metadata |
| extra_headers | dict \| None | Additional headers for API calls |

### ModelCapabilities

Optional metadata describing what a model supports. Validated at registration time.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| supports_tool_calling | bool | (required: true) | Whether model supports native tool-calling. Models without this are rejected. |
| supports_streaming | bool | true | Whether model supports `astream()`. If false, system uses `invoke()`. |
| supports_thinking | bool | false | Whether model produces structured thinking output in `additional_kwargs` |
| max_context_tokens | int | 32000 | Maximum context window size. Drives `max_token_budget` per session. |

**Validation**: `LLMModelStore.register_model()` validates `supports_tool_calling == true`. If false, raises `ModelCapabilityError` with message: "This model does not support tool-calling, which is required for the migration agent."

### InterAgentMessage

Structured JSON message passed between agent nodes via LangGraph state.

| Field | Type | Description |
|-------|------|-------------|
| message_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| message_type | enum | instruction, clarification_request, feedback, result |
| from_role | enum | orchestrator, planner, executor, validator |
| to_role | enum | orchestrator, planner, executor, validator |
| payload | dict | Typed per message_type (plan, execution result, validation feedback, etc.) |
| correlation_ids | dict | {session_id, plan_id, run_id} for audit tracing |
| timestamp | ISO 8601 str | Message creation time |

### MigrationPlan

Structured output from the Planner node.

| Field | Type | Description |
|-------|------|-------------|
| plan_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| repos | JSON array | Repos in topological dependency order |
| work_items | JSON array | Per-repo work items (pipeline, Bicep, secret, service connections, Boards, Test Plans, Artifacts, Wiki) |
| dry_run | bool | Dry-run/live flag |
| assumptions | JSON array | Assumptions from incomplete discovery |
| blocked_items | JSON array | Items blocked with reasons |
| revision | int | Revision number (0 = initial, incremented on replan) |
| pipeline_mappings | JSON array | ADO pipeline → GitHub workflow mappings |
| secret_mappings | JSON array | ADO secret → GitHub secret mappings |
| resource_mappings | JSON array | Service connection, Boards, Test Plans, Artifacts, Wiki mappings |

### ExecutorResult

Output from the Executor node.

| Field | Type | Description |
|-------|------|-------------|
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| per_repo_results | JSON array | Per-repo: git mirror status, workflows created, secrets provisioned, service connections migrated, Bicep transformations |
| failures | JSON array | Error details with error codes |
| skipped_items | JSON array | Skipped items with reasons |
| rollback_records | list[RollbackRecord] | Resources created during execution |

### ValidationResult

Output from the Validator node.

| Field | Type | Description |
|-------|------|-------------|
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| passed | bool | Overall pass/fail |
| per_scope | JSON object | Pass/fail per scope: repo, pipelines, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki |
| evidence | JSON array | Evidence suitable for audit review |
| failures | JSON array | Expected vs observed, file path, specific failure |
| recommended_remediation | JSON array | Remediation steps for failed scopes |

### ValidationFeedback

Structured feedback from Validator to Planner when validation fails.

| Field | Type | Description |
|-------|------|-------------|
| expected_state | dict | What the plan expected |
| observed_state | dict | What was actually observed |
| specific_failure | str | Description of the failure |
| file_path | str \| None | Relevant file path if applicable |
| recommended_remediation | str | Suggested fix |

### MigrationQueue

Ordered queue of per-repo work items for batch migrations.

| Field | Type | Description |
|-------|------|-------------|
| queue_id | UUID v4 (str) | Primary key |
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| items | JSON array | Ordered by topological dependency |
| current_index | int | Index of currently processing item |
| completed_items | JSON array | Completed item IDs |
| failed_items | JSON array | Failed item IDs |

### RollbackRecord

Record of resources created during a session eligible for rollback.

| Field | Type | Description |
|-------|------|-------------|
| session_id | UUID v4 (str) | FK to AgentSession |
| resource_type | enum | repo, workflow, secret, environment, issue, milestone, package, wiki |
| resource_name | str | Full resource name/path |
| github_org | str | GitHub organization |
| created_at | ISO 8601 str | Creation timestamp |
| correlation_id | str | Unique ID for audit tracing |
| rollback_status | enum | pending, completed, failed |

### GuardrailDecision

Record of a guardrail evaluation on a LangChain tool call.

| Field | Type | Description |
|-------|------|-------------|
| timestamp | ISO 8601 str | Evaluation time |
| agent_role | enum | orchestrator, planner, executor, validator |
| tool_name | str | Name of the tool called |
| operation_type | enum | read, write, delete |
| target_resource | str | Resource being operated on |
| decision | enum | allow, block |
| reason | str | Why allowed or blocked |
| plan_reference | str \| None | FK to MigrationPlan if applicable |
| session_correlation | str | FK to AgentSession |

### PevCycleSummary

Compact JSON summary generated after each PEV cycle.

| Field | Type | Description |
|-------|------|-------------|
| cycle_number | int | Sequential cycle number |
| repos_processed | int | Count of repos processed |
| repos_succeeded | int | Count of repos succeeded |
| repos_failed | int | Count of repos failed |
| failures | JSON array | Summary of failures |
| next_action | str | What the next cycle should do |
| timestamp | ISO 8601 str | Cycle completion time |

### SessionStateMachine

Formal state machine for session status. Transitions driven by LangGraph node entry/exit.

| State | Allowed Transitions To |
|-------|----------------------|
| idle | thinking |
| thinking | planning, awaiting_input |
| planning | executing, awaiting_input |
| executing | validating, awaiting_input, awaiting_approval |
| validating | completed, failed, planning (retry) |
| awaiting_input | resume(previous_state), idle (cancel) |
| awaiting_approval | planning (approved), failed (rejected) |
| completed | (terminal) |
| failed | (terminal) |

**Validation**: `SessionStateMachine.transition(current, target)` validates allowed transitions and raises `InvalidTransitionError` for disallowed pairs. LangGraph node entry calls `transition()` to update session status.

### StreamingTokenEvent

SSE event for real-time LLM token streaming.

| Field | Type | Description |
|-------|------|-------------|
| kind | str | "token" (regular content) or "thinking" (structured thinking) |
| content | str | Chunk text |
| subagent | enum | orchestrator, planner, executor, validator |
| timestamp | ISO 8601 str | Event time |

### SSE Event Types

Complete set of SSE event types emitted by the streaming endpoint.

| kind | Description | Fields |
|------|-------------|--------|
| token | LLM token chunk | content, subagent, timestamp |
| thinking | Structured thinking output | content, subagent, timestamp |
| tool_call | Tool invocation | tool_name, args (masked), subagent, timestamp |
| tool_result | Tool completion | tool_name, result (masked), guardrail_decision, subagent, timestamp |
| status | Graph node transition | message, subagent, timestamp |
| heartbeat | Keepalive | timestamp |
| message | User-facing message | content, role, timestamp |
| form_request | Dynamic form | form_id, title, fields, timestamp |

## State Diagram: LangGraph Flow

```
START → classify_intent → [general_chat/migration_info] → finalize → END
                         → [migration_action] → orchestrator → [pending_form] → finalize → END
                                                                  → [start_pev] → planner → [pending_clarification] → orchestrator
                                                                                          → [plan complete] → executor → [pending_clarification] → planner
                                                                                                                          → [execution complete] → validator → [validation passed] → orchestrator → END
                                                                                                                                                    → [validation failed, retries < max] → planner
                                                                                                                                                    → [validation failed, retries >= max] → orchestrator → END
                                                                                          → [iteration >= max] → orchestrator → END
```
