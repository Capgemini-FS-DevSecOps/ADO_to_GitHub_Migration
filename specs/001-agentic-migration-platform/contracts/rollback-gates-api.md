# Contract: Rollback & Phase Gates API

**Service**: `services/accelerator_api`  
**Base path**: `/v1`  
**Spec**: FR-026, FR-026a, FR-034, FR-019, FR-021b

## Assignment gate status

`GET /v1/assignments/{assignment_id}/gate-status`

Resolves the assignment's linked `ExecutionPhaseWave` and evaluates `PhaseGateChecker` against cohort repos.

**Response**:

```json
{
  "assignment_id": "asgn_wave2",
  "phase": "pilot",
  "status": "fail",
  "repo_success_pct": 0.92,
  "pipeline_success_pct": 1.0,
  "repos_completed": 46,
  "repos_total": 50,
  "failures": ["repo_success=92.0% < threshold=95%"],
  "can_advance": false,
  "checked_at": "2026-06-16T12:00:00Z"
}
```

`status` values: `pass`, `fail`, `override`.

## Gate override (Approver)

`POST /v1/assignments/{assignment_id}/gate-override`

**Auth**: Approver role

```json
{
  "reason": "Executive sign-off for Wave 2 pilot gate; remediation in progress"
}
```

**Response**: Updated gate status with `status: "override"`; writes `AuditEvent` `gate_override`.

Live migration on this assignment is allowed when `can_advance` is true (PASS or OVERRIDE).

## Pre-live enforcement

`POST /v1/migrate` and `POST /v1/jobs` (live, `dry_run: false`) MUST:

1. Resolve `assignment_id` when provided.
2. Call gate check; if `can_advance` is false → `409` with gate failures (no mutations).
3. Continue to workflow readiness + Approver live approval when gate passes.

## Scope-targeted rollback

`POST /v1/rollback`

**Auth**: Operator (request); Approver required before live execution (`dry_run: false`).

```json
{
  "assignment_id": "asgn_wave2",
  "repos": ["Payments/api-gateway"],
  "scopes": ["pipelines", "branch_policies"],
  "dry_run": true,
  "wave_id": 2
}
```

**Scopes** (subset of accelerator rollback): `branch_policies`, `pipelines`, `repo` (full repo deletion on target).

When `pipelines` scope is included for repos where ADO pipelines were disabled after migration (**FR-048**), live rollback MUST **re-enable** those ADO pipelines (**FR-026a**, symmetric rollback)—in addition to GitHub workflow rollback on the migration branch.

**Dry-run response**:

```json
{
  "dry_run": true,
  "assignment_id": "asgn_wave2",
  "actions": [
    {
      "repo": "Payments/api-gateway",
      "scope": "pipelines",
      "action": "rollback_github_workflows_on_migration_branch"
    },
    {
      "repo": "Payments/api-gateway",
      "scope": "pipelines",
      "action": "re_enable_ado_pipelines"
    }
  ],
  "audit_event_id": null
}
```

**Live response** (after Approver approval):

```json
{
  "dry_run": false,
  "status": "completed",
  "stats": {
    "repos_processed": 1,
    "scopes_rolled_back": 2,
    "ado_pipelines_reenabled": 1
  },
  "audit_event_id": "aud_rollback_01"
}
```

## Rollback approval flow

`POST /v1/rollback/request` — Operator creates approval ticket (mirrors live migrate).

`POST /v1/rollback/approve` — Approver approves; executes rollback.

## CLI parity

```bash
ado2gh phase gate-check --phase pilot
ado2gh rollback --wave 2 --scopes pipelines,branch_policies --dry-run
ado2gh rollback --wave 2 --scopes pipelines --assignment asgn_wave2
```

## Agent executor tool

| Tool | Subagent | Description |
|------|----------|-------------|
| `ado2gh_gate_check` | validator | Assignment-linked phase gate (existing) |
| `ado2gh_rollback` | executor | Scope-targeted rollback per plan/remediation; dry_run default true |

Executor MUST NOT invoke `ado2gh_rollback` with `dry_run: false` without recorded Approver approval.
