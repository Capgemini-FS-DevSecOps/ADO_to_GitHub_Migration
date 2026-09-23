# Quickstart: Agentic Migration Platform (validation scenarios)

**Feature**: `001-agentic-migration-platform`  
**Prerequisites**: Python 3.9+, `git`, profile with ADO/GH credentials, optional Docker for API services.

See [data-model.md](./data-model.md) and [contracts/](./contracts/) for entity and API shapes.

## 1. Environment

```bash
pip install -e ".[dev,api]"
export ADO_PAT=...
export ADO_ORG_URL=https://dev.azure.com/YOUR_ORG
export GH_TOKEN=...
# Local state (default)
export ADO2GH_STORAGE_BACKEND=sqlite
export ADO2GH_SQLITE_PATH=./migration_state.db
```

**Cloud / Docker prod**:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
# Sets ADO2GH_STORAGE_BACKEND=postgres and Postgres service
```

## 2. Manual accelerator (no agent)

```bash
ado2gh discover --config migration.yaml
ado2gh pipelines inventory --config migration.yaml
ado2gh phase assign --config migration.yaml --output migration_phase.yaml
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
```

**Expected**: Dry-run completes; no live GitHub mutations; state in `migration_state.db`.

## 3. Pipeline conversion + workflow branch

```bash
ado2gh run --wave 1 --config migration.yaml --scopes pipelines --dry-run
```

Generate and push workflows (after git mirror on default branch):

```bash
python -m ado2gh.tools.push_workflows \
  --workflows-dir output/workflows \
  --config migration_phase.yaml \
  --branch ado2gh/migrated-workflows \
  --dry-run
```

**Expected (dry-run)**: Lists workflow files per repo; no branch/PR created.

**Expected (live, after Approver)**: Branch `ado2gh/migrated-workflows` with maintainable workflow layout; open PR; ADO pipelines disabled for repo; `workflow_dispatch` succeeds when readiness checklist is green.

## 3b. Workflow dependency readiness

```bash
# Future: ado2gh workflow-readiness --config migration.yaml --profile prof_01
```

**Expected**: Per-repo checklist for secrets, environments, OIDC, feeds; blockers listed in **console and run logs** before live push.

## 3c. Layout policy

Simple repo → consolidated single workflow; complex/multi-pipeline repo → modular reusable workflows under `.github/workflows/reusable/`.

**Expected**: Generated tree matches readiness classification; no duplicate step blocks across modular files.

## 3d. Agent provision missing secret

1. Start agent session with repo missing a mapped secret.
2. Agent asks to create secret in GitHub.
3. Confirm **yes** via secure modal (not chat text).
4. **Expected**: Secret created, readiness re-run green, audit event recorded.

## 4. Dependency topological order

```bash
ado2gh discover --config migration.yaml
# Future CLI: ado2gh dependency-graph --config migration.yaml
```

**Expected**: `sorted_repos` lists dependencies before consumers; cycles reported if present.

Validate migration runs repos in that order (check run logs / state DB `repo_order` on plan).

## 5. Assignment cohort

Via UI (`apps/migration-ui`) or API:

1. Coordinator creates Wave 2 assignment with repo list.
2. Operator filters agent/accelerator to that assignment.
3. Run dry-run migration scoped to cohort only.

**Expected**: Repos outside cohort unchanged; run record includes `assignment_id`.

## 6. Agent PEV loop (services)

```bash
# Terminal 1
uvicorn services.accelerator_api.main:app --port 8080

# Terminal 2
export ACCELERATOR_URL=http://localhost:8080
uvicorn services.agent.main:app --port 8090
```

```bash
curl -X POST http://localhost:8090/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"config_path":"migration.yaml","phase":"poc","dry_run":true}'
```

**Expected**: Status progresses planning → executing → validating → completed; steps include plan, readiness, migrate, validate.

## 7. Approver gate (live execution)

1. Operator requests live run (`dry_run: false`).
2. **Expected**: Status `awaiting_approval` until Approver calls approve endpoint.
3. After approval: executor runs including `push_workflows` when in scope.

## 8. Assignment phase gate (FR-034)

Prerequisite: Wave assignment linked to execution phase (e.g. pilot) with gate thresholds configured.

```bash
curl http://localhost:8080/v1/assignments/asgn_wave2/gate-status
```

**Expected**: `status` reflects cohort repo/pipeline success vs phase thresholds; `can_advance: false` when below threshold.

Attempt live migrate without override:

```bash
curl -X POST http://localhost:8080/v1/migrate \
  -H "Content-Type: application/json" \
  -d '{"assignment_id":"asgn_wave2","dry_run":false}'
```

**Expected**: `409` with gate failures; no mutations.

Approver override (Approver token):

```bash
curl -X POST http://localhost:8080/v1/assignments/asgn_wave2/gate-override \
  -H "Content-Type: application/json" \
  -d '{"reason":"Executive sign-off for Wave 2"}'
```

**Expected**: `status: override`, `can_advance: true`, audit event recorded.

## 9. Rollback dry-run (FR-026)

```bash
ado2gh rollback --wave 2 --scopes pipelines,branch_policies --dry-run
```

Or via API:

```bash
curl -X POST http://localhost:8080/v1/rollback \
  -H "Content-Type: application/json" \
  -d '{"assignment_id":"asgn_wave2","repos":["Payments/api-gateway"],"scopes":["pipelines"],"dry_run":true}'
```

**Expected**: Preview actions listed; no GitHub mutations; no audit execute event.

Live rollback requires Approver approval (mirror §7 flow via `/v1/rollback/request` + `/approve`).

For live rollback with `--scopes pipelines` on repos where ADO pipelines were disabled after migration:

**Expected**: GitHub workflows on migration branch rolled back **and** matching ADO pipelines re-enabled (FR-026a); audit event records both; validator can confirm ADO enabled state.

## 10. Validation

```bash
ado2gh validate --config migration_phase.yaml
```

**Expected**: Per-repo SHA match; pipeline scope checks workflow branch/files when enabled.

## 11. UI smoke (OrchestrateAI)

```bash
cd apps/migration-ui && npm install && npm run dev
```

Visit `/migrate`, `/agent`, assignment views.

**Expected**: OAI theme; assignment filters; agent role labels on messages.

## 12. Tests and coverage

```bash
pytest tests/ --cov=ado2gh --cov-report=term-missing --cov-fail-under=85
```

**Expected**: CI gate passes at ≥85% line coverage on `ado2gh`.
