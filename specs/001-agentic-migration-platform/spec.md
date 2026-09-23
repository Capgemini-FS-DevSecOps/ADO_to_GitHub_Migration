# Feature Specification: Agentic ADO-to-GitHub Migration Platform

**Feature Branch**: `001-agentic-migration-platform`

**Created**: 2026-06-16

**Status**: Clarified

> **Note**: This is the umbrella spec for the agentic migration platform. Major concern areas have been split into dedicated specs:
> - **RBAC & access control** → see [004-agent-pev-rbac](../004-agent-pev-rbac/spec.md)
> - **Audit & compliance** → see [010-enterprise-audit-simplification](../010-enterprise-audit-simplification/spec.md)
> - **Boards / work tracking** → covered by this spec (FR-050–FR-055)
> - **Pipelines** → see [009-pipeline-step-decoupling](../009-pipeline-step-decoupling/spec.md)
> - **Assignments & cohorts** → covered by this spec (FR-006–FR-013)
> - **Login & auth** → see [002-login-bootstrap](../archive/002-login-bootstrap/spec.md) (archived/implemented)
> - **Profile onboarding** → see [005-profile-onboarding](../archive/005-profile-onboarding/spec.md) (archived/implemented)
> - **LLM model catalog** → see [006-llm-model-catalog](../006-llm-model-catalog/spec.md)
> - **Cloud credentials** → see [007-cloud-llm-credentials](../007-cloud-llm-credentials/spec.md)
> - **UI** → see [008-migration-ui-refactor](../008-migration-ui-refactor/spec.md)
> - **Agent PEV rebuild** → see [011-agent-pev-rebuild](../011-agent-pev-rebuild/spec.md)

**Input**: User description: "The overall project is a agentic AI implementation of a azure devops to github actions migration. It must contain 2 parts: (1) accelerator for manual migration, (2) LLM-backed migration agent with natural language, (3) frontend conforming to internal Agentic Platform (OrchestrateAI) standards. Architecture follows planner → executor → validator with retry or human escalation. Executor has API access but only runs authorized steps from planner or validator. Subagents with specific skills to be designed. Migration agent UI supports team assignments: specific repositories, wave-based cohorts (Wave 1–N), pilots, and proof-of-concept assignments; all agents/subagents must possess tools and skills to handle every assignment type."

## Clarifications

### Session 2026-06-16

- Q: Phase model vs assignment cohorts — are POC/pilot/wave assignments the same as execution phases? → A: Linked but separate — assignments are persisted cohorts; each cohort maps to a phase/wave for execution and gate enforcement.
- Q: Operator role permissions — who can assign, execute, approve live runs, and override gates? → A: Three roles — Coordinator (assignments), Operator (dry-run + request live), Approver (live execution, rollback, gate/assignment overrides).
- Q: Concurrent live migration runs — can different assignment cohorts run live at the same time? → A: Parallel per assignment — multiple live runs across different cohorts; one active live run per repository.
- Q: Audit and agent session retention — what is stored long-term? → A: Full retention — immutable in-product store of all agent transcripts, approvals, and runs for 7+ years.
- Q: Boards / work tracking migration depth — what ADO Boards artifacts must migrate? → A: Full Boards parity — work items, queries, test plans, dashboards, and board configuration with documented gaps report.
- Q: Pipeline conversion delivery — branch and ordering? → A: Convert all in-scope ADO pipelines to GitHub Actions; commit on dedicated migration branch (default `ado2gh/migrated-workflows`); order repo migration using topological sort when dependencies exist.
- Q: Workflow readiness and maintainability — out-of-box run, ADO cleanup, layout? → A: With target GitHub dependencies pre-configured, merged migration branch runs without extra setup; disable/remove ADO pipelines after branch push; workflows use consolidated single-file layout for simple repos OR modular reusable-workflow layout per GitHub best practices.
- Q: Missing secrets/deps logging and agent provisioning? → A: Accelerator logs missing items to console/execution logs (names only); SQLite for local, PostgreSQL for cloud/hyperscaler; agent prompts user to create missing secret/connection and auto-provisions in target GitHub when user confirms yes.
- Q: Agent auto-provision authorization — who approves secret/connection creation? → A: Tiered — Operator chat yes for repo-scoped secrets/environments; Approver required for org-level secrets and production environments.
- Q: Authentication for v1 — SSO vs profile tokens? → A: Profile PAT/tokens for v1 (existing profile model); enterprise SSO documented as stretch goal for a future release.
- Q: Cross-phase gate dependency — if Wave 3 assignment runs live while POC phase gate has not passed, should live be blocked? → A: Per-assignment phase gate only — live blocked only when that assignment's mapped execution phase/wave fails its gate (or lacks Approver override); not blocked solely because a different phase (e.g., POC) has not passed.
- Q: FR-019 policy rules — what is v1 scope beyond mandatory Approver live approval? → A: Profile policy rules (v1) — configurable per migration profile for bulk wave thresholds, production-phase extra Approver, cleanup/rollback extra approval, and optional program-order phase gates; evaluated after base phase gates (FR-034); not a full arbitrary rule engine in v1.
- Q: Pipelines rollback — when live rollback includes `pipelines` scope, should ADO pipelines be re-enabled? → A: Symmetric rollback — rolling back `pipelines` scope automatically re-enables matching ADO pipelines that were disabled after migration (paired with FR-048); audited like live rollback.
- Q: Audit retention backend — v1 primary store for 7+ year immutable audit/transcripts? → A: Postgres primary + optional S3 export — v1 writes to append-only Postgres (prod); optional scheduled/async export to WORM-capable object storage for long-term archive; SQLite for local dev.
- Q: FR-039 historical retrieval — v1 delivery for compliance review? → A: UI + API v1 — in-product history browser (sessions, runs, approvals) plus REST query/export endpoints; Operators and Approvers retrieve within retention period.

