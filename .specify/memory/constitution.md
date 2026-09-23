<!--
Sync Impact Report
==================
Version change: (none) → 1.0.0
Modified principles: Initial ratification — no prior principles
Added sections:
  - Core Principles (6)
  - Technology & Domain Constraints
  - Quality Gates & Development Workflow
  - Governance
Removed sections: None
Templates:
  - .specify/templates/plan-template.md — ✅ updated (Constitution Check gates)
  - .specify/templates/spec-template.md — ✅ updated (safeguards + compliance note)
  - .specify/templates/tasks-template.md — ✅ updated (mandatory tests, coverage gate)
  - .specify/templates/constitution-template.md — ⚠ pending (generic template; no project-specific change required)
  - CLAUDE.md — ⚠ pending (optional cross-reference to constitution in future edit)
Follow-up TODOs: None
-->

# ado2gh Constitution

## Core Principles

### I. Clean Code & Readability

All generated and maintained code MUST be readable and follow established clean-code
practices: clear naming, small focused units, minimal nesting, consistent formatting,
and no gratuitous complexity.

**Rationale**: Migration tooling is operated under pressure at scale; readable code
reduces operational risk and speeds incident response.

### II. Documented Functions & Classes

Every function and class MUST include a docstring (or equivalent structured comment)
that describes its purpose, inputs (parameters), and outputs (return values or side
effects). Module-level docstrings SHOULD summarize the module's responsibility when
non-obvious.

**Rationale**: Architects, operators, and LLM agents must understand behavior without
tracing implementation; documentation is a first-class deliverable.

### III. Deprecation Policy

Functions, classes, or modules that are deprecated MUST be explicitly marked (e.g.,
`@deprecated` decorator, `warnings.warn` with `DeprecationWarning`, or documented
deprecation in docstring and changelog) with a removal timeline OR removed from the
project entirely. Silent deprecation is forbidden.

**Rationale**: Stale APIs confuse operators and agents during long-running migrations;
explicit lifecycle management prevents accidental use of unsafe paths.

### IV. Intuitive Architecture & Naming

Folder structure, module names, and public APIs MUST be descriptive enough that an
architect can infer component responsibilities from layout alone. Names MUST reflect
domain intent (e.g., migration phases, pipelines, validation) rather than generic
or opaque labels.

**Rationale**: This project spans discovery, execution, validation, and cleanup;
navigable structure is essential for human review and agentic code generation.

### V. Enterprise Migration Safeguards (NON-NEGOTIABLE)

As an LLM/agent-assisted tool targeting banking and insurance-grade environments,
the system MUST implement industry-aligned guardrails:

- **Human-in-the-loop**: Destructive or irreversible actions (production migrations,
  cleanup, rollback overrides, gate bypasses) REQUIRE explicit operator confirmation
  or documented override with reason.
- **Dry-run and phased execution**: Support preview/dry-run paths and risk-based
  phasing before bulk execution.
- **Fail-safe defaults**: Prefer safe failure over silent partial success; checkpoint
  and resume for long runs.
- **Secrets and credentials**: Never log, commit, or echo tokens/PATs; use environment
  variables and secure stores.
- **Auditability**: Migration state, gate decisions, and overrides MUST be traceable
  (e.g., SQLite state DB, structured logs, reports).
- **Migration-appropriate patterns**: Use patterns suited to batch migration (state
  persistence, idempotency, scope-targeted rollback, validation after transfer).

**Rationale**: Regulated enterprises require demonstrable control, not just working
scripts.

### VI. Comprehensive Testing & Coverage (NON-NEGOTIABLE)

Every function and meaningful code path MUST be covered by automated tests (unit,
integration, or contract as appropriate). The project MUST maintain **at least 85%
line coverage** on the `ado2gh` package, enforced in CI. New code MUST not reduce
coverage below the threshold without a documented, approved exception in the
implementation plan's Complexity Tracking table.

**Rationale**: Migration bugs can corrupt thousands of repositories; test depth is
the primary safety net alongside operational gates.

## Technology & Domain Constraints

- **Language**: Python >= 3.9 (see `pyproject.toml`).
- **Domain**: Azure DevOps → GitHub enterprise migration at scale (repos, pipelines,
  metadata, phased rollout).
- **Runtime dependencies**: `git` on PATH; optional `gh` + `gh-gei` for GEI strategy.
- **Testing stack**: `pytest` (dev dependency); coverage measured via `pytest-cov` or
  equivalent and reported in CI.
- **Agent context**: `CLAUDE.md` and README document CLI workflow, package layout, and
  operational commands; agents MUST align generated code with existing `ado2gh/` structure.

## Quality Gates & Development Workflow

1. **Before merge**: All tests pass; coverage >= 85% on `ado2gh`; lint/format clean
   if configured.
2. **Constitution Check** (in implementation plans): Explicit pass/fail against all
   six principles before Phase 0 research and again after Phase 1 design.
3. **Feature specs**: User stories MUST remain independently testable; safeguards
   (dry-run, confirmation, audit) MUST appear in requirements when touching migration
   execution or cleanup.
4. **Tasks**: Test tasks are mandatory, not optional; each user story includes tests
   that can fail before implementation where behavior is new.
5. **Reviews**: PRs MUST verify docstrings, naming clarity, deprecation hygiene, and
   safeguard behavior for migration-sensitive changes.

## Governance

This constitution supersedes ad-hoc coding preferences for all Spec Kit–driven work
and agent-generated changes in this repository.

**Amendment procedure**:

1. Propose changes via `/speckit-constitution` or a documented PR editing this file.
2. Bump `CONSTITUTION_VERSION` per semantic versioning (MAJOR: principle removal or
   incompatible redefinition; MINOR: new principle or material expansion; PATCH:
   clarifications only).
3. Update dependent templates (`plan-template.md`, `spec-template.md`,
   `tasks-template.md`) and agent guidance when principles change.
4. Record rationale in the Sync Impact Report HTML comment at the top of this file.

**Compliance review**: Every implementation plan and significant PR MUST include an
explicit Constitution Check. Violations require entry in Complexity Tracking with
justification and approval before merge.

**Version**: 1.0.0 | **Ratified**: 2026-06-16 | **Last Amended**: 2026-06-16
