# Research: Agentic ADO-to-GitHub Migration Platform

**Feature**: `001-agentic-migration-platform`  
**Date**: 2026-06-16

## Pipeline conversion (ADO → GitHub Actions)

**Decision**: Every repository in scope receives ADO pipeline transformation; generated workflows are committed on a dedicated migration branch and opened as a PR—not pushed directly to the default branch.

**Rationale**: Matches enterprise review gates, keeps production branches stable until Approver merge, and aligns with existing `push_workflows_for_repos` (`ado2gh/migrated-workflows` branch + PR).

**Alternatives considered**:
- Commit workflows to default branch during migration — rejected (no HITL on workflow YAML).
- Local-only workflow output without push — rejected (user hard requirement for branch setup).

**Implementation notes**:
- Reuse `PipelineTransformer` + `PipelinesScopeHandler`; extend orchestration to run pipeline scope for **all** repos in assignment/cohort after git mirror (or in parallel where safe).
- Configurable branch name per profile: `workflow_branch` (default `ado2gh/migrated-workflows`).
- Validator checks branch exists, workflow files present, and PR state where policy requires.

## Out-of-the-box workflow execution (target dependencies)

**Decision**: Treat **pre-configured GitHub target dependencies** as a prerequisite gate. When secrets, environments, OIDC mappings, and internal package/registry access are satisfied (via `service-connections` manifest + profile readiness), workflows on the migration branch MUST run without post-migration YAML edits (except documented `[MANUAL]` placeholders).

**Rationale**: User requirement—assuming GitHub side is set up, migration branch should be immediately exercisable.

**Alternatives considered**:
- Always require manual YAML fix-up after push — rejected (operational burden, defeats accelerator goal).
- Skip validation of dependencies — rejected (false “success” when workflows cannot run).

**Implementation notes**:
- Extend pipeline-readiness + `credential_validation` into **WorkflowDependencyChecklist** (per repo).
- Planner surfaces checklist in plan; Executor blocks live `push_workflows` if critical items fail.
- Validator runs dry `workflow_dispatch` or syntax/dependency lint where feasible.

## ADO pipeline removal after migration branch

**Decision**: After successful live push of GitHub Actions workflows to the migration branch (Approver-approved), **disable or remove ADO pipeline definitions** for the migrated scope via `ado_cleanup` / pipeline disable APIs—source ADO CI is decommissioned for that repo/wave.

**Rationale**: User requirement—old pipelines removed once new workflow branch exists; avoids dual CI and confusion.

**Alternatives considered**:
- Leave ADO pipelines enabled until PR merge — rejected (user wants removal tied to branch creation/push outcome).
- Delete ADO pipelines without audit — rejected; disable + audit event required.

**Ordering**: Git mirror → transform → push migration branch → **disable ADO pipelines** → validate.

## Workflow maintainability (layout policy)

**Decision**: Per-repo **workflow layout policy** driven by pipeline readiness classification:

| Classification | Layout | When |
|----------------|--------|------|
| **consolidated** | One primary workflow file (or one per trigger family) | Simple YAML pipelines, few stages, low unsupported-task count |
| **modular** | Reusable workflows + `workflow_call`, shared job templates | Multiple pipelines, release/classic complexity, or readiness = assisted/manual |

Both layouts MUST follow GitHub Actions maintainability practices: clear `name`, top-level `env`, `concurrency`, pinned action versions, minimal duplication, migration notes for manual steps.

**Rationale**: User requirement—single workflow OR best-practice modular structure for team maintainability.

**Alternatives considered**:
- One file per ADO pipeline always — rejected (sprawl for enterprise repos with dozens of pipelines).
- Always single file — rejected (unmaintainable for complex multi-stage release pipelines).

**Implementation notes**:
- Add `WorkflowLayoutPolicy` to profile/plan; `PipelineTransformer` emits `workflows/` tree accordingly.
- Modular layout: `/.github/workflows/{name}.yml` + `/.github/workflows/reusable/{name}.yml` with documented entrypoints.

## Repository dependency topological ordering

**Decision**: Build a **repository dependency graph** from ADO discovery signals; execute git migration, pipeline conversion, and push-workflow steps in **topological order** (dependencies first). Cycles are detected and surfaced for human resolution—no silent ordering.

**Rationale**: User requirement for dependency-aware migration; prevents downstream repos migrating before upstream artifacts/repos exist on GitHub.

