# Implementation Plan: Unified Migration UI

**Branch**: `008-migration-ui-refactor` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-migration-ui-refactor/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Refactor the migration accelerator UI to combine Discovery, Readiness, Workflows, and Validation tabs into a unified navigation structure. Streamline the agent interface to a clean, full-page experience (Claude Code-like) without pipeline/PEV indicators. Simplify scanning to retrieve ADO organization data without automatic wave assignment. Enable on-demand migration with full transitive dependency analysis. Support custom-named migration waves for bulk operations. Add intelligent dependency graph analysis with pre-migration forms. Optimize local development stack startup to under 30 seconds.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.9+ (backend), TypeScript/Next.js 14 (frontend)

**Primary Dependencies**: FastAPI (accelerator API, agent API), Next.js 14 (UI), SQLite/PostgreSQL (state storage), pytest (testing)

**Storage**: SQLite (local dev), PostgreSQL (production) for migration state and discovery results

**Testing**: pytest for backend, vitest for frontend, contract tests for API boundaries

**Target Platform**: Linux server (production), Docker Compose (local development)

**Project Type**: Web application (Next.js frontend + FastAPI backend services)

**Performance Goals**: Local stack startup <30s, ADO scan <2min (500 repos), on-demand migration <5min (20 dependencies)

**Constraints**: Full transitive dependency graph analysis, sequential wave execution, 30s local startup target

**Scale/Scope**: Up to 1000 repositories per organization, full transitive dependency analysis

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Plan describes readable structure; no unjustified complexity |
| II. Documentation | New modules/functions will include purpose/inputs/outputs docstrings |
| III. Deprecation | No silent legacy paths; deprecations marked or removed |
| IV. Architecture & Naming | Folder/module names match domain (migration, phase, pipeline, validate) |
| V. Enterprise Safeguards | Dry-run/HITL/audit/secrets handling addressed for destructive scope |
| VI. Testing (85%+) | Test strategy defined; coverage gate will not regress below 85% on `ado2gh` |

**Result**: [X] PASS — all gates satisfied (post-design re-check)

## Project Structure

### Documentation (this feature)

```text
specs/008-migration-ui-refactor/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
services/
├── accelerator_api/
│   ├── main.py           # FastAPI service (existing, to be modified)
│   └── ...
├── agent/
│   ├── main.py           # FastAPI service (existing, to be modified)
│   └── ...

apps/migration-ui/
├── src/
│   ├── app/
│   │   ├── settings/
│   │   │   ├── discovery/     # New unified discovery tab
│   │   │   ├── migrate/       # New migrate tab
│   │   │   └── agent/         # Clean agent interface
│   │   └── components/
│   └── lib/

ado2gh/
├── api/
│   ├── discovery_store.py    # New: scan result persistence
│   ├── dependency_graph.py   # New: dependency analysis
│   ├── migration_executor.py # New: migration execution
│   ├── form_validator.py     # New: pre-migration form validation
│   ├── audit_logger.py       # New: audit event logging
│   ├── migrations/           # New: database schema migrations
│   ├── models/               # New: ORM models for new entities
│   └── ...
└── agents/
    └── ...

tests/
├── contract/
├── integration/
└── unit/
```

**Structure Decision**: Web application with existing FastAPI backend services (accelerator_api, agent) and Next.js frontend (apps/migration-ui). New backend modules in ado2gh/api for discovery and dependency analysis. New frontend pages in apps/migration-ui/src/app/settings for unified tabs.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | No violations — all constitution gates passed | — |
