# Contract: Pipeline Workflow Branch Migration

**Components**: `ado2gh.tools.push_workflows`, `PipelinesScopeHandler`, `ado_cleanup`, Accelerator API

## Hard requirements

1. **All repositories** in scope MUST receive ADO pipeline → GitHub Actions conversion.
2. Generated workflows MUST be committed to a **dedicated migration branch** (not default).
3. Default branch name: `ado2gh/migrated-workflows` (override via profile `workflow_branch`).
4. Migration order MUST follow **topological sort** of `RepoDependencyEdge` when edges exist.
5. **Target dependencies** (secrets, environments, OIDC, registries) MUST be verified before live push; workflows MUST be runnable out-of-the-box when checklist is green.
6. After live branch push, **ADO pipelines MUST be disabled/removed** for the migrated scope.
7. **Workflow layout** MUST be `consolidated` or `modular` per readiness rules (see below).

## GitHub dependency readiness (pre-push gate)

`GET /v1/profiles/{profile_id}/workflow-readiness?repos=...`

**Response** (per repo):

```json
{
  "repo": "Payments/api-gateway",
  "ready": true,
  "checks": [
    { "id": "secrets_mapped", "passed": true },
    { "id": "environments_exist", "passed": true },
    { "id": "oidc_configured", "passed": true },
    { "id": "package_feeds", "passed": true }
  ],
  "blockers": []
}
```

Live `push_workflows` returns `403` when `ready=false` and `blockers` non-empty.

## Workflow layout policy

Profile setting: `workflow_layout_policy` = `auto` (default)

| Readiness | Layout emitted |
|-----------|----------------|
| `auto` + simple YAML | **consolidated** — one primary `.github/workflows/ci.yml` (or per trigger) |
| `auto` + assisted/manual or multiple pipelines | **modular** — entry workflows + `reusable/*.yml` via `workflow_call` |

**Consolidated rules**:
- Single file MUST stay under GitHub size limits; if exceeded, auto-fallback to modular with plan note.

**Modular rules** (GitHub best practices):
- Entry workflows only define `on`, `concurrency`, and `jobs` that `uses:` reusable workflows.
- Shared build/test/deploy in reusable workflows with explicit `inputs` / `secrets: inherit`.
- Pinned action versions; no duplicated step blocks across files.

## Push workflows job

`POST /v1/jobs`

```json
{
  "job_type": "push_workflows",
  "payload": {
    "config_path": "migration.yaml",
    "profile_id": "prof_01",
    "assignment_id": "asgn_01",
    "branch": "ado2gh/migrated-workflows",
    "repo_order": ["Platform/common-lib", "Payments/api-gateway"],
    "layout_policy": "auto",
    "disable_ado_pipelines": true,
    "dry_run": false,
    "open_pr": true
  }
}
```

**Response**: `JobRecord` with `result` containing per-repo `branch`, `pr_url`, `workflow_files`, `layout`, `ado_pipelines_disabled`.

## Post-push sequence (live)

1. Push workflow YAML to migration branch (+ optional remove legacy `azure-pipelines.yml` on that branch when policy enabled).
2. Open PR to default branch (review gate).
3. **Disable ADO pipelines** for repo (API disable, not silent delete unless policy says remove).
4. Record audit events: `workflow_push`, `ado_pipeline_disabled`.

## Preconditions

| Check | Failure |
|-------|---------|
| Git mirror completed on GitHub (default branch has commits) | Job fails repo; message "empty destination" |
| Workflow dependency readiness green | `403 not_ready` with checklist |
| Live execution approved by Approver when `dry_run=false` | `403 awaiting_approval` |
| Repo not in another active live run | `409 repo_locked` |
| Dependencies migrated earlier in `repo_order` | Executor skips until upstream `MigrationRun` completed |

## Validator checks (`pipelines` scope)

| Check | Pass criteria |
|-------|----------------|
| Workflows generated locally | Count ≥ 1 `.yml` in output dir per repo |
| Layout policy | consolidated or modular structure matches plan |
| Branch exists on GitHub | Branch ref resolves |
| Workflow files on branch | `.github/workflows/*.yml` present on migration branch |
| Dependency refs | Secrets/env names exist on GitHub (no orphan `${{ secrets.X }}`) |
| Runnable | `workflow_dispatch` or policy-approved smoke succeeds when readiness green |
| ADO pipelines | Disabled/removed per scope after live push |
| PR state | `open` or `merged` per policy (configurable) |
| Dependency order | No consumer migrated before dependency in same run |

## Dry-run behavior

- Transform pipelines to local `output/workflows/{org}/{repo}/.github/workflows/`
- Log intended branch name, layout policy, PR title, and ADO disable plan
- No GitHub or ADO mutations
