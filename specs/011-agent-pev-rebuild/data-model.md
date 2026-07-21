# Data Model: Agent PEV Architecture Rebuild

**Feature**: 011-agent-pev-rebuild
**Date**: 2026-06-24

## Entities

### AgentSession

A conversation session between the user and the orchestrator.

| Field | Type | Description |
|-------|------|-------------|
| session_id | UUID v4 (str) | Primary key |
| profile_id | str | Deployment profile ID |
| model_id | str | Selected LLM model ID |
| status | SessionState | Current state machine state |
| messages | JSON array | All messages (user + agent, inter-agent) |
| pending_form | JSON object \| null | Active dynamic form awaiting user input |
| migration_plan | JSON object \| null | Current migration plan (latest revision) |
| dry_run | bool | Dry-run vs live execution flag |
| iteration_count | int | Total loop iterations in current session |
| pev_retry_count | int | PEV retry cycles in current migration |
| created_at | ISO 8601 str | Session creation timestamp |
| last_activity_at | ISO 8601 str | Last activity timestamp (for retention) |

**Relationships**: has many AgentMessage, has one MigrationPlan (current), has many MigrationPlan (revisions), has many PevCycleSummary, has many RepoLock, has many RollbackRecord.

### SessionState (Enum)

Formal state machine states with enforced transitions.

| State | Allowed Transitions To |
|-------|----------------------|
| idle | thinking |
| thinking | planning |
| planning | executing, awaiting_input |
| executing | validating, awaiting_input, awaiting_approval |
| validating | completed, failed, planning (retry) |
| awaiting_input | resume(previous_state), idle (cancel) |
| awaiting_approval | planning (approved), failed (rejected) |
| completed | (terminal) |
| failed | (terminal) |

**Validation**: The `SessionStateMachine.transition(current, target)` method validates allowed transitions and raises `InvalidTransitionError` for disallowed pairs.

### MigrationPlan

Structured output from the Planner.

| Field | Type | Description |
|-------|------|-------------|
| plan_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| repos | JSON array | Repos in topological order |
| work_items | JSON array | Per-repo work items (pipeline, Bicep, secret, service connections, Boards, Test Plans, Artifacts, Wiki) |
| dry_run | bool | Dry-run/live flag |
| assumptions | JSON array | Assumptions from incomplete discovery |
| blocked_items | JSON array | Items blocked with reasons |
| revision | int | Revision number (0 = initial, incremented on replan) |
| created_at | ISO 8601 str | Creation timestamp |

**Relationships**: belongs to AgentSession, has many ExecutorResult, has many ValidationResult.

### ExecutorResult

Output from the Executor sent to the Validator.

| Field | Type | Description |
|-------|------|-------------|
| result_id | UUID v4 (str) | Primary key |
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| session_id | UUID v4 (str) | FK to AgentSession |
| per_repo_results | JSON array | Git mirror status, workflows created, secrets provisioned, service connections migrated, Boards/Test Plans/Artifacts/Wiki results |
| failures | JSON array | Error details with error codes |
| skipped | JSON array | Skipped items with reasons |
| created_at | ISO 8601 str | Creation timestamp |

### ValidationResult

Output from the Validator sent to the Planner.

| Field | Type | Description |
|-------|------|-------------|
| validation_id | UUID v4 (str) | Primary key |
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| session_id | UUID v4 (str) | FK to AgentSession |
| per_scope_pass_fail | JSON object | Pass/fail per scope (git, pipelines, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki) |
| evidence | JSON array | API responses, YAML validation results |
| failures | JSON array | Expected vs observed, with file paths |
| recommended_remediation | JSON array | Remediation steps |
| created_at | ISO 8601 str | Creation timestamp |

### AgentMessage

Internal message between agents or user-facing message.

| Field | Type | Description |
|-------|------|-------------|
| message_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| from_role | str | orchestrator, planner, executor, validator, user |
| to_role | str | orchestrator, planner, executor, validator, user |
| message_type | str | instruction, clarification_request, feedback, result, user_message, user_facing |
| payload | JSON object | Typed per message_type |
| timestamp | ISO 8601 str | Message timestamp |
| correlation_ids | JSON object | session_id, plan_id, run_id for traceability |

**Validation**: `message_type` must be one of the allowed values. `payload` structure is validated per `message_type`.

### PevCycleSummary

Compact JSON summary generated after each PEV cycle.

