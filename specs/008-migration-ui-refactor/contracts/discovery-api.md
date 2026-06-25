# Discovery API Contract

**Feature**: 008-migration-ui-refactor
**Date**: 2026-06-23

## Overview

The Discovery API provides endpoints for scanning Azure DevOps organizations and retrieving scan results without automatic wave assignment. This API supports the simplified scanning workflow (US3).

## Endpoints

### POST /v1/discovery/scan

Initiates a scan of all configured ADO organizations to retrieve repository and pipeline information.

**Request**:
```json
{
  "organizations": ["org1", "org2"],
  "force_refresh": false
}
```

**Response** (202 Accepted):
```json
{
  "scan_id": "uuid",
  "status": "in_progress",
  "started_at": "2026-06-23T12:00:00Z"
}
```

**Response** (200 OK - if synchronous):
```json
{
  "scan_id": "uuid",
  "status": "completed",
  "started_at": "2026-06-23T12:00:00Z",
  "completed_at": "2026-06-23T12:02:00Z",
  "organizations_scanned": 2,
  "repositories_discovered": 150
}
```

**Error Responses**:
- 400 Bad Request: Invalid organization list
- 401 Unauthorized: Invalid ADO credentials
- 403 Forbidden: User lacks required permissions
- 500 Internal Server Error: Scan failure

### GET /v1/discovery/results

Retrieves persisted scan results for all organizations.

**Request Parameters**:
- `organization_id` (optional): Filter by specific organization
- `status` (optional): Filter by scan status (pending, completed, failed)

**Response** (200 OK):
```json
{
  "results": [
    {
      "id": "uuid",
      "organization_id": "org1",
      "repository_id": "repo1",
      "repository_name": "example-repo",
      "pipeline_count": 5,
      "last_scanned_at": "2026-06-23T12:00:00Z",
      "scan_status": "completed",
      "metadata": {
        "language": "python",
        "size": "medium"
      }
    }
  ],
  "total_count": 150,
  "scan_timestamp": "2026-06-23T12:02:00Z"
}
```

**Error Responses**:
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions
- 500 Internal Server Error: Retrieval failure

### GET /v1/discovery/scan/{scan_id}

Retrieves the status and results of a specific scan operation.

**Response** (200 OK):
```json
{
  "scan_id": "uuid",
  "status": "completed",
  "started_at": "2026-06-23T12:00:00Z",
  "completed_at": "2026-06-23T12:02:00Z",
  "organizations": [
    {
      "organization_id": "org1",
      "status": "completed",
      "repositories_discovered": 100,
      "error": null
    }
  ]
}
```

**Error Responses**:
- 404 Not Found: Scan ID does not exist
- 401 Unauthorized: Invalid credentials
- 403 Forbidden: User lacks required permissions

## Performance Requirements

- Scan completion within 2 minutes for organizations with up to 500 repositories (SC-003)
- Result retrieval within 1 second for typical queries

## Security Requirements

- Requires `require_manage_models(request)` guard (constitution principle V)
- ADO credentials must not appear in responses (constitution principle III)
- All scan operations must be audited (constitution principle V)

## Error Handling

- Partial scan failures (some organizations succeed, others fail) must be reported with per-organization status
- Scan results must persist even if some organizations fail
- Manual refresh option must be available via `force_refresh` parameter
