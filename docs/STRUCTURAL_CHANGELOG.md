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
