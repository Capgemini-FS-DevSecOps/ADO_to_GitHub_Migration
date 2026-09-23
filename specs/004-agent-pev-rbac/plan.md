# Implementation Plan: Agent PEV Console, Model Onboarding, and Admin/Operator RBAC

**Branch**: `004-agent-pev-rbac` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-agent-pev-rbac/spec.md`

**Plan addendum (2026-06-16 clarifications)**: Platform **admin** and **approver** approve live queue; **unified queue** for Agent PEV + dashboard/pipeline live migrate; **dual AND gates** (platform approval first, then assignment phase gate); operators **read-only** on deployment profiles; v1 requires **stub + OpenAI + Anthropic** adapters.

## Summary

Wire the **migration console Agent tab** to the existing **PEV agent service** with enterprise guardrails: dry-run by default, **platform RBAC** (admin settings, approver live gate, operator execute-only), **admin-managed LLM registry** (OpenAI + Anthropic + stub), and a **unified persistent live-execution approval queue** for all operator-initiated live mutations.

The backend already has substantial building blocks from `001`–`003` and partial UI (`AgentChat`, `LLMModelStore`). This feature closes gaps: model selection → runtime, approval **before** live PEV, cross-session queue visible to admin/approver, API enforcement, operator read-only profiles, and audit attribution.

## Technical Context

**Language/Version**: Python >= 3.9 (`ado2gh`, `services/agent`, `services/accelerator_api`); TypeScript / Next.js 14 (`apps/migration-ui`)

**Primary Dependencies**: FastAPI; PEV loop (`services/agent/main.py`); `AuthService` + `permissions_for()`; `LLMModelStore`; `AgentChat` + `agent.ts`; StateDB via `create_state_db()`; `enforce_live_gate()` for assignment gates

**Storage**: StateDB (`platform_users`, `auth_sessions`, `audit_events`, `live_execution_approvals`); LLM configs in `{ADO2GH_DATA_DIR}/llm_models.json` (v1)

**Testing**: pytest for RBAC, unified approval queue, OpenAI/Anthropic runtime, PEV live-gate order, dual AND gates; Docker prod-overlay E2E; **85%** coverage on changed modules

**Target Platform**: Docker Compose dev + `docker-compose.prod.yml` multi-user reference

**Project Type**: Web console + API extensions (no new deployable service)

**Performance Goals**: Agent session start < 3s; approval queue list < 200ms; model picker load < 500ms

**Constraints**: CA-001–CA-004; unified queue (FR-008/009); platform admin **or** approver approval; dual AND gates (FR-014); operator read-only profiles (FR-010); OpenAI + Anthropic required for v1 (FR-003); no secrets after save

**Scale/Scope**: Single-tenant Compose; concurrent browser sessions; deprecate agent in-memory `_approvals`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | `platform_rbac.py` + `live_approval_store.py`; single unified queue |
| II. Documentation | Docstrings + contracts reflect clarifications session |
| III. Deprecation | In-memory `_approvals` aliased then removed |
| IV. Architecture & Naming | Domain-clear modules; `/settings/approvals` for admin+approver |
| V. Enterprise Safeguards | Dry-run default; dual gates; secret redaction; audit actor+role |
| VI. Testing (85%+) | RBAC, queue, dual gate, both LLM providers |

**Result**: [x] PASS — all gates satisfied

**Post-design re-check**: [x] PASS — clarifications integrated; no unresolved NEEDS CLARIFICATION

## Project Structure

### Documentation (this feature)

```text
specs/004-agent-pev-rbac/
├── plan.md              # This file
├── research.md          # Phase 0 (updated)
├── data-model.md        # Phase 1 (updated)
├── quickstart.md        # Phase 1 (updated)
├── contracts/           # Phase 1 (updated)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
ado2gh/
├── auth/service.py                 # permissions_for() + approver capabilities
├── agents/llm_provider.py          # stub + OpenAI + Anthropic adapters
├── api/
│   ├── llm_model_store.py
│   ├── platform_rbac.py
│   └── live_approval_store.py      # unified queue CRUD
├── state/db.py + postgres_db.py    # live_execution_approvals

