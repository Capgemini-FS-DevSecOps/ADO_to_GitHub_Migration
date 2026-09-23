# Contract: Agent PEV UI API

**Feature**: `004-agent-pev-rbac`  
**Extends**: [001 agent-pev-api.md](../../001-agentic-migration-platform/contracts/agent-pev-api.md), [003 agent-ide-api.md](../../003-local-agent-ide/contracts/agent-ide-api.md)

**Services**: Agent (`services/agent`, port 8090); Accelerator proxy for models (`/v1/settings/llm-models`)

## Authentication

When `ADO2GH_AUTH_ENABLED=true`:

- All `POST /v1/sessions*` routes require `Cookie: ado2gh_session=<token>`.
- Agent resolves session via shared `AuthService` (StateDB).
- Responses include `user: { username, role }` on session GET (non-secret).

UI MUST use `credentials: 'include'` on all agent fetches.

---

## Health (remediation)

`GET /health`

**Response** (unchanged shape from 003; required for FR-013):

```json
{
  "status": "ok",
  "accelerator_reachable": true,
  "llm_provider": "openai",
  "llm_degraded": false,
  "remediation_steps": []
}
```

When `accelerator_reachable: false`, `remediation_steps` MUST include actionable strings (start compose, check `ACCELERATOR_URL`, auth cookie).

---

## Start PEV session

`POST /v1/sessions`

**Auth**: `can_operate`

```json
{
  "profile_id": "prof_01",
  "assignment_id": "asgn_01",
  "prompt": "Plan and dry-run Wave 2 migration",
  "model_id": "mdl_abc123",
  "dry_run": true
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `model_id` | No | If omitted, use default enabled model or stub |
| `dry_run` | No | Default `true` (CA-001) |

**Response** `201`:

```json
{
  "session_id": "sess_01",
  "status": "planning",
  "dry_run": true,
  "selected_model_id": "mdl_abc123",
  "llm_degraded": false
}
```

---

## Session status

`GET /v1/sessions/{session_id}`

**Response** includes PEV phase, linked run steps, approval state:

```json
{
  "session_id": "sess_01",
  "status": "validating",
  "phase": "validator",
  "dry_run": true,
  "live_approval_id": null,
  "live_approval_status": null,
  "user": { "username": "operator1", "role": "operator" },
  "messages": [],
  "run": { "steps": [] }
}
```

Poll interval: UI 2–5s while `planning|executing|validating|awaiting_approval`.

---

## Request live execution

`POST /v1/sessions/{session_id}/request-live`

**Auth**: `can_operate` (operator or admin)

**Precondition**: Dry-run PEV completed (`status` was `completed` with `dry_run=true`).

```json
{
  "reason": "POC validation passed; ready for live Wave 2"
}
```

**Behavior**:
1. Creates `LiveExecutionApproval` row (via accelerator store).
2. Sets session `status: awaiting_approval`.
3. Does **not** invoke live migrate.

**Response** `202`:

```json
{
  "session_id": "sess_01",
  "status": "awaiting_approval",
  "live_approval_id": "lve_01"
}
```

**Errors**: `403` operator blocked if session owned by another user; `409` if live already requested.

---

## Approve / deny live (session shortcut)

`POST /v1/sessions/{session_id}/approve`

**Auth**: `can_approve_live_execution`

```json
{
  "approved": true,
  "reason": "Approved for live Wave 2 after gate review"
}
```

**Behavior on approve**:
1. Updates `LiveExecutionApproval` to `approved`.
2. Re-runs PEV executor with `dry_run=false` (then validator).
3. Writes audit with approver username + role.

**Behavior on deny**: approval `denied`; session returns to safe completed dry-run state.

**Note**: Admin queue UI SHOULD prefer [live-approval-api.md](./live-approval-api.md) for cross-session visibility; this route remains for inline Agent tab actions.

---

## Chat message (planner assist)

`POST /v1/sessions/{session_id}/message`

**Auth**: `can_operate`

Uses selected model for inference; falls back to stub with `llm_degraded: true` on provider error (no secret in error body).

---

## LLM status

`GET /v1/llm/status`

```json
{
  "provider": "stub",
  "models_configured": 0,
  "default_model_id": null,
  "message": "No live model configured — using stub"
}
```

---

## UI contract (Agent tab)

| Element | Operator | Admin | Approver |
|---------|----------|-------|----------|
| Start dry-run session | yes | yes | no |
| Model picker | enabled models only | same | same |
| Request live | yes | yes | no |
| Inline approve/deny | no | yes | yes |
| Link to approval queue | own session status | yes | yes |

See [platform-rbac.md](./platform-rbac.md) for capability source.
