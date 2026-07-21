# Data Model: Local Agent IDE

**Feature**: `003-local-agent-ide` | **Date**: 2026-06-16

## Overview

Local IDE agent development adds **runtime configuration entities** (profiles, tool contracts) and extends **session/audit** records. Persistent storage uses existing StateDB tables; profile definitions are **YAML/env-backed**, not new DB tables.

## Entities

### LocalAgentProfile

Named runtime configuration bundle for local development.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `profile_id` | string | yes | `lightweight`, `full`, `prod-like` |
| `accelerator_url` | string | yes | Default `http://localhost:8080` |
| `agent_url` | string | yes | Default `http://localhost:8090` |
| `storage_backend` | enum | yes | `sqlite` \| `postgres` |
| `sqlite_path` | string | if sqlite | Path to `migration_state.db` |
| `auth_enabled` | boolean | yes | Maps to `ADO2GH_AUTH_ENABLED` |
| `llm_provider` | enum | yes | `stub` (default), `bedrock`, `openai`, … |
| `lightweight_mode` | boolean | yes | Skip Redis/worker; inline job stub |
| `dry_run_default` | boolean | yes | Always `true` for local profiles |
| `redis_url` | string | optional | Empty in lightweight |

**Validation**:
- `lightweight` ⇒ `lightweight_mode=true`, `redis_url` empty, `storage_backend=sqlite`
- `prod-like` ⇒ `auth_enabled=true`

**Source**: `config/local-profiles.yaml` + env overrides

### IDESession

In-memory (agent service) conversation bound to a profile; optionally linked to assignment.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | UUID, prefix `sess_` |
| `profile_id` | string | yes | FK to profile name |
| `assignment_id` | string | optional | Scoped migration assignment |
| `profile_role` | string | optional | RBAC context when auth enabled |
| `status` | enum | yes | `planning`, `executing`, `validating`, `awaiting_approval`, `completed`, `failed` |
| `dry_run` | boolean | yes | Session-level dry-run flag |
| `subagent` | string | optional | Current PEV role |
| `plan_id` | string | optional | Linked migration plan |
| `run_id` | string | optional | Linked execution run |
| `messages` | list | yes | Role-attributed chat/tool messages (no secrets) |
| `created_at` | datetime | yes | UTC |
| `updated_at` | datetime | yes | UTC |

**State transitions**:

```text
planning → executing → validating → completed
planning → awaiting_approval → executing → …
any → failed
```

**Isolation**: Sessions do not share credentials; each uses env/profile token injection at HTTP client layer.

### ToolContract

Versioned catalog entry (code artifact, not DB row).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | e.g. `ado2gh_plan_phase` |
| `subagent` | enum | yes | `planner`, `executor`, `validator` |
| `http_method` | string | yes | Accelerator route method |
| `http_path` | string | yes | Accelerator path template |
| `input_schema` | JSON Schema | yes | Tool arguments |
| `dry_run_only` | boolean | optional | Tool cannot run live without approval |
| `requires_approval` | boolean | optional | Live mutations |
| `description` | string | yes | IDE display |

**Validation**: Executor tools must be subset of hosted allowlist from `001-agentic-migration-platform`.

### AgentSkillPack

Versioned markdown instructions per PEV role.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `skill_id` | string | yes | e.g. `planner-wave-migration` |
| `role` | enum | yes | `planner`, `executor`, `validator` |
| `path` | string | yes | Repo path to markdown |
| `spec_ref` | string | yes | e.g. `specs/003-local-agent-ide/spec.md` |
| `version` | string | yes | Semver or feature branch id |

### AuditEvent (existing table — extended usage)

Local IDE actions write rows consistent with hosted agents.

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | string | Existing |
| `timestamp` | datetime | Existing |
| `actor` | string | `local-developer` or authenticated user id |
| `action` | string | e.g. `ide.session.start`, `tool.ado2gh_enqueue_job` |
| `resource_type` | string | `session`, `assignment`, `repo` |
| `resource_id` | string | session_id / assignment_id |
| `outcome` | enum | `success`, `failure`, `denied` |
| `metadata` | JSON | No tokens/passwords (CA-003) |
| `session_id` | string | IDE session correlation |

## Relationships

```text
LocalAgentProfile 1 ── * IDESession
IDESession 1 ── * AuditEvent
ToolContract * ── used by MCP bridge and HTTP executor
AgentSkillPack * ── guides PEV subagents per session phase
IDESession 0..1 ── Assignment (optional scope)
```

## Configuration Files

| File | Purpose |
|------|---------|
| `config/local-profiles.yaml` | Default profile definitions |
| `.env.example` | Env var documentation |
| `.vscode/mcp.json.example` | VS Code MCP registration |
| `docker-compose.lightweight.yml` | Lightweight stack |

## Non-Goals (this feature)

- New Postgres tables for profiles or sessions (sessions remain agent-service memory + optional persistence in later feature)
- IDE transcript persistence in StateDB
