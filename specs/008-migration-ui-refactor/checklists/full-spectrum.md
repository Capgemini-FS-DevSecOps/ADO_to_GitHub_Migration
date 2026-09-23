# Full-Spectrum Requirements Checklist: Unified Migration UI

**Purpose**: Validate requirements quality across all domains with enterprise safeguard emphasis
**Created**: 2026-06-23
**Audience**: Spec author (self-check before planning handoff)
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md) | [tasks.md](../tasks.md)

## Requirement Completeness

- [ ] CHK001 Are all 6 user stories mapped to at least one functional requirement (FR-###)?
- [ ] CHK002 Does the spec define behavior for the "no scan results yet" state in the discovery tab?
- [ ] CHK003 Does the spec define what happens when a user initiates migration for a repo with zero dependencies?
- [ ] CHK004 Are all 7 data model entities (DiscoveryResult, DependencyEdge, MigrationWave, WaveRepository, PreMigrationForm, MigrationOperation, AuditEvent) referenced in at least one functional requirement?
- [ ] CHK005 Does the spec define wave naming constraints (min/max length, allowed characters) beyond the assumption "1-100 characters"?
- [ ] CHK006 Is there a requirement defining how many migration waves can exist simultaneously?
- [ ] CHK007 Does the spec define what constitutes a "dependency" for the dependency graph (pipeline, service, artifact — are there others)?
- [ ] CHK008 Are all 6 edge cases from the spec addressed by at least one functional requirement or task?
- [ ] CHK009 Does the spec define the expected behavior when dependency analysis reveals no dependencies (empty graph)?
- [ ] CHK010 Is there a requirement for how scan results are cleaned up or expired, or is persistence indefinite?

## Requirement Clarity

- [ ] CHK011 Is "clean, full-page interface" (FR-002) quantified with specific layout criteria (e.g., no sidebar, no header bar, conversation fills viewport)?
- [ ] CHK012 Is "unified navigation structure" (FR-001) defined with specific tab names or grouping rules?
- [ ] CHK013 Is "pipeline configuration" in the pre-migration form (FR-008) defined with specific required fields or field types?
- [ ] CHK014 Is "team/permission mapping" in the pre-migration form defined with expected format (JSON structure, key-value pairs, role list)?
- [ ] CHK015 Does FR-005 ("on-demand migration") define what "migrate" means operationally (repos only, or repos + pipelines + secrets)?
- [ ] CHK016 Is "dependency order" in FR-007 defined as topological sort order, or could it be interpreted differently?
- [ ] CHK017 Does SC-003 ("2 minutes for 500 repositories") define whether this is wall-clock or per-org time?
- [ ] CHK018 Does SC-004 ("5 minutes for 20 dependencies") define whether this includes dry-run preview time or only execution time?
- [ ] CHK019 Is "manual refresh" (clarification) defined as a button click, API call, or both?

## Requirement Consistency

- [ ] CHK020 Do the spec and plan agree on the dependency graph algorithm (spec says "topological", plan says "Kahn's algorithm")?
- [ ] CHK021 Is the wave execution strategy consistent between spec ("dependency order"), clarifications ("sequential"), and plan ("sequential with dependency validation")?
- [ ] CHK022 Are the pre-migration form fields consistent between spec (FR-008), clarifications (target org, team mapping, pipeline config), and data model (PreMigrationForm entity)?
- [ ] CHK023 Does the spec consistently use "migration wave" vs "wave" without introducing ambiguity?
- [ ] CHK024 Is the scan persistence model consistent between spec ("persist with manual refresh"), data model (DiscoveryResult entity), and contracts (discovery API)?

## Acceptance Criteria Quality

- [ ] CHK025 Can US1 acceptance scenario 1 ("unified tab structure") be tested without subjective judgment on "coherent workflow"?
- [ ] CHK026 Can US2 acceptance scenario 1 ("no pipeline or PEV indicators") be tested with a definitive UI element check?
- [ ] CHK027 Can US4 acceptance scenario 2 ("all dependent repositories migrated") be verified with a specific count comparison?
- [ ] CHK028 Can US5 acceptance scenario 2 ("migrated in dependency order") be verified with order sequence logging?
- [ ] CHK029 Can US6 acceptance scenario 2 ("LLM uses this information") be objectively verified, or does it require subjective assessment?
- [ ] CHK030 Are all 8 success criteria (SC-001 through SC-008, excluding deferred SC-007) independently measurable?

## Edge Case Coverage

- [ ] CHK031 Does the spec define the error message format for circular dependency detection (edge case 2)?
- [ ] CHK032 Does the spec define whether partial scan failures (edge case 1) should display failed orgs alongside successful ones?
- [ ] CHK033 Does the spec define the retry policy for ADO credential expiration during migration (edge case 4)?
- [ ] CHK034 Does the spec define the threshold for "too large to display" dependency graphs (edge case 5)?
- [ ] CHK035 Does the spec define whether mid-wave failures (edge case 6) allow re-running the failed repos or require full wave restart?

## Enterprise Safeguard Requirements

- [ ] CHK036 Does FR-011 (dry-run) define what dry-run preview includes (repo list, dependency graph, form fields, or all)?
- [ ] CHK037 Does FR-012 (explicit confirmation) define the confirmation mechanism (button, dialog, typed confirmation)?
- [ ] CHK038 Does FR-010 (audit trails) define the audit event schema (what fields are captured per event)?
- [ ] CHK039 Does the spec define who can initiate on-demand migration vs who can initiate wave migration (role-based access)?
- [ ] CHK040 Does the spec define whether dry-run results are persisted or ephemeral?
- [ ] CHK041 Does the spec address secret handling during pre-migration form submission (CA-003 — secrets not in logs/reports)?
- [ ] CHK042 Does the spec define rollback behavior for on-demand migration failures (distinct from wave rollback)?

## Performance Requirements

- [ ] CHK043 Does SC-008 ("30 seconds") define what "typical developer machine" means (CPU, RAM, disk specs)?
- [ ] CHK044 Does the spec define performance expectations for the dependency graph visualization in the UI (render time for N nodes)?
- [ ] CHK045 Does the spec define the maximum acceptable API response time for discovery results with 1000 repos?

## Cross-Artifact Traceability

- [ ] CHK046 Does every FR-### have at least one task in tasks.md with explicit reference?
- [ ] CHK047 Does every SC-### (in-scope) have at least one task that validates or implements it?
- [ ] CHK048 Do all data model entities have corresponding database migration tasks?
- [ ] CHK049 Do all API contract endpoints have corresponding implementation tasks?
- [ ] CHK050 Do convergence tasks (T093-T101) trace back to specific FRs or constitution principles?
