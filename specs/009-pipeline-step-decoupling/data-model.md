# Data Model: Pipeline Step Decoupling & Dependency Resolution

**Date**: 2026-06-24 | **Spec**: `specs/009-pipeline-step-decoupling/spec.md`

## Entities

### StepResult (existing, extended)

The result data structure persisted on each `PipelineStep` after execution.

| Field | Type | Description |
|-------|------|-------------|
| `dependencies` | `dict[str, DependencyReport]` | Per-repo dependency data (keyed by `Project/RepoName`). Produced by `analyze_deps`. |
| `migration_order` | `list[str]` | Ordered list of repo IDs for migration. Produced by `analyze_deps`. |
| `warnings` | `list[str]` | Warning strings requiring operator attention. |
| `feasibility_report` | `dict[str, FeasibilityReport]` | Per-repo feasibility data. Produced by `migrate_repos`. |
| `workflow_files` | `list[ConversionResult]` | Per-pipeline conversion results. Produced by `convert_pipelines`. |
| `readiness` | `dict[str, int]` | Pipeline readiness counts `{auto, assisted, manual}`. |

**Relationships**: Stored as `PipelineStep.result` (a `dict[str, Any]`) on the `PipelineRun`.

### DependencyReport (new)

Per-repo structured data produced by the Analyze Dependencies step.

| Field | Type | Description |
|-------|------|-------------|
| `service_connections` | `list[ServiceConnectionRef]` | SC references found in repo pipelines |
| `variable_groups` | `list[VariableGroupRef]` | Variable group references |
| `repo_dependencies` | `list[str]` | Repo IDs this repo depends on |
| `environments` | `list[EnvironmentRef]` | ADO environments referenced |
| `unsupported_tasks` | `list[str]` | Task names not supported by the converter |
| `self_hosted_agents` | `list[str]` | Agent pool names requiring mapping |

**Sub-types**:

**ServiceConnectionRef**:
| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | ADO service connection name |
| `type` | `str` | Connection type (azurerm, kubernetes, acr, dockerregistry, etc.) |
| `id` | `str` | ADO service connection ID |
| `status` | `str` | `auto_provisionable` \| `operator_required` \| `mapped` |

**VariableGroupRef**:
| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | ADO variable group name |
| `status` | `str` | `operator_required` \| `mapped` |

**EnvironmentRef**:
| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | ADO environment name |
| `type` | `str` | `approval` \| `standard` |

### FeasibilityReport (new)

Per-repo data produced by Migrate Repository Contents.

| Field | Type | Description |
|-------|------|-------------|
| `repo_size_bytes` | `int` | Total repository size |
| `lfs_object_count` | `int` | Number of LFS objects |
| `lfs_size_bytes` | `int` | Total LFS object size |
| `largest_file_bytes` | `int` | Size of largest file in repo |
| `branch_count` | `int` | Number of branches |
| `tag_count` | `int` | Number of tags |
| `has_wiki` | `bool` | Wiki presence |
| `has_policies` | `bool` | Branch policies present |
| `recommended_strategy` | `str` | `mirror` \| `gei` \| `manual` |
| `feasibility` | `str` | `ok` \| `warn` \| `fail_soft` |
| `warnings` | `list[str]` | Limiting factors (e.g., "repo size 12GB exceeds 10GB GEI limit") |

**Thresholds**:
- `warn`: repo > 2GB or any file > 100MB
- `fail_soft`: repo > 10GB or LFS file > 2GB
- `ok`: all checks pass

### ConversionResult (new)

Per-pipeline data produced by Convert Pipelines.

| Field | Type | Description |
|-------|------|-------------|
| `source_pipeline` | `str` | ADO pipeline name |
| `output_path` | `str` | Local workflow file path (dry-run) or GitHub path (live) |
| `commit_sha` | `str \| null` | SHA of auto-commit in live mode; `null` in dry-run |
| `validation_status` | `str` | `valid` \| `invalid` \| `warn` |
| `validation_errors` | `list[str]` | Specific error details from actionlint or fallback |
| `validation_mode` | `str` | `actionlint` \| `yaml_fallback` |
| `unmapped_secrets` | `list[str]` | SC names without GitHub secret mappings |
| `conflict_renamed` | `bool \| null` | True if file was renamed due to conflict |

### OperatorResolution (new)

Operator-provided mapping for a dependency gap, persisted per-profile.

| Field | Type | Description |
|-------|------|-------------|
| `field` | `str` | Field key (e.g., `secret_mapping__Project__SCName`) |
| `value` | `str` | GitHub secret name or OIDC credential ID |
| `type` | `str` | `service_connection` \| `variable_group` \| `environment` |
| `profile_id` | `str` | Profile this resolution belongs to |
| `created_at` | `str` | ISO timestamp |

**Persistence**: Stored in `SettingsStore` under `operator_resolutions` key per profile.

### RepoLock (new)

Lock acquired per-repo to prevent concurrent migration runs.

| Field | Type | Description |
|-------|------|-------------|
| `repo_id` | `str` | `Project/RepoName` identifier |
| `run_id` | `str` | PipelineRun ID holding the lock |
| `acquired_at` | `str` | ISO timestamp of acquisition |

**Lifecycle**:
1. `acquired` — lock created before step execution
2. `released` — lock deleted when run completes, fails, or is cancelled

**Persistence**: In-memory `dict[str, RepoLock]` in `RepoLockManager`. Interface designed to swap to SQLite if multi-process support is needed.

## State Transitions

### StepStatus (existing)

```
PENDING → RUNNING → COMPLETED
                   → WARN
                   → FAILED
                   → SKIPPED
```

### ServiceConnectionRef.status

```
auto_provisionable → mapped     (OIDC auto-provisioning succeeded)
auto_provisionable → operator_required  (OIDC provisioning failed)
operator_required  → mapped     (operator provided value via resolution flow)
```

### FeasibilityReport.feasibility

```
ok → (migration proceeds with recommended strategy)
warn → (migration proceeds, warnings displayed)
fail_soft → (migration skipped, manual handling recommended)
```

### ConversionResult.validation_status

```
valid → (workflow committed in live mode)
invalid → (workflow not committed, errors reported)
warn → (workflow committed with warnings, e.g., unsupported tasks)
```

## Validation Rules

- **VR-001**: GitHub secret names must match `^[A-Z0-9_]+$` (uppercase, alphanumeric, underscore). Invalid entries are rejected in the resolution flow.
- **VR-002**: `FeasibilityReport.feasibility` must be `fail_soft` if `repo_size_bytes > 10GB` or `lfs_size_bytes > 2GB`.
- **VR-003**: `ConversionResult.commit_sha` must be `null` when `dry_run=True`.
- **VR-004**: `OperatorResolution.value` must not be empty or whitespace-only.
- **VR-005**: `RepoLock` for a given `repo_id` can only be held by one `run_id` at a time.
