# Specs Directory

This directory contains all feature specifications for the ADO2GitHub Migration Accelerator.

## Active Specs

| Spec | Title | Status | Summary |
|------|-------|--------|---------|
| 001 | Agentic Migration Platform | active | Platform architecture, RBAC, audit, boards, pipelines, assignments |
| 003 | Local Agent IDE / MCP | active | Local IDE agent with MCP tool exposure |
| 004 | Agent PEV RBAC | active | Planner-Executor-Validator loop with role-based access control |
| 006 | LLM Model Catalog | active | LLM provider registry, model catalog, validation gates |
| 007 | Cloud LLM Credentials | active | Ambient cloud credential detection, probing, and approval |
| 008 | Migration UI Refactor | active | Unified migration UI with tab-based navigation |
| 009 | Pipeline Step Decoupling | active | Decouple pipeline step definitions from execution logic |
| 010 | Enterprise Audit & Simplification | active | Repository audit, state layer consolidation, file decomposition, Docker/deployment simplification, spec consolidation, module structure flattening, UI consolidation, scripts cleanup, enterprise readiness hardening |
| 011 | Agent PEV Rebuild | active | Rebuild agent PEV loop with improved orchestration, session management, and tool routing |

## Archived Specs

| Spec | Title | Status | Archived Because | Implementation Pointer |
|------|-------|--------|------------------|----------------------|
| 002 | Login Bootstrap | archived (implemented) | Login flow, admin bootstrap, and session management fully implemented | `ado2gh/auth/`, `services/accelerator_api/auth_routes.py` |
| 005 | Profile Onboarding | archived (implemented) | Profile creation, approval workflow, and credential validation fully implemented | `ado2gh/api/settings_store.py`, `ado2gh/api/settings_profiles.py`, `ado2gh/api/credential_validation.py` |

## Superseded Specs

None at this time.

## Spec Lifecycle

- **active**: Spec is under development or has pending implementation work
- **archived**: Spec has been fully implemented; no further work expected. Moved to `specs/archive/`.
- **superseded**: Spec has been replaced by a newer spec. The superseding spec is noted in the table above.
