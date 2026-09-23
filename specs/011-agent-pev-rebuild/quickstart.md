# Quickstart: Agent PEV Architecture Rebuild

**Feature**: 011-agent-pev-rebuild
**Date**: 2026-06-24

## Prerequisites

- Python 3.9+ with `pip install -e ".[api,dev]"` completed
- Node.js 18+ with `cd apps/migration-ui && npm install` completed
- Docker (for full-stack local dev) or local Python + Node dev servers
- At least one LLM model configured in Settings → LLM Models
- A deployment profile configured (lightweight or full)
- Spec 010 (Enterprise Audit Simplification) completed — `session_orchestrator.py` decomposed into modules under 800 lines

## Setup

```bash
# 1. Start the full stack (SQLite backend by default)
docker compose up --build

# OR run services individually:
# Terminal 1 — Accelerator API
uvicorn services.accelerator_api.main:app --port 8080 --reload

# Terminal 2 — Agent backend
uvicorn services.agent.main:app --port 8090 --reload

# Terminal 3 — Migration UI
cd apps/migration-ui && npm run dev
```

## Validation Scenarios

### Scenario 1: Hands-Off Single Repo Migration (SC-001)

**Prerequisites**: Discovery data loaded for profile, LLM configured.

1. Open `http://localhost:3000` and navigate to the Agent tab
2. Type: `migrate Project/RepoName to GitHub`
3. Expected: Orchestrator classifies intent as migration action, invokes PEV chain
4. Verify: Session status transitions through thinking → planning → executing → validating → completed
5. Verify: Only orchestrator user-facing messages are visible (no inter-agent messages)
6. Verify: Migration plan includes all resource types (repos, pipelines, secrets, service connections, Boards, Test Plans, Artifacts, Wiki)

**Pass criteria**: Migration completes without intermediate user interaction for repos with complete discovery data.

### Scenario 2: Continuous PEV Loop with Retry (SC-002)

**Prerequisites**: A repo with a deliberately broken pipeline (invalid YAML).

1. Type: `migrate Project/BrokenRepo to GitHub`
2. Expected: Executor attempts migration, Validator detects YAML syntax error
3. Verify: Validator sends structured feedback to Planner, Planner creates revised plan
4. Verify: PEV cycle retries up to 3 times, then escalates to user with failure summary
5. Verify: `pev_retry_count` increments on each retry

**Pass criteria**: Loop continues autonomously through PEV cycles until complete or retry limit reached.

### Scenario 3: Session Persistence and Resume (SC-013)

**Prerequisites**: A migration in progress.

1. Start a migration: `migrate Project/RepoName to GitHub`
2. While status is `executing`, kill the agent backend (Ctrl+C or `docker stop agent`)
3. Restart the agent backend
4. Open the same session from the sidebar
5. Verify: Session resumes from the last persisted PEV cycle state
6. Verify: No data loss — all messages, plans, and cycle state are intact

**Pass criteria**: Session resumes mid-migration from the last checkpoint.

### Scenario 4: Concurrent Session Repo Lock (SC-020)

**Prerequisites**: Two browser windows, same profile.

1. Window 1: Type `migrate Project/RepoName to GitHub` (live mode)
2. Window 2: Create a new session, type `migrate Project/RepoName to GitHub`
3. Verify: Window 2 receives a message that the repo is locked by another session
4. Verify: No concurrent write operations occur on the same repo

**Pass criteria**: Second session is informed of the lock, no concurrent writes.

### Scenario 5: Batch Migration (SC-015)

**Prerequisites**: 50+ repos in discovery data with phase assignments.

1. Type: `migrate all repos in wave 1`
2. Verify: Planner creates a migration queue with topological ordering
3. Verify: Executor processes repos sequentially from the queue
4. Verify: Validator validates each repo before the next begins
5. Verify: LLM context window stays within model limits (sliding window)

**Pass criteria**: All repos processed sequentially through PEV loop with no manual per-repo invocation.

### Scenario 6: State Machine Enforcement (SC-018)

1. Attempt to transition a session from `completed` to `executing`
2. Verify: Backend rejects the transition with an error
3. Attempt to transition from `idle` directly to `validating`
4. Verify: Backend rejects the transition

**Pass criteria**: All invalid state transitions are rejected.

### Scenario 7: LLM Timeout (SC-019)

1. Configure LLM with an artificially slow endpoint (e.g., mock that sleeps 70s)
2. Start a migration
3. Verify: First call times out at 60s, system retries once
4. Verify: Second timeout → session pauses with user-facing message
5. Verify: Session status is `awaiting_input`

