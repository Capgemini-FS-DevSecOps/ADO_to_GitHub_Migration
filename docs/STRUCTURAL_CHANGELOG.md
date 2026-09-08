# Structural Change Log

**Feature**: 010-enterprise-audit-simplification  
**Created**: 2026-06-24  

This file records every file move, rename, split, merge, deletion, and gitignore action performed during the Enterprise Audit & Framework Simplification. Entries are append-only.

## Entry Format

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|

## 2026-06-24 — Phase 2: Test Baseline

- **Tests**: 537 passed, 0 failed
- **Coverage**: 60.98% (below 85% constitution gate — baseline recorded, target is to not regress below this and improve to 85% via US9)
- **Pre-existing issues**: Duplicate test file basenames in tests/contract/ and tests/integration/ cause collection errors when both directories are collected together

## 2026-06-24 — Setup: Gitignore Artifacts & Stray Venv Cleanup

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `cloud_credentials.json` | — | gitignored | Runtime-generated artifact should not be tracked (FR-005) | yes | pass | 2026-06-24T18:55:00Z |
| `llm_models.json` | — | gitignored | Runtime-generated artifact should not be tracked (FR-005) | yes | pass | 2026-06-24T18:55:00Z |
| `ui_settings.json` | — | gitignored | Runtime-generated artifact should not be tracked (FR-005) | yes | pass | 2026-06-24T18:55:00Z |
| `ado2gh/__pycache__/ib-ai-agent/` | — | deleted | Stray virtual environment artifact from previous experiment (FR-002) | yes | pass | 2026-06-24T18:55:00Z |

## 2026-06-24 — US1: Dead Code Removal & CLI Wrapper Cleanup

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/api/migrations/008_unified_ui.py` | — | deleted | Zero inbound imports from ado2gh/, services/, or tests/; schema migration never integrated (FR-004, FR-007) | yes | pass | 2026-06-24T19:10:00Z |
| `ado2gh/cli.py` | — | deleted | Redundant wrapper — identical re-export already in `ado2gh/cli/__init__.py`; Python package takes precedence over module file (FR-013) | yes | pass | 2026-06-24T19:10:00Z |

## 2026-06-24 — US2: State Layer Consolidation

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/state/base.py` | — | created | Abstract base class `StateDBBase` defining shared interface for all backends (FR-009) | yes | pass | 2026-06-24T19:30:00Z |
| `ado2gh/state/db.py` | `ado2gh/state/sqlite_db.py` | split | Concrete SQLite implementation extracted to `sqlite_db.py` as `SQLiteStateDB`; `db.py` now re-export shim (FR-009, FR-013) | yes | pass | 2026-06-24T19:30:00Z |
| `ado2gh/state/postgres_db.py` | — | modified | `PostgresStateDB` now inherits from `StateDBBase` (FR-009) | yes | pass | 2026-06-24T19:30:00Z |
| `ado2gh/state/factory.py` | — | modified | Removed DynamoDB code path; uses `SQLiteStateDB` from `sqlite_db` (FR-009) | yes | pass | 2026-06-24T19:30:00Z |
| `ado2gh/state/dynamodb_db.py` | — | deleted | DynamoDB backend removed — not used in production; reduces maintenance surface (FR-009) | yes | pass | 2026-06-24T19:30:00Z |
| `ado2gh/state/__init__.py` | — | modified | Exports `SQLiteStateDB` alongside backward-compatible `StateDB` alias (FR-013) | yes | pass | 2026-06-24T19:30:00Z |

## US3-T035-T040: Decompose session_orchestrator.py (2026-06-25)

