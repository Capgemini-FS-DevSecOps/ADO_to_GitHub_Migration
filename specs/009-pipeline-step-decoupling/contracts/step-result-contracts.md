# Step Result Contracts: Pipeline Step Decoupling

**Date**: 2026-06-24 | **Spec**: `specs/009-pipeline-step-decoupling/spec.md`

## Overview

Each pipeline step produces a structured `result` dict on its `PipelineStep` object. Downstream steps read these results by step ID. This document defines the contract for each step's result data shape.

## analyze_deps — Step Result Contract

```json
{
  "migration_order": ["Project/RepoA", "Project/RepoB"],
  "dependency_count": 1,
  "pipelines_scanned": 5,
  "dependencies": {
    "Project/RepoA": {
      "service_connections": [
        {"name": "Azure-Prod", "type": "azurerm", "id": "guid", "status": "auto_provisionable"}
      ],
      "variable_groups": [
        {"name": "BuildVars", "status": "operator_required"}
      ],
      "repo_dependencies": ["Project/RepoB"],
      "environments": [
        {"name": "prod", "type": "approval"}
      ],
      "unsupported_tasks": ["CustomTask@1"],
      "self_hosted_agents": ["OnPremPool"]
    }
  },
  "warnings": [
    "Project/RepoA: ⚠ Service connection 'Azure-Prod' — create matching GitHub secret or OIDC login"
  ],
  "readiness": {"auto": 3, "assisted": 1, "manual": 1}
}
```

**Consumed by**: `convert_pipelines` (reads `dependencies` for SC mapping), `migrate_repos` (reads `migration_order`), `validate` (reads `dependencies` for SC mapping context).

## migrate_repos — Step Result Contract

```json
{
  "completed": 4,
  "failed": 1,
  "repos": {
    "Project/RepoA": {"status": "completed", "strategy": "mirror"},
    "Project/RepoB": {"status": "failed", "error": "LFS file too large"}
  },
  "repo_details": [
    {"repo": "Project/RepoA", "status": "completed", "strategy": "mirror", "branches": 12}
  ],
  "feasibility_report": {
    "Project/RepoA": {
      "repo_size_bytes": 524288000,
      "lfs_object_count": 0,
      "lfs_size_bytes": 0,
      "largest_file_bytes": 1048576,
      "branch_count": 12,
      "tag_count": 3,
      "has_wiki": false,
      "has_policies": true,
      "recommended_strategy": "mirror",
      "feasibility": "ok",
      "warnings": []
    }
  }
}
```

**Consumed by**: `validate` (reads `repo_details` for which repos to validate), `convert_pipelines` (reads `repos` to know which repos were successfully migrated).

## convert_pipelines — Step Result Contract

```json
{
  "workflow_files": [
    {
      "source_pipeline": "build-ci",
      "output_path": ".github/workflows/build-ci.yml",
      "commit_sha": "abc123def456",
      "validation_status": "valid",
      "validation_errors": [],
      "validation_mode": "actionlint",
      "unmapped_secrets": [],
      "conflict_renamed": false
    }
  ],
  "dry_run": false,
  "repos": 3,
  "validated": 3
}
```

**Consumed by**: `validate` (reads `workflow_files` for expected workflow paths and commit SHAs).

## validate — Step Result Contract

```json
{
  "passed": 3,
  "failed": 0,
  "total": 3,
  "repo_details": [
    {
      "project": "Project",
      "repo": "RepoA",
      "gh_target": "Org/RepoA",
      "overall": "PASS",
      "primary_reason": "All checks passed",
      "checks": [
        {"check": "commit_sha", "verdict": "PASS", "detail": "HEAD SHA matches"},
        {"check": "workflow_integrity", "verdict": "PASS", "detail": "2/2 workflows verified"}
      ]
    }
  ]
}
```

## connect — Step Result Contract

```json
{
  "projects": 15,
  "profile_id": "profile-uuid"
}
```

## discover — Step Result Contract

```json
{
  "output_dir": "/path/to/discovery/output"
}
```

## inventory — Step Result Contract

```json
{
  "pipelines": 42,
  "projects": {"ProjectA": {"pipelines": 10, "service_connections": 5}}
}
```

## readiness — Step Result Contract

```json
{
  "auto": 30,
  "assisted": 8,
  "manual": 4
}
```

## assign — Step Result Contract

```json
{
  "waves": 3
}
```
