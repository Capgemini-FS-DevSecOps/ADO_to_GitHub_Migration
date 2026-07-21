# Data Model: Agentic ADO-to-GitHub Migration Platform

**Feature**: `001-agentic-migration-platform`

## MigrationProfile

| Field | Type | Notes |
|-------|------|-------|
| id | string | Primary key |
| name | string | Display name |
| ado_org_url | string | Source |
| gh_org | string | Target org |
| workflow_branch | string | Default `ado2gh/migrated-workflows` |
| workflow_layout_policy | enum | `auto`, `consolidated`, `modular` |
| disable_ado_on_workflow_push | bool | Default true for live runs |
| policy | json | Gates, retry limits, retention |
| created_at | timestamp | |

**Relationships**: has many Assignments, Runs, AuditEvents, Users/Roles

## UserRole (profile-scoped)

| Field | Type | Notes |
|-------|------|-------|
| profile_id | string | FK |
| user_id | string | Identity from auth system |
| roles | set enum | `coordinator`, `operator`, `approver` |

**Rules**: Operator cannot approve live actions; Coordinator cannot approve unless also Approver.

## MigrationAssignment

| Field | Type | Notes |
|-------|------|-------|
| id | string | Primary key |
| profile_id | string | FK |
| name | string | e.g. "Wave 2 - Payments" |
| assignment_type | enum | `poc`, `pilot`, `wave`, `adhoc` |
| wave_number | int? | For `wave` type (1–N) |
| execution_phase_id | string | FK → ExecutionPhaseWave |
| status | enum | `draft`, `active`, `completed`, `archived` |
| created_by | string | Coordinator user_id |

**Relationships**: has many CohortMemberships, Runs

## CohortMembership

| Field | Type | Notes |
|-------|------|-------|
| assignment_id | string | FK |
| ado_project | string | |
| ado_repo | string | |
| gh_org | string | |
| gh_repo | string | |
| active | bool | One active assignment per repo per profile |

**Validation**: Unique (profile_id, ado_project, ado_repo) where active=true

## ExecutionPhaseWave

| Field | Type | Notes |
|-------|------|-------|
| id | string | Primary key |
| profile_id | string | FK |
| phase_type | enum | Aligns with `PhaseType` / custom defs |
| gate_thresholds | json | Success rates, etc. |
| order | int | POC before pilot before waves |

## RepoDependencyEdge

| Field | Type | Notes |
|-------|------|-------|
| profile_id | string | FK |
| from_repo | string | Consumer `project/repo` |
| to_repo | string | Dependency `project/repo` |
| edge_type | enum | `pipeline_resource`, `template`, `package`, `manual` |
| source_pipeline_id | int? | Provenance |
| discovered_at | timestamp | |

**Graph rules**: Used for topological sort; cycles → `MigrationPlan.blocked_cycles` list

## MigrationPlan

| Field | Type | Notes |
|-------|------|-------|
| id | string | Primary key |
| profile_id | string | FK |
| assignment_id | string? | FK |
| agent_session_id | string? | FK |
| scopes | list enum | repo, pipelines, work_items, … |
| repo_order | list string | Topologically sorted repo keys |
| blocked_cycles | list json | Cycle diagnostics |
| workflow_branch | string | Per-plan branch name |
| status | enum | `draft`, `pending_approval`, `approved`, `rejected` |
| created_by_role | enum | planner |

## MigrationRun

| Field | Type | Notes |
|-------|------|-------|
| id | string | Primary key |
| profile_id | string | FK |
| plan_id | string | FK |
| assignment_id | string? | FK |
| mode | enum | `manual`, `agent` |
| dry_run | bool | |
| status | enum | pending, running, completed, failed, paused |
| current_repo | string? | Checkpoint |

## PipelineMigrationRecord (extends existing)

| Field | Type | Notes |
|-------|------|-------|
| wave_id / run_id | string/int | Correlation |
| workflow_file | string | Local path |
| workflow_branch | string | Target branch |
| pr_url | string? | GitHub PR for workflows |
| pr_status | enum | `open`, `merged`, `none` |
| layout_policy | enum | `consolidated`, `modular` |
| ado_pipelines_disabled | bool | Post-push ADO cleanup |

## WorkflowBranchPush

| Field | Type | Notes |
|-------|------|-------|
| run_id | string | FK |
| gh_org | string | |
| gh_repo | string | |
| branch | string | |
| pr_url | string? | |
| status | enum | `pending_approval`, `pushed`, `failed` |

## ValidationResult

| Field | Type | Notes |
|-------|------|-------|
| run_id | string | FK |
| repo | string | |
| scope | enum | repo, pipelines, boards, … |
| passed | bool | |
| evidence | json | SHAs, file counts, etc. |
| boards_gaps_ref | string? | FK → BoardsGapsReport |

## BoardsGapsReport

| Field | Type | Notes |
|-------|------|-------|
| id | string | PK |
| run_id | string | FK |
| items | list json | artifact, ado_ref, github_fallback, disposition |

## RemediationLoop

| Field | Type | Notes |
|-------|------|-------|
| validation_result_id | string | FK |
| retry_count | int | Max 3 default |
| scopes_retried | list | |
| outcome | enum | resolved, escalated |

## AgentSession

| Field | Type | Notes |
|-------|------|-------|
| id | string | PK |
| profile_id | string | FK |
| assignment_id | string? | FK |
| messages | list json | Full transcript (immutable append) |
| retention_until | date | ≥7 years from created_at |

## SubagentSkillDefinition

| Field | Type | Notes |
|-------|------|-------|
| role | enum | planner, executor, validator |
| version | string | Semver |
| tools_allowed | list string | Tool names |
| assignment_types | list enum | All types for parity |

## AuditEvent (immutable)

| Field | Type | Notes |
|-------|------|-------|
| id | string | PK |
| profile_id | string | FK |
| event_type | enum | plan_approved, live_execution, workflow_push, assignment_change, … |
| actor_id | string | |
| actor_role | enum | |
| payload | json | Redacted |
| correlation_ids | json | run_id, session_id, assignment_id |
| created_at | timestamp | Insert-only |

## WorkflowDependencyCheck

| Field | Type | Notes |
|-------|------|-------|
| run_id | string | FK |
| repo | string | |
| check_id | string | e.g. `secrets_mapped` |
| passed | bool | |
| resource_name | string? | Secret/env name if failed |
| log_line | string | Console/log formatted line (no values) |

## SecretProvisionRequest

| Field | Type | Notes |
|-------|------|-------|
| session_id | string | FK |
| blocker_id | string | |
| action | enum | create_repo_secret, create_environment, … |
| confirmed_by | string | Operator user_id |
| audit_event_id | string | FK |
| status | enum | pending, completed, failed |

## State transitions

### MigrationRun

`pending` → `running` → `completed` | `failed` | `paused` (credential refresh)

### MigrationPlan

`draft` → `pending_approval` → `approved` | `rejected`

### WorkflowBranchPush

`pending_approval` → (Approver) → `pushed` | `failed`