| Field | Type | Description |
|-------|------|-------------|
| cycle_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| cycle_number | int | Sequential cycle number |
| repos_processed | int | Count of repos processed |
| repos_succeeded | int | Count of repos succeeded |
| repos_failed | int | Count of repos failed |
| failures | JSON array | Failures scoped to repo + scope |
| next_action | str | replan, continue, escalate, complete |
| timestamp | ISO 8601 str | Cycle completion timestamp |

### MigrationQueue

Ordered queue for batch phase migrations.

| Field | Type | Description |
|-------|------|-------------|
| queue_id | UUID v4 (str) | Primary key |
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| session_id | UUID v4 (str) | FK to AgentSession |
| items | JSON array | Ordered per-repo work items (topological) |
| current_index | int | Index of current item being processed |
| completed_items | JSON array | Completed item IDs |
| failed_items | JSON array | Failed item IDs |

### RepoLock

Repo-level lock preventing concurrent migration.

| Field | Type | Description |
|-------|------|-------------|
| lock_id | UUID v4 (str) | Primary key |
| repo_key | str | Unique key (project/repo) |
| session_id | UUID v4 (str) | FK to AgentSession |
| acquired_at | ISO 8601 str | Lock acquisition timestamp |
| released_at | ISO 8601 str \| null | Lock release timestamp |
| lock_state | str | active, released, stale |

**Validation**: `lock_state` must be one of: active, released, stale. Only one active lock per `repo_key` (enforced by DB unique constraint on `repo_key WHERE lock_state = 'active'`).

### GuardrailDecision

Record of a guardrail evaluation.

| Field | Type | Description |
|-------|------|-------------|
| decision_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| timestamp | ISO 8601 str | Decision timestamp |
| agent_role | str | Agent that initiated the operation |
| tool_name | str | Tool that was intercepted |
| operation_type | str | create, modify, delete |
| target_resource | str | Resource being operated on |
| decision | str | allow, block |
| reason | str | Decision reason |
| plan_reference | str | Reference to plan item authorizing the operation |

### RollbackRecord

Record of resources created during a session eligible for rollback.

| Field | Type | Description |
|-------|------|-------------|
| record_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| resource_type | str | repo, workflow, secret, environment, issue, wiki_page, package |
| resource_name | str | Full resource name/URL |
| github_org | str | GitHub organization |
| created_at | ISO 8601 str | Resource creation timestamp |
| correlation_id | str | Session correlation ID |
| rollback_status | str | eligible, deleted, failed |

**Validation**: `resource_type` must be one of: repo, workflow, secret, environment, issue, wiki_page, package. `rollback_status` must be one of: eligible, deleted, failed.

### ResourceMapping

Fixed mapping configuration for ADO → GitHub resource migration.

| Field | Type | Description |
|-------|------|-------------|
| ado_type | str | ADO resource type (work_item, test_case, test_suite, artifact_feed, wiki_page) |
| github_target | str | GitHub target (issue, issue_with_labels, milestone, package, wiki) |
| field_mappings | JSON object | ADO field → GitHub field mappings |
| label_mappings | JSON object | ADO values → GitHub labels |
| override_allowed | bool | Whether planner may override this mapping |

### ServiceConnectionMapping

Mapping of an ADO service connection to a GitHub target.

| Field | Type | Description |
|-------|------|-------------|
| mapping_id | UUID v4 (str) | Primary key |
| session_id | UUID v4 (str) | FK to AgentSession |
| plan_id | UUID v4 (str) | FK to MigrationPlan |
| ado_connection_name | str | ADO service connection name |
| connection_type | str | simple_credential, deployment_scoped |
| github_target_type | str | secret, environment |
| github_target_name | str | GitHub secret name or environment name |
| protection_rules | JSON object \| null | Protection rules for environments (required reviewers, deployment branches) |
| correlation_id | str | Session correlation ID |
| created_at | ISO 8601 str | Creation timestamp |

**Validation**: `connection_type` must be one of: simple_credential, deployment_scoped. `github_target_type` must be one of: secret, environment. `protection_rules` is required when `github_target_type` is `environment`.

### MetricsSnapshot

Prometheus-compatible metrics export (in-memory, not persisted).

| Field | Type | Description |
|-------|------|-------------|
| active_sessions | int | Currently active session count |
| pev_cycles_total | int | Total PEV cycles executed |
| llm_call_duration_seconds | histogram | LLM call latency distribution |
| guardrail_blocks_total | int | Total guardrail blocks |
| tool_calls_total | int | Total tool calls |
| session_status_counts | JSON object | Count per status |

### RetentionPolicy

Data retention configuration (static, not a DB table).

| Field | Type | Description |
|-------|------|-------------|
| session_data_ttl_days | int | 90 days from last activity |
| audit_log_retention | str | "indefinite" |

