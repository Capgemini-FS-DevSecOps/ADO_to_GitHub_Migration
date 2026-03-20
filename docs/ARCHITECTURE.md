# Architecture — ADO2GH Migration Tool

Technical architecture and design decisions for the ado2gh migration accelerator.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLI Layer (cli.py)                         │
│  Click commands, argument parsing, client initialization           │
└──────────────┬──────────────────────────────┬──────────────────────┘
               │                              │
  ┌────────────▼────────────┐    ┌────────────▼────────────┐
  │   Phase Orchestration   │    │    Core Migration       │
  │                         │    │                         │
  │  RiskScorer             │    │  MigrationEngine        │
  │  WaveAssigner           │    │  ├── _migrate_git       │
  │  PhaseGateChecker       │    │  │   ├── mirror clone   │
  │  BatchExecutor          │    │  │   └── GEI (alt)      │
  │  ProgressTracker        │    │  ├── _migrate_pipelines │
  │                         │    │  ├── _migrate_work_items│
  │  Controls:              │    │  ├── _migrate_wiki      │
  │  - Which repos when     │    │  ├── _migrate_secrets   │
  │  - Gate enforcement     │    │  └── _migrate_policies  │
  │  - Batch checkpointing  │    │                         │
  └─────────────────────────┘    │  WaveRunner             │
                                 │  ADOCleanup             │
                                 │  RollbackHandler        │
                                 └────────────┬────────────┘
                                              │
        ┌─────────────────────────────────────┼───────────────────┐
        │                                     │                   │
  ┌─────▼─────┐  ┌──────────────┐  ┌─────────▼─────────┐  ┌─────▼─────┐
  │ ADOClient │  │   GHClient   │  │  Pipeline System  │  │ Reporting │
  │           │  │              │  │                   │  │           │
  │ REST 7.1  │  │ Multi-token  │  │ Extractor         │  │ Reporter  │
  │ Projects  │  │ TokenManager │  │ Transformer       │  │ CSV       │
  │ Repos     │  │ ├─ PAT pool  │  │ InventoryBuilder  │  │ Validator │
  │ Pipelines │  │ ├─ Rate limts│  │                   │  │ Readiness │
  │ Work Items│  │ └─ App auth  │  │ 200+ task maps    │  │ SvcConn   │
  │ Wiki      │  │              │  │ Condition mapping  │  │ Manifest  │
  │ Policies  │  │ Rate-aware   │  │ Pool mapping      │  │           │
  └─────┬─────┘  │ rotation     │  └───────────────────┘  └───────────┘
        │        └──────┬───────┘
        │               │
  ┌─────▼───────────────▼─────┐
  │        StateDB            │
  │    SQLite (WAL mode)      │
  │                           │
  │  migrations               │  Per-repo scope tracking
  │  wave_runs                │  Wave execution history
  │  pipeline_inventory       │  Full pipeline metadata
  │  pipeline_migrations      │  Per-pipeline transform status
  │  repo_risk_scores         │  9-signal risk scores
  │  phase_gates              │  Gate pass/fail/override
  │  batch_checkpoints        │  Resume points for interruption
  └───────────────────────────┘
```

---

## Design Principles

### 1. ADO-Specific, Not Generic

Every feature addresses an ADO-to-GitHub challenge:
- Pipeline transformation handles ADO-specific concepts (classic builds, release pipelines, variable groups)
- Risk scoring signals are ADO metrics (pipeline count, classic ratio, service connections)
- Post-migration cleanup targets ADO (disable pipelines, archive repos)

### 2. Actually Migrate, Don't Just Plan

Previous versions recorded metadata but didn't execute the git migration. v5 runs real `git clone --mirror && git push --mirror` via subprocess, including LFS object transfer.

### 3. Validate Content, Not Counts

Post-migration validation compares HEAD commit SHAs between ADO and GitHub — proving code actually transferred. Branch counts are supplementary, not primary.

### 4. Idempotent & Resumable

Every operation is safe to re-run:
- Completed repos are skipped
- Batch checkpoints in SQLite allow mid-run recovery
- Pipeline transforms skip already-completed entries

### 5. Scope-Targeted Operations

Operations can target specific migration scopes:
- Migrate only `repo` + `pipelines` (skip work_items, wiki, etc.)
- Rollback only `branch_policies` without deleting the repo
- Validate specific scopes

---

## Data Flow

```
migration.yaml                     ADO REST API
     │                                  │
     ▼                                  ▼
 ConfigLoader ──────────────────► ADOClient
     │                              │
     ▼                              ▼
 phase assign ─── RiskScorer ──► StateDB (repo_risk_scores)
     │                              │
     ▼                              ▼