**Edge sources (v1)**:
| Signal | Edge meaning |
|--------|----------------|
| Pipeline `resources.repositories` / multi-repo checkout | Consumer repo → resource repo |
| Pipeline template extends from another repo | Consumer → template repo |
| Package/feed dependencies declared in pipeline YAML (NuGet/npm internal feeds tied to repo) | Consumer → publishing repo (when resolvable) |
| Explicit `repo_dependencies` in scan metadata (future ADO API / manual coordinator input) | Declared dependency |

**Alternatives considered**:
- Risk-score order only — rejected (ignores build dependency order).
- Parallel only, no graph — rejected (breaks dependent pipelines).

**Algorithm**: Kahn topological sort with cycle detection; persist graph in state DB (`repo_dependency_edges` table). Planner subagent reads sorted order for execution plans.

## Planner–Executor–Validator agent architecture

**Decision**: Three subagent roles implemented as separate modules with versioned **skills** (markdown contracts) and **tools** mapped to Accelerator API + MCP endpoints. Executor whitelist: only tools registered in skill manifests and authorized in active `MigrationPlan` / `RemediationInstruction`.

**Rationale**: Spec FR-006/007 and constitution Principle V; existing `services/agent/main.py` PEV skeleton delegates to Accelerator API.

**Alternatives considered**:
- Single monolithic agent — rejected (no role separation, harder audit).
- Executor with shell access — rejected (constitution / spec guardrails).

## LLM provider (hyperscaler agnostic)

**Decision**: Provider abstraction layer (`LLMProvider` interface) with pluggable backends (OpenAI-compatible, Azure OpenAI, Bedrock, Vertex, PCF-hosted). Profile stores allowed models; no vendor lock-in in agent code.

**Rationale**: Clarification Q1 — AWS/Azure/GCP/PCF; migration data remains from ADO APIs only.

## Assignment cohorts vs execution phases

**Decision**: Persisted `MigrationAssignment` entities (POC, pilot, Wave N, ad-hoc) **linked** to `ExecutionPhaseWave` for gate enforcement (clarification Q1-B).

**Rationale**: Team ownership (assignments) separate from risk/gate model (phases).

## RBAC

**Decision**: Profile-scoped roles: **Coordinator**, **Operator**, **Approver** (clarification Q2-C). Approver required for all live mutations including workflow branch push/PR merge requests.

## Concurrency

**Decision**: Parallel live runs across cohorts; **one active live run per repository** (clarification Q3-B).

## Audit retention

**Decision**: Immutable append-only audit store (PostgreSQL with insert-only policy or WORM S3 + index) retaining full agent transcripts 7+ years with secret redaction at write (clarification Q4-A).

**Alternatives considered**: SIEM-only export — rejected per user choice.

## Boards migration

**Decision**: Full parity intent with mandatory **Boards Gaps Report** when GitHub lacks 1:1 mapping (clarification Q5-C). Validator includes gaps as explicit findings.

## State storage

**Decision**: Extend existing `StateDB` / optional Postgres (`ado2gh/state/postgres_db.py`) for assignments, audit events, dependency graph, agent sessions. SQLite remains dev default.

## Frontend

**Decision**: Extend `apps/migration-ui` with OrchestrateAI styles; assignment management UI; agent chat with role-attributed messages; workflow branch/PR status per repo.

## Testing strategy (Constitution VI)

**Decision**: `pytest` + `pytest-cov`; minimum 85% line coverage on `ado2gh` package in CI; contract tests for API/agent tool schemas; golden tests for pipeline transformer and topological sort.

## Missing dependency logging (accelerator)

**Decision**: All readiness and pre-flight checks MUST write **actionable log output** to Rich console (CLI) and structured execution logs (API worker, UI run log stream) listing each missing secret name, environment, service connection mapping, or package feed—with repo/pipeline context and remediation links. Never log values.

**Rationale**: User requirement—operators see gaps in the environment where the accelerator runs.

**Implementation notes**:
- Extend `pipeline_readiness`, `credential_validation`, workflow readiness checklist.
- Log level `WARNING` for blockers, `INFO` for passed checks summary.
- UI `runs` page surfaces same messages from worker log tail.

## State database (SQLite vs PostgreSQL)

