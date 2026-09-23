# Research: Agent PEV Console, Model Onboarding, and Admin/Operator RBAC

**Feature**: `004-agent-pev-rbac` | **Date**: 2026-06-16

## R1 — PEV live execution order

**Decision**: Approval MUST precede any live (`dry_run=false`) PEV migrate step. Dry-run PEV completes fully first; operator calls `request-live`; **platform admin or approver** approves on unified queue; agent re-runs executor/validator with `dry_run=false`. If assignment-linked, `enforce_live_gate()` runs after platform approval (AND).

**Rationale**: Current `services/agent/main.py` can invoke migrate before `awaiting_approval`, violating CA-001/FR-008. Spec and constitution require explicit approval before irreversible steps.

**Alternatives considered**:
- *Post-migrate approval* — rejected; mutations already occurred.
- *Skip PEV re-run after approval* — rejected; executor must run with authorized live flag after approval record exists.

---

## R2 — Live approval persistence

**Decision**: Store `LiveExecutionApproval` rows in StateDB (`live_execution_approvals` table) via accelerator API. Deprecate agent in-memory `_approvals` dict (alias to StateDB during transition, remove in follow-up).

**Rationale**: FR-009 requires cross-session admin queue visible while operator session remains active. In-memory dict is lost on restart and invisible to second browser.

**Alternatives considered**:
- *Redis queue* — rejected for v1; adds infra dependency; StateDB already used for audit.
- *Agent-only queue* — rejected; admin UI already talks to accelerator with auth cookie.

---

## R3 — LLM model credential storage

**Decision**: v1 keeps `LLMModelStore` file layout (`llm_models.json` + `_secrets` sidecar under `ADO2GH_DATA_DIR`). Agent runtime resolves secrets server-side by model id. Add `default_for_agent` boolean; validate API key trim on save.

**Rationale**: Store and CRUD API already exist (`ado2gh/api/llm_model_store.py`, accelerator `/v1/settings/llm-models`). Moving to Postgres columns is optional v2; file store satisfies CA-003 for single-node Compose.

**Alternatives considered**:
- *Secrets Manager / Vault* — out of scope v1; env-based override remains for CI.
- *Postgres BYTEA secrets* — deferred; requires migration + rotation UX.

---

## R4 — Live LLM provider adapters

**Decision**: v1 MUST implement **OpenAI-compatible** and **Anthropic** HTTP adapters behind `get_llm_provider(model_id)`, plus stub/offline fallback. Both provider types are required for v1 acceptance (spec clarification). Invalid credentials fall back to stub with `degraded: true`.

**Rationale**: Admin onboarding implies at least one real provider path for demo; stub-only already covered by 003.

**Alternatives considered**:
- *Stub-only v1* — rejected; FR-003/004 require registry → runtime wiring.
- *Bedrock first* — deferred; org-specific; OpenAI/Anthropic cover common API-key onboarding.

---

## R5 — Platform RBAC capability matrix

**Decision**: Extend `permissions_for()` with explicit capabilities consumed by API and UI:

| Capability | admin | coordinator | operator | approver |
|------------|-------|-------------|----------|----------|
| `can_manage_settings` | yes | no | no | no |
| `can_manage_models` | yes | no | no | no |
| `can_manage_users` | yes | no | no | no |
| `can_operate` | yes | yes | yes | no |
| `can_approve_live_execution` | yes | no | no | yes |
| `can_coordinate` | yes | yes | no | no |

Profile create/update/delete and LLM CRUD require `can_manage_settings` / `can_manage_models`. Platform live approval requires `can_approve_live_execution`.

**Rationale**: FR-006/007 need finer grain than `role === 'admin'` checks scattered in UI. Coordinator unchanged for assignment work; does not gain settings access.

**Alternatives considered**:
- *Single admin flag only* — rejected; approver role must approve live without settings access (FR-009).
- *Replace assignment RBAC* — rejected per FR-014.

---

## R6 — Dual approval layers (assignment gate + platform admin)

**Decision**: Keep both layers independent:
1. **Assignment phase gate** — `enforce_live_gate()` when `assignment_id` present (001).
2. **Platform live approval** — `LiveExecutionApproval` for operator-initiated live agent/migrate from console.

When both apply, order is: platform approval recorded → assignment gate checked at migrate call → live execution **only if both succeed (AND)**.

**Rationale**: Spec clarification (2026-06-16) explicitly requires AND, not OR. FR-014 preserves assignment Approver RBAC separately from platform queue.

**Alternatives considered**:
- *Merge into single queue* — rejected; breaks assignment-scoped audit and existing API clients.

---

## R7 — Agent service authentication

**Decision**: When `ADO2GH_AUTH_ENABLED=true`, agent service validates `ado2gh_session` cookie on `/v1/sessions*` mutating routes by calling shared `AuthService.get_session()` (same StateDB). UI sends `credentials: 'include'` on agent fetch (already on accelerator).

**Rationale**: FR-011/012 require attribution and concurrent sessions; agent cannot trust client-supplied `actor` strings.

**Alternatives considered**:
- *Accelerator proxy for all agent routes* — heavier; duplicates WebSocket/polling paths.
- *API key per user* — rejected; cookie session already standardized in 002.

---

## R8 — Concurrent sessions in Docker

**Decision**: No code change to session model required; `auth_sessions` already supports multiple tokens. Validation: prod overlay E2E with two browsers, distinct users, 30-minute idle (SC-005). Container restart invalidates sessions (existing behavior); document in quickstart.

**Rationale**: Exploration confirmed per-token isolation; gap is test coverage and auth-enabled compose defaults, not schema.

**Alternatives considered**:
- *Sticky sessions / Redis* — unnecessary for v1 single-replica Compose.

---

## R9 — Unified live approval queue scope

**Decision**: One platform queue (`live_execution_approvals`) for **all** operator-initiated live paths: Agent PEV (`agent_session`), dashboard live migrate (`migrate_job`), and pipeline live runs (`pipeline_run`).

**Rationale**: Spec clarification requires unified queue (FR-008/009); single admin/approver inbox reduces operational confusion.

**Alternatives considered**:
- *Agent-only queue* — rejected; FR-008 covers dashboard/pipeline migrate.
- *Separate queues per surface* — rejected; splits approver workflow.

---

## R10 — Operator deployment profile access

**Decision**: Operators get **read-only** access to Settings → deployment profiles (view names and details); all mutations require `can_manage_settings` (admin only). API: `GET` allowed, `POST/PUT/DELETE` return `403`.

**Rationale**: Spec clarification (FR-010); operators need context for migrations without edit rights.

**Alternatives considered**:
- *Hide profiles entirely* — rejected; operators need visibility into active profile context.
- *List-only without details* — rejected; insufficient for operational clarity.
