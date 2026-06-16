# Execution Governance Checklist: Agentic ADO-to-GitHub Migration Platform

**Purpose**: Validate requirements quality for live execution gates, policy rules, rollback, audit retention, and Approver workflows—before implementation and PR review of spec/plan/contracts.
**Created**: 2026-06-16
**Feature**: [spec.md](../spec.md)

**Note**: Unit tests for requirements writing—not implementation verification. Check whether the spec (and supporting plan/contracts) states what must be true, clearly and consistently.

**Focus**: Enterprise safeguards, assignment-linked gates, symmetric rollback, audit/compliance retrieval  
**Depth**: Standard (PR reviewer gate)  
**Audience**: Spec/plan reviewers and implementers before `/speckit-implement`

## Requirement Completeness

- [ ] CHK001 Are live-execution authorization requirements defined for every mutation path (manual accelerator, API, agent executor)? [Completeness, Spec §FR-020, §CA-002]
- [ ] CHK002 Are per-assignment phase gate requirements documented before cohort live runs, including override mechanics? [Completeness, Spec §FR-034]
- [ ] CHK003 Are v1 profile policy rule types enumerated with minimum required categories (bulk thresholds, production extra Approver, cleanup/rollback approval, optional program-order gates)? [Completeness, Spec §FR-019]
- [ ] CHK004 Are scope-targeted rollback requirements defined for both accelerator and agent paths with scope granularity? [Completeness, Spec §FR-026, §FR-026a]
- [ ] CHK005 Are symmetric ADO pipeline re-enable requirements specified when `pipelines` rollback follows FR-048 disable? [Completeness, Spec §FR-026a, §FR-048]
- [ ] CHK006 Are immutable audit retention requirements defined for transcripts, approvals, runs, and linked artifacts with minimum duration? [Completeness, Spec §FR-037, §CA-005]
- [ ] CHK007 Are historical retrieval requirements specified for both in-product UI and programmatic export? [Completeness, Spec §FR-039, §FR-024]
- [ ] CHK008 Are tiered agent provisioning authorization rules documented for repo-scoped vs org/production resources? [Completeness, Spec §FR-054a, §FR-054b]
- [ ] CHK009 Are requirements for recording assignment cohort on runs, validation, and audit events when execution is assignment-scoped? [Completeness, Spec §FR-031]
- [ ] CHK010 Are concurrent execution rules documented (parallel cohorts vs one live run per repo)? [Completeness, Spec §FR-035, §FR-036]

## Requirement Clarity

- [ ] CHK011 Is “per-assignment phase gate only” distinguished from optional program-order gates without ambiguity? [Clarity, Spec §FR-034, Clarifications Session 2026-06-16]
- [ ] CHK012 Are Approver vs Operator permissions explicitly separated for live migration, rollback, cleanup, gate override, and remedial execution? [Clarity, Spec §FR-021a–c, §FR-021b]
- [ ] CHK013 Is “symmetric rollback” for pipelines scope defined with paired ADO re-enable behavior and approval requirements? [Clarity, Spec §FR-026a, Edge Cases]
- [ ] CHK014 Are secret/credential handling rules explicit for persisted transcripts and exports (masking, never values in chat)? [Clarity, Spec §CA-003, §FR-038]
- [ ] CHK015 Is the v1 audit storage model specified (Postgres operational store vs optional WORM object export)? [Clarity, Spec §FR-037, §CA-005, Clarifications]
- [ ] CHK016 Are policy rules scoped as “structured profile settings, not a general DSL” in v1? [Clarity, Spec §FR-019, Assumptions]
- [ ] CHK017 Are workflow branch delivery requirements unambiguous (dedicated branch, PR, not default branch)? [Clarity, Spec §FR-044]
- [ ] CHK018 Is the default automatic remediation retry limit stated or marked configurable with a default? [Clarity, Spec §FR-010, Assumptions]

## Requirement Consistency

- [ ] CHK019 Do gate enforcement requirements align between FR-034 (per-assignment) and edge-case narrative for cross-phase scenarios? [Consistency, Spec §FR-034, Edge Cases]
- [ ] CHK020 Do rollback requirements align between FR-026, FR-026a, FR-048, and SC-016 (disable on migrate, re-enable on rollback)? [Consistency, Spec §FR-026a, §SC-016]
- [ ] CHK021 Do agent executor guardrails (FR-006, FR-007) align with whitelist/tool contract expectations in agent PEV design? [Consistency, Spec §FR-006–007, contracts/agent-pev-api.md]
- [ ] CHK022 Do manual accelerator provisioning constraints (FR-056) align with agent provision flows (FR-054)? [Consistency, Spec §FR-054, §FR-056]
- [ ] CHK023 Are plan.md and spec.md consistent on pipelines rollback ADO re-enable behavior? [Consistency, Spec §FR-026a, plan.md Rollback & phase gates] — **resolved 2026-06-16**: plan aligned to symmetric rollback (FR-026a)
- [ ] CHK024 Do RBAC role definitions in user roles match FR-021a–c and Clarifications? [Consistency, Spec User roles, §FR-021a–c]

