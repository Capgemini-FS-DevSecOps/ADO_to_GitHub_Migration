# Pipeline Step Contracts: Step Decoupling & Prerequisites

**Date**: 2026-06-24 | **Spec**: `specs/009-pipeline-step-decoupling/spec.md`

## Pipeline Step Lists

### ACCELERATOR_PIPELINE_STEPS (updated)

| Step ID | Label | Prerequisites | Description |
|---------|-------|---------------|-------------|
| `connect` | Connect & validate credentials | — | Verify ADO PAT, GitHub token, and org access |
| `discover` | Discover repositories | `connect` | Scan ADO projects and repos; sync profile discovery |
| `inventory` | Inventory ADO pipelines | `connect` | Deep-scan YAML, classic, and release pipeline definitions into StateDB |
| `readiness` | Assess conversion readiness | `inventory` | Classify auto/assisted/manual; flag blockers |
| `assign` | Assign migration phases | `discover` | Risk-score repos and assign to phases |
| `analyze_deps` | Analyze dependencies | `inventory` | Consolidated dependency analysis: SCs, VGs, repo-to-repo, environments, self-hosted agents, task inputs. Merged former "Map secrets" step. Reports warnings as paginated bulleted list. Status `warn` if operator input required. |
| `migrate_repos` | Migrate repository contents | `analyze_deps` | Feasibility analysis (size, LFS, branches, tags, metadata) then git mirror, GEI transfer, or manual. Continue-on-error for batches. Per-repo lock. |
| `convert_pipelines` | Convert pipelines to GitHub Actions | `analyze_deps` | Execute `PipelineTransformer.transform()`, validate via `WorkflowValidator` (actionlint or YAML fallback), auto-commit workflows in live mode with versioned conflict handling. |
| `convert_metadata` | Convert branch policies & wiki | `migrate_repos` | Branch protection rules, wiki pages, work items to GitHub |
| `migrate` | Run all scoped migrations | `analyze_deps` | Execute every enabled scope for repos in the selected phase |
| `validate` | Validate migrated repos | `migrate_repos`, `convert_pipelines` | SHA/branch parity AND committed workflow integrity verification |

**Removed**: `map_secrets` step (merged into `analyze_deps`).

### MIGRATE_UI_PIPELINE_STEPS (updated)

| Step ID | Label | Prerequisites | Description |
|---------|-------|---------------|-------------|
| `connect` | Load discovery data | — | Load profile discovery data for the selected repos |
| `analyze_deps` | Analyze dependencies | `connect` | Consolidated dependency analysis: SCs, VGs, repo-to-repo, environments, self-hosted agents, task inputs. Merged former "Map secrets" step. Reports warnings as paginated bulleted list. Status `warn` if operator input required. |
| `migrate_repos` | Migrate repositories | `analyze_deps` | Transfer git content to GitHub (mirror or GEI) with feasibility check |
| `convert_pipelines` | Convert workflows | `analyze_deps` | Execute `PipelineTransformer.transform()`, validate via `WorkflowValidator` (actionlint or YAML fallback), auto-commit workflows in live mode with versioned conflict handling |
| `validate` | Validate | `migrate_repos`, `convert_pipelines` | SHA verification and workflow integrity check |

**Removed**: `map_secrets` step (merged into `analyze_deps`).

### AGENT_MIGRATION_PIPELINE_STEPS

Alias to `MIGRATE_UI_PIPELINE_STEPS` (unchanged from current behavior).

## Prerequisite Contract

Each step declares prerequisites as a list of step IDs that must be in `COMPLETED` or `WARN` status before the step can execute.

```python
STEP_PREREQUISITES: dict[str, list[str]] = {
    "connect": [],
    "discover": ["connect"],
    "inventory": ["connect"],
    "readiness": ["inventory"],
    "assign": ["discover"],
    "analyze_deps": ["inventory"],
    "migrate_repos": ["analyze_deps"],
    "convert_pipelines": ["analyze_deps"],
    "convert_metadata": ["migrate_repos"],
    "migrate": ["analyze_deps"],
    "validate": ["migrate_repos", "convert_pipelines"],
}
```

**Behavior**: Before executing a step, the runner checks `STEP_PREREQUISITES[step_id]`. If any prerequisite step is not in `COMPLETED` or `WARN` status, the step fails with: `"{prerequisite_label} must be completed before {step_label} can proceed."`

**Flexible ordering**: Steps can be executed in any order as long as prerequisites are satisfied. The default order in the pipeline lists is the recommended order.

## Step Handler Contract

Each step handler is a method on `PipelineRunner` with signature:

```python
def _step_<step_id>(self, run: PipelineRun) -> None
```

**Responsibilities**:
1. Read prerequisite data from `run.steps` by step ID
2. Execute step logic
3. Call `self._set_step(run, step_id, status, message, result_data)` exactly once
4. Store structured result data conforming to the step result contract
5. Never re-derive data available from prior step results

**Error handling**: Unhandled exceptions are caught by `_execute()` which sets the step to `FAILED` and halts the pipeline.

## Operator Resolution API Contract

### GET `/api/pipeline-runs/{run_id}/dependency-gaps`

Returns dependency gaps for the Resolve Dependencies UI panel.

```json
{
  "run_id": "uuid",
  "gaps": [
    {
      "field": "secret_mapping__Project__SCName",
      "label": "Project: SCName -> GitHub secret",
      "type": "service_connection",
      "sc_type": "azurerm",
      "auto_provisionable": true,
      "provisioning_status": "failed",
      "failure_reason": "Insufficient Azure AD permissions",
      "current_value": null
    }
  ]
}
```

### POST `/api/pipeline-runs/{run_id}/resolve-dependencies`

Accepts operator resolutions and persists them per-profile.

```json
{
  "resolutions": [
    {"field": "secret_mapping__Project__SCName", "value": "AZURE_CLIENT_ID"}
  ]
}
```

**Response**: `{"resolved": 2, "remaining": 1}`

### POST `/api/pipeline-runs/{run_id}/refresh-inventory`

Triggers a re-run of the inventory scan step before re-running analyze_deps.

**Response**: `{"status": "completed", "pipelines_scanned": 42}`