## User Scenarios & Testing *(mandatory)*

### User roles

- **Coordinator**: Creates and updates migration assignments (cohort membership, phase
  mapping); cannot approve live execution or gate overrides unless also granted Approver.
- **Operator**: Runs discovery, dry-run, planning, and agent sessions; may request live
  migration, rollback, cleanup, and remedial execution but cannot approve them.
- **Approver**: Approves or rejects live (non-dry-run) execution, rollback, cleanup, gate
  bypasses, and assignment overrides/reassignments that policy would otherwise block.

---

### User Story 1 - Manual Migration Accelerator (Priority: P1)

A migration operator wants to run an Azure DevOps to GitHub migration themselves using a
guided, step-by-step experience without relying on an AI agent. They configure source and
target connections, discover repositories, plan phases, execute migrations, and review
results through explicit controls they initiate at each stage.

**Why this priority**: The accelerator is the safety baseline and fallback when agents are
disabled, models are unavailable, or regulated workflows require fully manual control.
It must work independently of any LLM.

**Independent Test**: An operator completes discovery → plan preview → execution (with
dry-run) → validation for at least one repository using only manual UI/CLI controls, with
no agent session started. Success is a verified migration outcome and audit record.

**Acceptance Scenarios**:

1. **Given** valid source and target credentials configured, **When** the operator runs
   discovery and selects repositories for a phase, **Then** the system shows an
   actionable migration plan with scope, phase, and estimated risk before execution.
2. **Given** a migration plan in dry-run mode, **When** the operator confirms preview,
   **Then** no irreversible changes occur on source or target and the system records the
   preview outcome.
3. **Given** a approved live migration run, **When** execution completes, **Then** the
   operator sees per-repository status, checkpoints, and a validation summary without
   secrets appearing in the UI or exported reports.

---

### User Story 2 - Natural Language Migration Agent (Priority: P2)