## Acceptance Criteria Quality

- [ ] CHK025 Can SC-004 (100% Approver approval before live agent mutations) be objectively verified from requirements without implementation assumptions? [Measurability, Spec §SC-004]
- [ ] CHK026 Can SC-013 (retrievable audit for retention period) be tested given FR-039 UI+API delivery? [Measurability, Spec §SC-013, §FR-039]
- [ ] CHK027 Is SC-016 measurable for both ADO disable on migrate and ADO re-enable on pipelines rollback? [Measurability, Spec §SC-016, §FR-026a]
- [ ] CHK028 Is SC-017 tied to structured log line requirements for missing dependencies? [Measurability, Spec §SC-017, §FR-051]
- [ ] CHK029 Is SC-018 acceptance scoped to repo-scoped provision with explicit Approver requirement for org/production? [Measurability, Spec §SC-018, §FR-054a/b]

## Scenario Coverage

- [ ] CHK030 Are dry-run requirements defined for live-parity scope selection on accelerator paths? [Coverage, Spec §FR-014, §CA-001]
- [ ] CHK031 Are requirements defined for Operator **request** vs Approver **approve** flows for live migration and rollback? [Coverage, Spec §FR-020, §FR-026]
- [ ] CHK032 Are remediation loop requirements covered (validator→executor routing, retry limits, escalation)? [Coverage, Spec §FR-009, §FR-010, US3]
- [ ] CHK033 Are assignment-scoped discovery, planning, execution, validation, rollback, and status views required consistently? [Coverage, Spec §FR-029]
- [ ] CHK034 Are agent session requirements defined for role-attributed messages and linkage to runs/validation artifacts? [Coverage, Spec §FR-016]

## Edge Case Coverage

- [ ] CHK035 Are requirements defined when LLM provider is unavailable mid-session (degradation, accelerator fallback)? [Edge Case, Edge Cases §LLM unavailable]
- [ ] CHK036 Are requirements defined for expired/revoked credentials during runs (pause, checkpoint, no token leak)? [Edge Case, Edge Cases]
- [ ] CHK037 Are requirements defined for empty assignment cohorts blocking live execution? [Edge Case, Edge Cases]
- [ ] CHK038 Are requirements defined for overlapping repo assignments and single active membership? [Edge Case, Spec §FR-032, Edge Cases]
- [ ] CHK039 Are requirements defined for dependency graph cycles blocking automatic live execution? [Edge Case, Spec §FR-045]
- [ ] CHK040 Are requirements defined for org-level/production provision requiring Approver beyond Operator chat confirmation? [Edge Case, Spec §FR-054b, Edge Cases]

## Non-Functional Requirements

- [ ] CHK041 Are performance targets stated for manual POC end-to-end and agent plan latency? [NFR, Spec §SC-001, §SC-002]
- [ ] CHK042 Are security requirements for 7+ year retention compatible with secret redaction at capture and export? [NFR, Spec §CA-003, §FR-038]
- [ ] CHK043 Are scalability assumptions stated for large org discovery and parallel cohort execution? [NFR, Spec §FR-035, Assumptions]
- [ ] CHK044 Are observability/logging requirements defined for dependency blockers beyond FR-051 console/log lines? [Gap, NFR]

## Dependencies & Assumptions

- [ ] CHK045 Is the assumption that existing CLI/API forms the execution backbone documented and bounded? [Assumption, Assumptions]
- [ ] CHK046 Are storage backend assumptions (SQLite dev, Postgres prod, optional S3 export) documented in requirements? [Dependency, Spec §FR-052, §FR-037]
- [ ] CHK047 Is v1 authentication scope (profile tokens) vs SSO stretch goal explicitly bounded? [Assumption, Spec §FR-022, §FR-022a, Stretch goals]
- [ ] CHK048 Are external dependency failure modes (ADO/GitHub API, rate limits) addressed in requirements or explicitly deferred? [Gap, Integration]

## Ambiguities & Conflicts

- [ ] CHK049 Is SC-003 (“95% remediable failures resolved without human intervention”) defined with a remediable-failure taxonomy or marked as post-v1 KPI? [Ambiguity, Spec §SC-003]
- [ ] CHK050 Is git migration strategy (mirror vs GEI) intentionally out of spec scope given existing `ado2gh` config? [Gap, Assumption]
- [ ] CHK051 Do contracts (rollback-gates-api, agent-pev-api) trace to spec FRs without contradicting them? [Traceability, contracts/]
- [ ] CHK052 Are subagent skill versioning requirements (FR-012) testable without final skill text in spec? [Ambiguity, Spec §FR-012, Assumptions]

## Notes

- Check items off as completed: `[x]`
- Record findings inline; link to spec sections or contract files when resolving gaps
- Resolve [Conflict] CHK023 by updating plan.md or spec after reviewer decision — **done** (plan synced to FR-026a)
- Pair with [requirements.md](./requirements.md) for general spec quality gates