| File | SHA | Action | Notes | BC (FR-013) | Test | Date |
|------|-----|--------|-------|-------------|------|------|
| `ado2gh/agents/orchestration/__init__.py` | — | created | Package init re-exporting all public+private names | yes | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/orchestration/prompts.py` | — | created | LLM prompt templates and INTERNAL_TOOLS dict (149 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/orchestration/session_state.py` | — | created | OrchestratorResult, task/event helpers, status constants (135 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/orchestration/pev_coordination.py` | — | created | Intent classification, phase resolution, plan confirmation reply (654 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/orchestration/tool_routing.py` | — | created | Tool execution, plan forms, fallback reply (537 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/orchestration/loop.py` | — | created | Main orchestration loop, stub orchestrate, auto-chain (619 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/agents/session_orchestrator.py` | — | replaced | Re-export shim to `orchestration` package (86 lines, was 2281) | yes | pass | 2026-06-25T00:00:00Z |

## US3-T041-T044: Decompose pipeline_runner.py (2026-06-25)

| File | SHA | Action | Notes | BC (FR-013) | Test | Date |
|------|-----|--------|-------|-------------|------|------|
| `ado2gh/api/pipeline_models.py` | — | created | StepStatus, step definitions, PipelineStep, PipelineRun, enrich_pipeline_run_dict (185 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/pipeline_store.py` | — | created | PipelineRunStore in-memory registry (110 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/pipeline_steps.py` | — | created | PipelineStepsMixin with all step methods + _phase_config_exists (560 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/pipeline_runner.py` | — | replaced | PipelineRunner(PipelineStepsMixin) core + re-exports (170 lines, was 1250) | yes | pass | 2026-06-25T00:00:00Z |

## US3-T045-T048: Decompose settings_store.py (2026-06-25)

| File | SHA | Action | Notes | BC (FR-013) | Test | Date |
|------|-----|--------|-------|-------------|------|------|
| `ado2gh/api/settings_models.py` | — | created | GitHubToken, MigrationProfile, AdvancedSettings, UISettings, _settings_path (100 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/settings_profiles.py` | — | created | ProfileMixin: profile CRUD, approval workflow, queries (240 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/settings_scan.py` | — | created | ScanMixin: async profile scan, rescan after phase change (160 lines) | — | pass | 2026-06-25T00:00:00Z |
| `ado2gh/api/settings_store.py` | — | replaced | SettingsStore(ProfileMixin, ScanMixin) core + re-exports (370 lines, was 940) | yes | pass | 2026-06-25T00:00:00Z |

## US3-T049: Decompose accelerator_api/main.py (2026-06-26)

| File | SHA | Action | Notes | BC (FR-013) | Test | Date |
|------|-----|--------|-------|-------------|------|------|
| `services/accelerator_api/routes/__init__.py` | — | created | Route package init | — | pass | 2026-06-26T00:00:00Z |
| `services/accelerator_api/routes/_shared.py` | — | created | Shared state, helpers, lazy wrappers for test patching (622 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/accelerator_api/routes/settings_routes.py` | — | created | Settings, LLM, connectivity, cloud credential routes (346 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/accelerator_api/routes/profile_routes.py` | — | created | Profile management, scan, validation, token, discovery routes (572 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/accelerator_api/routes/pipeline_routes.py` | — | created | Pipeline run and live approval routes (177 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/accelerator_api/main.py` | — | replaced | App init + router registration + top-level routes (510 lines, was 1691) | yes | pass | 2026-06-26T00:00:00Z |

## US3-T050: Decompose agent/main.py (2026-06-26)

| File | SHA | Action | Notes | BC (FR-013) | Test | Date |
|------|-----|--------|-------|-------------|------|------|
| `services/agent/routes/__init__.py` | — | created | Route package init | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/routes/_shared.py` | — | created | Shared state, models, helpers, lazy wrappers (622 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/routes/pev_engine.py` | — | created | PEV loop, pipeline polling, start_pev_run, task sync (451 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/routes/run_routes.py` | — | created | Health, run CRUD, approval routes (120 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/routes/session_routes.py` | — | created | Session, plan, chat, form, execution mode, misc routes (530 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/routes/mcp_routes.py` | — | created | MCP tools route (20 lines) | — | pass | 2026-06-26T00:00:00Z |
| `services/agent/main.py` | — | replaced | App init + middleware + router registration + re-exports (93 lines, was 1807) | yes | pass | 2026-06-26T00:00:00Z |

## Phase 13 Convergence (T141–T160)

### T142: Docker Compose Consolidation

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `docker-compose.serverless.yml` | — | deleted | Consolidated into docker-compose.yml with profiles | yes | pass | 2026-06-24T16:30:00Z |
| `docker-compose.lightweight.yml` | — | deleted | Consolidated into docker-compose.yml with profiles | yes | pass | 2026-06-24T16:30:00Z |
| `docker-compose.yml` | — | replaced | Added profile support (accelerator+agent default, redis/worker/web behind --profile default) | yes | pass | 2026-06-24T16:30:00Z |

### T146–T147: Module Restructuring

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/tools/` | `ado2gh/pipelines/` | moved | Rename tools to pipelines | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/redis_queue.py` | `ado2gh/core/redis_queue.py` | moved | Merge infra into core | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/sessions.py` | `ado2gh/core/sessions.py` | moved | Merge infra into core | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/concurrency.py` | `ado2gh/core/concurrency.py` | moved | Merge infra into core | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/storage_config.py` | `ado2gh/state/storage_config.py` | moved | Merge infra into state | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/job_store.py` | `ado2gh/state/job_store.py` | moved | Merge infra into state | yes | pass | 2026-06-24T16:35:00Z |
| `ado2gh/infra/factory.py` | `ado2gh/state/factory.py` | moved | Merge infra into state | yes | pass | 2026-06-24T16:35:00Z |

### T148–T149: LLM & Credentials Subpackages

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/api/llm_model_store.py` | `ado2gh/api/llm/llm_model_store.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/llm_provider_registry.py` | `ado2gh/api/llm/llm_provider_registry.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/model_catalog.py` | `ado2gh/api/llm/model_catalog.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/model_validation.py` | `ado2gh/api/llm/model_validation.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/http_llm.py` | `ado2gh/api/llm/http_llm.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/platform_managed_model.py` | `ado2gh/api/llm/platform_managed_model.py` | moved | Consolidate LLM modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/data/llm_presets.json` | `ado2gh/api/llm/data/llm_presets.json` | moved | Data file follows module | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/llm/__init__.py` | — | created | Re-exports for LLM subpackage | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/cloud_credentials_store.py` | `ado2gh/api/credentials/cloud_credentials_store.py` | moved | Consolidate credential modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/cloud_credential_detector.py` | `ado2gh/api/credentials/cloud_credential_detector.py` | moved | Consolidate credential modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/cloud_credential_probe.py` | `ado2gh/api/credentials/cloud_credential_probe.py` | moved | Consolidate credential modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/credential_validation.py` | `ado2gh/api/credentials/credential_validation.py` | moved | Consolidate credential modules | yes | pass | 2026-06-24T16:45:00Z |
| `ado2gh/api/credentials/__init__.py` | — | created | Re-exports for credentials subpackage | yes | pass | 2026-06-24T16:45:00Z |

### T141: State DB Decomposition

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/state/sqlite_db.py` | — | decomposed | 1622→739 lines via mixin extraction | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/sqlite_agentic_mixin.py` | — | created | Assignments, audit, remediation, dependencies (278 lines) | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/sqlite_users_mixin.py` | — | created | Platform users, auth sessions, live execution approvals (250 lines) | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/sqlite_profile_scan_mixin.py` | — | created | Profile scan, wave management (203 lines) | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/sqlite_risk_gates_mixin.py` | — | created | Risk scores, phase gates, batch checkpoints (205 lines) | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/postgres_db.py` | — | decomposed | 1557→719 lines via mixin extraction | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/postgres_risk_gates_scan_mixin.py` | — | created | Risk scores, phase gates, batch checkpoints, profile scan (405 lines) | yes | pass | 2026-06-24T17:00:00Z |
| `ado2gh/state/postgres_agentic_users_mixin.py` | — | created | Audit, users, auth, live approvals, assignments (460 lines) | yes | pass | 2026-06-24T17:00:00Z |

### T143–T145: Spec Archiving

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `specs/002-login-bootstrap/` | `specs/archive/002-login-bootstrap/` | moved | Archived (implemented) | yes | pass | 2026-06-24T16:55:00Z |
| `specs/005-profile-onboarding/` | `specs/archive/005-profile-onboarding/` | moved | Archived (implemented) | yes | pass | 2026-06-24T16:55:00Z |
| `specs/README.md` | — | created | Spec lifecycle and index | yes | pass | 2026-06-24T16:55:00Z |
| `specs/001-agentic-migration-platform/spec.md` | — | updated | Added cross-reference note to dedicated specs | yes | pass | 2026-06-24T16:55:00Z |

### T150–T153, T158: Linting, CI, Metadata

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `pyproject.toml` | — | updated | Added ruff, mypy config, project metadata, fixed coverage paths | yes | pass | 2026-06-24T16:50:00Z |
| `.github/workflows/ci.yml` | — | updated | Added lint job (ruff+mypy), test job with coverage gate | yes | pass | 2026-06-24T16:50:00Z |
| `.pre-commit-config.yaml` | — | created | Pre-commit hooks with ruff, ruff-format, standard hooks | yes | pass | 2026-06-24T16:50:00Z |

### T155, T157, T160: Scripts Cleanup, Env Vars

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `scripts/discover.sh` | — | deleted | CLI wrapper, replaced by direct CLI commands | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/migrate.sh` | — | deleted | CLI wrapper, replaced by direct CLI commands | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/migrate-full.sh` | — | deleted | CLI wrapper, replaced by direct CLI commands | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/analyze_structure.py` | — | deleted | One-time analysis script | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/build_import_graph.py` | — | deleted | One-time analysis script | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/list_methods.py` | — | deleted | One-time analysis script | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/run-local.ps1` | `scripts/dev/run-local.ps1` | moved | Organize dev helpers | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/run-ui.ps1` | `scripts/dev/run-ui.ps1` | moved | Organize dev helpers | yes | pass | 2026-06-24T16:55:00Z |
| `scripts/_local-common.ps1` | `scripts/dev/_local-common.ps1` | moved | Organize dev helpers | yes | pass | 2026-06-24T16:55:00Z |
| `.env.example` | — | updated | Removed DynamoDB env vars | yes | pass | 2026-06-24T16:55:00Z |
| `docs/COMMAND_REFERENCE.md` | — | updated | Added removed scripts table | yes | pass | 2026-06-24T16:55:00Z |

### T154: UI Consolidation

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `apps/migration-ui/src/app/assignments/` | — | deleted | Empty directory | yes | pass | 2026-06-24T17:05:00Z |

## 2026-08-25 — Post-012 Dead Code Sweep

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/agents/rollback_tracker.py` | — | deleted | Superseded by `migration_agent/session/store.py` rollback records (spec 012) | yes | pass | 2026-08-25 |
| `ado2gh/agents/repo_lock_store.py` | — | deleted | Superseded by graph-state locks (spec 012); `api/repo_lock.py` covers pipeline runs | yes | pass | 2026-08-25 |
| `ado2gh/agents/resource_mapping.py` | — | deleted | Unused after spec 012 executor rewrite | yes | pass | 2026-08-25 |
| `ado2gh/agents/migration_agent/metrics.py` | — | deleted | Duplicate of `ado2gh/agents/metrics.py` MetricsCollector; call sites consolidated | yes | pass | 2026-08-25 |
| `ado2gh/api/form_validator.py` | — | deleted | Prod-unused; validation is inline in `routers/migration_router.py` | yes | pass | 2026-08-25 |
| `ado2gh/api/migrations/` | — | deleted | Empty package | yes | pass | 2026-08-25 |
| `ado2gh/reporting/boards_gaps.py` | — | deleted | Zero callers (allowlist-only reference) | yes | pass | 2026-08-25 |
| `scripts/dev/test_agent_e2e.py` | — | deleted | FR-034: scripts/dev is startup helpers only; harness unreferenced | yes | pass | 2026-08-25 |
| `tests/unit/test_guardrails_spec011.py` | — | deleted | Module-level skipped; superseded by `test_guardrails.py` (spec 012) | yes | pass | 2026-08-25 |
| `tests/agent/test_agent_skills_traceability.py` | — | deleted | Module-level skipped; skills/ removed in spec 012 | yes | pass | 2026-08-25 |
| `tests/integration/test_011_rollback_legacy.py` + unit tests for deleted agent modules | — | deleted | Tested removed legacy modules | yes | pass | 2026-08-25 |
| `apps/migration-ui/src/components/NavTabs.tsx` | — | deleted | Deprecated, unreferenced (UnifiedNavigation replaced it) | yes | pass | 2026-08-25 |
| `apps/migration-ui/src/lib/api/` | — | deleted | Type-only modules from spec 008 never imported | yes | pass | 2026-08-25 |
| `in/sample_repos.txt`, `in/sample_repos_with_scopes.csv` | — | deleted | Unreferenced sample inputs | yes | pass | 2026-08-25 |
| `services/agent/main.py` `/health` `/metrics` handlers | — | deleted | Shadowed by `routes/run_routes.py` handlers (routers registered first); session gauges folded into surviving `/metrics` | yes | pass | 2026-08-25 |
| ~55 dead functions/methods across `ado2gh/` and `services/` | — | deleted | Zero references repo-wide (vulture + cross-reference scan) | yes | pass | 2026-08-25 |

## 2026-09-02 — Adversarial Audit Fix Pass

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/api/models/` | — | deleted | Empty package (only stale `__pycache__`, source already removed in a prior refactor) | yes | n/a | 2026-09-02 |
| `tests/assignments/` | — | deleted | Empty package (only stale `__pycache__`, no test source) | yes | n/a | 2026-09-02 |
| `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`, `docs/LOCAL_DEVELOPMENT.md` | — | updated | FR-035: aligned with spec-012 agent layout, current scripts/, spec list | yes | pass | 2026-08-25 |

## 2026-09-08 — 013 Phase 2 / T006: Tests for routers removed in `0ec95d2`

`0ec95d2` deleted `ado2gh/api/routers/{discovery_router,migration_router}.py`,
`ado2gh/api/models/`, `ado2gh/api/workflow_readiness.py`, `ado2gh/assignments/*`
and `services/agent/{mcp_server.py,routes/mcp_routes.py}` without removing the
tests that exercised them. 40 tests asserted 2xx from paths that now 404; 4 more
were false passes (they asserted the 404 a deleted route naturally returns).
No production code changed.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `tests/contract/test_migration_api_contracts.py` | — | deleted | 16 tests on `/v1/migration/{repo,wave,pre-migration-form}` (`migration_router.py`); fixture also required the `migration_operations` table, whose model died with `ado2gh/api/models/` | yes | pass | 2026-09-08 |
| `tests/contract/test_discovery_api_contracts.py` | — | deleted | 8 tests on `/v1/discovery/{scan,results}` (`discovery_router.py`) | yes | pass | 2026-09-08 |
| `tests/agent/test_agentic_api.py` | — | deleted | 4 tests on `/v1/profiles/{id}/assignments`, `/v1/workflow-readiness`, `/v1/rollback`; `agentic_routes.router` now exposes only `/v1/history/*` | yes | pass | 2026-09-08 |
| `tests/integration/test_agent_interface_integration.py` | — | deleted | 4 tests on the removed discovery/migration-wave workflow; one already imported the deleted `ado2gh.api.models` | yes | pass | 2026-09-08 |
| `tests/integration/test_unified_tabs_integration.py` | — | deleted | 5 tests on `/v1/discovery/results` + `/v1/migration/wave`; one imported the deleted `ado2gh.api.models` and the orphaned `migration_operations` table | yes | pass | 2026-09-08 |
| `tests/contract/test_agent_interface_contracts.py` | (same) | tests deleted | 3 of 4 tests removed (`test_discovery_results_accessible`, `test_migration_wave_creation_accessible`, `test_pre_migration_form_endpoint_exists`); `test_health_endpoint_works` kept | yes | pass | 2026-09-08 |
| `tests/contract/test_unified_tabs_contracts.py` | (same) | tests deleted | 4 of 5 tests removed (`test_discovery_api_endpoint_exists`, `test_migration_api_endpoint_exists`, `test_discovery_scan_endpoint_exists`, `test_pre_migration_form_endpoint_exists`); `test_health_check_passes` kept | yes | pass | 2026-09-08 |
| `tests/agent/test_local_host_parity.py` | (same) | test deleted | `test_mcp_plan_tool_accepts_dry_run` spawned `python -m services.agent.mcp_server`, deleted in `0ec95d2`; unused `json`/`subprocess`/`sys` imports and the stale module docstring removed with it | yes | pass | 2026-09-08 |
| `tests/feature/test_services_coverage.py` | (same) | assertion deleted | `test_agent_health_llm_and_session` asserted 200 from `/v1/mcp/tools` (`routes/mcp_routes.py`, deleted in `0ec95d2`); rest of the test kept | yes | pass | 2026-09-08 |

- **Tests removed**: 44 (SC-004 input) — 37 deleted files, 7 in-place.
- **Production functions deleted**: 0. Deletions in `0ec95d2` are recorded against that commit.

## 2026-09-08 — 013 Phase 2 / T007: Test for the assignment-gate serializer removed in `859bb6b`

`859bb6b` cut 349 lines from `ado2gh/api/agentic_routes.py`, deleting the assignment-scoped
gate routes together with their private serializer `_gate_payload` and the
`PhaseGateChecker.check_for_assignment` method they called. `agentic_routes` now exposes only
`/v1/history/*`; no production code builds a gate payload. The surviving `PhaseGateChecker`
surface (`check`, `override`, `can_advance`) is already covered by
`tests/core/test_gate_checker.py`. No production code changed.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `tests/contract/test_rollback_gates_contract.py` | — | deleted | 1 test on `_gate_payload` and `PhaseGateChecker.check_for_assignment`, both deleted in `859bb6b`; restoring them would add a helper with zero production callers (FR-006a: no shims) | yes | pass | 2026-09-08 |

- **Tests removed**: 1 (SC-004 input) — 1 deleted file.
- **Production functions deleted**: 0. Deletions in `859bb6b` are recorded against that commit.

## 2026-09-08 — 013 Phase 2 / T008: Test for the repo dependency-graph feature removed in `859bb6b`

`859bb6b` deleted the repo dependency-graph feature end to end: `StateDB.upsert_dependency_edge`
and its table from both backends (`ado2gh/state/sqlite_db.py` -168, `ado2gh/state/postgres_db.py`
-138, plus the agentic mixins), and `BatchExecutor.plan_repo_order` / `BatchExecutor._sort_repos_topo`
(`ado2gh/phase/batch_executor.py` -82). `0ec95d2` then deleted `ado2gh/pipelines/dependency_graph.py`
and `ado2gh/api/dependency_graph.py`. A repo-wide search for `dependency` under `ado2gh/state/`
returns zero hits, so there is no Postgres counterpart to mirror; re-adding the method would
resurrect a deleted feature rather than repair a regression. No production code changed.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `tests/core/test_batch_executor_topo.py` | — | deleted | 2 tests on `StateDB.upsert_dependency_edge`, `BatchExecutor.plan_repo_order` and `BatchExecutor._sort_repos_topo`, all three deleted in `859bb6b` | yes | pass | 2026-09-08 |

- **Tests removed**: 2 (SC-004 input) — 1 deleted file.
- **Production functions deleted**: 0. Deletions in `859bb6b` and `0ec95d2` are recorded against those commits.

## 2026-09-08 — 013 Phase 2 / T011 prep: Tests for the assignment live gate removed in `859bb6b`

`859bb6b` deleted `ado2gh/assignments/*` and, with it, `enforce_live_gate` and the
`assignment_id` keyword on `LiveApprovalStore.create_or_get_pending`
(`ado2gh/api/live_approval_store.py` -33). `enforce_live_gate` now appears nowhere in
`ado2gh/` or `services/`; the surviving live-approval flow is platform approval only.
No production code changed.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `tests/agent/test_agent_pev_live_gate.py` | (same) | test deleted | `test_approve_calls_enforce_live_gate_before_resume` patched `enforce_live_gate` and passed `assignment_id=`, both deleted in `859bb6b`; unused `LiveApprovalStore`/`PlatformRole`/`PlatformUser` imports removed with it | yes | pass | 2026-09-08 |
| `tests/feature/test_agent_pev_quickstart_scenarios.py` | (same) | test + class deleted | `TestQuickstartScenario9DualAndGates::test_gate_blocks_after_platform_approval` was the same assertion; the class held no other test | yes | pass | 2026-09-08 |

- **Tests removed**: 2 (SC-004 input) — 0 deleted files, 2 in-place.
- **Production functions deleted**: 0. Deletions in `859bb6b` are recorded against that commit.

## 2026-09-08 — 013 Phase 5 / T040: Structural changes from the fifteen critical gap fixes

T039 remediated the fifteen critical gaps (GAP-002 through GAP-016) and T040 committed them,
one commit per gap. Most of that work changed behaviour inside existing functions and left the
file layout alone; the rows below are the moves, deletions and renames it did produce.

The largest is GAP-007. The nine `/v1/migrate/*` routes each carried their own ad-hoc live
check, so a new route could be added without one. `services/accelerator_api/routes/migrate_guard.py`
is the single choke point that replaced them (FR-025): one router-level dependency that resolves
the caller, requires the approve-live capability, parks operate-only callers in `LiveApprovalStore`,
and writes the audit event through a field allowlist so a secret value cannot reach an audit row.

GAP-004 removed the client's ability to certify its own live authority. The `agent_live_approved`
field on `PipelineRunStartRequest` and the whole `PipelineRunStartApprovedRequest` model were
deleted with no shim, and `PipelineRunStartRequest` gained `extra="forbid"` so the field cannot
be smuggled back in as an unknown key. The agent-side reader `agent_live_approved()` was deleted
with them. The operator approved this on 2026-09-08; entry 4 of the public contract freeze
records it.

GAP-003 merged `LiveApprovalStore._notify_agent_resume` and `_notify_agent_denied`, which were
the same request with a different URL and each swallowed failures with `pass`, into one
`_notify_agent` that logs and writes an audit event on failure.

GAP-014 split the concurrency guard's answer in two. `other_run_holds_repo` returned False both
when the repo was free and when the check had raised, so a failed check read as "no conflict".
Its body moved into a new `repo_conflict_reason`, which returns why the repo is unavailable or
None when it is provably free; `other_run_holds_repo` stays as a one-line wrapper for its
existing callers. In the engine, `_try_clear_orphaned_in_progress` became `_in_progress_block_reason`
for the same reason: a boolean could not say whether the refusal was a real conflict or a failed
check.

One public route changed method: `GET /v1/settings/llm-models/catalog` became
`POST /v1/settings/llm-models/catalog` so the API key stops travelling in a URL query string
(GAP-012). The operator authorised the matching edit to `tests/contract/public_surface_snapshot.json`
on 2026-09-08; the route count is unchanged at 148. No file moved for it.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `services/accelerator_api/routes/migrate_guard.py` | — | added | Single live-execution choke point for the nine `/v1/migrate/*` routes, replacing nine per-route checks (GAP-007, FR-025) | yes | pass | 2026-09-08 |
| `ado2gh/api/contracts.py` | (same) | model deleted | `PipelineRunStartApprovedRequest` let a client assert its own live approval; deleted outright with no shim (GAP-004, freeze entry 4) | yes | pass | 2026-09-08 |
| `ado2gh/api/contracts.py` | (same) | field deleted | `PipelineRunStartRequest.agent_live_approved`; the model now sets `extra="forbid"` so the key cannot return as an unknown field (GAP-004) | yes | pass | 2026-09-08 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py` | (same) | function deleted | `agent_live_approved()` read the deleted field; live authority now comes from server state (GAP-004) | yes | pass | 2026-09-08 |
| `ado2gh/api/live_approval_store.py` | (same) | functions merged | `_notify_agent_resume` and `_notify_agent_denied` merged into `_notify_agent`; both swallowed notify failures with `pass` (GAP-003) | yes | pass | 2026-09-08 |
| `ado2gh/core/migration_fr036.py` | (same) | function added | `repo_conflict_reason` holds the body of `other_run_holds_repo`, which stays as a wrapper for existing callers (GAP-014) | yes | pass | 2026-09-08 |
| `ado2gh/core/migration_engine.py` | (same) | method renamed | `_try_clear_orphaned_in_progress` became `_in_progress_block_reason`; a boolean could not distinguish a real conflict from a failed check (GAP-014) | yes | pass | 2026-09-08 |

- **Tests removed**: 0. Fifteen regression tests were added, one per gap, named `tests/**/test_gap_0NN_*.py`.
- **Production functions deleted**: 2 — `agent_live_approved()` and the `PipelineRunStartApprovedRequest` model; plus two private methods merged into one and one private method renamed.

## 2026-09-08 — 013 Increment 1: `ado2gh` root modules + `ado2gh/audit/`

Phase 6 (US2) increment 1, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`.

`ado2gh/assignments/` held only the audit event writer and the platform's secret-masking
choke point; the directory name described a feature that `859bb6b` deleted. The review
agent confirmed the `module_name_review` proposal at T020 (`tag-decisions.json`,
`decided_by: cavecrew-reviewer`), so the package moved to `ado2gh/audit/` (FR-013,
GAP-036). Nothing remains at the old path (FR-006a): every importer under `ado2gh/` and
`tests/` was updated in the same commit, and the package tree in `CLAUDE.md` follows.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/assignments/` | `ado2gh/audit/` | moved | GAP-036 / FR-013: the package holds only the audit writer; `module_name_review` confirmed at T020 | yes | pass | 2026-09-08 |
| `ado2gh/audit/audit.py` | `ado2gh/audit/redaction.py` | moved | Review agent confirmed `module_name_review`: `audit.py` hid the platform's secret-masking choke point beside the writer. The masking half (`redact_payload`, its patterns and helpers) lives here (FR-013, GAP-036) | yes | pass (886) | 2026-09-08 |
| `ado2gh/audit/audit.py` | `ado2gh/audit/writer.py` | moved | The `AuditWriter` half of the same split. Callers import both names from the `ado2gh.audit` package; nothing remains at `audit.py` (FR-006a) | yes | pass (886) | 2026-09-08 |
| `ado2gh/models.py` | (same) | docstrings added | Module docstring, class docstrings on the 17 undocumented enums and dataclasses, method docstrings on `PipelineMetadata.to_dict`/`from_dict` and `RiskScore.to_dict` (Google style, R3). `ExecutionMode.from_dry_run` takes `dry_run` keyword-only | yes | pass (886) | 2026-09-08 |
| `ado2gh/http_utils.py`, `ado2gh/logging_config.py` | (same) | docstrings added | `make_session` and `SecretRedactingFilter.filter`. The root-handler `SecretRedactingFilter` from GAP-010/011 is unchanged | yes | pass (886) | 2026-09-08 |
| `ado2gh/audit/redaction.py`, `ado2gh/audit/writer.py` | (same) | annotations tightened | `redact_payload` and `_is_secret_key` take and return `object` instead of `Any` (ANN401); `AuditWriter.__init__` is typed `db: StateDBBase`, the base that declares `insert_audit_event`, and returns `None` | yes | pass (886) | 2026-09-08 |
| `.github/workflows/ci.yml` | (same) | coverage ratchet raised | `--cov-fail-under` 56 → 58: measured TOTAL 58 % on the green suite (FR-027a / SC-004) | yes | pass (886) | 2026-09-08 |

- **Tests removed**: 0. Measured after the increment: 886 passed, 30 skipped, in a detached worktree of this exact tree (the T041 test added 6; the untracked `tests/unit/test_function_inventory_script.py`, 11 tests, is not part of this commit and was not in the measured tree).
- **Production functions deleted**: 0. No `dead` rows in `ado2gh` or `ado2gh/audit`, so no `inventory.json@c769ff9` pointer is needed.
- **Exception register**: 1 row of the 29-row cap. `ado2gh/models.py::ExecutionMode.from_dry_run` keeps `bool_flag`: the review agent confirmed the tag, but the parameter is the external boolean the converter exists to translate, so the FR-012 remediation would delete the boundary converter that data-model.md mandates.
- **Inventory**: `ado2gh` 8 functions (7 clean, 1 exception), `ado2gh/audit` 5 functions (all clean); `--pending --package` exits 0 for both; the T002 ruff set and the project ruff config report nothing; public-surface snapshot unchanged; orphan guard green.
- **Why a worktree**: in the shared working tree `tests/feature` stalls at its 17th test whatever the code state. The cause is local runtime state, not code: `data/agent_checkpoints.db` carries a hot WAL (`-wal` 3.5 MB, `-shm`) left behind by force-killed test processes, and the LangGraph checkpointer wedges on opening it. The same tests pass in the main tree once `ADO2GH_SQLITE_PATH` points at a fresh file (28 passed, 21.9 s), and the whole suite passes in a fresh detached worktree of this tree. CI has no such file. Nothing in this commit touches that path.

## 2026-09-08 — 013 Increment 2: `ado2gh/clients/`

Phase 6 (US2) increment 2, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `0c6ea27`.

The review agent (`cavecrew-reviewer`) decided the package's nine proposals: four
CONFIRM (`ADOClient._p:name_review`, `GHClient.create_repo:bool_flag`,
`TokenManager.summary:name_review`, `ado2gh/clients/token_manager.py:module_name_review`),
five REJECT (`GHClient.repo_exists:name_review` and `module_name_review` on the package,
`ado_client.py`, `ado_token_manager.py`, `gh_client.py`), no ESCALATE. After the move it
rejected `module_name_review` on the new `gh_token_manager.py`. The six REJECT lines are in
`tag-decisions.json`; the four CONFIRM lines were acted on and their targets no longer
exist, so the generator dropped those records at the next regeneration (it keeps a decision
only while the decided state exists) — this entry is their record.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/clients/token_manager.py` | `ado2gh/clients/gh_token_manager.py` | moved | Review agent confirmed `module_name_review`: the bare name collided with `ado_token_manager.py`; the module is GitHub-specific. Class name `TokenManager` unchanged; importers in `ado2gh/clients/`, `ado2gh/cli/helpers.py`, `ado2gh/api/accelerator.py`, `tests/core/test_gh_client.py` and `docs/ARCHITECTURE.md` updated; nothing remains at the old path (FR-006a/FR-013) | yes | pass (886) | 2026-09-08 |
| `ado2gh/clients/gh_token_manager.py` | (same) | renamed function | `TokenManager.summary` → `get_token_status` (review agent confirmed `name_review`); caller `ado2gh/cli/run_cmd.py` updated | yes | pass (886) | 2026-09-08 |
| `ado2gh/clients/ado_client.py` | (same) | renamed function | `ADOClient._p` → `_encode_project` (review agent confirmed `name_review`); 25 internal call sites and 5 in `ado2gh/core/ado_cleanup.py` updated | yes | pass (886) | 2026-09-08 |
| `ado2gh/clients/gh_client.py` | (same) | renamed function | `GHClient.create_repo(private: bool = True, …)` → `create_private_repo(org, repo, description)` (review agent confirmed `bool_flag`, FR-012). The flag's only caller, `ado2gh/core/scopes/git_scope.py`, always passed `private=True`; a `create_public_repo` twin would have had zero references and been deleted as `dead` at the next regeneration, so only the intent-named function that is used exists | yes | pass (886) | 2026-09-08 |
| `ado2gh/clients/gh_client.py` | (same) | signature grouped | `put_file` and `create_pull_request` (`gt5_params`) take the existing `RepoConfig` domain object instead of `org, repo`; `put_file` looks up the existing blob SHA itself (same request sequence its only caller, `ado2gh/pipelines/push_workflows.py`, performed), dropping the `sha` parameter | yes | pass (886) | 2026-09-08 |
| `ado2gh/clients/*.py` | (same) | docstrings and annotations | Package, class and method docstrings (Google style, R3) on all 80 functions; `-> None` on setters and `__init__`; `params`/`body`/`labels`/`reviewers`/`status_checks` typed `… \| None`; `_get`/`_post`/`_patch` return `dict[str, Any]` (GitHub `_get`: `dict[str, Any] \| list[dict[str, Any]]`, the two array endpoints) instead of `Any` (ANN401). Token managers describe credential shape in words only; no token value appears in any docstring, log line or message (CA-003) | yes | pass (886) | 2026-09-08 |

- **Tests removed**: 0. Measured after the increment: 886 passed, 30 skipped, 0 failed, coverage TOTAL 58 %, in a detached worktree of this exact tree (see below).
- **Production functions deleted**: 0. No `dead` rows in `ado2gh/clients`, so no `inventory.json@0c6ea27` pointer is needed.
- **Exception register**: unchanged (1 row of the 29-row cap); no `# noqa` added.
- **Coverage ratchet**: `--cov-fail-under` stays at 58 (measured TOTAL 58 %, not higher).
- **Inventory**: `ado2gh/clients` 80 functions, all `clean`; `--pending --package ado2gh/clients` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package; public-surface snapshot unchanged; orphan guard green.
- **Why a worktree, again**: in the shared working tree the suite stalls at `tests/contract/test_agent_pev_flow_contracts.py::test_health_endpoint_contract` (`/health` → `get_compiled_graph()`), the same wedge as increment 1. Pointing `ADO2GH_SQLITE_PATH` at a fresh file makes that test pass alone but does not unblock the full run, and it makes `tests/profile/test_profile_discovery.py` (2) and `tests/profile/test_profile_scan_db.py` (1) fail because the variable overrides the `db_path` those tests pass to `sync_profile_scan_to_risk_scores` — do not set it for a full run. The whole suite passes in a fresh detached worktree with no environment overrides. Nothing in this commit touches that path.

## 2026-09-08 — 013 Increment 3: `ado2gh/state/`

Phase 6 (US2) increment 3, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `c0ee1e7`.

The review agent (`cavecrew-reviewer`) decided the package's proposals: six CONFIRM, all
`bool_flag` (`mark_wave_run` and `save_profile_scan`, each on the base and both backends),
and REJECT for every `name_review` (the names describe the queries they run) and every
`module_name_review` (the package and all 14 modules), with no ESCALATE. The 57 REJECT
lines that still had a target after cleanup are in `tag-decisions.json`; the three REJECT
lines on `risk_score_count` and the six CONFIRM lines have no target any more (the
function was deleted as `dead`; the flags were replaced), so this entry is their record.
The agent's first reply used CONFIRM for "the name is accurate"; it re-issued every line
under the tag semantics before anything was recorded.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/state/base.py`, `sqlite_db.py`, `postgres_db.py` | (same) | `bool_flag` → `ExecutionMode` | `mark_wave_run(wave_id, status, dry_run: bool = False)` → `mark_wave_run(wave_id, status, mode: ExecutionMode = ExecutionMode.LIVE)` (review agent confirmed, FR-012, `contracts/public-contract-freeze.md`). The `wave_runs.dry_run` column keeps its boolean shape; the conversion is `int(mode is ExecutionMode.DRY_RUN)` at the write. Caller `ado2gh/phase/batch_executor.py` converts with `ExecutionMode.from_dry_run(dry_run=...)`; `ado2gh/core/rollback.py` never passed the flag | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_profile_scan_mixin.py`, `postgres_risk_gates_scan_mixin.py` | (same) | `bool_flag` → two intent-named functions | `save_profile_scan(profile_id, raw, *, preserve_manual_assignments=True)` → `save_profile_scan(profile_id, raw)` (keeps operator phase assignments) and `replace_profile_scan(profile_id, raw)` (discards them), both over a private `_write_profile_scan` (review agent confirmed, FR-012). Caller `ado2gh/api/migration_scan.py::persist_scan_results` branches on its own flag (that flag belongs to the `api` increment); `tests/profile/test_profile_scan_db.py` updated | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_agentic_mixin.py`, `postgres_agentic_users_mixin.py` | (same) | signature grouped | `search_audit_events(*, profile_id, limit, offset, actor, event_type, search, date_from, date_to)` → `search_audit_events(filters: AuditEventFilters, *, limit, offset)`; `count_audit_events(*, …)` → `count_audit_events(filters)`; the base's `**kwargs` declarations became these concrete signatures. `AuditEventFilters` is the existing record in `ado2gh/state/audit_query.py`. Callers `ado2gh/api/agentic_routes.py` (`_audit_search_kwargs` → `_audit_search_filters`, returns the record), `tests/auth/test_audit_history_api.py`, `tests/core/test_live_approval_queue.py` updated | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_users_mixin.py`, `postgres_agentic_users_mixin.py` | (same) | signature grouped | `create_platform_user(user_id, username, password_hash, role, display_name, created_at, status)` → `create_platform_user(user: PlatformUser, *, password_hash, status, created_at)` and `decide_live_execution_approval(approval_id, status, approver_user_id, approver_username, reason_decision, decided_at)` → `(approval_id, status, approver: PlatformUser, reason_decision, decided_at)`. `PlatformUser` is the existing record in `ado2gh/auth/models.py` (a leaf module); `state` names it under `TYPE_CHECKING` only, so no runtime import edge is added. Callers `ado2gh/auth/service.py` (three sites) and `ado2gh/api/live_approval_store.py` (two sites) updated | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_agentic_mixin.py`, `postgres_agentic_users_mixin.py` | (same) | signature reduced | `insert_audit_event(event_id, event_type, profile_id, actor, payload_json, created_at, assignment_id=None)` → `insert_audit_event(event_id, event_type, profile_id, actor, payload_json)`: `assignment_id` was never passed by any caller (the column stays and is written NULL as before) and `created_at` is now stamped inside the store, as `upsert_migration` and `mark_wave_run` already do. Caller `ado2gh/audit/writer.py` updated (its `datetime` import went with it) | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_db.py`, `postgres_db.py` | (same) | signature grouped | `upsert_pipeline_migration(wave_id, meta, gh_org, gh_repo, status, …)` → `upsert_pipeline_migration(wave_id, meta, repo: RepoConfig, status, …)`; only `repo.gh_org`/`repo.gh_repo` are stored. Callers `ado2gh/core/scopes/pipelines_scope.py` (three sites, which already held the `RepoConfig`) and `tests/pipeline/test_pipeline_readiness_report.py` updated. Still 9 parameters — see the exception rows below | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_profile_scan_mixin.py`, `postgres_risk_gates_scan_mixin.py` | (same) | unused parameter dropped | `get_profile_scan_repos(profile_id, phase=None)` → `get_profile_scan_repos(profile_id)`; `phase` was never read (ARG002) and never passed | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/base.py`, `sqlite_db.py`, `postgres_db.py`, `sqlite_risk_gates_mixin.py`, `postgres_risk_gates_scan_mixin.py` | — | deleted functions | `clear_inventory`, `risk_score_count`, `get_batch_checkpoints` (base declaration plus both backends: 9 functions) — `dead` rows, not `protected`, zero references outside the package. Record: `inventory.json@c0ee1e7`. No test exercised them, so no test was removed (FR-029) | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/postgres_risk_gates_scan_mixin.py` | (same) | contract mismatch fixed (FR-011) | `PostgresStateDB` had no `prune_risk_scores_not_in` although `StateDBBase` declares it abstract, so the class could not be instantiated; the PostgreSQL implementation mirrors the SQLite one. Noted rather than hidden: it is one new function in the inventory | yes | pass (886) | 2026-09-08 |
| `ado2gh/state/*.py` | (same) | docstrings and annotations | Package, module, class and function docstrings (Google style, R3) on all 236 functions; `-> None` on writers and `__init__`; `str = None` defaults typed `str \| None`; `dict`/`list` returns typed `dict[str, int]`, `dict[str, dict[str, int]]`, `list[dict]`; `score`, `result`, `cp`, `phase` typed with the existing `RiskScore`, `PhaseGateResult`, `BatchCheckpoint`, `PhaseType`; `PostgresJobStore._conn` and `DynamoDBJobStore._table` typed via `TYPE_CHECKING` imports of the driver types; `PostgresStateDB._conn` returns `Iterator[Any]` (nested `Any`, not ANN401). Connection strings, password hashes and session tokens are described in words only; no value appears in any docstring or message (CA-003). `audit_query` is imported at module top in the mixins instead of inside each method | yes | pass (886) | 2026-09-08 |

- **Tests removed**: 0. Measured after the increment: 886 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in a detached worktree of this exact tree (see below).
- **Production functions deleted**: 9 (three names, each on the base and both backends); pointer `inventory.json@c0ee1e7` (FR-029).
- **Exception register**: 10 rows of the 29-row cap (9 added). `upsert_migration`, `upsert_pipeline_migration` and `create_live_execution_approval`, each on the base and both backends, keep `gt5_params` with `# noqa: PLR0913`: no existing record model holds a scope or transform outcome (`ScopeResult` lives in `ado2gh/core`, and importing it would invert the layering), and the only model matching the approval columns is `LiveApprovalCreateRequest` in `ado2gh/api/contracts.py`, which would deepen the `state → api` inversion T078 owns (GAP-021). A new type is forbidden by T048.
- **Coverage ratchet**: `--cov-fail-under` raised 58 → 59 in `.github/workflows/ci.yml` in this commit (measured TOTAL 59 %, FR-027a/SC-004).
- **Inventory**: `ado2gh/state` 236 functions (239 − 9 deleted + 6 added: `replace_profile_scan` ×3, `_write_profile_scan` ×2, `prune_risk_scores_not_in` ×1), 227 `clean` and 9 `exception`; `--pending --package ado2gh/state` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package; public-surface snapshot unchanged; orphan guard and 800-line guard green (largest file `sqlite_db.py` at 649 lines, so no module split was needed).
- **Not changed, for later increments**: `ado2gh/state/db.py` is a pre-existing re-export shim (`StateDB` = `SQLiteStateDB`) that `ado2gh.state.__init__` re-exports and the public-surface snapshot freezes; the review agent rejected its `module_name_review`, so it stays. `SQLiteStateDB.mark_in_progress_migrations_failed` exists only on the SQLite backend (not on the base, not on PostgreSQL) — a pre-existing FR-011 mismatch left for T081 to assert and the operator to decide. `persist_scan_results(preserve_manual_assignments: bool)` in `ado2gh/api/migration_scan.py` is the `api` increment's `bool_flag`.
- **Why a worktree, again**: in the shared working tree the suite stalled after 12 tests with `data/agent_checkpoints.db-shm` present, the same wedge as increments 1 and 2. The run was killed and repeated in a fresh detached worktree with this exact diff applied (`git diff HEAD | git apply`), no environment overrides; the worktree was removed afterwards.
