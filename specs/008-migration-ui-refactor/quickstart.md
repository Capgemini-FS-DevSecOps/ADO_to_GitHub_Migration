# Quickstart Validation Guide: Unified Migration UI

**Feature**: 008-migration-ui-refactor
**Date**: 2026-06-23

## Prerequisites

- Docker Compose installed
- Python 3.9+ installed
- Node.js 18+ installed
- ADO organization credentials configured
- GitHub organization access for migration target

## Local Development Setup

### 1. Start the Local Stack

```bash
docker compose up --build
```

**Expected Outcome**: All services (accelerator API, agent API, UI) start within 30 seconds (SC-008). Services are ready to accept requests including database initialization and dependency loading.

### 2. Verify Services are Running

```bash
curl http://localhost:8000/health  # Accelerator API
curl http://localhost:8001/health  # Agent API
curl http://localhost:3000         # UI
```

**Expected Outcome**: All services return 200 OK health status.

## Validation Scenarios

### Scenario 1: Unified Tab Structure (US1)

**Objective**: Verify the unified navigation structure combines Discovery, Readiness, Workflows, and Validation tabs.

**Steps**:
1. Open the migration UI at `http://localhost:3000`
2. Navigate to Settings
3. Observe the tab structure

**Expected Outcome**:
- Unified tab structure is visible
- Navigation between migration activities is streamlined
- No redundant page loads when switching between activities
- All existing capabilities are accessible

**Success Criteria**: Admin can navigate the unified tab structure and complete migration workflows in under 3 minutes (SC-001).

### Scenario 2: Streamlined Agent Interface (US2)

**Objective**: Verify the agent interface is clean and full-page without pipeline/PEV indicators.

**Steps**:
1. Open the agent chat screen
2. Observe the interface layout
3. Initiate a simple agent conversation

**Expected Outcome**:
- Agent conversation takes up the entire page
- No pipeline status indicators visible
- No PEV progress displays visible
- Experience is comparable to Claude Code's minimalist design
- Migration progress shown as agent messages (not separate UI elements)

**Success Criteria**: Agent interface displays no pipeline or PEV indicators, maintaining a clean conversational experience (SC-002).

### Scenario 3: Simplified Scanning and Discovery (US3)

**Objective**: Verify scanning retrieves ADO organization data without automatic wave assignment.

**Steps**:
1. Navigate to the Discovery tab
2. Click "Scan" button
3. Wait for scan completion
4. Observe the results

**Expected Outcome**:
- Scan retrieves all repository and pipeline information from configured ADO organizations
- Results appear in a unified discovery tab
- No automatic wave assignments are made
- Scan completes within 2 minutes for organizations with up to 500 repositories (SC-003)
- Manual refresh button is available

**API Validation**:
```bash
curl -X POST http://localhost:8000/v1/discovery/scan \
  -H "Content-Type: application/json" \
  -d '{"organizations": ["org1"], "force_refresh": false}'

curl http://localhost:8000/v1/discovery/results
```

**Expected Outcome**: Scan initiates successfully, results are retrievable, and no wave assignment occurs.

### Scenario 4: On-Demand Migration with Dependencies (US4)

**Objective**: Verify on-demand migration migrates a repository and its full transitive dependencies.

**Steps**:
1. Navigate to the Migrate tab
2. Select a repository with dependencies
3. Click "Migrate" button
4. Complete the pre-migration form
5. Confirm migration
6. Observe the migration progress

**Expected Outcome**:
- System migrates the repository and its full transitive dependencies
- No wave assignment is required
- Migration completes within 5 minutes for repos with up to 20 dependencies (SC-004)
- All dependent repositories are migrated as part of the same operation
- Progress shown as agent messages

**API Validation**:
```bash
# Generate pre-migration form
curl -X POST http://localhost:8000/v1/migration/pre-migration-form \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "repo1", "organization_id": "org1"}'

# Submit form
curl -X PUT http://localhost:8000/v1/migration/pre-migration-form/{form_id} \
  -H "Content-Type: application/json" \
  -d '{"target_github_org": "my-org", "team_mapping": {...}, "pipeline_config": {...}}'

# Initiate migration
curl -X POST http://localhost:8000/v1/migration/repo \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "repo1", "organization_id": "org1", "pre_migration_form_id": "...", "dry_run": false}'
```

**Expected Outcome**: Form generated successfully, form validated, migration initiated and completed with all dependencies.

### Scenario 5: Bulk Migration with Custom Waves (US5)

**Objective**: Verify custom-named migration waves process repositories in dependency order.

**Steps**:
1. Navigate to the Migrate tab
2. Select multiple repositories
3. Assign to a custom-named wave (e.g., "Q3 Production Migration")
4. Click "Execute Wave"
5. Observe the wave execution

