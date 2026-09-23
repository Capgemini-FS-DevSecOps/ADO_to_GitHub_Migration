# Data Model: Agent PEV Console, Model Onboarding, and Admin/Operator RBAC

**Feature**: `004-agent-pev-rbac` | **Date**: 2026-06-16

## Entity Overview

```text
PlatformUser ──< AuthSession
PlatformUser ──< LiveExecutionApproval (as requester / approver)
LLMModelConfig (file store; admin-managed)
AgentPEVSession (agent service in-memory + audit_events)
LiveExecutionApproval (StateDB)
AuditEvent (StateDB; attribution extension)
PlatformRoleCapability (derived from PlatformRole via permissions_for)
```

---

## LLMModelConfig

Admin-managed model entry for agent planning/chat.

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `mdl_{uuid}` |
| `display_name` | string | UI label |
| `provider` | enum | `stub`, `openai`, `anthropic`, `offline` |
| `model_id` | string | Provider model name (e.g. `gpt-4o-mini`) |
| `api_key` | secret | Stored in `_secrets` sidecar; never returned after save |
| `enabled` | bool | Listed in operator picker when true |
| `default_for_agent` | bool | Pre-selected in Agent tab when true |
| `created_at` | ISO8601 | |
| `updated_at` | ISO8601 | |

**Validation**:
- Trim whitespace on `api_key` before save; reject empty key for non-stub providers.
- At most one `default_for_agent=true` (last write wins or explicit unset of prior default).

**v1 acceptance**: At least one onboarded **openai** and one **anthropic** model must be exercisable via agent runtime (stub when none configured).

---

## AgentPEVSession

UI-bound session mirroring agent service state (ephemeral in agent process; durable facets in audit).

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | string | `sess_{uuid}` |
| `user_id` | string | From auth session |
| `username` | string | Audit attribution |
| `platform_role` | string | admin / operator / … |
| `profile_id` | string | Migration profile context |
| `assignment_id` | string? | Optional assignment scope |
| `selected_model_id` | string? | From LLMModelConfig |
| `dry_run` | bool | Default `true` |
| `status` | enum | `planning`, `executing`, `validating`, `awaiting_approval`, `completed`, `failed` |
| `live_approval_id` | string? | FK to LiveExecutionApproval when pending |
| `plan_id` | string? | Linked plan artifact |
| `run_id` | string? | Linked PEV run |
| `degraded_llm` | bool | Stub fallback active |
| `created_at` | ISO8601 | |
| `updated_at` | ISO8601 | |

**State transitions**:

```text
planning → executing → validating → completed
                ↓                      ↑
         awaiting_approval ──(approve)─┘
                ↓ (deny)
            completed (dry-run only) / failed
```

Live path: `validating` (dry-run) → `awaiting_approval` → (platform admin or approver approve) → `executing` (live) → `validating` → `completed`. If assignment-linked, `enforce_live_gate()` runs after platform approval; failure blocks live execution (AND).

---

## LiveExecutionApproval

Persistent operator request for live execution on the **unified platform queue** (Agent PEV, dashboard migrate, pipeline live run).

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `lve_{uuid}` |
| `requester_user_id` | string | Operator |
| `requester_username` | string | Denormalized for audit display |
| `scope_type` | enum | `agent_session`, `migrate_job`, `pipeline_run` |
| `scope_id` | string | session_id / job_id / run_id |
| `assignment_id` | string? | Optional |
| `profile_id` | string? | Optional |
| `status` | enum | `pending`, `approved`, `denied`, `expired`, `superseded` |
| `reason_request` | string? | Operator note |
| `approver_user_id` | string? | Admin or approver |
| `approver_username` | string? | |
| `reason_decision` | string | Required on approve/deny |
| `requested_at` | ISO8601 | |
| `decided_at` | ISO8601? | |

**Validation**:
- Only one `pending` approval per `(scope_type, scope_id)`; duplicate request returns existing id (idempotency).
- Approve/deny requires `can_approve_live_execution` (platform **admin** or **approver**).
- On approve: resume scoped action; if `assignment_id` set, run `enforce_live_gate()` — abort with audit if gate fails after platform approval.

**Storage**: Table `live_execution_approvals` in StateDB (SQLite + Postgres).

---

## PlatformRoleCapability

Derived mapping (not persisted); returned in `GET /v1/auth/session` as `permissions` object.

| Capability | Description |
|------------|-------------|
| `can_manage_settings` | CRUD deployment profiles (operators: read-only view only) |
| `can_manage_models` | CRUD LLM model configs |
| `can_manage_users` | User admin (002) |
| `can_operate` | Dry-run migrations, agent sessions |
| `can_approve_live_execution` | Approve/deny live execution queue |
| `can_coordinate` | Assignment coordination (001) |
| `can_approve` | Legacy alias; true when `can_approve_live_execution` |

See [research.md](./research.md) R5 for role matrix.

---

## ConcurrentAuthSession

Per-browser session (002 `auth_sessions` table).

| Field | Type | Notes |
|-------|------|-------|
| `token` | string | Cookie value |
| `user_id` | string | |
| `expires_at` | ISO8601 | Default 8h |
| `created_at` | ISO8601 | |

**Rules**: Multiple active tokens per instance; no shared server-side “current user” singleton. Restart clears tokens; users re-login.

---

## AuditEvent (extension)

Existing `audit_events` table; new event types:

| event_type | When |
|------------|------|
| `agent.session.started` | PEV session created |
| `agent.session.live_requested` | Operator request-live |
| `agent.session.live_approved` | Admin approve |
| `agent.session.live_denied` | Admin deny |
| `llm.model.created` | Admin onboard model |
| `llm.model.updated` | Admin update |
| `llm.model.deleted` | Admin remove |
| `platform.live_execution.approved` | Queue decision |

**Payload rules (CA-003)**: `actor` = username; include `role`, `scope_id`, `approval_id`; never include api_key/token fields.

---

## Compatibility Notes

- **Assignment RBAC** (`ado2gh/assignments/rbac.py`): unchanged; profile-scoped Coordinator/Operator/Approver actors in assignment API bodies remain separate from platform capabilities.
- **Profile governance** (005): operator profile submit/appeal flows coexist; platform `can_manage_settings` governs profile CRUD at accelerator layer.
