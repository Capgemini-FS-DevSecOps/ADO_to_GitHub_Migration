# Contract: Agent IDE HTTP API

**Service**: `services/agent`  
**Base path**: `/v1`  
**Purpose**: Full Planner → Executor → Validator sessions from IDE or alternate local hosts.

**Related**: [mcp-tool-catalog.md](./mcp-tool-catalog.md) (direct tools), [local-profiles.md](./local-profiles.md)

## Health

`GET /health`

```json
{
  "status": "ok",
  "accelerator_url": "http://localhost:8080",
  "accelerator_reachable": true,
  "profile": "lightweight",
  "llm_provider": "stub",
  "auth_enabled": false
}
```

When `accelerator_reachable` is false, include:

```json
{
  "status": "degraded",
  "accelerator_reachable": false,
  "connection_error": "Connection refused",
  "remediation_steps": [
    "Start accelerator: python -m uvicorn services.accelerator_api.main:app --port 8080",
    "Verify ADO2GH_SQLITE_PATH and ADO2GH_STORAGE_BACKEND=sqlite",
    "Check ACCELERATOR_URL matches running service"
  ]
}
```

## Start IDE session

`POST /v1/sessions`

```json
{
  "profile_id": "lightweight",
  "assignment_id": "asgn_01",
  "prompt": "Plan POC phase dry-run for assignment asgn_01",
  "dry_run": true
}
```

**Response**:

```json
{
  "session_id": "sess_abc123",
  "status": "planning",
  "subagent": "planner",
  "dry_run": true
}
```

**Errors**:
- `503` — accelerator unreachable (structured remediation body)
- `401` — when `ADO2GH_AUTH_ENABLED=true` and no valid session

## Session status

`GET /v1/sessions/{session_id}`

```json
{
  "session_id": "sess_abc123",
  "status": "validating",
  "subagent": "validator",
  "dry_run": true,
  "assignment_id": "asgn_01",
  "plan_id": "plan_01",
  "run_id": "run_01",
  "messages": [
    {"role": "planner", "content": "Generated 3-repo dry-run plan"},
    {"role": "executor", "content": "Enqueued migrate jobs (dry-run)"}
  ],
  "approval": null
}
```

## Request live execution

`POST /v1/sessions/{session_id}/request-live`

Requires operator role when auth enabled. Sets `status: awaiting_approval`.

## Approve live execution

`POST /v1/sessions/{session_id}/approve`

```json
{
  "approved": true,
  "reason": "POC gate passed"
}
```

When `approved: false`, session returns to `planning` or `completed` without live mutations.

## Continue session (chat step)

`POST /v1/sessions/{session_id}/message`

```json
{
  "message": "Add pipeline conversion for repo wave2-app"
}
```

Returns updated session status and assistant message (stub LLM or cloud).

## LLM status

`GET /v1/llm/status`

```json
{
  "provider": "stub",
  "available": true,
  "degraded": false,
  "message": "Using deterministic stub planner"
}
```

## Audit

All session lifecycle and executor steps emit `audit_events` via accelerator/StateDB. IDE clients MUST NOT log request bodies containing tokens.

## Guardrails (non-negotiable)

| Rule | Behavior |
|------|----------|
| CA-001 | `dry_run` defaults `true` for local profiles |
| CA-002 | Live mutate tools require `/approve` or explicit approval record |
| CA-003 | No secrets in responses or `messages` |
| CA-004 | Every mutation writes `audit_events` with `session_id` |

## Auth (prod-like profile)

When `ADO2GH_AUTH_ENABLED=true`, requests to agent MAY forward `Cookie` or `Authorization` to accelerator. Agent does not implement login — use `002-login-bootstrap` `/v1/auth/login` first.

## Alternate local hosts (FR-006)

Third-party IDE agents MUST:

1. Consume this contract or [mcp-tool-catalog.md](./mcp-tool-catalog.md) — identical tool names and guardrails
2. Read `GET /health` for `tool_catalog_version` and `capabilities` matrix
3. Default executor tools to `dry_run: true`
4. Not register tools outside the published catalog

Capability matrix example on `/health`:

```json
{
  "capabilities": {
    "enqueue_job": "inline_stub",
    "llm": "stub",
    "auth": "disabled",
    "redis": "omitted"
  }
}
```
