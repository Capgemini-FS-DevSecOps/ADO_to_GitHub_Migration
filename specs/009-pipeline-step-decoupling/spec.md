# Feature Specification: Pipeline Step Decoupling & Dependency Resolution

**Feature Branch**: `009-pipeline-step-decoupling`

**Created**: 2026-06-24

**Status**: Draft

**Input**: User description: "The scope of the migrate pipeline is overlapping with each step. The Analyze Dependencies step should understand all the dependencies that a repo/list of repos needs (service connections/secrets/repos/etc.). Migrate repository contents should analyze the repository size/lfs files/metadata and determine if the GEI tool can migrate all the contents successfully. Convert pipelines to github actions should actually run the script to convert the pipelines to github actions and use a internal script to validate if the github action pipeline is valid or not. Map secrets and service connections - this should probably be combined with analyze dependencies. We need to ensure all steps in the azure pipeline are its own entity and if a step depends on another (like needs a dependency) that it has that data in the pipeline. There also needs to be a separate flow for users to provide the missing service connection/secret etc both for the Agent and if a user wants to migrate manually."

**Clarifications** (resolved 2026-06-24):
1. Operator resolutions persist **per-profile** — mappings are stored with the migration profile and reused across all runs for that profile.
2. **Both** `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` are updated to merge map_secrets into analyze_deps.
3. **Hard requirement**: Migration must work end-to-end out of the box with **zero manual GitHub changes**. The tool auto-provisions OIDC federated credentials for Azure RM, Kubernetes Service, and Azure Container Registry connections; operators are prompted only for unreadable secret types (third-party API keys, custom auth).
4. Conversion validation uses **`actionlint`** if installed, falling back to YAML parse + basic structural checks (jobs/steps/secrets refs) if not.
5. Analyze Dependencies uses **existing inventory data**; a "refresh inventory" option in the UI re-runs the scan step before re-running analyze_deps.
6. Convert Pipelines **auto-commits** generated GitHub Actions workflow files to the target GitHub repo's `.github/workflows/` directory in live mode; generates locally only in dry-run mode.
7. Auto-provision via OIDC for **Azure RM, Kubernetes Service, and Azure Container Registry** service connection types. Operators can **override** any auto-provisioned value by pre-configuring mappings in the profile (e.g., if GitHub secrets or OIDC credentials are already set up).
8. Validate step is **extended** to verify both SHA/branch parity AND that committed GitHub Actions workflows exist and match the converted output from the Convert step.
9. **All steps** in both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` are decoupled as self-contained entities — not just the overlapping ones.
10. **Flexible execution order** with prerequisite checks — steps can run in any order as long as prerequisite steps are completed; the pipeline defines a default/recommended order but does not enforce strict sequencing.
11. Feasibility thresholds use **GitHub documented GEI limits**: warn at repo > 2GB, fail-soft at repo > 10GB; warn at any file > 100MB, flag LFS for files > 2GB.
12. Mid-batch failure uses **continue-on-error** — a failed repo is marked failed, the batch continues with remaining repos, and the step reports a succeeded/failed summary.
13. Workflow commit conflicts use **overwrite with versioned name** — if a workflow file with the same name exists, the new workflow is written with a suffix, preserving the original.
14. OIDC auto-provisioning failure **falls back to the operator prompt** — the connection is surfaced in the Resolve Dependencies flow with the failure reason.
15. Concurrent runs targeting the same repo use a **per-repo lock** — the first run acquires the lock; a second run targeting the same repo is rejected with a "migration in progress" message.
16. **Cross-spec execution order**: Spec 010 (Enterprise Audit & Framework Simplification) MUST execute before spec 009 because spec 010 decomposes monolithic files (`pipeline_runner.py`, `session_orchestrator.py`, `settings_store.py`) that spec 009 modifies. Spec 009 tasks are updated to modify the decomposed modules after spec 010 completes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unified Dependency Analysis (Priority: P1)

An operator selects a repo (or a phase of repos) and runs the "Analyze Dependencies" step. The step scans all ADO pipelines associated with the target repo(s), extracts every dependency type — service connections, variable groups, repo-to-repo dependencies, environments, self-hosted agents, and task inputs — and presents a consolidated, per-repo dependency report. The former "Map secrets & service connections" step is merged into this step, so the operator sees a single complete picture of what must be resolved before migration can proceed. Warnings are displayed as a bulleted list in the step message, paginated at 10 items. The step status is `warn` when dependencies need operator input, `completed` when all clear.

**Why this priority**: Without a complete dependency picture, downstream steps (migrate, convert) operate on incomplete data and produce broken results. This is the foundation for all subsequent steps.

**Independent Test**: Run analyze_deps on a repo with known service connections and variable groups. Verify the step message lists each dependency as a bullet point, the step result data contains structured `dependencies` per repo, and the step status is `warn` when gaps exist.

**Acceptance Scenarios**:

1. **Given** a repo with 2 service connections and 1 variable group in its ADO pipelines, **When** the operator runs Analyze Dependencies, **Then** the step message displays a bulleted list with each SC and VG name, and the step status is `warn`
2. **Given** a repo with no service connections or variable groups, **When** the operator runs Analyze Dependencies, **Then** the step status is `completed` with no warnings
3. **Given** a repo with 15 service connections, **When** the operator runs Analyze Dependencies, **Then** the step message shows the first 10 as bullets and indicates "… and 5 more" with the full list available in result data
4. **Given** a repo that depends on another repo for build artifacts, **When** the operator runs Analyze Dependencies, **Then** the dependency graph and migration order include both repos and the step message notes the repo-to-repo dependency

---

### User Story 2 - Repository Migration Feasibility Check (Priority: P2)

When the "Migrate Repository Contents" step runs, it first analyzes the target repository's size, LFS files, branch count, tag count, and metadata (PRs, policies, wiki presence). Based on this analysis, it determines whether a standard git mirror, GEI tool transfer, or manual intervention is required. Feasibility uses GitHub's documented GEI limits: warn at repo > 2GB, fail-soft at repo > 10GB, warn at any file > 100MB, and flag LFS for files > 2GB. When a repo exceeds these limits, the step reports a warning with the specific limiting factor. In dry-run mode, the step validates feasibility without performing the actual migration. In live mode, it executes the migration using the determined strategy. When migrating a batch of repos, a failed repo is marked failed and the batch continues with the remaining repos (continue-on-error), with a succeeded/failed summary reported at the end. A per-repo lock prevents concurrent runs from migrating the same repo simultaneously.

**Why this priority**: Migration failures due to oversized repos or unsupported LFS configurations are costly and should be caught before execution. This step must be self-contained and not depend on pipeline conversion or secrets data.

**Independent Test**: Run migrate_repos in dry-run on a repo with known size and LFS configuration. Verify the step message reports the repo size, LFS status, and recommended strategy (mirror/GEI/manual).

**Acceptance Scenarios**:

1. **Given** a repo under 1GB with no LFS files, **When** the operator runs Migrate Repository Contents in dry-run, **Then** the step completes with a message indicating "git mirror" strategy is viable
2. **Given** a repo with an LFS file over 2GB, **When** the operator runs Migrate Repository Contents in dry-run, **Then** the step status is `warn` and the message flags the LFS file for manual handling
3. **Given** a repo between 2GB and 10GB, **When** the operator runs Migrate Repository Contents in dry-run, **Then** the step status is `warn` and the message recommends GEI tool transfer
4. **Given** a repo exceeding 10GB total size, **When** the operator runs Migrate Repository Contents in dry-run, **Then** the step status is `warn` (fail-soft) and the message recommends manual GEI handling
5. **Given** a batch of 5 repos where 1 fails, **When** the operator runs Migrate Repository Contents in live mode, **Then** the batch continues and the step reports 4 succeeded, 1 failed

---

### User Story 3 - Pipeline Conversion with Validation (Priority: P3)

When the "Convert Pipelines → GitHub Actions" step runs, it executes `PipelineTransformer.transform()` (the existing ADO-to-GitHub Actions YAML conversion module in `ado2gh/pipelines/transform/transformer.py`) for each pipeline associated with the target repo(s). After conversion, `WorkflowValidator` (`actionlint` if installed, YAML parse + structural checks otherwise) validates the generated GitHub Actions workflow YAML for syntax correctness, required secrets references, and job/step structure. In **live mode**, the step auto-commits validated workflow files to the target GitHub repo's `.github/workflows/` directory. If a workflow file with the same name already exists, the new workflow is written with a versioned suffix (e.g., `pipeline-migrated.yml`), preserving the original. In **dry-run mode**, files are generated locally only. If validation fails, the step reports which pipelines failed and why. If validation passes, the step reports the number of workflows generated and committed. This step depends on the Analyze Dependencies step having been run (for service connection mapping data), and the step result includes the generated workflow file paths and commit SHAs.

**Why this priority**: Conversion is the core value of the tool but depends on accurate dependency analysis. It must produce validated, runnable GitHub Actions workflows.

**Independent Test**: Run convert_pipelines on a repo with a simple YAML pipeline. Verify the step produces GitHub Actions workflow YAML and `WorkflowValidator` confirms it is syntactically valid.

**Acceptance Scenarios**:

1. **Given** a repo with a simple ADO YAML pipeline and completed Analyze Dependencies, **When** the operator runs Convert Pipelines, **Then** the step generates GitHub Actions workflow YAML and validation passes
2. **Given** a repo with a pipeline referencing an unmapped service connection, **When** the operator runs Convert Pipelines, **Then** the step status is `warn` and the message indicates which service connections lack GitHub secret mappings
3. **Given** a repo with a pipeline that uses an unsupported ADO task, **When** the operator runs Convert Pipelines, **Then** the step status is `warn` and the message lists the unsupported task and the generated workflow includes a placeholder comment

---

### User Story 4 - Operator Secret/Service Connection Resolution Flow (Priority: P2)

After Analyze Dependencies reports warnings for missing service connections or secrets, the operator can resolve them through a dedicated flow. The tool first attempts to **auto-provision OIDC federated credentials** for Azure RM, Kubernetes Service, and Azure Container Registry service connections — no operator input required for these connection types. If auto-provisioning fails (e.g., insufficient Azure AD permissions, GitHub OIDC config error), the connection is surfaced in the Resolve Dependencies flow with the failure reason so the operator can provide a secret/credential manually. For connection types where the secret value is fundamentally unreadable from ADO (e.g., third-party API keys, custom auth), the operator is prompted to provide the GitHub secret value. **Operators can also override any auto-provisioned value** by pre-configuring mappings in the profile — if GitHub secrets or OIDC credentials are already set up, the tool uses those instead of auto-provisioning. Operator resolutions are **persisted per-profile** so they are reused across all runs for the same migration profile — operators do not re-enter mappings on each run.

For Agent-driven migrations, the existing `inventory_gaps` form is enhanced to present all dependency gaps (not just service connections) and accept operator input. For manual migrations via the UI, a "Resolve Dependencies" panel appears on the run detail page when the Analyze Dependencies step has warnings, allowing the operator to provide GitHub secret names, OIDC credential IDs, or variable group confirmations. Once all gaps are resolved, the operator can re-run Analyze Dependencies to confirm the warnings are cleared, then proceed to downstream steps.

**Why this priority**: Without a resolution flow, warnings are dead-ends. Operators need an actionable path to provide missing information.

**Independent Test**: Run analyze_deps on a repo with SC warnings. Verify the UI shows a "Resolve Dependencies" panel. Submit mappings. Re-run analyze_deps and verify warnings are cleared.

**Acceptance Scenarios**:

1. **Given** an Analyze Dependencies step with 2 SC warnings, **When** the operator opens the run detail page, **Then** a "Resolve Dependencies" panel is displayed with input fields for each missing SC
2. **Given** the operator has submitted GitHub secret names for all missing SCs, **When** Analyze Dependencies is re-run, **Then** the step status is `completed` with no warnings
3. **Given** an Agent session with a migration plan blocked by SC gaps, **When** the `inventory_gaps` form is presented, **Then** the form includes fields for all dependency types (service connections, variable groups, environments) — not just service connections
4. **Given** the operator partially resolves gaps (2 of 3 SCs mapped), **When** Analyze Dependencies is re-run, **Then** the step status is still `warn` with 1 remaining warning

---

### User Story 5 - Step Data Independence and Pass-Through (Priority: P1)

Each pipeline step is a self-contained entity with its own inputs, outputs, and status. When a step depends on data from a prior step (e.g., Convert Pipelines needs dependency data from Analyze Dependencies), it reads that data from the prior step's `result` data structure on the `PipelineRun`. Steps do not re-derive data that a prior step already computed. If a required prior step was skipped or failed, the dependent step reports a clear error indicating which step must be completed first. **All steps** in both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` are decoupled as self-contained entities — not just the overlapping ones. The pipeline uses **flexible execution order with prerequisite checks** — steps can run in any order as long as prerequisite steps are completed; the pipeline defines a default/recommended order but does not enforce strict sequencing. The migration pipeline must work end-to-end with **zero manual GitHub changes** — the tool auto-provisions secrets, OIDC credentials, and repository settings as part of the pipeline.

