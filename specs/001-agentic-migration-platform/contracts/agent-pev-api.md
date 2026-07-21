# Contract: Agent PEV API

**Service**: `services/agent`  
**Base path**: `/v1`

## Start agent session (natural language)

`POST /v1/sessions`

```json
{
  "profile_id": "prof_01",
  "assignment_id": "asgn_01",
  "message": "Migrate Wave 2 repos and convert pipelines to GitHub Actions",
  "model_id": "org-approved-model",
  "dry_run": true
}
```

**Response**:

```json
{
  "session_id": "sess_01",
  "status": "planning",
  "subagent": "planner"
}
```

## Session status

`GET /v1/sessions/{session_id}`

Returns role-attributed messages, linked `plan_id`, `run_id`, approval state.

## Request live execution

`POST /v1/sessions/{session_id}/request-live`

**Auth**: Operator role

Creates approval ticket for Approver.

## Approve live execution

`POST /v1/sessions/{session_id}/approve`

**Auth**: Approver role

```json
{
  "approved": true,
  "reason": "POC gate passed; Wave 2 approved for live migration"
}
```

Triggers executor subagent with authorized tool set only.

## MCP tools (executor whitelist)

| Tool | Subagent | Description |
|------|----------|-------------|
| `ado2gh_discover` | planner | Read-only ADO scan |
| `ado2gh_build_dependency_graph` | planner | Topo sort + cycles |
| `ado2gh_plan_phase` | planner | Structured MigrationPlan |
| `ado2gh_readiness` | planner | Pipeline readiness |
| `ado2gh_enqueue_job` | executor | Scoped jobs (migrate, transform, push_workflows) |
| `ado2gh_job_status` | executor | Poll job |
| `ado2gh_validate_repo` | validator | Scope validation |
| `ado2gh_gate_check` | validator | Phase gates (assignment-linked) |
| `ado2gh_rollback` | executor | Scope-targeted rollback; dry_run default true |
| `ado2gh_workflow_readiness` | planner/validator | Dependency checklist + log lines |
| `ado2gh_create_repo_secret` | executor | After operator confirms in chat |
| `ado2gh_create_environment` | executor | After operator confirms |
| `ado2gh_link_service_connection` | executor | OIDC/mapping from manifest |

Executor MUST NOT invoke tools not in active plan scope or remediation instruction.

## Provision missing dependency

When readiness reports blockers, agent prompts: *Create missing secret/connection in GitHub? (yes/no)*

`POST /v1/sessions/{session_id}/provision`

```json
{
  "blocker_id": "secret:NUGET_FEED_TOKEN:Payments/api-gateway",
  "action": "create_repo_secret",
  "confirmed": true,
  "value_source": "secure_modal"
}
```

Secret values MUST NOT be submitted in chat body. See [dependency-logging-provisioning.md](./dependency-logging-provisioning.md).

## Remediation loop

`POST /v1/sessions/{session_id}/remediate`

Validator-only trigger; re-queues failed scopes to executor within retry policy.
