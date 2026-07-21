# Quickstart Validation Guide: Pipeline Step Decoupling

**Date**: 2026-06-24 | **Spec**: `specs/009-pipeline-step-decoupling/spec.md`

## Prerequisites

- Python 3.9+ with `pip install -e ".[api,dev]"` completed
- ADO PAT and GitHub token configured (`.env` or environment variables)
- At least one migration profile configured
- `actionlint` optionally installed on PATH (for full validation)
- `gh` CLI optionally installed (for GEI strategy)
- Test repos inventoried in StateDB

## Validation Scenarios

### Scenario 1: Analyze Dependencies with Warnings

**Tests**: FR-001, FR-002, FR-003, FR-014, FR-015

```bash
# Start the API server
python -m ado2gh.api.serve --reload

# Create a pipeline run targeting a repo with known SCs
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoWithSCs", "dry_run": true, "step_ids": ["connect", "analyze_deps"]}'

# Start the run
curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start

# Poll for completion
curl http://localhost:8000/api/pipeline-runs/{run_id}
```

**Expected outcomes**:
- `analyze_deps` step status is `warn`
- Step message contains bulleted list of SC/VG warnings
- Step result data contains `dependencies` dict with `service_connections`, `variable_groups` arrays
- Warnings are paginated at 10 items with "... and N more" indicator if > 10

### Scenario 2: Migrate Repository Feasibility Check (Dry-Run)

**Tests**: FR-004, FR-005, FR-027

```bash
# Run migrate_repos in dry-run on a small repo
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/SmallRepo", "dry_run": true, "step_ids": ["connect", "analyze_deps", "migrate_repos"]}'

curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start
```

**Expected outcomes**:
- `migrate_repos` step status is `completed`
- Step message reports repo size, LFS status, and recommended strategy ("git mirror")
- Step result data contains `feasibility_report` with `feasibility: "ok"`

### Scenario 3: Convert Pipelines with Validation

**Tests**: FR-006, FR-007, FR-022, FR-023

```bash
# Run convert_pipelines in dry-run on a repo with simple YAML pipeline
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoWithPipeline", "dry_run": true, "step_ids": ["connect", "analyze_deps", "convert_pipelines"]}'

curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start
```

**Expected outcomes**:
- `convert_pipelines` step status is `completed` or `warn`
- Step result data contains `workflow_files` array with `validation_status`, `validation_mode`
- If `actionlint` installed: `validation_mode` is `actionlint`
- If not installed: `validation_mode` is `yaml_fallback` and message notes reduced validation

### Scenario 4: Prerequisite Check Failure

**Tests**: FR-008, FR-009, FR-026

```bash
# Run convert_pipelines without analyze_deps
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoA", "dry_run": true, "step_ids": ["connect", "convert_pipelines"]}'

curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start
```

**Expected outcomes**:
- `convert_pipelines` step status is `failed`
- Step message: "Analyze Dependencies must be completed before Convert Pipelines to GitHub Actions can proceed."

### Scenario 5: Operator Resolution Flow

**Tests**: FR-010, FR-012, FR-019

```bash
# After Scenario 1 produces warnings, fetch dependency gaps
curl http://localhost:8000/api/pipeline-runs/{run_id}/dependency-gaps

# Submit resolutions
curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/resolve-dependencies \
  -H "Content-Type: application/json" \
  -d '{"resolutions": [{"field": "secret_mapping__Project__SCName", "value": "AZURE_CLIENT_ID"}]}'

# Re-run analyze_deps
curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start \
  -H "Content-Type: application/json" \
  -d '{"step_ids": ["analyze_deps"]}'
```

**Expected outcomes**:
- GET returns list of gaps with `auto_provisionable` flag and `provisioning_status`
- POST returns `{"resolved": N, "remaining": M}`
- Re-running `analyze_deps` shows reduced or zero warnings
- Resolutions persist across new runs for the same profile

### Scenario 6: Per-Repo Lock

**Tests**: FR-031

```bash
# Start two runs targeting the same repo simultaneously
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoA", "dry_run": false, "step_ids": ["migrate_repos"]}'

# Second run should be rejected
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoA", "dry_run": false, "step_ids": ["migrate_repos"]}'
```

**Expected outcomes**:
- First run starts successfully
- Second run is rejected with "migration in progress for Project/RepoA"

### Scenario 7: Batch Continue-on-Error

**Tests**: FR-028

```bash
# Run migrate_repos on a phase with multiple repos where one will fail
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "pilot", "dry_run": false, "step_ids": ["connect", "analyze_deps", "migrate_repos"]}'

curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start
```

**Expected outcomes**:
- Step status is `warn` (not `failed`) when some repos fail
- Step message reports "N succeeded, M failed"
- All non-failed repos are migrated successfully

### Scenario 8: Validate Workflow Integrity

**Tests**: FR-024

```bash
# After a live migration with convert_pipelines, run validate
curl -X POST http://localhost:8000/api/pipeline-runs \
  -H "Content-Type: application/json" \
  -d '{"phase": "poc", "repository_id": "Project/RepoA", "dry_run": false, "step_ids": ["validate"]}'

curl -X POST http://localhost:8000/api/pipeline-runs/{run_id}/start
```

**Expected outcomes**:
- Validate step result includes `workflow_integrity` check
- Check verifies committed workflow files exist in GitHub and match convert step output
- If workflows missing: check verdict is `FAIL`

## Running Tests

```bash
# Unit tests for new modules
pytest tests/unit/test_step_prerequisites.py tests/unit/test_repo_lock.py tests/unit/test_oidc_provisioner.py tests/unit/test_workflow_validator.py tests/unit/test_feasibility_analysis.py -v

# Contract tests for step result data shapes
pytest tests/contract/test_009_step_contracts.py -v

# Integration test for end-to-end pipeline decoupling
pytest tests/integration/test_009_pipeline_decoupling.py -v

# Full coverage check
pytest tests/ --cov=ado2gh --cov-report=term-missing
```

**Coverage gate**: `ado2gh` package must maintain >= 85% line coverage.
