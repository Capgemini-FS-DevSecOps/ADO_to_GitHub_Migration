# Contract: Live Execution Approval API

**Feature**: `004-agent-pev-rbac`  
**Service**: Accelerator API  
**Base path**: `/v1/platform/approvals`

Persistent **unified** queue for operator-initiated live requests: Agent PEV sessions, dashboard live migrate jobs, and pipeline live runs (FR-008, FR-009).

## List pending approvals

`GET /v1/platform/approvals?status=pending`

**Auth**: `can_approve_live_execution`

**Response** `200`:

```json
{
  "approvals": [
    {
      "id": "lve_01",
      "requester_username": "operator1",
      "scope_type": "agent_session",
      "scope_id": "sess_01",
      "profile_id": "prof_01",
      "assignment_id": "asgn_01",
      "status": "pending",
      "reason_request": "Ready for live after dry-run",
      "requested_at": "2026-06-16T14:00:00Z"
    }
  ]
}
```

Optional query: `status=approved|denied|all`.

---

## Get approval

`GET /v1/platform/approvals/{approval_id}`

**Auth**: requester (own pending) or `can_approve_live_execution`

---

## Approve

`POST /v1/platform/approvals/{approval_id}/approve`

**Auth**: `can_approve_live_execution`

```json
{
  "reason": "Wave 2 approved after POC gate"
}
```

**Behavior**:
1. Idempotent: second approve → `409` or returns existing decision.
2. Updates row to `approved`, sets `decided_at`, approver fields.
3. Invokes agent resume callback for `scope_type=agent_session` OR unblocks migrate job.
4. Audit: `platform.live_execution.approved`.

**Response** `200`:

```json
{
  "id": "lve_01",
  "status": "approved",
  "scope_id": "sess_01"
}
```

---

## Deny

`POST /v1/platform/approvals/{approval_id}/deny`

**Auth**: `can_approve_live_execution`

```json
{
  "reason": "POC gate not yet passed"
}
```

**Behavior**: status `denied`; agent session notified; no live execution.

---

## Create (internal / agent delegate)

`POST /v1/platform/approvals`

**Auth**: `can_operate` (typically called by agent service on behalf of operator session)

```json
{
  "scope_type": "agent_session",
  "scope_id": "sess_01",
  "profile_id": "prof_01",
  "assignment_id": "asgn_01",
  "reason_request": "Request live PEV"
}
```

**Idempotency**: duplicate pending for same `(scope_type, scope_id)` returns existing approval.

---

## Dual gate interaction (FR-014)

When `assignment_id` is set, **both** gates MUST pass (**AND**), in order:

1. Platform approval on this queue MUST be `approved` (by admin or approver).
2. `enforce_live_gate(assignment_id, dry_run=false)` MUST pass.

If (2) fails after (1), execution aborts with `409`, user sees gate-failure message, audit records platform approval + gate outcome.

---

## UI contract

**Route**: `/settings/approvals` (admin/approver)

| Element | Behavior |
|---------|----------|
| Pending list | Poll or refresh every 10s |
| Approve button | Modal requiring reason |
| Deny button | Modal requiring reason |
| Empty state | “No pending live execution requests” |

Operator sees pending state on Agent tab only for their own session (via session GET `live_approval_status`).

---

## Deprecation

Agent `GET /v1/approvals` (in-memory) → deprecated; returns empty or proxies to this API until removed.
