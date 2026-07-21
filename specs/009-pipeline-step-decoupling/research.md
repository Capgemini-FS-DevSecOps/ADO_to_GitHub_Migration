# Phase 0 Research: Pipeline Step Decoupling & Dependency Resolution

**Date**: 2026-06-24 | **Status**: Complete

## Research Tasks

### R-001: Step prerequisite checking pattern for flexible execution order

**Decision**: Implement a `StepPrerequisiteChecker` class in `ado2gh/api/step_prerequisites.py` that maps each step ID to a list of prerequisite step IDs. Before executing a step, the runner calls `checker.check(run, step_id)` which returns `(ok: bool, missing: list[str])`. If prerequisites are not met, the step fails with a message naming the missing steps.

**Rationale**: The existing `_execute` method in `pipeline_runner.py` already iterates steps in order and skips completed/warn steps. Adding a prerequisite checker as a separate module keeps the runner focused on orchestration while the checker encodes dependency knowledge. This aligns with FR-009 and FR-026 (flexible order with prerequisites).

**Alternatives considered**:
- DAG-based topological sort: Rejected as over-engineered for 11 steps with a known fixed dependency graph. A simple lookup table is sufficient and more readable.
- Inline prerequisite checks in each handler: Rejected because it scatters dependency knowledge across handlers and makes it harder to audit the full prerequisite graph.

### R-002: Per-repo lock implementation for concurrent run prevention

**Decision**: Implement a `RepoLockManager` class in `ado2gh/api/repo_lock.py` using an in-memory `dict[str, RepoLock]` keyed by `repo_id`. Locks are acquired before step execution and released in a `finally` block. A `RepoLock` dataclass stores `repo_id`, `run_id`, and `acquired_at` timestamp. If a lock is already held by a different run, `acquire()` raises `RepoLockedException`.

**Rationale**: The pipeline runner is single-process (threaded but not multi-process), so an in-memory dict is sufficient. If the system later scales to multi-process, the lock table can be moved to SQLite without changing the interface. This aligns with FR-031.

**Alternatives considered**:
- SQLite-based locks: Rejected for now as the runner is single-process; adds I/O overhead unnecessarily. Interface is designed to swap to SQLite later.
- File-based locks: Rejected as fragile across Docker/container boundaries.

### R-003: actionlint integration and fallback validation

**Decision**: Create `ado2gh/pipelines/validation/workflow_validator.py` with a `WorkflowValidator` class. It first checks if `actionlint` is on PATH via `shutil.which("actionlint")`. If found, it runs `actionlint` as a subprocess on each generated workflow file and parses the output. If not found, it falls back to: (1) YAML parse via `yaml.safe_load()`, (2) structural checks: `jobs` key exists and is a dict, each job has `runs-on` and `steps`, (3) secret reference scan: all `${{ secrets.* }}` references are collected for reporting. The step message notes which validation mode was used.

**Rationale**: `actionlint` is the de facto standard for GitHub Actions workflow validation but may not be installed in all environments. The fallback ensures the pipeline still provides value without it. This aligns with FR-022.

**Alternatives considered**:
- Require actionlint as a dependency: Rejected because it's a Go binary, not a pip package, and may not be available in all deployment environments.
- Use only YAML parse: Rejected as too weak — misses common issues like invalid job dependencies, missing `runs-on`, etc.

### R-004: OIDC federated credential auto-provisioning

