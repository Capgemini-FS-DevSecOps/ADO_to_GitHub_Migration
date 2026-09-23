# API Contracts: Agent PEV Architecture Rebuild

**Feature**: 011-agent-pev-rebuild
**Date**: 2026-06-24

## Agent Backend API (services/agent/main.py)

Base URL: `http://localhost:8090` (configurable via `AGENT_URL`)

### Session Management

#### POST /v1/sessions

Create a new agent session.

```json
// Request
{
  "profile_id": "lightweight",
  "prompt": "migrate Project/RepoName to GitHub",
  "dry_run": true,
  "assignment_id": "asg_abc123",
  "model_id": "gpt-4o"
}

// Response (200)
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "profile_id": "lightweight",
  "status": "thinking",
  "dry_run": true,
  "selected_model_id": "gpt-4o",
  "llm_degraded": false,
  "llm_unconfigured": false,
  "messages": [...],
  "tasks": [...],
  "pending_form": null,
  "migration_plan": null
}
```

#### GET /v1/sessions/{session_id}

Retrieve session state (polled by UI at 800ms-1s interval).

```json
// Response (200)
{
  "session_id": "...",
  "status": "executing",
  "subagent": "executor",
  "dry_run": false,
  "messages": [...],
  "tasks": [...],
  "pending_form": null,
  "migration_plan": {...},
  "iteration_count": 5,
  "pev_retry_count": 1
}
```

#### GET /v1/sessions?profile_id={profile_id}

List sessions for a profile.

```json
// Response (200)
{
  "sessions": [
    {
      "session_id": "...",
      "profile_id": "lightweight",
      "title": "migrate Project/RepoName",
      "status": "completed",
      "updated_at": "2026-06-24T14:30:00Z",
      "created_at": "2026-06-24T14:00:00Z",
      "message_count": 15
    }
  ]
}
```

#### POST /v1/sessions/{session_id}/message

Send a user message to the session.

```json
// Request
{
  "message": "migrate Project/RepoName to GitHub"
}

// Response (200) — same as GET session
```

#### DELETE /v1/sessions/{session_id}

Delete a session and its data.

```json
// Response (200)
{ "deleted": "550e8400-e29b-41d4-a716-446655440000" }
```

### Form Interaction

#### POST /v1/sessions/{session_id}/form-submit

Submit a dynamic form.

```json
// Request
{
  "values": {
    "repository_id": "Project/RepoName",
    "confirm_execute": true
  }
}

// Response (200) — updated session
```

#### POST /v1/sessions/{session_id}/form-cancel

Cancel a pending form.

```json
// Response (200) — updated session with status idle
```

### Execution Control

#### PATCH /v1/sessions/{session_id}/execution-mode

Toggle dry-run/live mode.

```json
// Request
{ "dry_run": false }

// Response (200) — updated session
```

#### POST /v1/sessions/{session_id}/request-live

Request live execution approval.

```json
// Response (200) — updated session with status awaiting_approval
```

#### POST /v1/sessions/{session_id}/approve

Approve or reject live execution.

```json
// Request
{
  "approved": true,
  "reason": "Approved by admin"
}

// Response (200) — updated session
```

### Cancellation with Rollback

#### POST /v1/sessions/{session_id}/cancel

Cancel a migration. Orchestrator presents rollback options.

```json
// Request
{
  "rollback": true
}

// Response (200)
{
  "session_id": "...",
  "status": "failed",
  "rollback_summary": {
    "deleted": [
      {"type": "repo", "name": "org/RepoName"},
      {"type": "workflow", "name": "org/RepoName/.github/workflows/ci.yml"}
    ],
    "failed": [],
    "skipped": []
  }
}
```

If `rollback: false`, the current operation completes and the session stops.

### Health & Metrics

#### GET /health

Agent-specific health check.

```json
// Response (200)
{
  "status": "healthy",
  "llm_available": true,
  "active_session_count": 3,
  "storage_connected": true,
  "configuration_status": "configured"
}
```

#### GET /metrics

Prometheus-compatible metrics (text/plain).

```
# HELP active_sessions Currently active agent sessions
# TYPE active_sessions gauge
active_sessions 3

# HELP pev_cycles_total Total PEV cycles executed
# TYPE pev_cycles_total counter
pev_cycles_total 42

# HELP llm_call_duration_seconds LLM call latency
# TYPE llm_call_duration_seconds histogram
llm_call_duration_seconds_bucket{le="1"} 15
llm_call_duration_seconds_bucket{le="5"} 30
llm_call_duration_seconds_bucket{le="10"} 38
llm_call_duration_seconds_bucket{le="30"} 40
llm_call_duration_seconds_bucket{le="60"} 41
llm_call_duration_seconds_bucket{le="+Inf"} 42
llm_call_duration_seconds_sum 215.5
llm_call_duration_seconds_count 42

# HELP guardrail_blocks_total Total guardrail blocks
# TYPE guardrail_blocks_total counter
guardrail_blocks_total 2

# HELP tool_calls_total Total tool calls
# TYPE tool_calls_total counter
tool_calls_total 156

# HELP session_status_counts Sessions per status
# TYPE session_status_counts gauge
session_status_counts{status="idle"} 0
session_status_counts{status="thinking"} 1
session_status_counts{status="planning"} 1
session_status_counts{status="executing"} 1
session_status_counts{status="validating"} 0
session_status_counts{status="awaiting_input"} 0
session_status_counts{status="awaiting_approval"} 0
session_status_counts{status="completed"} 15
session_status_counts{status="failed"} 2
```

## Inter-Agent Message Contracts

### Planner → Executor (instruction)