**Expected Outcome**:
- Wave is created with the specified name
- Repositories are associated with the wave
- Wave executes sequentially (one wave at a time)
- Repositories are migrated in correct dependency order according to the topological graph
- Dependencies that are repositories are validated and included in the wave
- Wave name is preserved for tracking

**API Validation**:
```bash
# Create wave
curl -X POST http://localhost:8000/v1/migration/wave \
  -H "Content-Type: application/json" \
  -d '{"name": "Q3 Production Migration", "description": "...", "repository_ids": ["repo1", "repo2"], "organization_id": "org1"}'

# Execute wave
curl -X POST http://localhost:8000/v1/migration/wave/{wave_id}/execute \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'

# Check wave status
curl http://localhost:8000/v1/migration/wave/{wave_id}
```

**Expected Outcome**: Wave created successfully, wave executes sequentially, repositories migrated in correct dependency order with 100% accuracy (SC-005).

### Scenario 6: Intelligent Dependency Graph with Pre-Migration Form (US6)

**Objective**: Verify dependency graph analysis generates correct pre-migration forms.

**Steps**:
1. Select a repository with complex dependencies
2. Initiate migration
3. Observe the pre-migration form
4. Complete required fields
5. Observe migration execution

**Expected Outcome**:
- System analyzes the full transitive dependency graph
- Form displays all required specifications based on dependencies (target org, team mappings, pipeline configs)
- Required fields: target GitHub organization, team/permission mapping, pipeline configuration
- Optional fields: repo description, topics, labels
- LLM uses the form data to execute migration with correct configuration
- Pre-migration forms correctly identify all required specifications for complex dependency graphs in 95% of cases (SC-006)

**API Validation**:
```bash
# Generate form for complex repo
curl -X POST http://localhost:8000/v1/migration/pre-migration-form \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "complex-repo", "organization_id": "org1"}'
```

**Expected Outcome**: Form includes dependency graph visualization and all required/optional fields based on dependency analysis.

## Edge Case Validation

### Edge Case 1: Partial Scan Failure

**Steps**:
1. Configure credentials for multiple ADO organizations
2. Invalidate credentials for one organization
3. Initiate scan
4. Observe results

**Expected Outcome**: Scan succeeds for valid organizations, fails for invalid organization with clear error message. Results from successful organizations are persisted.

### Edge Case 2: Circular Dependencies

**Steps**:
1. Select a repository with circular dependencies
2. Initiate migration
3. Observe the dependency graph analysis

**Expected Outcome**: Circular dependencies are detected and reported with clear error message. Migration is blocked until circular dependencies are resolved.

### Edge Case 3: Concurrent Migration Attempts

**Steps**:
1. Initiate migration for a repository
2. Attempt to migrate the same repository in another wave
3. Observe the response

**Expected Outcome**: Second attempt is rejected with 409 Conflict error indicating the repository is already in progress.

### Edge Case 4: Large Dependency Graph

**Steps**:
1. Select a repository with >100 dependencies
2. Initiate migration
3. Observe the dependency graph visualization

**Expected Outcome**: Dependency graph is displayed with appropriate visualization for large graphs (e.g., collapsed view, pagination, or filtering). System handles organizations with up to 1000 repositories without performance degradation.

## Performance Validation

### Local Stack Startup

**Steps**:
1. Stop all services
2. Run `docker compose up --build`
3. Measure time to all services ready

**Expected Outcome**: Local development stack starts within 30 seconds on a typical developer machine (SC-008).

### Scan Performance

**Steps**:
1. Configure an ADO organization with 500 repositories
2. Initiate scan
3. Measure scan completion time

**Expected Outcome**: Scanning retrieves data within 2 minutes for organizations with up to 500 repositories (SC-003).

### Migration Performance

**Steps**:
1. Select a repository with 20 dependencies
2. Initiate migration
3. Measure migration completion time

**Expected Outcome**: On-demand migration completes within 5 minutes for repos with up to 20 dependencies (SC-004).

## Security Validation

### Dry-Run Capability

**Steps**:
1. Initiate migration with `dry_run: true`
2. Observe the results
3. Verify no actual migration occurs

**Expected Outcome**: Dry-run completes successfully, shows what would be migrated, and no actual changes are made (constitution principle V).

### Audit Trail

**Steps**:
1. Perform various operations (scan, migrate, wave execution)
2. Query audit logs
3. Verify all operations are logged

**Expected Outcome**: All migration operations are auditable with state DB, structured logs, or reports (constitution principle V).

### Secret Protection

**Steps**:
1. Perform scan and migration operations
2. Check logs and API responses
3. Verify no ADO credentials appear

**Expected Outcome**: Secrets do not appear in logs, reports, or persisted artifacts (constitution principle III).

## Cleanup

```bash
docker compose down
```

**Expected Outcome**: All services stop cleanly, no data loss for persisted scan results.
