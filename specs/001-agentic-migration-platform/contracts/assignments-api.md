# Contract: Migration Assignments API

**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/profiles/{profile_id}/assignments`

## Create assignment

`POST /v1/profiles/{profile_id}/assignments`

**Auth**: Coordinator role

```json
{
  "name": "Wave 2 - Core Services",
  "assignment_type": "wave",
  "wave_number": 2,
  "execution_phase_id": "phase-wave2",
  "repos": [
    { "ado_project": "Payments", "ado_repo": "api-gateway" }
  ]
}
```

**Response** `201`:

```json
{
  "id": "asgn_01H...",
  "name": "Wave 2 - Core Services",
  "assignment_type": "wave",
  "wave_number": 2,
  "execution_phase_id": "phase-wave2",
  "repo_count": 1,
  "status": "active"
}
```

## List assignments

`GET /v1/profiles/{profile_id}/assignments?type=wave`

## Update membership

`PATCH /v1/profiles/{profile_id}/assignments/{assignment_id}/repos`

**Auth**: Coordinator role

## Get dependency graph

`GET /v1/profiles/{profile_id}/dependency-graph`

**Response**:

```json
{
  "edges": [
    {
      "from_repo": "Payments/api-gateway",
      "to_repo": "Platform/common-lib",
      "edge_type": "pipeline_resource"
    }
  ],
  "sorted_repos": ["Platform/common-lib", "Payments/api-gateway"],
  "cycles": []
}
```