**Why this priority**: Step decoupling is the architectural foundation that makes all other user stories possible. Without clean data boundaries, steps remain tangled.

**Independent Test**: Run a pipeline with analyze_deps skipped. Verify convert_pipelines reports "Analyze Dependencies must be completed before pipeline conversion can proceed."

**Acceptance Scenarios**:

1. **Given** a pipeline run where Analyze Dependencies completed with warnings, **When** Convert Pipelines runs, **Then** it reads the `dependencies` and `warnings` from the analyze_deps step result and uses them during conversion
2. **Given** a pipeline run where Analyze Dependencies was skipped, **When** Convert Pipelines runs, **Then** it fails with a message indicating Analyze Dependencies must run first
3. **Given** the updated MIGRATE_UI_PIPELINE_STEPS, **When** the operator views the migrate page, **Then** the steps shown are: Load discovery data, Analyze dependencies (including secrets/SCs), Migrate repository contents, Convert pipelines, Validate — with no separate "Map secrets" step

---

### Edge Cases

- What happens when a repo has no ADO pipelines at all? Analyze Dependencies should report "no pipelines found" with a `completed` status (no warnings, no dependencies)
- What happens when the ADO API is unavailable during Analyze Dependencies? The step should fail with a clear connectivity error, not silently produce empty results
- What happens when an operator provides an invalid GitHub secret name (e.g., with spaces)? The resolution flow should validate input format and reject invalid entries
- What happens when a repo has both YAML and classic pipelines? Analyze Dependencies should scan both and merge their dependencies into a single per-repo report
- What happens when the GEI tool is not installed but the repo requires it? Migrate Repository Contents should report a warning indicating GEI tool installation is required
- What happens when inventory data is stale (pipelines modified in ADO since last scan)? Analyze Dependencies uses existing inventory data; a "refresh inventory" option in the UI re-runs the scan step before re-running analyze_deps
- What happens when a service connection is an Azure RM type that supports OIDC? The tool auto-provisions the federated credential and GitHub secret without operator input
- What happens when an operator has pre-configured GitHub secrets for a service connection that would otherwise be auto-provisioned? The tool detects the existing mapping in the profile and uses it instead of auto-provisioning
- What happens when a step is run out of order (e.g., convert_pipelines before analyze_deps)? The step checks prerequisites and fails with a clear message naming the required prior step
- What happens when a repo exceeds 10GB? Migrate Repository Contents fail-soft (warn status) and recommends manual GEI handling; it does not attempt an automatic transfer that would fail
- What happens when one repo in a batch fails? The batch continues with the remaining repos (continue-on-error); the step reports a succeeded/failed summary
- What happens when a converted workflow filename collides with an existing file in `.github/workflows/`? The new workflow is written with a versioned suffix, preserving the original
- What happens when OIDC auto-provisioning fails due to permissions? The connection falls back to the operator prompt in the Resolve Dependencies flow with the failure reason
- What happens when two runs target the same repo concurrently? The second run is rejected with a "migration in progress" message (per-repo lock)

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