migration_phase.yaml          pipeline_inventory
     │                              │
     ▼                              ▼
 phase run ───► BatchExecutor ──► MigrationEngine
                    │                  │
                    │          ┌───────┼───────┐
                    │          ▼       ▼       ▼
                    │     git mirror  transform  issues
                    │          │       │       │
                    │          ▼       ▼       ▼
                    │     GitHub    output/   GitHub
                    │      repo    workflows  Issues
                    │
                    ▼
              batch_checkpoints
                    │
                    ▼
              gate-check ──► phase_gates
                    │
                    ▼
              validate ──► SHA comparison ──► validation_report.csv
                    │
                    ▼
              ado-cleanup ──► Disable pipelines, push notice, archive
```

---

## Token Management

```
                    ┌──────────────────────┐
                    │    TokenManager      │
                    │                      │
                    │  tokens: [           │
                    │    PAT_1 (rem: 4200) │ ◄── round-robin selection
                    │    PAT_2 (rem: 3800) │     per API call
                    │    PAT_3 (rem: 1200) │
                    │    APP_TOKEN (exp: T) │ ◄── auto-refreshed JWT
                    │  ]                   │
                    │                      │
                    │  get_token() ─────────┼──► selects highest-remaining
                    │  update_rate_limit() ─┼──► from response headers
                    │  _get_app_token() ────┼──► JWT + installation token
                    └──────────────────────┘
```

Token selection:
1. Round-robin through available tokens
2. Skip tokens with `remaining < 50`
3. If all tokens exhausted, wait for the soonest reset
4. App tokens auto-refresh 60s before expiry

---

## Pipeline Transformation Pipeline

```
ADO Pipeline Definition
     │
     ▼
PipelineMetadataExtractor
  ├── extract_yaml_pipeline()      ── parse YAML structure, triggers, vars
  ├── extract_classic_build()      ── map phases, build tasks
  └── extract_release_pipeline()   ── map environments, approvals
     │
     ▼
PipelineMetadata (normalized)
  ├── stages, environments, variables
  ├── service_connections, agent_pools
  ├── trigger_branches, schedules
  └── complexity score (simple/medium/complex)
     │
     ▼
PipelineTransformer
  ├── _build_yaml_workflow()       ── direct YAML→GHA mapping
  ├── _build_classic_workflow()    ── best-effort phase→job mapping
  ├── _build_release_workflow()    ── stages→deployment jobs
  ├── _map_step()                  ── ADO task → GHA action (200+ mappings)
  ├── _map_condition()             ── ADO conditions → GHA if expressions
  └── _build_triggers()            ── push/PR/schedule/workflow_dispatch
     │
     ▼
GitHub Actions YAML + migration notes markdown
```

---

## State Database Schema

```sql
-- Per-repo migration tracking
migrations (wave_id, ado_project, ado_repo, gh_org, gh_repo, scope, status, ...)
-- UNIQUE(wave_id, ado_project, ado_repo, scope)

-- Wave execution log
wave_runs (wave_id, started_at, completed_at, status, dry_run)

-- Full pipeline metadata cache
pipeline_inventory (project, pipeline_id, pipeline_name, pipeline_type,
                    repo_name, complexity, metadata_json, scanned_at)
-- UNIQUE(project, pipeline_id)

-- Per-pipeline migration status
pipeline_migrations (wave_id, project, pipeline_id, status, workflow_file,
                     warnings, unsupported_tasks, complexity, ...)
-- UNIQUE(wave_id, project, pipeline_id)

-- Risk scores per repo
repo_risk_scores (project, repo_name, total_score, assigned_phase,
                  gh_org, gh_repo, score_json, scored_at)
-- UNIQUE(project, repo_name)

-- Phase gate results
phase_gates (phase, status, repo_success_pct, pipeline_success_pct,
             failures_json, override_reason, checked_at)
-- UNIQUE(phase)

-- Batch execution checkpoints
batch_checkpoints (phase, batch_num, total_batches, repos_done,
                   repos_total, status, started_at, completed_at)
-- UNIQUE(phase, batch_num)
```

All tables use `ON CONFLICT ... DO UPDATE` for idempotent upserts. Database uses WAL mode for concurrent read/write access during parallel execution.

---

## Error Handling Strategy

1. **Per-scope isolation**: A failure in `work_items` doesn't block `pipelines` for the same repo
2. **Per-repo isolation**: A failure in one repo doesn't block other repos in the batch
3. **Automatic retry**: Re-running a phase skips completed repos/scopes
4. **Failed repo tracking**: `failed_repos_{phase}.txt` generated after every phase run
5. **Gate enforcement**: Phase can't advance until success thresholds met (or override)
6. **Rollback granularity**: Can undo specific scopes without destroying the repo