**Decision**: Create `ado2gh/api/oidc_provisioner.py` with an `OIDCProvisioner` class. For Azure RM, Kubernetes Service, and ACR service connection types, it: (1) creates a repo-level GitHub OIDC federated credential via the GitHub API (`POST /repos/{owner}/{repo}/actions/oidc/custom-subjects` — repo-level for per-repo scoping, not org-level), (2) creates corresponding GitHub repo secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` for Azure RM; `KUBE_CONFIG` for K8s; `ACR_*` for ACR) via `PUT /repos/{owner}/{repo}/actions/secrets/{secret_name}`. If provisioning fails, it returns a `ProvisioningResult` with `success=False` and `failure_reason`, which the caller surfaces in the Resolve Dependencies flow. Operators can pre-configure mappings in the profile to skip auto-provisioning entirely.

**Rationale**: The existing `ServiceConnectionManifest` already documents OIDC setup instructions per SC type but doesn't execute them. The provisioner automates this. The GitHub API for OIDC federated credentials is well-documented and PyGithub supports the underlying REST calls. This aligns with FR-017, FR-018, FR-020, FR-030.

**Alternatives considered**:
- Azure CLI-based provisioning: Rejected as it requires `az` CLI installed and authenticated, adding another external dependency.
- Manual-only with enhanced manifest: Rejected as it violates the zero-manual-GitHub-changes hard requirement.

### R-005: Repository feasibility analysis thresholds and strategy selection

**Decision**: Extend `GitScopeHandler.migrate()` in `git_scope.py` to call a new `_analyze_feasibility()` method before migration. This method queries ADO repo stats (already available via `ctx.ado.get_repo_stats()`) and checks against GitHub GEI limits: warn at repo > 2GB, fail-soft at > 10GB, warn at any file > 100MB, flag LFS > 2GB. Returns a `FeasibilityReport` dict stored in step result data. Strategy selection: `mirror` for repos < 2GB with no LFS issues, `gei` for repos 2-10GB or with LFS < 2GB, `manual` for repos > 10GB or LFS > 2GB.

**Rationale**: The existing `git_scope.py` already retrieves `repo_stats` including `branch_count` and `size_kb`. The ADO API provides file-level size info via the Git API. GitHub's GEI documentation specifies these limits explicitly. This aligns with FR-004, FR-005, FR-027.

**Alternatives considered**:
- Configurable thresholds: Rejected for now as GitHub's limits are platform-determined, not user-preference. Can be added later if needed.
- Pre-migration dry-run of GEI tool: Rejected as GEI doesn't have a "check-only" mode that reports feasibility without starting the migration.

### R-006: Workflow auto-commit with conflict handling

**Decision**: In the Convert Pipelines step handler, after validation passes, commit workflow files to the target GitHub repo using the GitHub API (`PUT /repos/{owner}/{repo}/contents/.github/workflows/{filename}`). Before committing, check if a file with the same name exists via `GET` request. If it exists, append a `-migrated` suffix to the filename (e.g., `build-migrated.yml`). Store the commit SHA in `ConversionResult.commit_sha`.

**Rationale**: PyGithub's `Repository.create_file()` method handles the API call. The versioned suffix approach preserves existing workflows while ensuring the new one is committed. This aligns with FR-023, FR-029.

**Alternatives considered**:
- Overwrite existing files: Rejected as it could destroy manually-created workflows.
- Create a PR instead: Rejected as it requires manual merge, violating zero-manual-changes.
- Use git push to a branch: Rejected as it's more complex than the Contents API and requires a local clone.

### R-007: Operator resolution persistence per-profile

**Decision**: Store operator resolutions in the `SettingsStore` under a `operator_resolutions` key per profile. The `SettingsStore` already manages per-profile settings (advanced config, credentials). Add methods `get_operator_resolutions(profile_id) -> dict[str, str]` and `set_operator_resolutions(profile_id, resolutions: dict[str, str])`. The `apply_operator_secret_mappings()` function in `migration_work_plan.py` is updated to load from the profile store instead of accepting a parameter.

**Rationale**: The SettingsStore is the existing per-profile persistence layer. Adding operator resolutions here avoids introducing a new storage mechanism and naturally scopes resolutions to the profile. This aligns with FR-019, FR-020.

**Alternatives considered**:
- Separate SQLite table: Rejected as it adds a new table for data that fits naturally in the profile settings.
- JSON file per profile: Rejected as it introduces file I/O outside the existing storage abstraction.

### R-008: Extended validation for committed workflow integrity

**Decision**: Extend `PostMigrationValidator._validate_one()` in `post_migration_validator.py` to add a new check: `workflow_integrity`. This check reads the `convert_pipelines` step result from the `PipelineRun` to get expected workflow file paths and commit SHAs, then verifies the files exist in the GitHub repo via the Contents API and the commit SHA matches. The check result is added to the `checks` list with verdict PASS/FAIL/WARN.

**Rationale**: The validator already has access to `self.gh` (GitHub client) and runs per-repo. Reading the convert step result from the run requires passing the run object or its step results to the validator. The check is naturally per-repo since workflows are committed per-repo. This aligns with FR-024.

**Alternatives considered**:
- Separate validation step: Rejected as it adds pipeline complexity; extending the existing validator is simpler.
- Check during Convert step: Rejected as the commit may take time to propagate; validation after all steps is more reliable.

### R-009: Continue-on-error batch handling

**Decision**: Modify the `_migrate_scoped` method in `pipeline_runner.py` to catch per-repo exceptions in the batch executor callback. Instead of letting the first failure propagate, mark the failed repo's result as `failed` and continue. After the batch completes, aggregate succeeded/failed counts. If any repo failed, set step status to `WARN` (not `FAILED`) with a summary message. The existing `BatchExecutor.execute_wave()` already returns per-repo results, so the change is in how failures are handled post-execution.

**Rationale**: The current code sets `StepStatus.FAILED` if any repo fails, which halts the pipeline. Changing to `WARN` with continue-on-error aligns with FR-028 and allows the operator to see all results in one run rather than fixing one repo at a time.

**Alternatives considered**:
- Configurable failure threshold: Rejected as over-engineered for now; can be added later if needed.
- Halt on first failure: Rejected per user clarification #12 (continue-on-error).
