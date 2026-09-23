# Implementation Plan: LangGraph Agent Refactor

**Branch**: `012-langgraph-agent-refactor` | **Date**: 2026-06-24 | **Spec**: `specs/012-langgraph-agent-refactor/spec.md`

**Input**: Feature specification from `/specs/012-langgraph-agent-refactor/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Refactor the migration agent to use LangGraph/LangChain as the orchestration framework. The existing custom orchestration loop (`session_orchestrator.py`, `pev_cycle.py`, `orchestration/loop.py`) is replaced with a single unified LangGraph `StateGraph` containing four agent nodes (Orchestrator, Planner, Executor, Validator) with conditional edges for the continuous PEV loop. All four agents share the same LangChain `BaseChatModel` instance, differentiated by system prompts and tool bindings. LangChain native tool-calling (`bind_tools()`) replaces custom JSON parsing. LLM thoughts stream in real time to the UI via SSE with per-token events. The system supports any LangChain-compatible chat model provider with capability detection (tool-calling required, streaming/thinking optional). All legacy agent modules are deleted and consolidated into a new `migration_agent/` package. LangGraph checkpointing (`SqliteSaver`/`PostgresSaver`) handles in-flight graph state; the session store handles long-term persistence.

**Prerequisite**: Spec 011 (Agent PEV Architecture Rebuild) — the existing agent architecture and PEV loop concepts are preserved but reimplemented using LangGraph/LangChain.

## Technical Context

**Language/Version**: Python 3.9+ (backend), TypeScript / Next.js 14 (frontend)

**Primary Dependencies**: LangGraph (>=0.1), LangChain (>=0.2), langchain-core (>=0.2), langchain-openai (>=0.1), langchain-anthropic (>=0.1), FastAPI, Click, Rich, httpx, Pydantic 2.x, React 18, TailwindCSS

**Storage**: LangGraph checkpointing via `SqliteSaver` (default) / `PostgresSaver` (when `ADO2GH_STORAGE_BACKEND=postgresql`) for in-flight graph state. Session store (SQLite/PostgreSQL) for long-term persistence (session metadata, migration plans, PEV cycle summaries, audit logs).

**Testing**: pytest + pytest-cov (85% coverage gate on `ado2gh` package), contract tests in `tests/contract/`, integration tests in `tests/integration/`. New tests use descriptive names (not numbered prefixes).

**Target Platform**: Linux server (Docker containers via docker-compose), Windows local dev

**Project Type**: Web service (FastAPI backend) + Web app (Next.js frontend) — LangGraph-based multi-agent migration orchestrator

**Performance Goals**: 10 concurrent agent sessions, 60s per-LLM-call timeout with 1 retry (LangChain native timeout where supported, `asyncio.wait_for` fallback), 15s SSE heartbeat keepalive, 50+ repo batch migration via sequential PEV queue

**Constraints**: LLM context window managed via LangChain `trim_messages` (last 2 PEV cycles full + JSON summary of prior), repo-level locks prevent concurrent migration of same repo, 20 max total loop iterations / 3 PEV retry cycles, LangGraph recursion limit >= 40, native tool-calling required for all models

**Scale/Scope**: 4 agent nodes in single StateGraph, 103+ functional requirements, 14 key entities, 20+ success criteria. Creates new `migration_agent/` package (15+ modules), deletes 18+ legacy modules, rewrites `session_routes.py`, updates `AgentChat.tsx`, `agent.ts`, `types/agent.ts`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Plan describes readable structure; no unjustified complexity |
| II. Documentation | New modules/functions will include purpose/inputs/outputs docstrings |
| III. Deprecation | No silent legacy paths; deprecations marked or removed |
| IV. Architecture & Naming | Folder/module names match domain (migration, phase, pipeline, validate) |
| V. Enterprise Safeguards | Dry-run/HITL/audit/secrets handling addressed for destructive scope |
| VI. Testing (85%+) | Test strategy defined; coverage gate will not regress below 85% on `ado2gh` |

**Result**: [x] PASS — all gates satisfied

## Project Structure

### Documentation (this feature)

```text
specs/012-langgraph-agent-refactor/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── api-contracts.md # SSE streaming, session, and graph API contracts
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
ado2gh/agents/migration_agent/
├── __init__.py
├── nodes.py                    # LangGraph node functions (orchestrator, planner, executor, validator, tool exec, finalize)
├── graph.py                    # StateGraph definition, conditional edges, compiled graph
├── state.py                    # AgentState TypedDict with reducers (operator.add for lists)
├── orchestrator.py             # process_user_message(), stream_user_message() wrappers
├── llm_bridge.py               # resolve_langchain_llm() → BaseChatModel or None
├── prompts.py                  # Loads .md files from prompts/ dir, exposes system prompt constants
├── constants.py                # NO_LLM_CONFIGURED_MESSAGE, shared constants
├── forms.py                    # Dynamic form builders (extracted from session_orchestrator.py)
├── utils.py                    # Event appending, task init, session helpers
├── policies.py                 # execution_mode, live_execution_policy, session_access, agent_scope (consolidated)
├── session_state.py            # State machine + OrchestratorResult (moved from orchestration/)
├── session_store.py            # Long-term persistence (moved from agents/)
├── guardrails.py               # Guardrail evaluation as LangChain tool wrapper
├── prompts/                    # .md skill files (moved from skills/)
│   ├── orchestrator.md
│   ├── planner.md
│   ├── executor.md
│   └── validator.md
└── tools/
    ├── __init__.py
    ├── planner_tools.py        # LangChain StructuredTools for Planner node
    ├── executor_tools.py       # LangChain StructuredTools for Executor node
    └── validator_tools.py      # LangChain StructuredTools for Validator node