A migration operator describes intent in plain language (e.g., "migrate the POC wave
repos and convert their build pipelines"), and the system orchestrates specialized
subagents—a planner, an executor, and a validator—to accomplish the migration with
guardrails and human oversight where required.

**Why this priority**: This is the core agentic value proposition: lower expertise
barrier and faster execution while preserving enterprise controls.

**Independent Test**: An operator submits a natural-language request, receives an
reviewable plan, approves execution (where required), observes executor progress, and
receives validator results—all within a single agent session traceable in the UI.

**Acceptance Scenarios**:

1. **Given** an operator message describing migration intent, **When** the planner
   subagent responds, **Then** the operator sees a structured migration plan (repos,
   scopes, sequence, risks) before any mutating work begins.
2. **Given** an approved plan, **When** the executor subagent runs, **Then** only
   migration operations authorized by the plan or a subsequent validator remediation
   instruction are performed—no ad-hoc or unrelated operations.
3. **Given** a completed executor pass, **When** the validator subagent runs, **Then**
   the operator sees pass/fail per scope (repository content, dependencies, pipelines,
   boards/work items, queries, test plans, dashboards, board configuration, access and
   teams, and other in-scope ADO artifacts) with evidence suitable for audit review.
4. **Given** a migration assignment (POC, pilot, or numbered wave), **When** the
   operator asks the agent to migrate that cohort, **Then** the planner scopes the plan
   to only repositories in that assignment and labels the assignment type in the plan.

---

### User Story 5 - Team Repository Assignments (Priority: P2)

A migration program lead or team coordinator assigns repositories to migration cohorts—
proof-of-concept (POC), pilot, or numbered waves (Wave 1 through Wave N)—or to ad-hoc
repository lists, so teams can execute migrations in aligned batches with clear ownership.
Operators and agents work against these assignments from the migration agent UI and
accelerator without manually re-selecting the same repos each time.

**Why this priority**: Enterprise migrations are organized by wave and program phase; UI
and agents must reflect how teams actually partition work, not only individual repo picks.

**Independent Test**: A coordinator creates a Wave 2 assignment with a defined repo set,
an operator opens the agent UI filtered to that assignment, runs dry-run then live
migration for one repo in the cohort, and validation results are recorded against the
assignment identifier—without affecting repos outside the cohort.

**Acceptance Scenarios**:

1. **Given** discovered repositories in a profile, **When** a coordinator assigns
   repositories to a POC, pilot, or Wave N cohort, **Then** the assignment is visible in
   the UI with cohort type, name, repo count, and status summary.
2. **Given** multiple assignment types exist (POC, pilot, Wave 1, Wave 2), **When** an
   operator selects an assignment in the agent or accelerator UI, **Then** discovery,
   planning, execution, and validation are scoped to that assignment's repository set.
3. **Given** an operator uses natural language referencing a cohort (e.g., "migrate Wave 3
   repos"), **When** the planner responds, **Then** the plan resolves to the correct
   assignment cohort or asks for disambiguation if multiple matches exist.
4. **Given** a repository is reassigned from one wave to another, **When** the change is
   saved, **Then** the audit trail records the prior and new assignment and active runs
   on that repository are blocked or reconciled per policy.

---

### User Story 3 - Validation Loop and Human Escalation (Priority: P3)

When validation detects missing or failed migration outcomes, the system automatically
attempts remediation through the executor (within policy limits) or surfaces a clear
error in the UI for human inspection when automatic retry cannot resolve the issue.

**Why this priority**: Regulated migrations require proof of correctness and explicit
escalation paths; silent partial success is unacceptable.

**Independent Test**: A seeded or simulated validation failure triggers either a bounded
automatic re-execution with validator re-check, or a UI error state with remediation
context—without exposing credentials.

**Acceptance Scenarios**:

1. **Given** a validator report listing failed scopes for a repository, **When**
   automatic remediation is allowed, **Then** the executor re-runs only the failed scopes
   and the validator re-evaluates until pass or retry limit is reached.
2. **Given** retries are exhausted or the failure is not auto-remediable, **When** the
   loop ends, **Then** the UI shows an error state with repository, scope, failure
   reason, and recommended human actions—and no further executor actions occur without
   operator acknowledgment.
3. **Given** an operator reviews an escalated failure, **When** they approve a remedial
   action, **Then** the system records the approval, reason, and subsequent executor run
   in the audit trail.

---

### User Story 4 - OrchestrateAI-Compliant Frontend (Priority: P4)

A migration operator uses a single web experience that conforms to the internal Agentic
Platform (OrchestrateAI) visual and interaction standards, with clear navigation between
manual accelerator workflows and the conversational agent.

**Why this priority**: Platform consistency reduces training cost and ensures the
migration product feels part of the enterprise agentic suite.

**Independent Test**: UI review against OrchestrateAI style guidelines (colors, typography,
components, layout patterns) confirms conformance for accelerator pages, agent chat, run
monitoring, and error states—without functional dependency on other stories beyond
static/mock views where needed.

**Acceptance Scenarios**:

1. **Given** any primary screen (dashboard, migrate, agent, runs), **When** compared to
   OrchestrateAI CSS standards, **Then** visual elements use prescribed tokens, components,
   and layout patterns (dark theme, primary accent, card/button/input/tab styles).
2. **Given** an operator switches between manual accelerator and agent mode, **When**
   navigating via primary tabs or equivalent, **Then** context (profile, environment,
   active run) remains consistent across modes.
3. **Given** an agent or migration error, **When** displayed in the UI, **Then** error
   presentation uses platform-standard severity styling and actionable messaging.

---

### Edge Cases

- What happens when the LLM provider is unavailable mid-session? Agent mode MUST degrade
  gracefully; manual accelerator remains available.
- How does the system handle expired or revoked credentials during executor runs? Runs
  pause safely, state is checkpointed, and the operator is prompted to refresh credentials
  without leaking token values.
- What if the planner proposes scopes outside organizational policy? Plan MUST be blocked
  or require explicit override with documented reason before executor activation.
- What if validator and executor disagree repeatedly (flapping failures)? Retry limits MUST
  stop automatic loops and escalate to humans.
- What if the operator requests ambiguous natural language ("migrate everything")? Planner
  MUST clarify scope or produce a bounded plan requiring explicit confirmation.
- Concurrent manual and agent runs on the same repository MUST be prevented or coordinated
  to avoid conflicting migrations.
- Multiple live migration runs MAY execute in parallel across different assignment cohorts
  within the same profile; each repository MUST have at most one active live run at a time.
- What if the same repository appears in multiple assignments? System MUST detect overlap,
  surface it in the UI, and enforce a single active assignment per repository per profile
  unless an authorized override is recorded.
- What if an operator requests a wave that has no assigned repositories? UI and planner
  MUST indicate the cohort is empty and block live execution until repos are assigned.
- What if assignment metadata conflicts with phase gates (e.g., Wave 3 assignment while POC phase gate has not passed)? Live execution on Wave 3 is blocked only when **Wave 3's mapped** execution phase/wave gate fails—not solely because POC (or another phase) has not passed; program-order gate dependencies are optional via profile policy rules (FR-019), not the default.
- What if agent proposes org-level or production environment provisioning? Executor MUST
  route to Approver approval queue; Operator yes alone is insufficient.
- What if an operator runs live rollback with `pipelines` scope after ADO pipelines were
  disabled (FR-048)? System MUST symmetrically **re-enable** the matching ADO pipeline
  definitions for the rolled-back repos (Approver-approved, audited)—restoring ADO CI
  alongside GitHub workflow rollback on the migration branch.

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

- **CA-001**: Operators MUST have dry-run or preview before irreversible migration actions
  in both manual and agent-driven paths.
- **CA-002**: Destructive actions (live migration, cleanup, rollback, gate override,
  post-retry remedial execution) MUST require explicit confirmation or documented override
  with reason.
- **CA-003**: Secrets MUST NOT appear in logs, agent transcripts persisted for audit,
  reports, or UI beyond masked indicators—even when transcripts are retained long-term.
- **CA-004**: Plans, approvals, executor actions, validator results, retries,
  escalations, and full agent session transcripts MUST be auditable with correlation to
  migration profile and run identifiers.
- **CA-005**: Audit and agent transcript data MUST be stored in an immutable in-product
  retention store for at least seven years unless organizational policy mandates longer.
  v1 primary store: append-only PostgreSQL in cloud deployments (SQLite for local dev);
  optional asynchronous export to WORM-capable object storage (e.g., S3 Object Lock) for
  long-term archive when profile policy enables it.

### Functional Requirements

#### Platform composition

- **FR-001**: System MUST provide a manual migration accelerator operable without any
  LLM dependency.
- **FR-002**: System MUST provide a natural-language migration agent operable from the
  same product surface as the accelerator.
- **FR-003**: System MUST provide a web frontend conforming to OrchestrateAI (Agentic
  Platform) visual and component standards.

#### Planner–executor–validator architecture

- **FR-004**: System MUST implement three distinct subagent roles: Planner (plans),
  Executor (executes authorized migration work), Validator (verifies outcomes).
- **FR-005**: Planner MUST produce human-reviewable migration plans from natural language
  and available discovery context before mutating operations begin.
- **FR-006**: Executor MUST have access to migration APIs and operational capabilities
  needed to perform migrations but MUST ONLY execute operations authorized by an active
  approved plan or an explicit validator remediation instruction.
- **FR-007**: Executor MUST NOT run arbitrary commands or operations solely based on
  unstructured model output outside the authorized plan/remediation contract.
- **FR-008**: Validator MUST verify migration success across full accelerator scope:
  repository contents, repository dependencies, pipelines/workflows, boards (work items,
  queries, test plans, dashboards, and board configuration), access and teams, and any
  other ADO artifacts supported by the accelerator.
- **FR-009**: When Validator reports failures, system MUST route remediation instructions
  to Executor for re-execution of failed scopes OR escalate to human inspection in the UI.
- **FR-010**: System MUST enforce configurable retry limits for automatic validator→executor
  loops before mandatory human escalation.

#### Subagent skills (design-time)

- **FR-011**: Each subagent role MUST be implemented as a distinct subagent with a
  documented skill definition (purpose, inputs, outputs, guardrails) before production use.
- **FR-012**: Skill definitions MUST be versioned and reviewable as part of release
  governance for regulated environments.
- **FR-012a**: Planner, Executor, and Validator subagents MUST each have documented
  skills and operational tools covering every assignment type: ad-hoc repository lists,
  POC cohorts, pilot cohorts, and numbered waves (Wave 1 through Wave N)—with no
  assignment type restricted to manual UI-only operation.

#### Migration assignments (teams, waves, POC, pilot)

- **FR-027**: System MUST support migration assignments that group repositories under
  identifiable cohort types: POC, pilot, and numbered waves (Wave 1–N), plus ad-hoc
  repository selections not tied to a named wave.
- **FR-028**: Authorized coordinators MUST be able to create, update, and view assignments
  from the migration UI, including assigning specific repositories to a cohort and
  viewing which cohort each repository belongs to.
- **FR-029**: Agent and accelerator workflows MUST allow operators to scope discovery,
  planning, execution, validation, rollback, and status views to a selected assignment
  cohort.
- **FR-030**: Natural-language agent requests MUST resolve cohort references (POC, pilot,
  wave number, assignment name) to the correct repository set from persisted assignments.
- **FR-031**: Migration runs, validation results, and audit events MUST record the
  assignment cohort (type, identifier, and name) when execution is assignment-scoped.
- **FR-032**: System MUST prevent ambiguous or conflicting active assignments per repository
  within a migration profile unless an authorized reassignment or override is recorded.
- **FR-033**: Each migration assignment (POC, pilot, Wave N, or ad-hoc list) MUST map to
  exactly one execution phase/wave for planning, gate checks, and live execution; the
  assignment entity (cohort membership, team ownership) remains separate from the phase
  execution model (risk gates, batch runs, checkpoints).
- **FR-034**: Gate enforcement MUST evaluate the **mapped phase/wave for that assignment** before allowing live execution on that cohort; assignment selection alone does not bypass gates. Live execution is NOT blocked solely because a different execution phase (e.g., POC) has not passed its gate; optional program-order gate rules MAY be added via profile policy (FR-019).
- **FR-035**: System MUST allow parallel live migration runs across different assignment
  cohorts within a migration profile when gates and Approver approvals are satisfied.
- **FR-036**: System MUST enforce at most one active live migration run per repository per
  profile at any time, regardless of assignment cohort or mode (manual vs agent).

#### Audit retention and compliance

- **FR-037**: System MUST retain immutable audit records and full agent session transcripts
  (operator messages, subagent outputs, approvals, and linked run/validation artifacts) in
  product for at least seven years per migration profile policy. v1 writes to append-only
  PostgreSQL tables in cloud deployments (SQLite locally); profile MAY enable optional
  scheduled or async export of redacted audit bundles to WORM-capable object storage for
  long-term archive without replacing Postgres as the operational retrieval source.
- **FR-038**: Retained transcripts and audit exports (including optional S3 archive
  bundles) MUST redact or mask secrets and credential material; retention of full
  transcripts does not relax CA-003.
- **FR-039**: Operators and Approvers MUST be able to retrieve historical agent sessions,
  approvals, and migration runs within the retention period for compliance review. v1 MUST
  ship both: (1) an in-product **history browser** UI (filterable sessions, linked runs,
  approval records, redacted transcripts); and (2) **REST query/export endpoints** for
  programmatic retrieval and compliance export (redacted payloads only).

#### Boards and work tracking

- **FR-040**: System MUST migrate ADO Boards artifacts to GitHub-target equivalents with
  full parity intent: work items, queries, test plans, dashboards, and board configuration.
- **FR-041**: When GitHub cannot support a one-to-one mapping for a Boards artifact,
  system MUST migrate the closest supported equivalent and record the item in a documented
  gaps report delivered to operators and Approvers.
- **FR-042**: Validator MUST verify Boards migration outcomes per artifact type (work
  items, queries, test plans, dashboards, board configuration) and flag gaps documented
  in the gaps report as explicit validation findings, not silent success.

#### Pipeline conversion (ADO → GitHub Actions)

- **FR-043**: System MUST convert ADO pipelines to GitHub Actions workflows for **every
  repository** in the migration scope (assignment cohort or selected plan)—not optional
  per repo when pipelines scope is included in the program.
- **FR-044**: Generated workflows MUST be committed to a **dedicated migration branch**
  (configurable per profile; default `ado2gh/migrated-workflows`), not directly to the
  default branch; delivery via branch + pull request unless policy documents override.
- **FR-045**: System MUST build a repository dependency graph from ADO/pipeline discovery
  signals and execute git migration, pipeline conversion, and workflow branch push in
  **topological order** when dependencies exist; cycles MUST be reported and block
  automatic live execution until resolved or overridden by Approver.
- **FR-046**: Planner and Executor subagents MUST include dependency order and workflow
  branch strategy in every migration plan; Validator MUST confirm workflows on the
  migration branch and dependency order compliance.
- **FR-047**: System MUST validate that required GitHub target dependencies (secrets,
  environments, OIDC/service connection mappings, package/registry access) are configured
  before live workflow execution; plans MUST surface a readiness checklist and block live
  push when critical dependencies are missing.
- **FR-048**: After workflow branch push (live, Approver-approved), system MUST disable
  or remove corresponding ADO pipeline definitions for migrated repos so only the GitHub
  Actions path remains active for CI/CD on the migration branch outcome.
- **FR-049**: Generated workflows MUST reference configured GitHub dependencies (secrets,
  environments, reusable workflow refs) so that, when prerequisites are satisfied,
  workflows are runnable out-of-the-box on the migration branch without manual YAML edits.
- **FR-050**: Workflow layout MUST follow team maintainability rules per profile:
  **consolidated** (single primary workflow per repo when complexity is low) OR
  **modular** (reusable workflows / `workflow_call`, shared jobs, GitHub Actions best
  practices for repos with multiple pipelines or high complexity)—chosen by pipeline
  readiness classification, not arbitrary file splitting.

#### Dependency visibility and storage

- **FR-051**: When a required secret, service connection mapping, environment, or package
  dependency is missing, the **accelerator** (CLI, worker, API) MUST emit clear messages
  to the console and execution logs identifying what is missing, which repo/pipeline it
  affects, and remediation hints—without logging secret values or PATs.
- **FR-052**: Accelerator state persistence MUST use **SQLite** for local/offline
  execution and **PostgreSQL** for cloud or hyperscaler deployments; selection via
  environment configuration with documented defaults (`ADO2GH_STORAGE_BACKEND`).
- **FR-053**: Docker/deployment manifests for cloud MUST include PostgreSQL (or managed
  DB URL) when accelerator services require shared state across replicas.

#### Agent-assisted dependency provisioning

- **FR-054**: In **agent** flows, when readiness detects a missing secret or connection,
  the agent MUST ask the operator whether to create/add it in the target GitHub
  environment; if the operator confirms, the executor MUST provision the resource via
  authorized APIs (e.g., repository/org secret, environment, OIDC linkage per manifest)
  and re-run readiness before continuing migration.
- **FR-054a**: **Repo-scoped** provisioning (repository secrets, non-production
  environments) MAY proceed after **Operator** confirmation in the agent session.
- **FR-054b**: **Org-level** secrets and **production** environment creation or updates
  MUST require **Approver** sign-off in addition to Operator initiation; executor MUST
  NOT provision these without recorded Approver approval.
- **FR-055**: Auto-provision actions MUST be recorded in the audit trail with resource
  name, scope (org/repo/environment), tier (repo vs org/production), approving role,
  and confirming user; secret **values** MUST be collected via secure UI input or
  external vault reference—never from LLM-generated text in chat logs.
- **FR-056**: Manual accelerator paths MUST NOT auto-provision secrets without explicit
  operator CLI/UI action; logging and checklist guidance only unless operator invokes
  provision commands directly.

#### Manual accelerator

- **FR-013**: Accelerator MUST support the end-to-end migration operator journey:
  configure profile → discover → assess readiness → phase/wave planning → execute →
  validate → review status.
- **FR-014**: Accelerator MUST support dry-run execution paths aligned with live execution
  scope selection.

#### Agent experience

- **FR-015**: Agent MUST present planner output for operator review; any live
  (non-dry-run) executor migration action MUST NOT begin until an Approver explicitly
  approves the plan or remedial action (Operators may request; dry-run and read-only
  discovery MAY proceed after plan presentation without live-execution approval).
- **FR-016**: Agent sessions MUST show role-attributed messages (planner/executor/validator)
  and link to underlying run and validation artifacts.
- **FR-017**: Agent MUST support hyperscaler-agnostic LLM provider configuration (AWS,
  Azure, GCP, PCF, or equivalent enterprise-hosted endpoints) without binding to a single
  cloud vendor; operators MAY select among organization-approved models/providers.
- **FR-017a**: Migration agent MUST treat Azure DevOps as the authoritative source for
  migration data—plans, discovery, and executor inputs are derived from ADO APIs and
  repositories, not from external copies of source content unless explicitly synchronized
  from ADO.

#### Scope and policy

- **FR-018**: Agent MUST achieve full parity with the manual accelerator migration scope,
  including: repository contents, repository dependencies, pipelines/workflows, boards
  (work items, queries, test plans, dashboards, and board configuration), access and
  teams, phased wave execution, post-migration validation, scope-targeted rollback, and
  ADO cleanup operations available in the accelerator.
- **FR-019**: System MUST support **profile policy rules** (v1) that block or require additional Approver approval for high-risk actions in addition to the mandatory live-execution approval gate (FR-020). v1 rules are configurable per migration profile and evaluated after base per-assignment phase gates (FR-034). Minimum v1 rule types: (1) bulk wave/repo-count thresholds requiring extra approval or blocking live runs above threshold; (2) production execution phase requiring additional Approver sign-off beyond standard live approval; (3) cleanup and scope-targeted rollback requiring explicit Approver approval tickets; (4) optional program-order phase gates (e.g., require POC phase gate pass before later wave assignments may run live). v1 does NOT require a general-purpose arbitrary rule engine or DSL.
- **FR-020**: Human approval gates MUST require explicit Approver approval before any
  live (non-dry-run) executor migration action; Operators may submit requests; dry-run,
  discovery, and plan generation do not require Approver sign-off but live mutations MUST
  not proceed without it.
- **FR-026**: Operators MUST be able to initiate rollback through both accelerator and
  agent paths for scopes supported by the accelerator, with the same confirmation and
  audit requirements as live migration.
- **FR-026a**: When live rollback includes the **`pipelines`** scope for repos where ADO
  pipelines were disabled after migration (FR-048), the system MUST **re-enable** the
  corresponding ADO pipeline definitions (symmetric rollback)—not leave ADO disabled while
  only rolling back GitHub workflow artifacts. Re-enable actions MUST require Approver
  approval, be recorded in the audit trail, and be verifiable by the Validator.

#### Identity and access

- **FR-021**: System MUST restrict migration and agent actions to authenticated users with
  role-appropriate permissions for the selected migration profile.
- **FR-021a**: System MUST enforce three roles per migration profile: Coordinator
  (assignment management), Operator (dry-run and live-execution requests), and Approver
  (approval of live execution, rollback, cleanup, gate bypasses, and blocked assignment
  changes).
- **FR-021b**: Operators MUST NOT self-approve live (non-dry-run) migration, rollback,
  cleanup, or remedial executor actions; Approver action MUST be recorded in the audit trail.
- **FR-021c**: Coordinators MUST manage assignment cohorts and phase mapping but MUST NOT
  approve live execution or gate overrides unless explicitly granted Approver permission.
- **FR-022**: Authentication for v1 MUST use the existing migration **profile and token**
  management model (ADO/GH credentials per profile, UI settings flows).
- **FR-022a**: Enterprise **SSO** (e.g., Azure AD / OIDC for UI login) is **out of scope
  for v1** but MUST be documented as a **stretch goal** with non-breaking extension
  points in the auth layer (role mapping from IdP groups to Coordinator/Operator/Approver).

#### Frontend (OrchestrateAI)

- **FR-023**: UI MUST import and apply OrchestrateAI platform styles (official CSS tokens
  and component patterns per the internal Agentic Platform style guide) as the baseline theme.
- **FR-024**: UI MUST provide navigation between accelerator workflows, agent chat, run
  monitoring, validation results, assignment management, audit/history review, and
  settings without losing active profile context.
- **FR-025**: UI MUST display validator failures and human-escalation states with clear
  severity, scope, repository, and recommended next steps.
- **FR-025a**: UI MUST present assignment cohorts (POC, pilot, Wave 1–N, ad-hoc lists)
  with counts, progress, and filters usable in both manual accelerator and agent views.

### Key Entities *(include if feature involves data)*

- **Migration Profile**: Named configuration binding source ADO org, target GitHub org,
  credentials references, and policy settings.
- **Migration Plan**: Structured output from Planner (or manual planning)—repos, scopes,
  phase/wave, assignment cohort, sequence, risk notes, approval status.
- **Migration Run**: A single execution attempt correlated to profile, plan, mode
  (manual vs agent), checkpoints, and timestamps.
- **Executor Instruction**: Authorized unit of work derived from plan or validator
  remediation—scope, operation type, parameters (non-secret), correlation IDs.
- **Validation Result**: Per-repo, per-scope pass/fail with evidence summaries suitable
  for audit (e.g., content parity indicators, failed checks, Boards gaps report entries).
- **Boards Gaps Report**: Per-migration artifact listing ADO Boards items that could not
  map one-to-one to GitHub, the chosen fallback mapping, and validator disposition.
- **Remediation Loop**: Validator failure → executor retry count, scopes retried, outcome,
  escalation reason.
- **Agent Session**: Conversational context linking operator messages, subagent outputs,
  approvals, and underlying runs; full transcript retained immutably for the audit
  retention period.
- **Subagent Skill Definition**: Documented contract for a subagent role—capabilities,
  forbidden actions, input/output schema at product level, version.
- **Audit Event**: Immutable record of plan approval, execution, validation, override,
  escalation, assignment change, and Approver sign-off events.
- **User Role**: Coordinator, Operator, or Approver permission set bound to a migration
  profile (users may hold multiple roles when policy allows).
- **Migration Assignment**: A named cohort (POC, pilot, Wave N, or ad-hoc list) binding
  a set of repositories under a migration profile, with ownership metadata, status
  summary, and a required link to one execution phase/wave used for gates and batch runs.
- **Assignment Type**: Classification of a cohort—POC, pilot, numbered wave, or ad-hoc
  repository list—with rules for ordering and policy (e.g., gate prerequisites). Type
  informs default phase mapping but does not replace explicit phase/wave linkage.
- **Execution Phase/Wave**: The risk-scored migration phase used for gate checks, batch
  execution, and checkpoints—linked from each assignment but distinct from cohort membership.
- **Cohort Membership**: Link between a repository and exactly one active assignment per
  profile, with history of prior memberships for audit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can complete a single-repository manual migration (discover →
  dry-run → live run → validation) in under 30 minutes for a standard POC repository
  without agent assistance.
- **SC-002**: Operators can initiate a natural-language migration request and receive a
  reviewable plan within 2 minutes of submitting a well-formed request (excluding
  external API latency for large org discovery).
- **SC-003**: 95% of validator-detected remediable failures (within defined auto-retry
  policy) are resolved without human intervention before retry limit is reached, OR are
  escalated with complete failure context when not auto-remediable.
- **SC-004**: 100% of live (non-dry-run) migration, cleanup, and rollback actions in
  agent mode have explicit Approver approval recorded before executor mutates target
  systems (or documents override with reason where policy allows).
- **SC-005**: Zero incidents of credentials appearing in agent transcripts, UI exports,
  or standard audit logs during acceptance testing.
- **SC-006**: 100% of primary UI screens pass OrchestrateAI visual conformance review
  (checklist-based) for tokens, typography, buttons, inputs, tabs, cards, and error states.
- **SC-007**: When LLM services are unavailable, manual accelerator remains fully usable
  and agent mode shows a clear degraded state within 5 seconds of operator interaction.

- **SC-008**: Agent and accelerator paths demonstrate equivalent outcomes for each
  supported migration scope (repos, dependencies, pipelines, boards, access/teams) when
  given the same profile, scope selection, and execution mode (manual vs agent).
- **SC-009**: Coordinators can create a POC, pilot, or Wave N assignment and assign at
  least 10 repositories in under 5 minutes without CLI-only steps.
- **SC-010**: 100% of agent- and accelerator-scoped migration runs triggered from an
  assignment cohort execute only against repositories in that cohort (zero cross-cohort
  mutations in acceptance testing).
- **SC-011**: Planner, Executor, and Validator each demonstrate handling of POC, pilot,
  wave, and ad-hoc assignment types in acceptance scenarios with recorded skill/tool
  coverage matrix sign-off.
- **SC-012**: When two non-overlapping assignment cohorts are approved, both can execute
  live migrations concurrently with no repository appearing in more than one active live
  run (verified in acceptance testing).
- **SC-013**: 100% of agent sessions, Approver approvals, and migration runs during
  acceptance testing are retrievable from the immutable audit store for the configured
  retention period (minimum seven years).
- **SC-014**: Boards migration acceptance includes work items, queries, test plans,
  dashboards, and board configuration; any unmappable artifact appears in a gaps report
  reviewed by operators with zero undocumented Boards gaps in acceptance testing.
- **SC-015**: For repos with green dependency readiness, first `workflow_dispatch` or PR
  trigger on the migration branch succeeds without post-migration YAML edits in ≥90% of
  acceptance test repos (excludes intentional `[MANUAL]` steps).
- **SC-016**: 100% of live pipeline migrations disable or remove source ADO pipelines for
  the migrated scope and leave only GitHub Actions workflows on the migration branch; 100%
  of live `pipelines`-scope rollbacks (FR-026a) re-enable matching ADO pipelines when they
  had been disabled by migration.
- **SC-017**: 100% of readiness failures in accelerator runs produce structured log lines
  naming missing dependencies (zero silent skips without log output in acceptance tests).
- **SC-018**: Agent-assisted secret provisioning completes readiness re-check and records
  audit events when operator confirms creation in ≥95% of scripted **repo-scoped**
  missing-secret scenarios; org/production provisions require Approver in 100% of
  acceptance test cases.

## Assumptions

- The existing migration CLI and API capabilities form the execution backbone; agent
  executor actions map to established migration operations rather than novel ad-hoc scripts.
- OrchestrateAI styles are defined in the internal Agentic Platform style guide and
  official CSS asset bundle referenced by the migration UI product.
- Migration operators are technical staff (platform engineers, release engineers) familiar
  with ADO and GitHub concepts; the agent reduces orchestration burden, not basic VCS
  literacy.
- Phased/risk-based migration (POC → pilot → waves) remains the default operating model
  for both manual and agent paths.
- Profile **policy rules** (FR-019) are configurable per migration profile in v1 via
  structured settings (not a general rule DSL): bulk thresholds, production-phase extra
  Approver, cleanup/rollback approval, and optional program-order phase prerequisites.
- Subagent skill documents will be authored during planning/implementation phases; this
  spec defines the requirement but not the final skill text.
- Default automatic retry limit is three remediation attempts per repository per run
  unless organizational policy overrides (configurable).
- Authentication for v1 uses migration profile and token storage (ADO/GH PATs per profile);
  enterprise SSO is not required for v1.
- LLM inference may run on any organization-approved hyperscaler (AWS, Azure, GCP, PCF);
  provider choice is a deployment configuration, not a product constraint.
- All migration source content and metadata required for planning and execution originates
  from Azure DevOps; GitHub is the target for migrated artifacts.
- Wave numbering is sequential from 1 through N per migration profile; organizations may
  define how many waves exist; the product supports arbitrary N without a fixed upper
  limit in the specification.
- POC and pilot are distinct assignment types with potentially different default policies
  (e.g., stricter gates for pilot than POC) configurable per organization; each assignment
  still maps to an execution phase/wave for gate enforcement.
- Team coordinators with assignment permissions may differ from operators who execute
  migrations; Approvers are a distinct role for live execution and override decisions,
  enforced per migration profile policy (users may hold multiple roles when policy allows).
- Immutable in-product audit storage retains full agent transcripts, approvals, and run
  history for at least seven years; secrets are redacted at capture and export. v1
  operational store is append-only Postgres (SQLite dev); optional WORM object-storage
  export is profile-configurable for long-term archive.
- Boards migration targets full parity (work items, queries, test plans, dashboards, board
  configuration); unmappable ADO artifacts are recorded in a gaps report rather than omitted.

## Stretch goals (post-v1)

- **Enterprise SSO** for UI login (Azure AD / OIDC) with IdP group mapping to
  Coordinator, Operator, and Approver roles—without replacing profile-based credential
  storage for ADO/GitHub migration tokens in v1.
