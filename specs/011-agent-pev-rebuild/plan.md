# Implementation Plan: Agent PEV Architecture Rebuild

**Branch**: `011-agent-pev-rebuild` | **Date**: 2026-06-24 | **Spec**: `specs/011-agent-pev-rebuild/spec.md`

**Input**: Feature specification from `/specs/011-agent-pev-rebuild/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Rebuild the agent tab with a continuous PEV (Planner → Executor → Validator) loop architecture. Four LLM-driven agents (Orchestrator, Planner, Executor, Validator) share the same LLM provider instance but are differentiated by system prompts and tool access. The orchestrator runs a continuous reasoning loop that cycles through PEV until a terminal state is reached. Sessions are persisted to SQLite/PostgreSQL, survive server restarts, and support checkpoint/resume. The executor migrates all ADO resource types (repos, pipelines, secrets, service connections, Boards, Test Plans, Artifacts, Wiki) using a hybrid API routing strategy. Enterprise guardrails intercept all GitHub write operations. The UI is simplified to a Claude Code/Cursor-like chat interface showing only orchestrator messages.

**Prerequisite**: Spec 010 (Enterprise Audit Simplification) executes first — it decomposes `session_orchestrator.py` (2281 lines) into modules under 800 lines. This spec rewrites those decomposed modules for the continuous loop architecture.

## Technical Context

**Language/Version**: Python 3.9+ (backend), TypeScript / Next.js 14 (frontend)

**Primary Dependencies**: FastAPI, Click, Rich, httpx, Pydantic 2.x, React 18, TailwindCSS

**Storage**: SQLite (default) / PostgreSQL / DynamoDB via `ADO2GH_STORAGE_BACKEND` — persistent session store for agent sessions, messages, plans, PEV cycle state, repo locks, and rollback records. Audit logs retained indefinitely.

**Testing**: pytest + pytest-cov (85% coverage gate on `ado2gh` package), contract tests in `tests/contract/`, integration tests in `tests/integration/`

**Target Platform**: Linux server (Docker containers via docker-compose), Windows local dev

**Project Type**: Web service (FastAPI backend) + Web app (Next.js frontend) — multi-agent migration orchestrator

**Performance Goals**: 10 concurrent agent sessions, 60s per-LLM-call timeout with 1 retry, 800ms-1s UI poll interval, 50+ repo batch migration via sequential PEV queue

**Constraints**: LLM context window managed via sliding window (last 2 cycles full + JSON summary of prior), repo-level locks prevent concurrent migration of same repo, 20 max total loop iterations / 3 PEV retry cycles, 90-day session data retention

**Scale/Scope**: 4 agent roles, 88 functional requirements, 15 key entities, 35 success criteria, 9 user stories, 9 edge cases. Rewrites `session_orchestrator.py` (post-010 decomposition), `pev_coordinator.py`, `services/agent/main.py`, `AgentChat.tsx`, and extends `tool_catalog.py`, `llm_provider.py`, `agent.ts`

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
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
ado2gh/agents/
├── __init__.py
├── agent_scope.py              # existing — out-of-scope detection
├── execution_mode.py           # existing — dry-run/live parsing
├── live_execution_policy.py    # existing — approval gate logic
├── session_access.py           # existing — RBAC helpers
├── llm_provider.py             # extend — add 60s timeout + retry
├── pev_coordinator.py          # rewrite — continuous feedback loop
├── failure_analysis.py         # existing — pipeline failure analysis
├── planner.py                  # rewrite — LLM-driven planning with all resource types
├── executor.py                 # rewrite — LLM-driven execution with guardrails
├── validator.py                # rewrite — LLM-driven validation for all resource types
├── session_orchestrator.py     # rewrite (post-010) — continuous loop, state machine
├── session_state_machine.py    # NEW — formal state machine with enforced transitions
├── session_store.py            # NEW — persistent session storage (SQLite/PG)
├── repo_lock_store.py          # NEW — persistent repo-level locks
├── rollback_tracker.py         # NEW — tracks resources created for rollback
├── resource_mapping.py         # NEW — fixed ADO→GitHub field mappings
├── context_window.py           # NEW — sliding window context management
├── pev_cycle.py                # NEW — PEV cycle orchestrator (continuous loop)
├── metrics.py                  # NEW — Prometheus-compatible metrics
├── local/
│   ├── __init__.py
│   ├── audit_bridge.py         # existing — extend for agent-to-agent audit
│   ├── profiles.py             # existing
│   ├── stub_llm.py             # existing
│   └── tool_catalog.py         # extend — role-based access, new resource tools
└── skills/
    ├── orchestrator.md         # NEW — orchestrator system prompt
    ├── planner.md              # existing — update for all resource types
    ├── executor.md             # existing — update for all resource types
    └── validator.md            # existing — update for all resource types

services/agent/
├── Dockerfile
├── main.py                     # rewrite — persistent sessions, /metrics, /health, state machine
└── mcp_server.py               # existing — update tool catalog

apps/migration-ui/src/
├── components/
│   └── AgentChat.tsx           # rewrite — simplified chat, distinct UI states
├── lib/
│   ├── agent.ts                # extend — new session states, metrics, health
│   ├── agentSessions.ts        # extend — persistent session listing
│   └── types/
│       └── agent.ts            # rewrite — new message types, state machine states

ado2gh/api/
└── repo_lock.py                # extend — persistent lock store (currently in-memory)

tests/
├── contract/
│   └── test_011_agent_pev_contracts.py   # NEW
├── integration/
│   ├── test_011_session_persistence.py   # NEW
│   ├── test_011_pev_loop.py              # NEW
│   └── test_011_rollback.py              # NEW
└── unit/
    ├── test_011_state_machine.py         # NEW
    ├── test_011_repo_lock_store.py       # NEW
    ├── test_011_context_window.py        # NEW
    ├── test_011_resource_mapping.py      # NEW
    ├── test_011_metrics.py               # NEW
    ├── test_011_rollback_tracker.py      # NEW
    ├── test_011_orchestrator.py          # NEW
    ├── test_011_guardrails.py            # NEW
    ├── test_011_executor.py              # NEW
    └── test_011_validator.py             # NEW
```

**Structure Decision**: Existing repository structure maintained. New modules added to `ado2gh/agents/` for state machine, session store, repo locks, rollback tracking, resource mapping, context window, PEV cycle, and metrics. Backend service (`services/agent/main.py`) rewritten for persistent sessions and new endpoints. Frontend (`AgentChat.tsx`, `agent.ts`, `types/agent.ts`) rewritten for simplified chat interface. Tests organized in existing `tests/` structure with `011_` prefix.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