**Decision**: **SQLite** default for local CLI, dev Docker Compose, and single-node runs. **PostgreSQL** for cloud/hyperscaler production (`docker-compose.prod.yml` overlay, `ADO2GH_STORAGE_BACKEND=postgres`, `ADO2GH_DATABASE_URL`). Existing `create_state_db()` factory is the single selection point; extend schema for assignments, audit, agent sessions on both backends.

**Rationale**: User requirement; pattern already implemented in `ado2gh/state/factory.py`.

**Alternatives considered**:
- SQLite in production — rejected for multi-replica accelerator/worker coordination.
- New database product — rejected; Postgres already in prod compose.

**Cloud deployment**: `docker-compose -f docker-compose.yml -f docker-compose.prod.yml` adds Postgres service; hyperscaler deployments use managed Postgres URL via env.

## Agent-assisted secret/connection provisioning

**Decision**: When agent readiness finds gaps, **Planner/Executor** prompts operator in chat: "Missing `MY_REGISTRY_TOKEN` for repo X—create in GitHub? [Yes/No]". On **Yes**, Executor calls whitelisted provision tools (`create_repo_secret`, `create_environment`, `map_service_connection` per manifest) using secure value capture (UI modal or vault ref, not LLM). Re-run readiness; continue plan on green.

**Rationale**: User requirement for agentic flows; manual accelerator remains log-only unless operator runs explicit provision commands.

**Guardrails**:
- Operator confirmation required (chat Yes); Approver additionally required for org-level secrets if profile policy says so.
- Audit event `secret_provisioned` / `environment_created` with redacted payload.
- Executor cannot invent secret values; must prompt secure input channel.

**Alternatives considered**:
- Auto-provision without prompt — rejected (HITL, constitution).
- LLM suggests secret values — rejected (security).

## Phase gates (assignment-linked)

**Decision**: Extend existing `PhaseGateChecker` (`ado2gh/phase/gate_checker.py`) to evaluate the **execution phase/wave linked from the selected assignment** before any cohort live migration. Assignment selection alone does not bypass gates (FR-034).

**Rationale**: Clarification Q1-B separates cohort ownership from risk phases; gates remain on phase success thresholds (repo/pipeline completion %, min completed count). Analysis gap C2 — tasks must wire gate checker into assignment-scoped live paths.

**Alternatives considered**:
- Gate only on global `phase run` CLI — rejected (agent/API could bypass without assignment linkage).
- Skip gates for POC assignments — rejected (spec requires mapped phase for all assignment types).

**Implementation notes**:
- `assignments/store.py` resolves `PhaseType` from `assignment_id`.
- Gate check scoped to cohort repo list when assignment-scoped.
- `override(phase, reason)` requires Approver; audit event `gate_override`.
- `BatchExecutor` and accelerator migrate endpoints call `can_advance()` before live jobs.
- Supplemental **policy rules** (`ado2gh/phase/policy_rules.py`) for FR-019 (bulk wave size, production phase).

## Scope-targeted rollback

**Decision**: Extend existing `RollbackHandler` (`ado2gh/core/rollback.py`) and CLI `rollback` command for **assignment-scoped**, scope-targeted rollback via accelerator API, UI, and agent executor tool `ado2gh_rollback` (FR-026, FR-018).

**Rationale**: Constitution Principle V and existing `rollback --scopes` pattern; regulated environments require undo without full repo delete. Analysis gap C1 — no tasks existed until plan subsection added.

**Alternatives considered**:
- Rollback = delete GitHub repo only — rejected (spec requires scope-targeted undo).
- Agent shell/git rollback — rejected (FR-007 executor whitelist).

**Implementation notes**:
- Scopes: `branch_policies`, `pipelines`, `repo` (full target repo delete).
- **Symmetric pipelines rollback (FR-026a)**: When `pipelines` scope is rolled back live for repos where ADO pipelines were disabled (FR-048), **re-enable matching ADO pipeline definitions** via `ado_cleanup` enable path—in addition to GitHub workflow rollback on the migration branch. Approver-approved, audited, validator-verified (pairs with SC-016).
- Dry-run previews actions without mutations; live requires Approver (same ticket pattern as live migrate).
- Audit: `rollback_requested`, `rollback_executed` with assignment cohort, scopes, approver; include `ado_pipelines_reenabled` count when applicable.
- Contract: `contracts/rollback-gates-api.md`.