- **CA-001**: Operators MUST have a dry-run or preview path before irreversible actions (each step supports dry-run mode)
- **CA-002**: Destructive actions (live migration, pipeline conversion commit) MUST require explicit confirmation or documented override with reason
- **CA-003**: Secrets MUST NOT appear in logs, reports, or persisted artifacts (service connection names are fine; secret values are never stored)
- **CA-004**: State changes MUST be auditable (step results, warnings, and operator resolutions are persisted in the run's step result data)

### Functional Requirements

- **FR-001**: System MUST merge the "Map secrets & service connections" step into "Analyze Dependencies" so that a single step surfaces all dependency types (service connections, variable groups, repo-to-repo, environments, self-hosted agents)
- **FR-002**: Analyze Dependencies MUST produce structured per-repo dependency data in its step result, including: `service_connections`, `variable_groups`, `repo_dependencies`, `environments`, `unsupported_tasks`, `self_hosted_agents`
- **FR-003**: Analyze Dependencies MUST display warnings as a bulleted list in the step message, paginated at 10 items with a "… and N more" indicator when exceeded
- **FR-004**: Migrate Repository Contents MUST analyze repo size, LFS file count/size, branch count, tag count, and metadata before attempting migration, and report feasibility in the step message
- **FR-005**: Migrate Repository Contents MUST recommend a migration strategy (git mirror, GEI transfer, or manual) based on the feasibility analysis
- **FR-006**: Convert Pipelines MUST execute `PipelineTransformer.transform()` and run `WorkflowValidator` on the generated workflow YAML
- **FR-007**: Convert Pipelines MUST report validation failures with specific error details (syntax error, missing secret reference, invalid job structure)
- **FR-008**: Each step MUST read dependent data from prior step results on the PipelineRun, not re-derive it
- **FR-009**: If a required prior step was not completed, the dependent step MUST fail with a message naming the prerequisite step
- **FR-010**: System MUST provide a "Resolve Dependencies" flow in the UI when Analyze Dependencies has warnings, allowing operators to input GitHub secret names, OIDC credential IDs, and variable group confirmations
- **FR-011**: The Agent `inventory_gaps` form MUST include all dependency gap types (service connections, variable groups, environments), not just service connections
- **FR-012**: Operators MUST be able to re-run Analyze Dependencies after resolving gaps to confirm warnings are cleared
- **FR-013**: Both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` MUST be updated to remove the separate "Map secrets" step and reflect the merged step responsibilities
- **FR-014**: Each step MUST set status `warn` (not `completed`) when its analysis produces warnings that require operator attention
- **FR-015**: Step result data MUST include a `dependencies` key with structured per-repo dependency information that downstream steps can consume
- **FR-016**: The migration pipeline MUST work end-to-end with zero manual GitHub changes — the tool auto-provisions GitHub secrets, OIDC federated credentials, and repository settings as part of the pipeline execution
- **FR-017**: For Azure RM, Kubernetes Service, and Azure Container Registry service connections, the tool MUST auto-provision repo-level OIDC federated credentials and create corresponding GitHub repo secrets without operator input
- **FR-018**: For service connection types where the secret value is fundamentally unreadable from ADO (third-party API keys, custom auth), the tool MUST prompt the operator for the secret value via the resolution flow
- **FR-019**: Operator resolutions MUST persist per-profile — mappings are stored with the migration profile and reused across all runs for that profile without re-entry
- **FR-020**: Operators MUST be able to override any auto-provisioned value by pre-configuring mappings in the profile; if a mapping exists, the tool uses it instead of auto-provisioning
- **FR-021**: Analyze Dependencies MUST use existing inventory data from the StateDB; a "refresh inventory" option MUST be available in the UI to re-run the scan step before re-running analyze_deps
- **FR-022**: Convert Pipelines MUST use `actionlint` for workflow validation if installed; if `actionlint` is not available, the step MUST fall back to YAML parse + basic structural checks (jobs/steps/secrets refs) and note the reduced validation in the step message
- **FR-023**: Convert Pipelines MUST auto-commit validated workflow files to the target GitHub repo's `.github/workflows/` directory in live mode; in dry-run mode, files MUST be generated locally only
- **FR-024**: The Validate step MUST verify both SHA/branch parity against ADO source AND that committed GitHub Actions workflows exist and match the converted output from the Convert step
- **FR-025**: All steps in both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` MUST be decoupled as self-contained entities with clean data pass-through — not just the overlapping ones
- **FR-026**: The pipeline MUST use flexible execution order with prerequisite checks — steps can run in any order as long as prerequisite steps are completed; the pipeline defines a default/recommended order but does not enforce strict sequencing
- **FR-027**: Migrate Repository Contents MUST use GitHub documented GEI limits for feasibility: warn at repo > 2GB, fail-soft at repo > 10GB, warn at any file > 100MB, flag LFS for files > 2GB
- **FR-028**: When migrating a batch of repos, the step MUST use continue-on-error — a failed repo is marked failed, the batch continues with remaining repos, and the step reports a succeeded/failed summary
- **FR-029**: When auto-committing a workflow that collides with an existing file in `.github/workflows/`, the step MUST write the new workflow with a versioned suffix, preserving the original
- **FR-030**: When OIDC auto-provisioning fails, the tool MUST fall back to surfacing the connection in the Resolve Dependencies flow with the failure reason, not fail the step outright
- **FR-031**: The system MUST use a per-repo lock to prevent concurrent runs from migrating the same repo simultaneously; a second run targeting a locked repo MUST be rejected with a "migration in progress" message

### Key Entities *(include if feature involves data)*

- **StepResult**: The result data structure persisted on each PipelineStep after execution. Contains step-specific outputs (e.g., `dependencies`, `migration_order`, `workflow_files`, `feasibility_report`) and `warnings` list.
- **DependencyReport**: Per-repo structured data produced by Analyze Dependencies. Includes `service_connections` (list of {name, type, id, status}), `variable_groups` (list of {name, status}), `repo_dependencies` (list of repo IDs), `environments` (list of {name, type}), `unsupported_tasks` (list of task names), `self_hosted_agents` (list of agent pool names).
- **FeasibilityReport**: Per-repo data produced by Migrate Repository Contents. Includes `repo_size_bytes`, `lfs_object_count`, `lfs_size_bytes`, `largest_file_bytes`, `branch_count`, `tag_count`, `has_wiki`, `has_policies`, `recommended_strategy` (mirror|gei|manual), `feasibility` (ok|warn|fail_soft), `warnings` (list of limiting factors). Thresholds: warn at repo > 2GB, fail_soft at repo > 10GB, warn at any file > 100MB, flag LFS for files > 2GB.
- **ConversionResult**: Per-pipeline data produced by Convert Pipelines. Includes `source_pipeline` (name), `output_path` (workflow file path), `commit_sha` (SHA of the auto-commit in live mode, null in dry-run), `validation_status` (valid|invalid|warn), `validation_errors` (list), `validation_mode` (actionlint|yaml_fallback), `unmapped_secrets` (list of SC names without GitHub secret mappings), `conflict_renamed` (boolean — true if the workflow file was written with a versioned suffix due to name collision; false in dry-run mode).
- **OperatorResolution**: Operator-provided mapping for a dependency gap. Includes `field` (e.g., `secret_mapping__Project__SCName`), `value` (GitHub secret name or OIDC credential ID), `type` (service_connection|variable_group|environment). Persisted per-profile in the settings store and reused across all runs for that profile.
- **RepoLock**: A lock acquired per-repo at the start of a migration run to prevent concurrent runs from migrating the same repo. Includes `repo_id`, `run_id`, `acquired_at`. Released when the run completes, fails, or is cancelled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Analyze Dependencies surfaces 100% of service connections, variable groups, and repo-to-repo dependencies for repos with inventoried pipelines (zero false negatives vs. manual ADO inspection)
- **SC-002**: Migrate Repository Contents correctly identifies feasibility blockers (size, LFS) for 95%+ of repos without attempting a migration that would fail
- **SC-003**: Convert Pipelines produces syntactically valid GitHub Actions YAML for 90%+ of simple-to-moderate ADO pipelines (no unsupported tasks, standard triggers)
- **SC-004**: Operators can resolve all dependency warnings through the UI or Agent flow without editing config files or running CLI commands
- **SC-005**: Each pipeline step completes independently — a step only fails due to missing prerequisites, never due to missing data that a prior step should have provided
- **SC-006**: The merged Analyze Dependencies step replaces the separate Map Secrets step with no loss of information or capability
- **SC-007**: A repo with only Azure RM service connections can be migrated end-to-end with zero operator input for secrets (OIDC auto-provisioned)
- **SC-008**: Operator resolutions entered for one run are automatically available on subsequent runs for the same profile without re-entry
- **SC-009**: A repo with pre-configured GitHub secrets in the profile is migrated using those secrets instead of auto-provisioning new ones
- **SC-010**: The Validate step catches mismatched or missing GitHub Actions workflows that were supposed to be committed by the Convert step
- **SC-011**: An operator can re-run any individual step without re-running the entire pipeline, as long as prerequisite steps are completed
- **SC-012**: A batch migration with one failing repo completes the remaining repos and reports an accurate succeeded/failed summary (no silent halt)
- **SC-013**: Two concurrent runs targeting the same repo never both write to GitHub — the second is rejected with a clear message
- **SC-014**: A repo over 10GB is never auto-migrated in a way that fails partway; it is flagged fail-soft before any transfer is attempted

## Assumptions

- Pipeline inventory has already been built (either via the Discover/Inventory steps or a prior scan) before Analyze Dependencies is run; a "refresh inventory" option is available if data may be stale
- The ADO PAT has sufficient permissions to read build definitions, release definitions, service connections, and variable groups for all target projects
- The GitHub token has permissions to create secrets, configure OIDC federated credentials, and create workflows in the target organization
- The GEI tool (`gh-gei`) may or may not be installed; Migrate Repository Contents checks for it and falls back to git mirror if unavailable
- `actionlint` may or may not be installed; Convert Pipelines uses it if available and falls back to YAML parse + basic structural checks if not
- Existing `inventory_gaps` form infrastructure in the Agent session orchestrator will be extended, not replaced
- Both `ACCELERATOR_PIPELINE_STEPS` and `MIGRATE_UI_PIPELINE_STEPS` are updated to merge map_secrets into analyze_deps
- Operator resolutions persist per-profile in the settings store
- Dry-run mode is the default for all steps; live mode requires explicit operator opt-in
- Azure RM, Kubernetes Service, and Azure Container Registry service connections support OIDC federated credentials; the tool auto-provisions these without operator input
- Operators can override any auto-provisioned value by pre-configuring mappings in the profile
- Third-party service connection types (custom auth, API keys) require operator-provided secret values via the resolution flow
- Convert Pipelines auto-commits workflow files to GitHub in live mode; dry-run generates locally only
- Validate step verifies both SHA/branch parity and committed workflow integrity
- All steps in both pipeline lists are decoupled, not just the overlapping ones
- Pipeline execution order is flexible with prerequisite checks, not strict sequential