services/agent/
├── Dockerfile
├── main.py                     # update — remove _shared.py import, use migration_agent
└── routes/
    └── session_routes.py       # rewrite — use migration_agent.orchestrator, checkpoint resume for forms

apps/migration-ui/src/
├── components/
│   └── AgentChat.tsx           # rewrite — streaming thinking blocks, collapsible per-agent
├── lib/
│   ├── agent.ts                # extend — new SSE event types (token, thinking, tool_call, tool_result, status, heartbeat)
│   └── types/
│       └── agent.ts            # rewrite — new message types, ModelCapabilities

requirements.txt                # update — add LangChain/LangGraph deps with >= pins

tests/
├── contract/
│   └── test_langgraph_contracts.py     # NEW — graph structure, SSE event contracts
├── integration/
│   ├── test_graph_execution.py          # NEW — end-to-end graph execution
│   ├── test_checkpoint_resume.py        # NEW — checkpoint/resume after restart
│   └── test_pev_loop_integration.py     # NEW — full PEV cycle via LangGraph
└── unit/
    ├── test_graph_structure.py          # NEW — StateGraph nodes and edges
    ├── test_state_reducers.py           # NEW — operator.add reducers
    ├── test_llm_bridge.py               # NEW — model resolution and capabilities
    ├── test_orchestrator_node.py        # NEW — intent classification, forms
    ├── test_planner_node.py             # NEW — plan generation, revision
    ├── test_executor_node.py            # NEW — execution, guardrails
    ├── test_validator_node.py           # NEW — validation, feedback
    ├── test_conditional_edges.py        # NEW — routing logic
    ├── test_tool_bindings.py            # NEW — role-based tool access
    ├── test_guardrails.py               # NEW — guardrail wrapper
    ├── test_streaming.py                # NEW — SSE event generation
    ├── test_session_state.py            # NEW — state machine transitions
    └── test_model_capabilities.py       # NEW — capability detection/validation
```

**Modules to delete from `ado2gh/agents/`:** `langgraph_agent/`, `orchestration/`, `local/`, `planner.py`, `executor.py`, `validator.py`, `pev_cycle.py`, `pev_coordinator.py`, `session_orchestrator.py`, `llm_provider.py`, `agent_scope.py`, `context_window.py`, `execution_mode.py`, `live_execution_policy.py`, `session_access.py`, `session_state_machine.py`, `session_store.py`, `skills/`. **Also delete:** `services/agent/routes/_shared.py`.

**Structure Decision**: New `migration_agent/` package consolidates all agent logic. Existing `langgraph_agent/` is the source — files are copied and refactored, old package deleted after tests pass. All legacy modules are deleted; no parallel packages or feature flags. Tests use descriptive names without numbered prefixes.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _(none)_ | — | — |
