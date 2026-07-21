# Implementation Plan: Local Agent Development in the IDE

**Branch**: `003-local-agent-ide` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Developers run migration agents locally from Cursor/VS Code using Spec Kit for agent feature work; dual integration paths (agent HTTP PEV + MCP tool bridge); lightweight SQLite profile without Redis/worker; optional platform auth; stub LLM by default; unified audit store.

## Summary

Deliver a **local agent development surface** that mirrors hosted agent guardrails while optimizing for IDE-native workflows. Developers invoke migration agents from **Cursor** (reference) and **VS Code** (secondary) via:

1. **Agent HTTP service** (`services/agent`) — full Planner → Executor → Validator sessions, approvals, remediation loops.
2. **MCP-style tool bridge** (`services/agent/mcp_server.py`) — direct IDE tool calls against the accelerator with a **shared tool catalog**.

**Local profiles**:

| Profile | Services | Auth | LLM |
|---------|----------|------|-----|
| `lightweight` | Accelerator + SQLite only (no Redis/worker) | `ADO2GH_AUTH_ENABLED=false` | Stub |
| `full` | Compose: redis, accelerator, worker, agent, web | Optional (default off in dev Compose) | Stub or cloud via env |
| `prod-like` | Compose prod overlay or K8s | `ADO2GH_AUTH_ENABLED=true` | Configurable |

Spec Kit remains the governance path for agent changes (`specs/003-*`, skills in `.cursor/skills/` and `ado2gh/agents/skills/`).

## Technical Context

**Language/Version**: Python >= 3.9 (`services/agent`, `ado2gh/agents/`); TypeScript for IDE config snippets only (no new UI required for P1)

**Primary Dependencies**: FastAPI + httpx (agent service); stdio JSON MCP loop; existing `ado2gh` planner/executor/validator modules; Cursor rules/skills; optional `mcp` SDK for future stdio hardening

**Storage**: StateDB SQLite (`ADO2GH_SQLITE_PATH`) for lightweight; Postgres in prod-like profiles; `audit_events` table for all local migration actions (FR-015)

**Testing**: pytest for agent routes, MCP tool dispatch, profile loader, stub LLM, audit emission; contract tests against `contracts/`; integration test: lightweight stack → session dry-run; **85% coverage** on new/changed `services/agent/` and `ado2gh/agents/local/` modules

**Target Platform**: Developer workstations (Windows primary in quickstart, macOS/Linux notes); Docker Compose for full/prod-like

**Project Type**: Agent service extension + IDE configuration + documentation/scripts (no new deployable product)

**Performance Goals**: Local session start < 2s after stack ready; MCP tool round-trip < 5s for dry-run plan (excluding real ADO/GH network)

**Constraints**: CA-001–CA-004; tool allowlist parity with hosted agents; no secrets in transcripts/logs/audit payloads; dual-path contract identity (FR-006)

**Scale/Scope**: Single-developer sessions; concurrent sessions isolated by `session_id`; no multi-tenant local hosting

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | `ado2gh/agents/local/` for profile + IDE helpers; thin FastAPI routes in `services/agent` |
| II. Documentation | Docstrings on profile loader, MCP catalog, session lifecycle; quickstart + contracts |
| III. Deprecation | Legacy one-off scripts deprecated with pointer to `scripts/run-local-agent.ps1` |
| IV. Architecture & Naming | `local/`, `LocalAgentProfile`, `ToolContract` — domain-clear |
| V. Enterprise Safeguards | Dry-run default, approval gates, audit_events, no secret echo (CA-001–CA-004) |
| VI. Testing (85%+) | Scoped pytest gate on agent + local modules |

**Result**: [x] PASS — all gates satisfied

**Post-design re-check**: [x] PASS — lightweight mode uses real accelerator (not mocks for core path); stubs limited to LLM/queue per spec clarifications.

## Project Structure

### Documentation (this feature)