```json
{
  "message_type": "instruction",
  "from_role": "planner",
  "to_role": "executor",
  "payload": {
    "plan_id": "uuid-v4",
    "repo": "Project/RepoName",
    "work_items": [
      {"type": "git_mirror", "source": "ado://Project/RepoName", "target": "github://org/RepoName"},
      {"type": "pipeline_conversion", "pipelines": ["ci.yml", "deploy.yml"]},
      {"type": "secret_provisioning", "secrets": ["PAT", "API_KEY"]},
      {"type": "service_connection_mapping", "connections": [{"name": "AzureRM-prod", "target": "environment:production"}, {"name": "DockerHub", "target": "secret:DOCKER_HUB_TOKEN"}]},
      {"type": "boards_migration", "work_items_csv": "..."},
      {"type": "test_plans_migration", "test_suites": ["Suite1", "Suite2"]},
      {"type": "artifacts_migration", "feeds": ["npm-feed", "nuget-feed"]},
      {"type": "wiki_migration", "wiki_pages": ["Home", "Architecture"]}
    ],
    "dry_run": true
  },
  "correlation_ids": {"session_id": "...", "plan_id": "...", "run_id": "..."}
}
```

### Executor → Validator (result)

```json
{
  "message_type": "result",
  "from_role": "executor",
  "to_role": "validator",
  "payload": {
    "plan_id": "uuid-v4",
    "per_repo_results": [
      {"repo": "Project/RepoName", "git_mirror": "success", "head_sha": "abc123"},
      {"repo": "Project/RepoName", "workflows_created": [".github/workflows/ci.yml"]},
      {"repo": "Project/RepoName", "secrets_provisioned": ["PAT", "API_KEY"]},
      {"repo": "Project/RepoName", "service_connections_migrated": [{"name": "AzureRM-prod", "target": "environment:production"}, {"name": "DockerHub", "target": "secret:DOCKER_HUB_TOKEN"}]},
      {"repo": "Project/RepoName", "boards_migrated": 15, "issues_created": 15},
      {"repo": "Project/RepoName", "wiki_migrated": true}
    ],
    "failures": [],
    "skipped": []
  },
  "correlation_ids": {"session_id": "...", "plan_id": "...", "run_id": "..."}
}
```

### Validator → Planner (feedback)

```json
{
  "message_type": "feedback",
  "from_role": "validator",
  "to_role": "planner",
  "payload": {
    "plan_id": "uuid-v4",
    "per_scope": {
      "git": "pass",
      "pipelines": "pass",
      "secrets": "fail",
      "service_connections": "pass",
      "dependencies": "pass",
      "boards": "pass",
      "test_plans": "pass",
      "artifacts": "skip",
      "wiki": "pass"
    },
    "failures": [
      {
        "scope": "secrets",
        "expected": "secret PAT provisioned",
        "observed": "secret PAT not found",
        "file_path": null,
        "remediation": "Re-provision secret PAT with correct value"
      }
    ],
    "recommended_remediation": ["Re-provision secret PAT"]
  },
  "correlation_ids": {"session_id": "...", "plan_id": "...", "run_id": "..."}
}
```

### Orchestrator → User (user_facing)

```json
{
  "message_type": "user_facing",
  "from_role": "orchestrator",
  "to_role": "user",
  "payload": {
    "content": "Migration of Project/RepoName completed successfully. 3 pipelines converted, 2 secrets provisioned, 15 work items migrated to Issues.",
    "pending_form": null
  },
  "correlation_ids": {"session_id": "..."}
}
```

## Resource Mapping Contract

### ADO Boards → GitHub Issues

```json
{
  "ado_type": "work_item",
  "github_target": "issue",
  "field_mappings": {
    "Title": "title",
    "Description": "body",
    "Assigned To": "assignee",
    "State": "state"
  },
  "label_mappings": {
    "Tags": "labels",
    "Area Path": "label:area:{value}",
    "Iteration Path": "label:iteration:{value}"
  },
  "override_allowed": false
}
```

### ADO Test Plans → GitHub Issues

```json
{
  "ado_type": "test_case",
  "github_target": "issue_with_labels",
  "field_mappings": {
    "Title": "title",
    "Steps": "body_checklist"
  },
  "label_mappings": {
    "test_case": "label:test-case",
    "test_suite": "label:test-suite:{suite_name}"
  },
  "milestone_mapping": {
    "test_suite": "milestone"
  },
  "override_allowed": true
}
```

### ADO Artifacts → GitHub Packages

```json
{
  "ado_type": "artifact_feed",
  "github_target": "package",
  "supported_types": ["npm", "NuGet", "Docker", "Maven", "PyPI"],
  "unsupported_types_documented_as": "gap",
  "override_allowed": true
}
```

### ADO Wiki → GitHub Wiki

```json
{
  "ado_type": "wiki_page",
  "github_target": "wiki",
  "field_mappings": {
    "content": "markdown",
    "page_name": "page_title"
  },
  "html_conversion": "convert_to_markdown_or_document_as_gap",
  "override_allowed": false
}
```

### ADO Service Connections → GitHub Secrets/Environments

```json
{
  "ado_type": "service_connection",
  "connection_types": {
    "simple_credential": {
      "github_target": "secret",
      "examples": ["Docker registry", "npm feed", "NuGet feed"],
      "mapping": {
        "connection_name": "secret_name",
        "credentials": "secret_value"
      }
    },
    "deployment_scoped": {
      "github_target": "environment",
      "examples": ["Azure RM", "Kubernetes", "environment-specific endpoints"],
      "mapping": {
        "connection_name": "environment_name",
        "credentials": "environment_secret",
        "scope": "protection_rules"
      },
      "protection_rules_default": {
        "required_reviewers": true,
        "deployment_branch_policy": "protected_branches_only"
      }
    }
  },
  "override_allowed": true
}
```
