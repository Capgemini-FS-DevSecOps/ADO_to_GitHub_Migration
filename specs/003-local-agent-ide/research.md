# Research: Local Agent Development in the IDE

**Feature**: `003-local-agent-ide` | **Date**: 2026-06-16

## R1: Dual integration architecture (HTTP vs MCP)

**Decision**: Canonical **both-path** integration — agent HTTP for full PEV sessions; MCP-style stdio bridge for direct tool calls; **single shared tool catalog** (`ado2gh/agents/local/tool_catalog.py`).

**Rationale**: HTTP sessions carry approval state, subagent routing, and remediation loops that a raw tool call cannot represent. IDE chat often needs one-off tools (readiness, gate-check) without starting a full session. Shared catalog prevents privileged local-only tools (FR-007).

**Alternatives considered**:
- MCP-only: Rejected — PEV approval workflow would be reimplemented in every IDE host.
- HTTP-only: Rejected — poor ergonomics for Cursor tool palette / command-style invocations.
- Separate catalogs per path: Rejected — drift risk violates FR-006.

## R2: Lightweight local profile (minimal real accelerator)

**Decision**: `lightweight` profile runs **uvicorn accelerator only** with `ADO2GH_STORAGE_BACKEND=sqlite`, **no Redis, no worker**. Job enqueue runs **inline stub executor** in accelerator when `ADO2GH_LIGHTWEIGHT_MODE=true` (synchronous dry-run job record, no queue).

**Rationale**: Spec clarification requires real SQLite StateDB for planning/validation/audit, not mocked backend. Redis/worker are optional for agent authors iterating on prompts/skills.

**Alternatives considered**:
- Full mocks: Rejected per clarification Session 2026-06-16.
- In-process only (no HTTP): Rejected — breaks FR-008 (configurable remote URL) and parity with hosted API.

## R3: Default LLM for local development

**Decision**: `LLM_PROVIDER=stub` default in all local profiles via `.env.example` and profile loader. Stub returns deterministic planner steps from assignment context without external API. Cloud providers enabled when `LLM_PROVIDER=bedrock|openai` and credentials present.

**Rationale**: SC-001 (15-minute setup) and FR-014 require no cloud API key for first run.

**Alternatives considered**:
- Require cloud LLM: Rejected — blocks onboarding.
- No LLM at all (hardcoded planner): Rejected — cannot test prompt/skill changes meaningfully.

## R4: Platform auth on local profiles

**Decision**: Align with `002-login-bootstrap`: `ADO2GH_AUTH_ENABLED=false` default for `lightweight` and dev `full` Compose; `true` for prod-like Compose overlay / K8s. Agent service reads auth flag and attaches session cookie from env `ADO2GH_SESSION_COOKIE` when calling accelerator; IDE quickstart documents login only for prod-like.

**Rationale**: FR-013 and spec edge cases — local dev speed vs prod-like security mirror.

**Alternatives considered**:
- Always require login: Rejected for local dev friction.
- Never support login locally: Rejected — cannot test auth integration.

## R5: Audit store for IDE actions

**Decision**: All migration mutations and tool calls that change state write to existing `audit_events` via `ado2gh.assignments.audit` (or `audit_bridge.py` wrapper) with fields: `session_id`, `actor` (profile default `local-developer` or authenticated user), `action`, `outcome`, `metadata` (no secrets).

**Rationale**: FR-015, CA-004 — single audit trail for hosted and local.

**Alternatives considered**:
- Local-only log file: Rejected — breaks audit parity.
- Skip audit in lightweight: Rejected — violates CA-004.

## R6: IDE integration pattern (Cursor + VS Code)

**Decision**: **Cursor** — `.cursor/rules/local-agent.mdc` + `.cursor/skills/ado2gh-local-agent/SKILL.md` referencing contracts and quickstart. **VS Code** — `.vscode/mcp.json.example` registering stdio MCP server; documentation for Copilot/agent extensions that consume MCP.

**Rationale**: FR-004, FR-005 — repo-native discoverability without per-developer scripts.

**Alternatives considered**:
- Private Cursor config outside repo: Rejected — not reproducible (SC-004).
- Custom VS Code extension: Deferred — MCP + docs sufficient for P1.

## R7: MCP protocol fidelity

**Decision**: Phase 1 keeps minimal JSON-line stdio loop (existing `mcp_server.py`) for dogfood; tasks phase may add `mcp` package adapter for full `tools/list` / `tools/call` schema if IDE clients require it.

**Rationale**: Existing code works for internal testing; avoid blocking plan on SDK version churn.

**Alternatives considered**:
- Full MCP SDK immediately: Optional upgrade in implementation tasks.

## R8: Error guidance when backend unreachable

**Decision**: Agent HTTP returns structured errors (`connection_error`, `remediation_steps[]`) when accelerator health check fails; MCP bridge returns same JSON error envelope; Cursor skill documents checking `http://localhost:8080/health` and env vars.

**Rationale**: FR-010, SC-006 — actionable IDE-visible guidance.

**Alternatives considered**:
- Generic 500 text: Rejected — fails dogfood metric.

## R9: Spec Kit traceability for agent skills

**Decision**: Agent skill markdown lives under `ado2gh/agents/skills/` with front-matter `spec: specs/003-local-agent-ide/spec.md`; Spec Kit features reference skill paths in plan/tasks.

**Rationale**: FR-011, SC-004 — traceable agent evolution.

**Alternatives considered**:
- Skills only in `.cursor/`: Rejected — not visible to non-Cursor hosts.