## Database Schema (SQLite/PostgreSQL)

```sql
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id        TEXT PRIMARY KEY,
    profile_id        TEXT NOT NULL,
    model_id          TEXT,
    status            TEXT NOT NULL DEFAULT 'idle',
    messages_json     TEXT NOT NULL DEFAULT '[]',
    pending_form_json TEXT,
    migration_plan_json TEXT,
    dry_run           INTEGER NOT NULL DEFAULT 1,
    iteration_count   INTEGER NOT NULL DEFAULT 0,
    pev_retry_count   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    last_activity_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id        TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    from_role         TEXT NOT NULL,
    to_role           TEXT NOT NULL,
    message_type      TEXT NOT NULL,
    payload_json      TEXT NOT NULL DEFAULT '{}',
    timestamp         TEXT NOT NULL,
    correlation_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS migration_plans (
    plan_id           TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    repos_json        TEXT NOT NULL DEFAULT '[]',
    work_items_json   TEXT NOT NULL DEFAULT '[]',
    dry_run           INTEGER NOT NULL DEFAULT 1,
    assumptions_json  TEXT NOT NULL DEFAULT '[]',
    blocked_items_json TEXT NOT NULL DEFAULT '[]',
    revision          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executor_results (
    result_id         TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    per_repo_results_json TEXT NOT NULL DEFAULT '[]',
    failures_json     TEXT NOT NULL DEFAULT '[]',
    skipped_json      TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id     TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    per_scope_json    TEXT NOT NULL DEFAULT '{}',
    evidence_json     TEXT NOT NULL DEFAULT '[]',
    failures_json     TEXT NOT NULL DEFAULT '[]',
    remediation_json  TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pev_cycle_summaries (
    cycle_id          TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    cycle_number      INTEGER NOT NULL,
    repos_processed   INTEGER NOT NULL DEFAULT 0,
    repos_succeeded   INTEGER NOT NULL DEFAULT 0,
    repos_failed      INTEGER NOT NULL DEFAULT 0,
    failures_json     TEXT NOT NULL DEFAULT '[]',
    next_action       TEXT NOT NULL,
    timestamp         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_queues (
    queue_id          TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    items_json        TEXT NOT NULL DEFAULT '[]',
    current_index     INTEGER NOT NULL DEFAULT 0,
    completed_items_json TEXT NOT NULL DEFAULT '[]',
    failed_items_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS repo_locks (
    lock_id           TEXT PRIMARY KEY,
    repo_key          TEXT NOT NULL,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    acquired_at       TEXT NOT NULL,
    released_at       TEXT,
    lock_state        TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_repo_locks_active ON repo_locks(repo_key) WHERE lock_state = 'active';

CREATE TABLE IF NOT EXISTS guardrail_decisions (
    decision_id       TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    timestamp         TEXT NOT NULL,
    agent_role        TEXT NOT NULL,
    tool_name         TEXT NOT NULL,
    operation_type    TEXT NOT NULL,
    target_resource   TEXT NOT NULL,
    decision          TEXT NOT NULL,
    reason            TEXT NOT NULL,
    plan_reference    TEXT
);

CREATE TABLE IF NOT EXISTS rollback_records (
    record_id         TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    resource_type     TEXT NOT NULL,
    resource_name     TEXT NOT NULL,
    github_org        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    correlation_id    TEXT NOT NULL,
    rollback_status   TEXT NOT NULL DEFAULT 'eligible'
);

CREATE INDEX IF NOT EXISTS idx_rollback_session ON rollback_records(session_id) WHERE rollback_status = 'eligible';
CREATE INDEX IF NOT EXISTS idx_sessions_activity ON agent_sessions(last_activity_at);

CREATE TABLE IF NOT EXISTS service_connection_mappings (
    mapping_id          TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES agent_sessions(session_id),
    plan_id             TEXT NOT NULL REFERENCES migration_plans(plan_id),
    ado_connection_name TEXT NOT NULL,
    connection_type     TEXT NOT NULL,
    github_target_type  TEXT NOT NULL,
    github_target_name  TEXT NOT NULL,
    protection_rules_json TEXT,
    correlation_id      TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
```

## State Diagram

```
idle ──→ thinking ──→ planning ──→ executing ──→ validating ──→ completed
                ↑           |          |              |
                |           ↓          ↓              ↓
                └──── awaiting_input ←────────── (retry) → planning
                          |
                          ↓
                        idle (cancel)

any ──→ awaiting_approval ──→ planning (approved)
                           ──→ failed (rejected)

any ──→ failed (unrecoverable error)
```