```text
specs/003-local-agent-ide/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1 validation guide
├── contracts/           # HTTP API, MCP catalog, IDE setup, profiles
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
ado2gh/agents/
├── local/
│   ├── __init__.py
│   ├── profiles.py        # LocalAgentProfile loader (lightweight|full|prod-like)
│   ├── tool_catalog.py    # Shared ToolContract definitions + allowlist
│   ├── stub_llm.py        # Default offline LLM for local dev
│   └── audit_bridge.py    # Emit audit_events via StateDB for IDE actions
├── skills/                # Versioned planner/executor/validator markdown (existing)
├── planner.py             # Wire stub_llm when LLM_PROVIDER=stub
├── executor.py
└── validator.py

services/agent/
├── main.py                # Sessions, approvals, health, profile-aware config
├── mcp_server.py          # Unified tool catalog; stdio MCP; env-based ACCEL_URL
└── Dockerfile

.cursor/
├── rules/
│   └── local-agent.mdc    # IDE agent context: profiles, tools, guardrails
└── skills/
    └── ado2gh-local-agent/
        └── SKILL.md       # Invoke local agent workflows from Cursor

.vscode/
└── mcp.json.example       # VS Code MCP server registration template

scripts/
├── run-local-agent.ps1    # Lightweight: accel + agent only
├── run-local-agent.sh     # macOS/Linux equivalent
├── run-local.ps1          # Existing full stack (reference)
└── ide-check.ps1          # Smoke: health + tools/list + dry-run session

docker-compose.yml         # Existing full profile
docker-compose.lightweight.yml  # NEW: accelerator only, no redis/worker
.env.example               # Profile env vars documented

tests/
├── test_local_profiles.py
├── test_mcp_tool_catalog.py
├── test_agent_ide_sessions.py
└── contract/
    └── test_ide_contracts.py
```

**Structure Decision**: Extend existing `services/agent` and `ado2gh/agents` rather than a new service. IDE integration is configuration + contracts; Spec Kit skills live under `.cursor/skills/`.

## Complexity Tracking

> No constitution violations requiring justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## Phase 0: Research

See [research.md](./research.md) — resolves MCP vs HTTP split, lightweight accelerator mode, stub LLM wiring, auth profile alignment with `002-login-bootstrap`, and audit bridge design.

## Phase 1: Design Artifacts

| Artifact | Path |
|----------|------|
| Data model | [data-model.md](./data-model.md) |
| Agent HTTP contract | [contracts/agent-ide-api.md](./contracts/agent-ide-api.md) |
| MCP tool catalog | [contracts/mcp-tool-catalog.md](./contracts/mcp-tool-catalog.md) |
| IDE setup | [contracts/ide-setup.md](./contracts/ide-setup.md) |
| Local profiles | [contracts/local-profiles.md](./contracts/local-profiles.md) |
| Validation guide | [quickstart.md](./quickstart.md) |

## Phase 2: Tasks (out of scope for /speckit-plan)

Run `/speckit-tasks` to generate `tasks.md` from this plan and contracts.

## Dependencies & Integration

- **001-agentic-migration-platform**: PEV loop, assignments, audit store, agent routes baseline
- **002-login-bootstrap**: `ADO2GH_AUTH_ENABLED` for prod-like profiles; agent passes session cookie or bearer when auth on
- **Spec Kit**: `.specify/` workflows; agent context in `.cursor/rules/specify-rules.mdc`

## Risks

| Risk | Mitigation |
|------|------------|
| MCP stdio loop too minimal for real IDE MCP clients | Document Cursor-native skills first; add official MCP SDK adapter in tasks if needed |
| Lightweight accel without worker breaks enqueue | Stub synchronous job execution in lightweight profile; document degraded `enqueue` behavior |
| Tool catalog drift between HTTP and MCP | Single `tool_catalog.py` source; MCP and HTTP import same definitions |
| Windows vs Unix script parity | PowerShell + bash scripts; quickstart notes for each OS |