services/agent/main.py              # PEV order, auth, enqueue unified queue
services/accelerator_api/main.py    # RBAC guards, queue routes, migrate enqueue

apps/migration-ui/src/
├── app/agent/page.tsx
├── app/settings/models/page.tsx
├── app/settings/approvals/page.tsx # admin + approver
├── app/settings/profiles/page.tsx  # operator read-only
├── components/AgentChat.tsx
└── lib/agent.ts + auth.ts

tests/
├── test_platform_rbac.py
├── test_live_approval_queue.py
├── test_llm_model_runtime.py       # OpenAI + Anthropic paths
└── test_agent_pev_live_gate.py     # dual AND gates
```

**Structure Decision**: Extend monorepo; unified queue in accelerator; dashboard/pipeline live migrate hooks enqueue same store.

## Complexity Tracking

No violations.

## Implementation Phases

### Phase A — PEV UI + model wiring (P1, FR-001/002/005)

1. Pass `model_id` from `AgentChat` / `agent.ts`; stub/degraded badge from `/v1/llm/status`.
2. Fix PEV live order: dry-run completes → `request-live` → approval → `pev_loop(dry_run=False)`.
3. Implement **OpenAI-compatible** and **Anthropic** adapters in `llm_provider.py` (v1 acceptance).
4. Update `agent/page.tsx` — remove placeholder copy.

### Phase B — Platform RBAC (P1, FR-006/007/010)

1. Extend `permissions_for()` — `can_manage_settings`, `can_manage_models`, `can_approve_live_execution`.
2. Profile **mutations** admin-only; profile **GET** allowed for operators (read-only UI).
3. LLM CRUD admin-only; models list readable for operator Agent picker.
4. Role hints on Settings nav and Agent tab (SC-007).

### Phase C — Unified live approval queue (P1/P2, FR-008/009)

1. StateDB `live_execution_approvals` with `scope_type`: `agent_session`, `migrate_job`, `pipeline_run`.
2. Routes: `GET/POST /v1/platform/approvals`, approve/deny — **`can_approve_live_execution`** (admin + approver).
3. Enqueue from agent `request-live`, dashboard live migrate, pipeline live run.
4. UI `/settings/approvals` for admin and approver roles.

### Phase D — Audit + concurrent sessions (P2, FR-011/012)

1. Cookie forwarding UI → agent → accelerator; audit `actor=username`, `role`.
2. E2E two browsers (SC-005).

### Phase E — Dual AND gates + tests (FR-014)

1. After platform approval, call `enforce_live_gate()` when `assignment_id` set; abort with `409` if gate fails.
2. Quickstart Scenario 9 + contract tests.

## Phase 0 & Phase 1 Artifacts

| Artifact | Path | Status |
|----------|------|--------|
| Research | [research.md](./research.md) | Updated |
| Data model | [data-model.md](./data-model.md) | Updated |
| Agent PEV UI API | [contracts/agent-pev-ui-api.md](./contracts/agent-pev-ui-api.md) | Updated |
| LLM model API | [contracts/llm-model-api.md](./contracts/llm-model-api.md) | Updated |
| Platform RBAC | [contracts/platform-rbac.md](./contracts/platform-rbac.md) | Updated |
| Live approval API | [contracts/live-approval-api.md](./contracts/live-approval-api.md) | Updated |
| Quickstart | [quickstart.md](./quickstart.md) | Updated |

## Dependencies

| Feature | Reuse |
|---------|-------|
| `002-login-bootstrap` | Sessions, platform roles including **approver** |
| `001-agentic-migration-platform` | PEV, assignments, `enforce_live_gate`, audit |
| `003-local-agent-ide` | Agent service, stub LLM, tool catalog |
| `005-profile-onboarding` | Profile governance; operator read-only complements FR-010 |
