# Migration API Contract

**Feature**: 008-migration-ui-refactor
**Date**: 2026-06-23

## Overview

The Migration API provides endpoints for on-demand migration with dependency resolution and wave-based bulk migration. This API supports the flexible migration workflow (US4, US5).

## Endpoints

### POST /v1/migration/repo

Initiates on-demand migration of a repository and its full transitive dependencies.

**Request**:
```json
{
  "repository_id": "repo1",
  "organization_id": "org1",
  "pre_migration_form_id": "uuid",
  "dry_run": false
}
```

**Response** (202 Accepted):
```json
{
  "operation_id": "uuid",
  "status": "pending",
  "repository_id": "repo1",
  "operation_type": "on_demand",
  "created_at": "2026-06-23T12:00:00Z"
}
```

**Response** (200 OK - if synchronous):
```json
{
  "operation_id": "uuid",
  "status": "completed",
  "repository_id": "repo1",
  "operation_type": "on_demand",
  "dependencies_migrated": 5,
  "started_at": "2026-06-23T12:00:00Z",
  "completed_at": "2026-06-23T12:05:00Z"
}
```

**Error Responses**:
- 400 Bad Request: Invalid request or missing pre-migration form
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 409 Conflict: Repository already in progress in another wave
- 500 Internal Server Error: Migration failure

### POST /v1/migration/wave

Creates a new migration wave with custom name and repository assignments.

**Request**:
```json
{
  "name": "Q3 Production Migration",
  "description": "Migrate all production repos",
  "repository_ids": ["repo1", "repo2", "repo3"],
  "organization_id": "org1"
}
```

**Response** (201 Created):
```json
{
  "wave_id": "uuid",
  "name": "Q3 Production Migration",
  "description": "Migrate all production repos",
  "status": "draft",
  "repository_count": 3,
  "created_at": "2026-06-23T12:00:00Z"
}
```

**Error Responses**:
- 400 Bad Request: Invalid wave name or repository list
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 409 Conflict: Repository already assigned to another active wave
- 500 Internal Server Error: Wave creation failure

### POST /v1/migration/wave/{wave_id}/execute

Executes a migration wave (sequential execution with dependency order).

**Request**:
```json
{
  "dry_run": false
}
```

**Response** (202 Accepted):
```json
{
  "wave_id": "uuid",
  "status": "in_progress",
  "started_at": "2026-06-23T12:00:00Z",
  "repositories_in_wave": 3
}
```

**Error Responses**:
- 400 Bad Request: Wave not in draft status
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 404 Not Found: Wave ID does not exist
- 500 Internal Server Error: Wave execution failure

### GET /v1/migration/wave/{wave_id}

Retrieves the status and progress of a migration wave.

**Response** (200 OK):
```json
{
  "wave_id": "uuid",
  "name": "Q3 Production Migration",
  "status": "in_progress",
  "created_at": "2026-06-23T12:00:00Z",
  "started_at": "2026-06-23T12:00:00Z",
  "repositories": [
    {
      "repository_id": "repo1",
      "migration_order": 1,
      "status": "completed"
    },
    {
      "repository_id": "repo2",
      "migration_order": 2,
      "status": "in_progress"
    }
  ]
}
```

**Error Responses**:
- 404 Not Found: Wave ID does not exist
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions

### POST /v1/migration/pre-migration-form

Generates a pre-migration form based on dependency analysis for a repository.

**Request**:
```json
{
  "repository_id": "repo1",
  "organization_id": "org1"
}
```

**Response** (200 OK):
```json
{
  "form_id": "uuid",
  "repository_id": "repo1",
  "required_fields": {
    "target_github_org": {
      "type": "string",
      "description": "Target GitHub organization"
    },
    "team_mapping": {
      "type": "object",
      "description": "Team/permission mapping configuration"
    },
    "pipeline_config": {
      "type": "object",
      "description": "Pipeline configuration"
    }
  },
  "optional_fields": {
    "repo_description": {
      "type": "string",
      "description": "Repository description"
    },
    "topics": {
      "type": "array",
      "description": "Repository topics"
    },
    "labels": {
      "type": "array",
      "description": "Repository labels"
    }
  },
  "dependency_graph": {
    "nodes": ["repo1", "repo2", "repo3"],
    "edges": [
      {"source": "repo1", "target": "repo2"},
      {"source": "repo2", "target": "repo3"}
    ]
  }
}
```

**Error Responses**:
- 400 Bad Request: Invalid repository ID
- 404 Not Found: Repository not found in discovery results
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 500 Internal Server Error: Form generation failure

### PUT /v1/migration/pre-migration-form/{form_id}

Submits a completed pre-migration form for validation.

**Request**:
```json
{
  "target_github_org": "my-org",
  "team_mapping": {"team1": "maintain"},
  "pipeline_config": {"enabled": true},
  "repo_description": "Example repository",
  "topics": ["python", "api"],
  "labels": ["production"]
}
```

**Response** (200 OK):
```json
{
  "form_id": "uuid",
  "form_status": "validated",
  "validated_at": "2026-06-23T12:00:00Z"
}
```

**Error Responses**:
- 400 Bad Request: Missing required fields or invalid data
- 404 Not Found: Form ID does not exist
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 422 Unprocessable Entity: Validation failed
- 500 Internal Server Error: Validation failure

## Performance Requirements

- On-demand migration completion within 5 minutes for repos with up to 20 dependencies (SC-004)
- Wave execution processes repositories in correct dependency order with 100% accuracy (SC-005)
- Pre-migration form generation within 10 seconds for complex dependency graphs

## Security Requirements

- Requires `require_manage_models(request)` guard (constitution principle V)
- Destructive actions require explicit confirmation (constitution principle V)
- All migration operations must be audited (constitution principle V)
- Dry-run capability must be provided before irreversible actions (constitution principle V)

## Error Handling

- Circular dependencies must be detected and reported with clear error messages
- Migration failures mid-wave must not stop the entire wave (continue with remaining repos)
- Dependency validation must ensure all required repositories are included in the wave
