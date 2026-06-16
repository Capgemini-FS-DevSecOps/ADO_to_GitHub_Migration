# Contract: MCP Tool Catalog

**Bridge**: `services/agent/mcp_server.py` (stdio JSON-line loop)  
**Backend**: Accelerator API at `ACCELERATOR_URL`  
**Purpose**: Direct IDE tool invocation without full PEV session.

**Related**: [agent-ide-api.md](./agent-ide-api.md), [data-model.md](../data-model.md) ToolContract

## Protocol (Phase 1)

Minimal stdio transport: one JSON object per line.

### `tools/list`

**Request**:

```json
{"method": "tools/list"}
```

**Response**:

```json
{
  "tools": [
    {
      "name": "ado2gh_discover",
      "description": "Read-only ADO org scan",
      "subagent": "planner",
      "requires_approval": false
    }
  ]
}
```

### `tools/call`

**Request**:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ado2gh_plan_phase",
    "arguments": {
      "phase": "poc",
      "dry_run": true,
      "assignment_id": "asgn_01"
    }
  }
}
```

**Response**:

```json
{
  "content": [
    {
      "type": "text",
      "text": "{ ... accelerator JSON response ... }"
    }
  ],
  "audit_event_id": "evt_123"
}
```

**Error response**:

```json
{
  "error": "accelerator_unreachable",
  "remediation_steps": ["Start accelerator on port 8080"]
}
```

## Tool catalog (shared with HTTP executor)

| Tool | Subagent | Accelerator route | Live requires approval |
|------|----------|-------------------|------------------------|
| `ado2gh_discover` | planner | `POST /v1/discover` | no |
| `ado2gh_build_dependency_graph` | planner | `POST /v1/assignments/{id}/dependency-graph` | no |
| `ado2gh_plan_phase` | planner | `POST /v1/plan` | no |
| `ado2gh_readiness` | planner | `POST /v1/pipeline-readiness` | no |
| `ado2gh_workflow_readiness` | planner/validator | `GET /v1/dashboard` | no |
| `ado2gh_enqueue_job` | executor | `POST /v1/jobs` | **yes** |
| `ado2gh_job_status` | executor | `GET /v1/jobs/{job_id}` | no |
| `ado2gh_validate_repo` | validator | `POST /v1/validate` | no |
| `ado2gh_gate_check` | validator | `GET /v1/assignments/{id}/gate-status` | no |
| `ado2gh_rollback` | executor | `POST /v1/rollback` | **yes** (default dry_run) |
| `ado2gh_get_report` | planner | `GET /v1/dashboard` | no |
| `ado2gh_create_repo_secret` | executor | `POST /v1/service-connections/...` | **yes** |
| `ado2gh_create_environment` | executor | `POST /v1/...` | **yes** |
| `ado2gh_link_service_connection` | executor | `POST /v1/...` | **yes** |

## Guardrails

1. **Unknown tool** → error, no fallback shell execution.
2. **dry_run default** — executor tools default `dry_run: true` when argument omitted.
3. **Allowlist** — tools not in this catalog MUST NOT be registered locally.
4. **Audit** — each `tools/call` that mutates state writes `audit_events` (action `tool.{name}`).
5. **Auth** — when auth enabled, MCP process must receive session cookie via env `ADO2GH_SESSION_COOKIE` or login before calls.

## Source of truth

Implementation MUST import tool definitions from `ado2gh/agents/local/tool_catalog.py` — not duplicate in MCP and HTTP layers.

## Capability matrix (degraded modes)

| Profile | enqueue_job | Notes |
|---------|-------------|-------|
| lightweight | inline stub | Job completes synchronously in accelerator |
| full | redis worker | Async queue |
| prod-like | redis worker | + auth required |

IDE hosts SHOULD read `/health` on agent service to display degraded mode to developer.