**Pass criteria**: Timeout protocol triggers correctly, session pauses gracefully.

### Scenario 8: Cancellation with Rollback (SC-031, SC-032)

1. Start a live migration: `migrate Project/RepoName to GitHub` (live mode, approved)
2. While executing, type: `cancel`
3. Verify: Orchestrator presents two options: rollback or stop
4. Choose rollback
5. Verify: All GitHub resources created during the session are deleted
6. Verify: Pre-existing resources are preserved
7. Verify: Rollback operations are logged for audit

**Pass criteria**: Rollback deletes only session-created resources, pre-existing resources preserved.

### Scenario 9: ADO Boards → GitHub Issues (SC-027)

1. Type: `migrate Project/RepoName with boards to GitHub`
2. Verify: Planner includes Boards work items in the plan
3. Verify: Executor exports ADO work items to CSV with fixed field mapping
4. Verify: GitHub Issues are created with correct titles, labels, and assignees
5. Verify: Area Path → `area:{value}` label, Iteration Path → `iteration:{value}` label

**Pass criteria**: Work items migrated to GitHub Issues with correct field mapping.

### Scenario 10: Metrics and Health (SC-021)

1. Start a few migrations
2. Query `http://localhost:8090/metrics`
3. Verify: Prometheus-compatible text format with all expected metrics
4. Query `http://localhost:8090/health`
5. Verify: JSON response with LLM availability, session count, storage connectivity

**Pass criteria**: Both endpoints return correctly formatted responses.

### Scenario 11: ADO Service Connections → GitHub Secrets/Environments (SC-033)

**Prerequisites**: A project with both simple credential-based and deployment-scoped service connections.

1. Type: `migrate Project/RepoName with service connections to GitHub`
2. Verify: Planner includes service connection mapping in the plan (simple → secrets, deployment-scoped → environments)
3. Verify: Executor creates GitHub secrets for simple credential connections
4. Verify: Executor creates GitHub environments with protection rules for deployment-scoped connections
5. Verify: Rollback tracker records both secrets and environments as eligible for rollback

**Pass criteria**: Service connections migrated to correct GitHub targets based on connection type.

### Scenario 12: Idempotency — Already-Migrated Repo Detection (SC-034)

**Prerequisites**: A repo that was already migrated to GitHub in a previous session.

1. Type: `migrate Project/AlreadyMigratedRepo to GitHub`
2. Verify: Executor detects the repo was already migrated via API checks
3. Verify: Orchestrator prompts user with three options: overwrite, skip, or abort
4. Choose `skip`
5. Verify: Validator confirms existing GitHub state is valid
6. Verify: No duplicate resources are created

**Pass criteria**: Already-migrated repos are detected and user is prompted with overwrite/skip/abort options.

### Scenario 13: Local-Only Workflow Validation (SC-035)

**Prerequisites**: A migration with at least one workflow file.

1. Start a migration: `migrate Project/RepoName to GitHub`
2. Wait for validator phase
3. Verify: Validator checks YAML syntax locally
4. Verify: Validator runs local workflow simulation (e.g., `act`) if available
5. Verify: No workflow dispatch events are sent to GitHub (check GitHub Actions tab — no new runs triggered)
6. If `act` is unavailable, verify: Validator notes reduced validation depth in result

**Pass criteria**: Workflows are validated locally only, no GitHub dispatch events.

## Test Commands

```bash
# Unit tests for new modules
pytest tests/unit/test_011_state_machine.py -v
pytest tests/unit/test_011_repo_lock_store.py -v
pytest tests/unit/test_011_context_window.py -v
pytest tests/unit/test_011_resource_mapping.py -v
pytest tests/unit/test_011_metrics.py -v
pytest tests/unit/test_011_rollback_tracker.py -v
pytest tests/unit/test_011_orchestrator.py -v
pytest tests/unit/test_011_guardrails.py -v
pytest tests/unit/test_011_executor.py -v
pytest tests/unit/test_011_validator.py -v

# Contract tests
pytest tests/contract/test_011_agent_pev_contracts.py -v

# Integration tests
pytest tests/integration/test_011_session_persistence.py -v
pytest tests/integration/test_011_pev_loop.py -v
pytest tests/integration/test_011_rollback.py -v

# Full test suite with coverage
pytest tests/ --cov=ado2gh.agents --cov-report=term-missing --cov-fail-under=85
```

## References

- [Feature Spec](../spec.md)
- [Data Model](../data-model.md)
- [API Contracts](../contracts/api-contracts.md)
- [Research](../research.md)
- [Implementation Plan](../plan.md)
