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

## 2026-09-08 — 013 Increment 4: `ado2gh/phase/`

Phase 6 (US2) increment 4, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `8ee3251`.

The review agent (`cavecrew-reviewer`) decided the package's eleven proposals: four CONFIRM
— `bool_flag` on `BatchExecutor.execute_phase`, `BatchExecutor.execute_wave` and
`BatchExecutor._run_batch`, and `name_review` on `RiskScorer._s` — and seven REJECT:
`name_review` on `ProgressTracker.snapshot` (point-in-time progress figures are what
"snapshot" means) and `module_name_review` on the package and all five modules. No
ESCALATE. The `RiskScorer._s` decision was recorded by hand in `tag-decisions.json` with
the pre-increment `state_hash`, because the rename had already been applied and
`--confirm` only matches ids that still exist in the regenerated inventory.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/phase/batch_executor.py` | (same) | `bool_flag` → `ExecutionMode` | `execute_phase(phase, waves, dry_run: bool = False)` → `execute_phase(phase, waves, mode: ExecutionMode = ExecutionMode.LIVE)`; `execute_wave(wave, dry_run: bool = False, …)` → `execute_wave(wave, mode: ExecutionMode = ExecutionMode.LIVE, …)`; `_run_batch(wave, dry_run: bool, …)` → `_run_batch(wave, mode: ExecutionMode, …)` (all three confirmed by the review agent, FR-012, `contracts/public-contract-freeze.md`). The `LIVE` default preserves the old `dry_run=False` behaviour. Boundary conversion with `ExecutionMode.from_dry_run(dry_run=…)` sits at the HTTP request fields in `ado2gh/api/accelerator.py` (`RunWaveRequest.dry_run`, `PhaseRunRequest.dry_run`) and `ado2gh/api/pipeline_steps.py` (`run.dry_run`), and at `ado2gh/core/wave_runner.py`, which keeps its own boolean until increment 7. The wave summary dict still carries a boolean `dry_run` key, so the accelerator contracts are unchanged | yes | pass (898) | 2026-09-08 |
| `ado2gh/phase/risk_scorer.py` | (same) | renamed + signature grouped | `RiskScorer._s(name, raw, max_pts, fn)` → `RiskScorer._score_signal(name, raw, max_pts, fn)` (review agent confirmed `name_review`). `RiskScorer.score(project, repo_meta, pipelines, repo_stats, commits, var_groups, svc_conns, gh_org="")` (eight parameters, `gt5_params`) → `score(rs: RiskScore, pipelines, commits)`: the caller now builds the existing `RiskScore` record — no new type, per the protocol — and the scorer only derives the pipeline ratios, the commit recency, the signals and the total. The `gh_repo` slug that `score` used to compute is filled by `WaveAssigner.assign` and `ConfigurableWaveAssigner.assign`, both of which run over every scored repo, so the stored value is unchanged. Only caller: `ado2gh/api/migration_scan.py` | yes | pass (898) | 2026-09-08 |
| `ado2gh/phase/*.py` | (same) | docstrings and annotations | Package, class and method docstrings (Google style, R3) on all 15 functions and all five modules; `-> None` on every `__init__` and on `record_repo`; `phase_configs` typed `dict[PhaseType, PhaseConfig] \| None`; `cancel_event` typed `threading.Event \| None`; `engine` typed `MigrationEngine` behind `TYPE_CHECKING`; `_events` typed `list[tuple[float, str]]`; the signal scoring function typed `Callable[[float], float]`. `PhaseGateChecker.override(phase, reason)` keeps `reason` mandatory with no default, so the audit trail always names why a gate was forced (CA-002), and `check` stays a pure evaluator | yes | pass (898) | 2026-09-08 |

- **Tests removed**: 0. Measured for this increment: 898 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in the shared working tree (no worktree needed this time).
- **Production functions deleted**: 0. No `dead` rows in `ado2gh/phase`, so no `inventory.json@8ee3251` pointer is needed.
- **Exception register**: unchanged at 10 rows of the 29-row cap; no `# noqa` was added.
- **Coverage ratchet**: `--cov-fail-under` stays at 59 in `.github/workflows/ci.yml` (measured TOTAL 59 %, not higher — FR-027a/SC-004).
- **Inventory**: `ado2gh/phase` 15 functions, all `clean`; `--pending --package ado2gh/phase` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package; public-surface snapshot unchanged; orphan guard and 800-line guard green (largest file `batch_executor.py` at 273 lines).
- **Not changed, for later increments**: `ado2gh/cli/phase.py::phase_assign` calls `RiskScorer(ado, state).score_all()` and `WaveAssigner(state).assign_and_write(scores, config)`, neither of which has ever existed on those classes — `ado2gh phase assign` is broken at `8ee3251` and before, so this is not a regression from this increment. It is left for the `ado2gh/cli` increment and belongs in the gap register. `WaveRunner.run_wave(wave, dry_run: bool)` in `ado2gh/core/` keeps its boolean flag until increment 7.

## 2026-09-08 — 013 Increment 5: `ado2gh/pipelines/`

Phase 6 (US2) increment 5, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `463cc35`.

The review agent (`cavecrew-reviewer`) decided the package's thirty proposals: three
CONFIRM, all `bool_flag` on a `dry_run` parameter (`PipelineInventoryBuilder.__init__`,
`push_repo_workflows`, `push_workflows_for_repos`), and twenty-seven REJECT — the
`bool_flag` on `build_for_projects` (`include_releases` selects which pipeline types are
scanned, so it is data rather than a mode), eight `name_review` proposals, and every one
of the seventeen `module_name_review` proposals, package and subpackages included. No
ESCALATE, so nothing went to the operator and no module was moved. The ten REJECT lines
whose target still exists were re-recorded after the cleanup, because a decision is
matched by `state_hash` and every edited function got a new one.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/pipelines/push_workflows.py` | (same) | `bool_flag` → `ExecutionMode` | `push_repo_workflows(…, dry_run: bool = False, db=None)` → `push_repo_workflows(…, mode: ExecutionMode = ExecutionMode.LIVE, db=None)`, and the same on `push_workflows_for_repos` (review agent confirmed, FR-012, `contracts/public-contract-freeze.md`). The `LIVE` default preserves the old `dry_run=False` behaviour, and the GAP-016 fail-closed semantics are untouched: readiness is still consulted on exactly the live path (`mode is ExecutionMode.LIVE`), a live push with no state store still fails closed, and the preview path is still the only ungated one. The result dict still carries a boolean `dry_run` key for the UI. Boundary conversion with `ExecutionMode.from_dry_run(dry_run=…)` sits at the CLI flag in `ado2gh/cli/misc.py` (`push-workflows`); `ado2gh/core/scopes/pipelines_scope.py` passes `ExecutionMode.LIVE` at both live call sites | yes | pass (900) | 2026-09-08 |
| `ado2gh/pipelines/inventory.py` | (same) | `bool_flag` → `ExecutionMode` | `PipelineInventoryBuilder(ado, db, parallel=12, dry_run: bool = False)` → `PipelineInventoryBuilder(ado, db, parallel=12, mode: ExecutionMode = ExecutionMode.LIVE)` (review agent confirmed). The attribute `self.dry_run` became `self.mode`, and both write sites now read `self.mode is ExecutionMode.LIVE`. `ado2gh/api/accelerator.py` never passed the flag; `ado2gh/api/migration_scan.py` passed `dry_run=False`, which was the default, so the argument was dropped rather than translated | yes | pass (900) | 2026-09-08 |
| `ado2gh/pipelines/inventory.py` | (same) | keyword-only boolean | `build_for_projects(projects, include_releases: bool = True)` → `build_for_projects(projects, *, include_releases: bool = True)`. The review agent rejected the `bool_flag` proposal — it selects which pipeline types are scanned — so the flag stays a boolean; making it keyword-only clears FBT001/FBT002 without an exception row. No caller passed it | yes | pass (900) | 2026-09-08 |
| `ado2gh/pipelines/validation/workflow_validator.py` | (same) | unused parameter dropped | `WorkflowValidator._validate_actionlint(content, file_path)` → `_validate_actionlint(content)` and `validate_content(content, file_path="workflow.yml")` → `validate_content(content)`. `file_path` was threaded through both methods and read by neither: actionlint is fed the workflow on stdin (`-`), so the name never reaches it. No caller outside the class passed it (`ado2gh/agents/.../tools/validator_tools.py` and the five tests in `tests/unit/test_workflow_validator.py` / `tests/contract/test_step_contracts.py` all call it with content alone) | yes | pass (900) | 2026-09-08 |
| `ado2gh/pipelines/*.py` | (same) | docstrings and annotations | Google-style docstrings (R3) on all 74 functions, on both previously undocumented packages (`ado2gh/pipelines/`, `ado2gh/pipelines/resolve/`) and on the class docstrings that were bare prose blocks; `-> None` on the seven extractor helpers and the builder's `__init__`; `Any` replaced with the concrete type or with `object` where the value really is any YAML node (`_collect_refs`, `_walk_tasks`, `_walk_sc_refs`, `rewrite_expressions_inplace`, `_resolve_template_lists`), so ANN401 no longer fires; `ado: Any` → `ADOClient`, `meta: Any` → `PipelineMetadata`, `extractor: Any` → `PipelineMetadataExtractor`, and `db: Any` → `StateDBBase | None`, all four behind `TYPE_CHECKING` where a runtime import would add an edge; `env_keys: set` → `set[str]`; `build_for_projects` returns `dict[str, dict]`. The `TemplateFetcher` alias gained a comment describing its three arguments. The workflow validator's docstrings say that only secret *names* are collected and that the notes file lists service connections by name, never a value (CA-003); no token, connection string or secret value appears in any docstring or message | yes | pass (900) | 2026-09-08 |
| `ado2gh/pipelines/task_scanner.py` | (same) | import moved | `import json as _json` inside `enrich_pipeline_readiness` became a module-level `import json`, the same treatment increment 3 gave the mixins | yes | pass (900) | 2026-09-08 |

- **Tests removed**: 0. Measured for this increment: 900 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in the shared working tree.
- **Production functions deleted**: 0. No `dead` rows in `ado2gh/pipelines`, so no `inventory.json@463cc35` pointer is needed.
- **Data tables, not functions (T052)**: the ADO task mapping tables in `ado2gh/pipelines/transform/` are module-level `dict`/`set` literals, and the inventory has one row per function, so they were never inventoried. They are now named in `inventory-summary.md` under **Data tables (FR-003b, recorded not excluded)** — `ADO_TASK_MAP`, `RUN_BASED_TASKS`, `_POOL_RUNNER_MAP` and `_extra` in `transform/task_registry.py`, `_DOW_NAMES`/`_DOW_BITS` in `transform/triggers.py`, plus `_SC_INPUT_KEYS`/`_SC_KEY_PATTERNS` in `task_scanner.py` and `POOL_MAP` in `extractor.py` for completeness. They are deliberately **not** added to `excluded-paths.txt`: excluding those modules would also hide the 25 hand-written functions beside the tables, which is the decision T003 already recorded there as a comment.
- **Exception register**: 14 rows of the 29-row cap (4 added), all `gt5_params` with `# noqa: PLR0913`. `extract_yaml_pipeline` takes three separate raw ADO payloads that no existing record model describes; `push_repo_workflows` and `push_workflows_for_repos` take a client, a path and four per-push options around the already-grouped `RepoConfig`; `_expand_template_list` carries the recursion state that `_merge_extends` and `_resolve_template_lists` pass the same way. A new type is forbidden by T052 in every case.
- **Coverage ratchet**: `--cov-fail-under` stays at 59 in `.github/workflows/ci.yml` (measured TOTAL 59 %, not higher — FR-027a/SC-004).
- **Inventory**: `ado2gh/pipelines` 74 functions, 70 `disposition: clean` and 4 `exception`; the summary's clean column reads 69, because `build_for_projects` carries the `bool_data` tag the rejected `bool_flag` leaves behind. `bool_data` means the boolean was decided to be data rather than a switch (data-model.md § tags), so it is a resolution, not an unmet rule — but the SC-001 one-liner in `quickstart.md` § 5 counts every row with any tag and no `exception` disposition, so it will report this row as `tagged-not-excepted` at the final gate. Raised here rather than papered over with an exception row: the check needs to ignore `bool_data`, and that is the operator's call. This is the first `bool_data` row in the tree. `--pending --package ado2gh/pipelines` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package; public-surface snapshot unchanged (`cli_commands`, `db_tables`, `env_vars`, `http_routes` all untouched — the inventory and migration tables keep their shape); orphan guard and 800-line guard green (largest file `transform/transformer.py` at 515 lines).
- **Not changed, for later increments**: `ado2gh/pipelines/transformer.py` is a pre-existing re-export shim for `ado2gh.pipelines.transform` that only `ado2gh/pipelines/__init__.py` reads; the review agent rejected its `module_name_review`, so it stays, the same call increment 3 made for `ado2gh/state/db.py`. `ScopeContext.dry_run` in `ado2gh/core/` is still a boolean and is converted at the `ado2gh/core` increment, not here.
- **Concurrent work**: `ado2gh/cli/phase.py`, `ado2gh/phase/repo_scoring.py`, `tests/unit/test_gap_052_phase_assign_broken.py` and `gap-register.md` were being edited by another agent (GAP-052) while this increment ran; none of them is in this commit. `ado2gh/api/migration_scan.py` was edited by both — only the one-line `PipelineInventoryBuilder` call-site hunk is staged here, the rest stays for that agent's own commit. The 900-test count includes its two new tests.

## 2026-09-08 — 013 Increment 6: `ado2gh/reporting/`

Phase 6 (US2) increment 6, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `7fd1c86`.

The review agent (`cavecrew-reviewer`) decided the package's fourteen proposals: zero
CONFIRM and fourteen REJECT — the eight `name_review` proposals (`_assess_pipeline`,
`workflow_push_readiness`, and the six console and HTML helpers in `reporter.py`, whose
names already say what they return) and all six `module_name_review` proposals (the
package itself and its five modules). No ESCALATE, so nothing went to the operator, no
function was renamed and no module was moved. The `workflow_push_readiness` REJECT was
re-recorded after the cleanup, because a decision is matched by `state_hash` and
annotating its `db` parameter gave the function a new one.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/reporting/csv_exporter.py` | (same) | dead function deleted | `_count_json(raw)` had zero references anywhere in the tree and no test of its own; it was left behind when the pipeline and risk-score export blocks were removed (the three section comments above it are still empty). Deleting it made the module-level `json` and `typing.Optional` imports unused, so both went too | yes | pass (909) | 2026-09-08 |
| `ado2gh/reporting/csv_exporter.py` | (same) | implicit optional fixed | `export_migrations(..., wave_id: int = None)` → `wave_id: int \| None = None` and `export_failed_repos(..., phase: str = None)` → `phase: str \| None = None`. Both already treated `None` as "no filter"; the annotation now says so. No caller changed | yes | pass (909) | 2026-09-08 |
| `ado2gh/reporting/pipeline_readiness.py` | (same) | annotation added | `workflow_push_readiness(db, repo)` → `workflow_push_readiness(db: StateDBBase \| None, repo: RepoConfig)`, the same `StateDBBase \| None` increment 5 used for the pipelines package. `None` is a real argument here, not an oversight: the GAP-016 gate treats a missing state store as a blocker, so the annotation records the fail-closed contract. The return shape `{"blockers": [...], "notes": [...]}` is unchanged and `tests/pipeline/test_gap_016_workflow_push_approval_gate.py` is untouched and green | yes | pass (909) | 2026-09-08 |
| `ado2gh/reporting/pipeline_readiness.py` | (same) | implicit optional fixed | `generate(self, repos: list[RepoConfig] = None, output_path: str = None, ...)` → `repos: list[RepoConfig] \| None = None, output_path: str \| None = None`. Both defaults were already `None` at every call site | yes | pass (909) | 2026-09-08 |
| `ado2gh/reporting/service_connection_manifest.py` | (same) | misplaced docstring fixed | `ServiceConnectionManifest.generate` carried its description as a bare string expression *after* the `output_path` default-resolution block, so it was a no-op statement rather than a docstring and D102 fired. The text moved to the top of the function and grew `Args:` and `Returns:` sections. The default-resolution block is unchanged and still runs first | yes | pass (909) | 2026-09-08 |
| `ado2gh/reporting/*.py` | (same) | docstrings and annotations | Google-style docstrings (R3) on the eight functions that had none — the four `__init__` methods, `PostMigrationValidator.validate`, both `print_summary` methods and `ServiceConnectionManifest.generate` — plus a package docstring on `ado2gh/reporting/__init__.py`; `-> None` on the four `__init__` methods, the three writer helpers (`_write_csv` ×2, `_write_report`) and the three `print_summary` methods. `PostMigrationValidator.validate` and `_validate_one` also lost their implicit-optional `output_path` and `workflow_files` annotations. The service connection docstrings say the manifest carries secret *names* and setup guidance only, never a value (CA-003); no token or connection string appears in any docstring | yes | pass (909) | 2026-09-08 |

- **Tests removed**: 0. Measured for this increment: 909 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in the shared working tree.
- **Production functions deleted**: 1 (`ado2gh/reporting/csv_exporter.py::_count_json`). Its pre-deletion row is at `inventory.json@7fd1c86` (FR-029). It had no exclusive tests, so no test file was touched.
- **Exception register**: unchanged at 14 rows of the 29-row cap. The package needed none: it has no function over five parameters, no `dry_run` boolean and no boolean flag at all, so nothing was left that a `# noqa` would have to cover.
- **Coverage ratchet**: `--cov-fail-under` stays at 59 in `.github/workflows/ci.yml` (measured TOTAL 59 %, equal to the current gate, not higher — FR-027a/SC-004).
- **Inventory**: `ado2gh/reporting` 39 functions (40 before the dead one went), all `disposition: clean`, none `exception`. `--pending --package ado2gh/reporting` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package, and neither does a default `ruff check`; public-surface snapshot unchanged (`report`, `validate`, `pipeline-readiness`, `service-connections` keep their options — only annotations and docstrings moved); orphan guard and 800-line guard green (largest file `reporter.py` at 459 lines).
- **Not changed, for later increments**: `reporter.py` still imports `typing.Optional` for `_fmt_ts`; the package has no other legacy typing use and modernising one alias alone would widen the diff for nothing. `ServiceConnectionManifest` stays deprecated in place — its module docstring already names `analyze_deps` as the replacement and the removal is scheduled for v2.0.0, which is not this feature's call.
- **Concurrent work**: `ado2gh/cli/phase.py`, `ado2gh/phase/repo_scoring.py`, `ado2gh/api/migration_scan.py`, `tests/unit/test_gap_052_phase_assign_broken.py` and `gap-register.md` were being edited by other agents (GAP-052 and the deployment fixes) while this increment ran; none of them is in this commit, and this increment needed no caller change in any of them. The 909-test count includes their new tests.

## 2026-09-08 — 013 Increment 8: `ado2gh/auth/`

Phase 6 (US2) increment 8, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `a780fa3`.

**Run out of order.** The plan orders the fourteen increments 1 → 14 because signature
changes ripple to callers, and increment 7 (`ado2gh/core/`) had not landed when this one
ran. It was taken out of order at the operator's request for maximum parallelism, and it
is safe here for one reason that was checked before any edit: `ado2gh/auth` needed no
signature change and therefore no caller update. The package has no `dry_run` boolean, no
other boolean flag, no function over five parameters, no unused parameter and no
inconsistent return, and the review agent rejected every rename. Nothing in this increment
is visible outside `ado2gh/auth/`, so it cannot collide with increment 7's work on
`ado2gh/core/` or with increments 13 and 14.

The review agent (`cavecrew-reviewer`) decided the package's fifteen proposals: zero
CONFIRM and fifteen REJECT — the eleven `name_review` proposals (`auth_enabled`, `_db`,
`_user_from_row`, `_user_status`, `_user_public`, `permissions_for`, `_audit_auth`,
`needs_bootstrap`, `login`, `logout`, `approve_user`, whose names already say what they
do, several of which are frozen by `contracts/public-contract-freeze.md` anyway) and all
four `module_name_review` proposals (the package itself and its three modules, which
follow the same `models.py` / `service.py` convention as the rest of the tree). No
ESCALATE, so nothing went to the operator, no function was renamed and no module was
moved.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/auth/service.py` | (same) | `Any` replaced with the concrete type | `_db() -> Any` → `_db() -> StateStore` and `AuthService.__init__(self, db: Optional[Any] = None)` → `Optional[StateStore]`, reusing the existing `StateStore` union from `ado2gh/state/factory.py` rather than adding a type (ANN401 ×2). `__init__` also gained its missing `-> None` (ANN204). The import direction stays `auth → state`, which increment 3 established; no `auth → api` edge was added (GAP-021). Every construction site already passes a `create_state_db(...)` result or `StateDB(...)` — an alias for `SQLiteStateDB`, inside the union — so the annotation is accurate at all 28 of them and none had to change. `typing.Any` is no longer imported | yes | see note below | 2026-09-08 |
| `ado2gh/auth/*.py` | (same) | docstrings | Google-style docstrings (R3) on the twenty functions and methods that had none or only a summary line, plus class docstrings with `Attributes:` on `PlatformRole`, `PlatformUserStatus`, `PlatformUser` and `AuthSession` (D101 ×4, D102 ×9, D103 ×2, D107 ×1). Per T058 and CA-003, no session or password function gained a docstring example containing credentials: `hash_password` describes the stored format in words with no example hash or salt, `AuthSession.token` is documented as a bearer credential that must not be logged, `_audit_auth` states that callers must keep passwords, hashes and tokens out of the payload, and `_user_from_row` / `_user_public` record that the password hash is dropped. The docstrings also capture two security behaviours that were previously only comments — that an unknown username, a wrong password and a lockout all raise the same message, and that `get_session` deletes a token whose account may no longer authenticate | yes | see note below | 2026-09-08 |

- **No behaviour change**: password hashing, session-token generation, the lockout counter, the permission table, the audit events and every `db_tables` / `env_vars` entry are untouched. The diff is docstrings, four annotations and one dropped import.
- **Tests removed**: 0. **Production functions deleted**: 0 — no `dead` rows in `ado2gh/auth`, so no `inventory.json@a780fa3` pointer is needed.
- **Test status**: this increment's own gate set is green — `tests/auth` (39 passed, including the untouched `test_gap_002_*`, `test_gap_005_*` and `test_gap_010_*` regressions), the public-surface snapshot, the orphan guard and the 800-line guard, 44 passed together. The **full** suite in the shared working tree reports 893 passed, 16 failed, 30 skipped. All sixteen failures are one half-applied change in increment 7's concurrent work: `MigrationEngine.__init__()` and `ScopeContext.__init__()` have been converted from `dry_run: bool` to `ExecutionMode` in `ado2gh/core/` but their test callers have not been updated yet, so every failure is `TypeError: … got an unexpected keyword argument 'dry_run'` in `tests/core/`, `tests/pipeline/test_pipelines_scope.py`, `tests/pipeline/test_pipeline_runs.py`, `tests/pipeline/test_gap_016_*`, `tests/unit/test_migrate_routes_core.py` and `tests/unit/test_gap_007_*`. None of them imports `ado2gh/auth`, and all sixteen pass again once increment 7 finishes its caller updates. Full output in `specs/013-clean-code-arch-remediation/run-inc8-full.txt`.
- **Coverage ratchet**: `--cov-fail-under` stays at **59** in `.github/workflows/ci.yml`. Measured TOTAL is 58 %, one point below the gate, because the sixteen failing core tests leave their own code paths uncounted; it is not lowered (FR-027a/SC-004 forbids lowering, and the shortfall is not this package's). The true figure for this tree is the 59 % increment 6 measured — `ado2gh/auth` gained no new lines, only docstrings.
- **Exception register**: unchanged at 14 rows of the 29-row cap. The package needed none — after the cleanup the T002 ruff set with `max-args=5` reports nothing for `ado2gh/auth`, and neither does a default `ruff check`, so there was nothing for a `# noqa` to cover.
- **Inventory**: `ado2gh/auth` 26 functions, all `disposition: clean`, none `exception`. `--pending --package ado2gh/auth` exits 0.
- **Concurrent work**: increments 7 (`ado2gh/core/`), 13 (`services/agent/`) and 14 (`apps/migration-ui/`) were running in the same tree. None of their files is in this commit, and this increment needed no change in any of them. One transient effect is worth recording: a first full-suite run wedged indefinitely in `tests/agent/test_agent_llm_health.py` while increment 13 was mid-write splitting `services/agent/routes/`; the same file passes in 6 s on its own once those writes settled, so it is a race against an in-flight edit, not a defect.

## 2026-09-08 — GAP-052

Recorded here by the increment-7 agent on behalf of the GAP-052 commit agent, which
could not edit this file while increment 6 held it.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/phase/repo_scoring.py` | (same) | **added** | New module holding the per-repository risk-scoring step (`score_repo`) lifted out of `MigrationScanner.scan`, plus `score_org_repos` which walks the organisation and takes each repo's pipelines from the state DB inventory. Created for GAP-052 so that the CLI (`ado2gh phase assign`) and the accelerator scan path share one implementation; placed under `phase/` so `phase/` continues to import nothing from `api/`. Commit `dc96e29`. | yes | pass | 2026-09-08 |

## 2026-09-09 — 013 Increment 7: `ado2gh/core/`

Phase 6 (US2) increment 7, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `79b5673`.

The review agent (`cavecrew-reviewer`) decided the package's forty-five proposals in two
rounds. The first is the tracked pre-check `review-precheck.md` (committed at `79b5673`),
which covered all forty-five — seventeen CONFIRM, twenty-eight REJECT, no ESCALATE — and
whose `state_hash` matched on every one at regeneration, so every verdict applied without
re-asking. The second round was needed because a decision is matched by `state_hash`:
after the cleanup, eight function rows and one module id no longer matched (six renames
and two signature changes carried the rest away, and the module rename created a new id),
so those nine went back to the reviewer. That round REJECTed all nine. Two of them,
`ADOCleanup.cleanup_repos` and `ADOCleanup._cleanup_one`, had been CONFIRM in the
pre-check and are now REJECT, which turns their `bool_flag` proposal into a `bool_data`
tag; the reviewer's reason is that the three cleanup switches are independent and every
combination of them is valid, so there is no two-function or enum form to move them to.
The reversal is recorded here because it is a genuine change of verdict on the same
functions, not a stale row.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/core/migration_fr036.py` | `ado2gh/core/conflict_detection.py` | moved | Confirmed `module_name_review` (FR-013): the old name pointed at a requirement id rather than at a responsibility. The module detects concurrent-run conflicts on a repository, so the new name says that. Imports updated in `ado2gh/agents/migration_agent/session/lifecycle.py` (two function-local imports), `ado2gh/api/pipeline_steps.py`, `ado2gh/core/migration_engine.py`, `tests/core/test_gap_014_concurrency_guard_fails_closed.py` and `tests/core/test_migration_fr036.py`. No alias is left at the old path (FR-006a / R16) and no module imports `ado2gh.core.migration_fr036` any more. The orphan-guard allowlist never named the module, so it needed no edit. The GAP-014 regression test imports `other_run_holds_repo` and `clear_stale_in_progress_migrations` from the new path and is otherwise untouched; its assertions are unchanged and it stays green | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/conflict_detection.py` | (same) | renamed function | Confirmed `name_review`: `repo_conflict_reason` → `get_repo_conflict_reason`, a verb form for a function that returns a value. Its two callers are in the same module and in `migration_engine.py`; no test names it. The GAP-014 fail-closed behaviour is untouched — both conflict sources are still asked, and a source that raises still produces a reason string rather than a silent "free" | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/migration_engine.py` | (same) | renamed function | Confirmed `name_review`: `MigrationEngine._in_progress_block_reason` → `_get_in_progress_block_reason`. Private, one call site in the same method body | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/gei_runtime.py` | (same) | renamed function | Confirmed `name_review`: `gei_subprocess_env` → `build_gei_subprocess_env`, since the function builds the environment rather than naming one. Callers updated in `ado2gh/core/scopes/git_scope.py` and `tests/core/test_gei_runtime.py` | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/scopes/git_scope.py` | (same) | renamed functions | Confirmed `name_review` ×2: `_ado_git_env` → `_build_ado_git_env` and `GitScopeHandler._source_default_branch` → `_get_source_default_branch`. Callers updated in the same module and in `tests/core/test_git_scope_auth.py` | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/scopes/pipelines_scope.py` | (same) | renamed function | Confirmed `name_review`: `_workflow_branch` → `_get_workflow_branch`. Module-private, one call site | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/rollback.py` | (same) | dead function deleted | `RollbackHandler.rollback_repos` had zero references anywhere in the tree (vulture confidence 60, reference scan 0) and no test of its own. The CLI rolls back by wave (`ado2gh rollback --wave N [--scopes ...]`), which goes through `rollback_wave`; nothing ever reached the per-repo entry point. The class docstring lost its matching "Repo-level rollback" bullet. Its pre-deletion row is at `inventory.json@79b5673` (FR-029). Deleting it also removed one of the ten confirmed `dry_run` booleans before it had to be converted | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/scopes/base.py` | (same) | `bool_flag` → `ExecutionMode` | `ScopeContext.dry_run: bool = False` → `mode: ExecutionMode = ExecutionMode.LIVE`. This is the field all six scope handlers read, so it is the hinge of the package's conversion: `ctx.dry_run` became `ctx.mode is ExecutionMode.DRY_RUN` in `git_scope`, `pipelines_scope`, `branch_policies_scope`, `secrets_scope`, `wiki_scope` and `work_items_scope`. `LIVE` preserves the old `dry_run=False` default exactly. The boundary conversion sits at the one external constructor, `services/accelerator_api/routes/migrate_routes.py`, which still takes a boolean `dry_run` from the HTTP layer and calls `ExecutionMode.from_dry_run(dry_run=...)`. Every handler still writes the boolean `dry_run: True` key into its stats dict, so the result shape the UI reads is unchanged | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/scopes/base.py` | (same) | shared handler signature (FR-011) | `ScopeHandler.migrate` and all six implementations now carry one identical signature, `def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: object) -> ScopeResult`. `**kwargs: Any` became `**kwargs: object` so ANN401 no longer fires on any of the seven; the two handlers that read the mapping narrow it at the point of use — `git_scope` and `pipelines_scope` take `concurrency` through `isinstance(..., ConcurrencyManager)`, and `pipelines_scope` takes `pipeline_parallel` and `wave_id` through `isinstance(..., int)` with the `ctx` value as the fallback, which is what the old `kwargs.get(key, ctx.value)` did. `MigrationEngine` now builds `kwargs: dict[str, object]`. The four handlers that ignore the mapping keep `**kwargs` for the shared signature and carry `# noqa: ARG002` with an exception-register row each. `ScopeContext.ado`/`gh`/`db` also lost their `Any` annotations for `ADOClient`, `GHClient` and `StateDBBase` under `TYPE_CHECKING` | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/migration_engine.py` | (same) | `bool_flag` → `ExecutionMode` | `MigrationEngine.__init__(..., dry_run: bool = False, ...)` → `mode: ExecutionMode = ExecutionMode.LIVE`; the attribute `self.dry_run` became `self.mode` and its eight read sites became `self.mode is ExecutionMode.LIVE`. No caller read `engine.dry_run`. Boundary conversions at `ado2gh/api/accelerator.py` (two call sites, from `request.dry_run`), `ado2gh/api/pipeline_steps.py` (from `run.dry_run`) and `ado2gh/cli/pipelines.py` (from the `--dry-run` flag). The FR-036 guard still only runs on the live path, so GAP-014's refusal path is reached under exactly the same condition as before | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/wave_runner.py` | (same) | `bool_flag` → `ExecutionMode` | `WaveRunner.run_wave(wave, dry_run: bool = False)` → `mode: ExecutionMode = ExecutionMode.LIVE`. The method already converted internally with `ExecutionMode.from_dry_run` before handing the wave to `BatchExecutor`; that conversion moved out to the single CLI caller in `ado2gh/cli/pipelines.py`, which now builds one `mode` and passes it to both the engine and the runner | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/rollback.py` | (same) | `bool_flag` → `ExecutionMode` | Four conversions: `rollback_wave`, `_rollback_repo`, `_rollback_branch_protection` and `_rollback_pipelines` all take `mode: ExecutionMode` in place of `dry_run: bool`, with `LIVE` preserving the old `False` default on the public entry point. `rollback_wave` keeps a local `dry_run` boolean for one log string only. The CLI surface is frozen and unchanged: `ado2gh rollback --wave N --dry-run --scopes branch_policies,pipelines` still takes the same options, and `ado2gh/cli/run_cmd.py` converts at the flag with `ExecutionMode.from_dry_run`. The destructive paths still gate on mode — a dry run logs "would delete" and deletes nothing, and the interactive `click.confirm` before a live rollback is untouched | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/rollback.py` | (same) | unused param dropped | `_rollback_branch_protection(gh_org, gh_repo, dry_run, stats)` never read `stats`; the parameter is gone and the one internal call site updated. `_rollback_repo` and `_rollback_pipelines` do use theirs and keep it | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/ado_cleanup.py` | (same) | `bool_flag` → `ExecutionMode` | `ADOCleanup.__init__(ado, dry_run: bool = False)` → `mode: ExecutionMode = ExecutionMode.LIVE`; `self.dry_run` became `self.mode` and its five read sites became `self.mode is ExecutionMode.DRY_RUN`. This class disables ADO pipelines, pushes `MIGRATION_NOTICE.md` and archives repositories, so every one of those three write paths still returns early on `DRY_RUN` and touches nothing (CA-001). Boundary conversions at `ado2gh/cli/misc.py` (the `ado-cleanup --dry-run` flag) and `ado2gh/core/rollback.py`, which passes its own `mode` straight through. The `{"dry_run": True}` entries in the returned dicts are unchanged | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/ado_cleanup.py` | (same) | booleans made keyword-only | `cleanup_repos` and `_cleanup_one` take `disable_pipelines`, `add_redirect` and `archive_repo` keyword-only, which silences nine FBT001/FBT002 findings without changing any default or any behaviour; the internal `pool.submit` call now passes them by name. The three switches stay booleans: the review agent REJECTed `bool_flag` on both functions in the second round, so the rows carry `bool_data`, the tag that means the boolean is data rather than a mode switch. No exception-register row is needed for either | yes | pass (909) | 2026-09-09 |
| `ado2gh/core/*.py`, `ado2gh/core/scopes/*.py`, `ado2gh/core/orchestration/*.py` | (same) | docstrings and annotations | Google-style docstrings (R3) on the thirty-three functions that had none, package docstrings on `ado2gh/core/__init__.py`, `ado2gh/core/orchestration/__init__.py` and `ado2gh/core/scopes/__init__.py`, and class docstrings on the six scope handlers plus `ConcurrencyManager`, `ConfigLoader`, `ScopeContext`, `ScopeResult` and `ScopeHandler`. Twenty-eight missing annotations added: `-> None` on eight `__init__` methods and the writer helpers, `execute_job(job: JobRecord)`, `RedisJobQueue.redis -> "redis.Redis"` behind a `TYPE_CHECKING` import, `build_scope_registry() -> dict[str, ScopeHandler]`, `_do_transform(..., output_root: Path)`, and `MigrationEngine.migrate_repo(progress: Progress \| None, task_id: TaskID \| None)` with `rich.progress` imported under `TYPE_CHECKING` in place of two `Any`. `ConfigLoader.parse_config(raw: Any)` became `raw: object`, which the existing `isinstance(raw, dict)` guard already narrowed. Every non-void function now carries a `Returns:` (or `Yields:`) section and every private helper carries a docstring. The git and secrets docstrings name the credential *variables* and say the values are masked before reaching logs, errors or audit records; no token, PAT or connection string appears in any of them (CA-003) | yes | pass (909) | 2026-09-09 |

- **Tests removed**: 0. Measured for this increment: 909 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in the shared working tree.
- **Production functions deleted**: 1 (`ado2gh/core/rollback.py::RollbackHandler.rollback_repos`). Its pre-deletion row is at `inventory.json@79b5673` (FR-029). It had no exclusive tests, so no test file was deleted.
- **`dry_run` conversions**: nine, not ten. The task named ten confirmed `dry_run` booleans; the tenth was `RollbackHandler.rollback_repos`, which the `dead` row removed before it needed converting. The nine are `ADOCleanup.__init__`, `MigrationEngine.__init__`, `WaveRunner.run_wave`, `RollbackHandler.rollback_wave`, `_rollback_repo`, `_rollback_branch_protection`, `_rollback_pipelines`, and the `ScopeContext.dry_run` field that all six scope handlers read. Every one keeps its old default exactly: `LIVE` wherever the old default was `dry_run=False`, and the two destructive classes (`ADOCleanup`, `RollbackHandler`) still refuse to touch anything unless the caller asks for `LIVE` (CA-001).
- **Masking (GAP-011)**: `ado2gh/core/scopes/git_scope.py::_redact` is unchanged in behaviour — it still strips the exact credential values the caller holds and then routes the result through `redact_payload`, the platform's single masking choke point. Only its docstring grew.
- **Exception register**: 14 → 19 rows of the 29-row cap. One is `MigrationEngine.__init__` (`gt5_params`, 9 > 5, `# noqa: PLR0913`): it takes two clients, the state store, the parsed config, the execution mode and four run-scoping arguments that different callers set independently, and no existing record model holds that set. The other four are the scope handlers that ignore `**kwargs` (`# noqa: ARG002`), which FR-011 requires them to keep so that `MigrationEngine` can dispatch through one signature.
- **Coverage ratchet**: `--cov-fail-under` stays at 59 in `.github/workflows/ci.yml` (measured TOTAL 59 %, equal to the current gate, not higher — FR-027a/SC-004).
- **Inventory**: `ado2gh/core` 76 functions (77 before the dead one went), 71 `disposition: clean` and 5 `exception`. The summary's clean column reads 73 because it counts untagged rows, and the two `bool_data` rows in `ado_cleanup.py` are tagged without being exceptions — the same accounting increment 5 recorded for `build_for_projects`. `--pending --package ado2gh/core` exits 0; the T002 ruff set with `max-args=5` reports nothing for the package, and a default `ruff check ado2gh/ services/` reports only the five findings that pre-date this feature in `ado2gh/agents/`; public-surface snapshot unchanged (`rollback`, `ado-cleanup`, `pipelines retry-failed` keep their options — only internal signatures moved); orphan guard and 800-line guard green (largest file `scopes/git_scope.py` at 567 lines, up from 398 because of the docstrings).
- **Not changed, for later increments**: `ado2gh/core/scopes/pipelines_scope.py` still imports `typing.Any` for two `dict[str, Any]` stats dicts, which are genuinely heterogeneous; `ado2gh/core/scopes/git_scope.py` does the same. `tests/pipeline/test_pipelines_scope.py` carries a pre-existing unused `pathlib.Path` import that this increment did not introduce and did not remove.
- **Concurrent work**: increments 13 (`services/agent/`) and 14 (`apps/migration-ui/`) were editing the same tree while this increment ran, and increment 8 (`ado2gh/auth/`) committed part-way through. None of their files is in this commit. The 909-test count includes their work.

## 2026-09-09 — 013 Increment 13: `services/agent/`

Phase 6 (US2) increment 13, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `7fd1c86`.

**Run out of order.** The plan orders the fourteen increments 1 → 14 because Python
signature changes ripple to callers, and increments 9 through 12 had not landed when this
one ran. It was taken out of order at the operator's request for maximum parallelism, and
is safe for one reason checked before any edit: `services/agent/` is a leaf — nothing in
the tree imports it — so cleaning it cannot break a later increment. The reverse ripple,
increment 10 changing `ado2gh/agents/` signatures that these route modules call, is
handled there by FR-007's all-callers rule.

The review agent (`cavecrew-reviewer`) decided the package's twelve function-level
proposals: zero CONFIRM, twelve REJECT — eleven `name_review` proposals (the FastAPI
route handlers, whose names are fixed by their endpoints and the frozen HTTP surface,
plus `_recover_sessions_on_restart`, `agent_auth_middleware`, `capability_matrix` and
`_continue_graph_after_form`, whose names already say what they do) and the
`unused_param` proposal on `agent_models`, where `request: Request` is framework-injected
and must not be dropped. Those eleven `name_review` verdicts match the ones recorded
independently in `review-precheck.md` batch B13 by `state_hash`. No function was renamed
and no parameter dropped.

The reviewer returned one **ESCALATE** on `services/agent/routes/session_routes.py`'s
`module_name_review` — it judged that the 1439-line module spanned six domains and would
benefit from a split, but that module granularity was a team preference it could not
settle from code alone. **The operator decided it on 2026-09-08: CONFIRM, split it.**
That decision overrides both the reviewer's ESCALATE and the REJECT that
`review-precheck.md` had recorded for the same module, and `tag-decisions.json` records
it as `--decided-by operator`, not by the reviewer. The other nine `module_name_review`
proposals in the package were rejected.

The package had no `dry_run` boolean parameter, no other boolean flag, no function over
five parameters, no mutable default and no inconsistent return, so no `ExecutionMode`
conversion happens here. Every `dry_run` in `services/agent` is a Pydantic field read, a
session-dict key, or a keyword passed into `ado2gh/`; the models and
`new_isolated_agent_session` that own those booleans live in
`ado2gh/agents/migration_agent/`, so their boundary conversion belongs to increment 10.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `services/agent/routes/session_routes.py` | (same) | module split — source retained | Operator-confirmed `module_name_review`. The 1439-line module was cut into six by domain. This file survives rather than being recreated so `git blame` stays useful, and keeps the session lifecycle handlers: `create_session` (was lines 138-235), `list_sessions` (237-297), `get_session` (299-305), `delete_session` (307-330), `provision_session` (1279-1290), `remediate_session` (1292-1304). Now 270 lines | yes | pass | 2026-09-09 |
| `services/agent/routes/session_routes.py` | `services/agent/routes/message_routes.py` | module split — created | Chat message domain: `_reject_if_session_busy` (was lines 79-85), `session_message` (546-597), `session_message_stream` (599-673). The helper moved with its only two call sites. 173 lines | yes | pass | 2026-09-09 |
| `services/agent/routes/session_routes.py` | `services/agent/routes/form_routes.py` | module split — created | Human-in-the-loop form domain: `_resolve_pending_form` (was lines 87-103), `_continue_graph_after_form` (105-124), `_ensure_repo_valid_for_migration` (126-136), `submit_session_form` (675-937), `submit_session_form_stream` (939-1222), `cancel_session_form` (1259-1277). All three helpers are used only by these handlers. 665 lines, the largest of the six | yes | pass | 2026-09-09 |
| `services/agent/routes/session_routes.py` | `services/agent/routes/plan_routes.py` | module split — created | Migration plan domain: `create_migration_plan` (was lines 332-379) and `get_plan_summary` (1343-1389). The three plural `scopes` / `blocked_reasons` reads that `sync_work_item_wire_keys` keeps in step (GAP-015, formerly `session_routes.py:1301,1315,1317`) moved intact with `get_plan_summary` and are now `plan_routes.py:98,112,114`. 123 lines | yes | pass | 2026-09-09 |
| `services/agent/routes/session_routes.py` | `services/agent/routes/execution_routes.py` | module split — created | Execution and live-approval domain: `run_pev` (was lines 381-417), `request_live` (419-427), `approve_session` (429-486), `confirm_live_execution` (1391-1415), `update_execution_mode` (1224-1257), `cancel_session` (1417-1439), `resume_live_internal` (488-523), `deny_live_internal` (525-544). `approve_session` was moved here rather than to `plan_routes` as first sketched, because it is part of the live-approval handshake, not a plan operation — it calls `_require_approve_live`, forwards to the platform approval queue and sets `live_approval_status`. The GAP-002/GAP-006 wiring (`enforce_live_mode_request` after `_require_operate`, the gate on `confirm_live_execution`, the `session.confirm_live` and `session.resume_live` audit events) moved unchanged. 294 lines | yes | pass | 2026-09-09 |
| `services/agent/routes/session_routes.py` | `services/agent/routes/model_routes.py` | module split — created | LLM and model catalogue domain: `agent_models` (was lines 1306-1312) and `llm_status` (1314-1338). 46 lines | yes | pass | 2026-09-09 |
| `services/agent/main.py` | (same) | router wiring + signature cleanup | The two router imports and two `include_router` calls became seven of each, one per route module; paths are disjoint so registration order does not affect matching. Separately, `agent_auth_middleware` gained `call_next: Callable[[Request], Awaitable[Response]]`, `-> Response` and a Google-style docstring. Per CA-003 that docstring names the `x-ado2gh-internal-token` header and never a value, and it describes the GAP-003 fail-closed behaviour without quoting the secret | yes | pass | 2026-09-09 |
| `services/agent/routes/run_routes.py` | (same) | docstrings + annotations | `health` gained a docstring and `-> dict[str, Any]`; `metrics` gained `-> PlainTextResponse`, which required hoisting its function-local `PlainTextResponse` import to module level, and its `T077:` docstring was rewritten as an OpenAPI-facing description per FR-010a | yes | pass | 2026-09-09 |
| `services/agent/**` | (same) | docstrings + annotations | Google-style docstrings (R3, FR-010a for route handlers) on the fifteen functions that had none, and return annotations on the thirty-seven that had none — twenty-three route handlers, nine nested SSE generator functions now `-> AsyncIterator[str]`, and the middleware. `agent_models`'s unused but framework-injected `request` was renamed `_request`, which satisfies ARG001 by the standard dummy-argument convention instead of spending an exception-register row. Two route docstrings that opened with task ids (`get_plan_summary`, `confirm_live_execution` — "T063: …") were rewritten as consumer-facing OpenAPI descriptions, keeping the CA-001 warning that `confirm-live` is the last gate; no test greps for those markers | yes | pass | 2026-09-09 |
| `tests/unit/test_file_size_limit.py` | (same) | allowlist entry removed | `services/agent/routes/session_routes.py` dropped from `US2_ADDRESSED`. All six route modules now pass the 800-line cap on their own merit, the largest being `form_routes.py` at 665 lines | yes | pass | 2026-09-09 |

- **Caller updates (FR-007)**: the split moved handlers out from under the module paths that
  tests patch, so five patch-target strings were repointed —
  `tests/agent/test_agent_live_execution_gate.py` (`_try_start_pev_run` → `execution_routes`),
  `tests/agent/test_agent_admin_live_gate.py` (`_try_start_pev_run` → `form_routes`, where its
  `assert_called_once` fires; that test is `@pytest.mark.skip` pending a LangGraph rewrite, so
  the mapping is unverified), and `tests/agent/test_gap_003_internal_live_routes_unauthenticated.py`
  (`_audit` → `execution_routes`, plus the file reference in its module docstring). **That
  GAP-003 regression test's assertions, fixtures and body were not touched** — only the patch
  target string, which a module move requires. Two doc comments in
  `tests/contract/test_gap_015_work_item_field_contract.py` citing `session_routes.py:1301`
  and `:1298` now cite `plan_routes.py::get_plan_summary`. `CLAUDE.md` lists all seven route
  modules. `tests/agent/test_agent_chat_idle.py`, `test_agent_pev_live_gate.py` and the two
  `tests/feature/` files needed no change: every one of their patch targets drives
  `create_session`, which stayed in `session_routes.py`.
- **Tests removed**: 0. **Production functions deleted**: 0 — no `dead` rows in
  `services/agent`, so no `inventory.json@7fd1c86` pointer is needed.
- **Exception register**: unchanged at 14 of 29 rows. This increment added no `# noqa` and
  needed no exception row.
- **Frozen seams**: `tests/contract/test_public_surface_snapshot.py` passes unchanged — no
  route added, removed or re-verbed by the split. The SSE seams are byte-identical: the
  twenty-eight `yield f"data: …"` lines at `7fd1c86` diff clean against the twenty-eight now
  spread across the six modules, so event names, JSON keys and ordering are untouched.
- **Verification**: `ruff check services/agent --select D1,ANN,FBT001,FBT002,PLR0913,B006,ARG,RET501,RET502,RET503 --config "lint.pylint.max-args=5"` reports zero, down from fifty-two at
  baseline, and `services/` is clean under the default `ruff check` too.
  `--pending --package services/agent` exits 0. All forty rows are `clean` with zero tags.
  The orphan guard and the 800-line guard pass; the five new modules are statically imported
  by `main.py`, so no orphan allowlist entry was needed.
- **Coverage**: TOTAL 59 %, equal to the `--cov-fail-under=59` already in
  `.github/workflows/ci.yml`, so the ratchet stays where it is and the file is not touched
  (FR-027a/SC-004 — never lowered).
- **Test status**: full suite green in the shared working tree — 909 passed, 30 skipped,
  0 failed, coverage TOTAL 59 %. This increment's own gate set (`tests/agent`,
  `tests/contract`, the public-surface snapshot, the orphan guard and the 800-line guard)
  is green on its own at 126 passed, 21 skipped. Full output in
  `specs/013-clean-code-arch-remediation/run-inc13-full.txt`; the gate-set output is in
  `run-inc13-phaseA.txt` and `run-inc13-phaseA2.txt`.
- **Concurrent work**: increment 8 measured sixteen `TypeError: … got unexpected keyword
  argument 'dry_run'` failures earlier the same day, from increment 7's half-applied
  `ado2gh/core/` conversion of `MigrationEngine.__init__` and `ScopeContext.__init__` to
  `ExecutionMode`. Those had cleared by the time this increment measured, so none are
  recorded here. Increment 7's staged
  `git mv ado2gh/core/migration_fr036.py → conflict_detection.py` sat in the index
  throughout and was left untouched by using a path-limited commit. An earlier full run of
  this increment wedged in `tests/contract/test_agent_pev_flow_contracts.py` under
  contention from other agents' concurrent test runs; killing and re-running it completed
  normally in 87 s.

## 2026-09-09 — 013 Increment 14: `apps/migration-ui/src`

Phase 6 (US2) increment 14, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `7fd1c86`.

**Run out of order, at the operator's request.** The plan orders increments 1 - 14 strictly
because Python signature changes ripple through their callers. This increment is the Next.js
console: TypeScript with no Python callers, so it sits outside that ripple and could run
alongside the Python increments without reading a signature any of them was mid-way through
changing. It touches no file under `ado2gh/` or `services/`, and the only shared artefacts it
writes are the four inventory and decision files, written after increments 8 and 13 had
committed.

Decisions came from `review-precheck.md`, whose eleven `apps/migration-ui` rows all matched the
inventory by `state_hash`: three CONFIRM and eight REJECT, no ESCALATE. A twelfth row,
`gateOverrideReason`, is newer than the pre-check — GAP-009 added it later in the same session —
so it was reviewed separately and confirmed, giving four CONFIRM in total. Eleven decisions are
recorded; the REJECT on `agent.ts::patchAgentExecutionMode` is not, because that function was
deleted as dead in this increment and a decision against a row that no longer exists is pruned
on the next regeneration.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `apps/migration-ui/src/**/*.ts`, `*.tsx` | (same) | JSDoc on every export | FR-010: a leading `/** ... */` block on all 198 exported functions, components, hooks and consts across 62 files, in the console's existing house style (one-line block where it fits, multi-line otherwise). Written deliberately **without `@param` tags**: the walker raises `stale_docstring` whenever the `@param` names do not match the parameter list exactly, and a destructured props parameter can never match, so `@param` would have traded one tag for another. CA-003 honoured — `auth.ts` and `llmSettings.ts` describe credential and API-key shapes in words, and no doc block contains a token, key or secret value | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/components/DependencyGraph.tsx` | — | deleted (file) | Its only export `DependencyGraph` had zero references; the local `DependencyGraphProps` interface went with it | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/lib/navigationState.ts` | — | deleted (file) | All four exports (`useActiveTab`, `useDiscoverySubTab`, `useMigrateSubTab`, `getTabById`) had zero references. What remained was a re-export barrel for three constants that live in `lib/types/navigation.ts`, so the three importers — `components/UnifiedNavigation.tsx`, `app/settings/discovery/page.tsx` and `app/settings/discovery/WorkflowsAndValidation.tsx` — now import `UNIFIED_TABS` / `DISCOVERY_SUB_TABS` straight from `@/lib/types/navigation` (FR-007) | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/components/Icons.tsx`, `lib/agent.ts`, `lib/api.ts`, `lib/llmSettings.ts` | (same) | deleted functions | 21 dead exports in total across these files and the two deleted above — zero references, each re-verified by grep over `apps/migration-ui` before deletion. The rows are in `inventory.json@7fd1c86` carrying the `dead` tag; the names are listed below. 0 tests removed — nothing in the suite referenced any of them | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/components/PipelineProgress.tsx` | (same) | `bool_flag` to two components | `StepPipelineBar({ steps, compact = false })` became a private `PipelineBar({ steps, compact })` holding the single copy of the render logic, plus two intent-named exports `StepPipelineBar({ steps })` and `CompactStepPipelineBar({ steps })` (review agent confirmed, FR-012). Rendered output is unchanged on both branches — same element tree, class names, icon sizes and conditional labels row. The only call site needing a change was `app/page.tsx:312` | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/components/ScanRecommendations.tsx` | (same) | `bool_flag` to prop deleted | The confirmed `compact` prop was never passed by its one call site, so both of its branches were dead: the `scan-phase-grid-compact` class was never applied and the `!compact` repo-list guard was always true. Deleting the prop and the dead branches is a smaller remediation than a two-component split and leaves the render identical | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/lib/pipelineRunStatus.ts` | (same) | `bool_flag` to two functions | `gateOverrideReason(dryRun: boolean, reason: string)` became `liveRunOverrideReason(reason)` and `dryRunOverrideReason()` (reviewed separately as a post-pre-check row and confirmed; the boolean did branch inside the function rather than passing through to the wire). Call site `app/settings/migrate/page.tsx:79` now selects on `dryRun`. GAP-009's three regression assertions in `lib/pipelineRunStatus.test.ts` were retargeted rather than dropped, so "a dry run sends no justification" is still an assertable unit | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/lib/agent.ts`, `api.ts`, `agentSessions.ts`, `cloudCredentials.ts` | (same) | annotations | Six `untyped` rows were default-valued parameters with no explicit annotation: `reason: string = ''` on `agent.ts::approveAgentSession`, `api.ts::denyProfile` and `cloudCredentials.ts::rejectCloudCredential`; `status: string = 'pending'` on `api.ts::fetchLiveApprovals`; `max: number = 56` on `agentSessions.ts::truncateTitle`; `scan: boolean = true` on `cloudCredentials.ts::fetchCloudCredentials`. No `any` parameter existed anywhere under `src/`, so that half of T070 was a no-op | yes | pass (47) | 2026-09-09 |
| `apps/migration-ui/src/components/AgentChat.tsx` | (same) | unused parameter dropped | The one `unused_param` row was not a prop of `AgentChat` itself but two unread destructured parameters (`streaming`, `messageId`) on `TypewriterMessage`, a local non-exported component inside the file. Removed from the destructuring pattern only; the type annotation and the call site are unchanged, so the public surface is untouched | yes | pass (47) | 2026-09-09 |

- **Dead exports deleted**: 21, zero tests removed. `components/DependencyGraph.tsx::DependencyGraph`; from `components/Icons.tsx` — `CheckCircleIcon`, `ChevronLeftIcon`, `ChevronRightIcon`, `ClipboardIcon`, `ClipboardListIcon`, `HistoryIcon`, `WorkflowIcon`; from `lib/agent.ts` — `confirmLiveExecution`, `fetchAgentMetrics`, `getPlanSummary`, `patchAgentExecutionMode`; from `lib/api.ts` — `deactivateProfile`, `fetchHealth`, `runMigrationScan`, `validateConnection`; `lib/llmSettings.ts::validateSavedModel`; from `lib/navigationState.ts` — `getTabById`, `useActiveTab`, `useDiscoverySubTab`, `useMigrateSubTab`. FR-029 pointer: `inventory.json@7fd1c86`, package `apps/migration-ui`, the rows carrying the `dead` tag.
- **Agent routes now without a console client**: deleting `patchAgentExecutionMode`, `getPlanSummary`, `confirmLiveExecution` and `fetchAgentMetrics` leaves `PATCH /v1/sessions/{id}/execution-mode`, `GET /v1/sessions/{id}/plan-summary`, `POST /v1/sessions/{id}/confirm-live` and `GET /metrics` on `services/agent` with no caller in the console. Those routes are a frozen seam and stay exactly as they are — this is recorded so a reader does not mistake the missing client for a broken feature, and so nobody deletes the routes on the strength of a console-only grep.
- **Tests**: the console suite is 9 files / 47 tests, green before and after. The Python suite was not run — this increment changes no Python. `src/__tests__/exports-documented.test.ts` (T015's walker guard) drives the walker over a temporary fixture rather than real console source, so the deletions and the JSDoc could not affect it; it passes 4/4.
- **Exception register**: 20 rows of the 29-row cap (1 added) — `lib/agentChat.ts::isAgentInterruptible`, the one confirmed `bool_flag` with no available remediation. Its `flags` parameter is already the options object FR-012 prescribes; the walker raises the tag on the textual `boolean` inside that object type, so no signature keeping the flags can clear it, and T052 forbids a new type. No `# noqa` was added: the tag is walker-derived and ruff does not lint TypeScript.
- **Inventory**: `apps/migration-ui` 198 rows (was 216 — 21 deleted, offset by `CompactStepPipelineBar`, `dryRunOverrideReason` and GAP-009's newer exports), 197 `clean` and 1 `exception`. Zero `missing_docstring`, `untyped`, `unused_param`, `dead`, `gt5_params` and `stale_docstring`. Seven rows carry `bool_data`, the tag a rejected `bool_flag` leaves behind — a resolution rather than an unmet rule, the same situation increment 5 flagged for the SC-001 one-liner in `quickstart.md` section 5, which counts any tag on a non-`exception` row. `--pending --package apps/migration-ui` exits 0.
- **Defect found in the tooling, needs a decision before the SC-001 gate**: `function_inventory.py::apply_decisions` builds its live-hash set from the freshly walked **Python** rows only, and drops every decision whose id is not in it. TypeScript decisions are therefore deleted from `tag-decisions.json` by *any* subsequent `function_inventory.py` run that regenerates — including one restricted with `--package apps/migration-ui`, which walks no Python at all. The eleven decisions in this commit were written directly into `tag-decisions.json` for that reason, and `node scripts/function_inventory_ts.mjs` then applied them (its own `applyDecisions` reads the file and never rewrites it). `--pending` is safe, since it returns before generating. Anyone running the Python generator after this commit must re-apply the TypeScript decisions, or the eight `bool_flag` proposals will silently reappear.
- **Not changed, for later increments**: `TypewriterMessage` in `AgentChat.tsx` is now a pass-through wrapper around `MarkdownMessage` that ignores two of its three props — a deletion candidate, but removing it changes call sites and it is not a tagged row. The `bool_data` rows named above are left as they are, pending the operator's call on how SC-001 should count that tag.
- **Concurrent work**: increment 7's `git mv ado2gh/core/migration_fr036.py` to `conflict_detection.py` sat staged in the shared index for part of this increment; both commits here are path-limited, so it was never swept in. `apps/migration-ui/src/__tests__/` is untracked and belongs to T015/T073 — it is deliberately not in this commit. The console diff also carries this session's earlier GAP-009 and GAP-012 edits in `lib/llmSettings.ts`, `lib/api.ts`, `lib/pipelineRunStatus.ts` and `app/settings/migrate/page.tsx`, which were already in the working tree when this increment started.

## 2026-09-09 — 013 Increment 9: `ado2gh/api/`

Phase 6 (US2) increment 9, run per the per-increment protocol in `specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `baee79d`.

The review agent (`cavecrew-reviewer`) decided the package's 157 proposals in two rounds. The first was the tracked pre-check in `review-precheck.md` (committed `79b5673`), which covered all 157 — 13 CONFIRM, 143 REJECT, 1 ESCALATE. Only 53 of those verdicts could be applied straight: the 43 `module_name_review` ids (whose `state_hash` is `sha1(<module id>)` and therefore stable) and 10 function rows whose hash survived the cleanup. Writing a docstring changes a row's `state_hash`, so the docstring pass invalidated the rest by construction; 15 rows disappeared entirely (their target was renamed, split, or the tagged boolean was removed) and 89 went back to the reviewer in three batches with the verbatim handoff instruction from `contracts/artifact-schemas.md`. **The second round REJECTed all 89 — zero CONFIRM, zero ESCALATE, and no reversal of any first-round CONFIRM.** Every one of the 12 confirmed function-level proposals was remediated in this increment, so none of them survived to be re-asked.

Two rows were decided by the **operator**, not the reviewer, and are recorded in `tag-decisions.json` with `decided_by: operator`:

- `ado2gh/api/migration_work_plan.py::_scope_status:bool_flag` — the pre-check's single ESCALATE. **REJECT, resolution `bool_data`**: `enabled` is a data input describing whether the scope is switched on in configuration, not a behaviour-mode toggle on this function. No code change.
- `ado2gh/api/:module_name_review` — the reviewer CONFIRMed renaming the whole package ("api" is overly generic). **Operator override: REJECT, keep `ado2gh/api`.** T078 (layering) and T079 (route code out of `api/`) restructure this package in Phase 8; a rename now would collide with both; the name is load-bearing in every doc; deferred to a follow-up spec. No exception-register row.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/api/llm/http_llm.py` | (same) | `bool_flag` → two functions | Confirmed `bool_flag` (FR-012): `build_llm_http_client(*, for_cloud, profile, timeout)` split into `build_cloud_llm_http_client(*, profile=None, timeout=DEFAULT_TIMEOUT)` (egress proxy + custom CA) and `build_local_llm_http_client(*, timeout=DEFAULT_TIMEOUT)` (bare client; `profile` dropped because the old code ignored it on that branch). 9 of the 10 call sites passed a hard-coded literal. Callers updated in `ado2gh/api/llm/__init__.py` (import + `__all__`), `ado2gh/api/llm/model_catalog.py` (5), `ado2gh/api/llm/model_validation.py` (4), `services/accelerator_api/routes/settings_routes.py`, and the six test files that patch it by module-path string (`tests/contract/test_llm_catalog_contracts.py`, `tests/core/test_local_hosts.py`, `tests/feature/test_llm_quickstart_scenarios.py`, `tests/llm/test_http_llm.py`, `tests/llm/test_model_catalog.py`, `tests/llm/test_model_validation.py`) | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/llm/llm_provider_registry.py` | (same) | `bool_flag` → param dropped | Confirmed `bool_flag`: `list_provider_specs(*, include_internal=False)` → `list_provider_specs()`. Nothing in the repo passed `include_internal=True`, so the flag was dropped rather than duplicated into a second function with no callers; the `offline` alias is still filtered and the reason is in the docstring. Name unchanged (its `name_review` was REJECTed) | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/llm/model_validation.py` | (same) | `bool_flag` → two functions, `gt5_params` resolved | Confirmed `bool_flag`: `_validate_openai_compatible(api_key, model_id, *, base_url, extra_headers, auth_style, completions_path, azure_api_version, for_cloud)` (8 params) split by intent into `_validate_cloud_openai_compatible(api_key, model_id, *, spec: LLMProviderSpec, base_url="")` and `_validate_local_openai_compatible(api_key, model_id, *, base_url)` — 4 params each, so the `gt5_params` row cleared at the same time. The four transport parameters now come off the existing `LLMProviderSpec` the callers already held; no new type. Shared probe body extracted to `_probe_openai_chat`. `_validate_openai`, a 3-line wrapper hard-coding the OpenAI base URL, was folded into the `openai` dispatch branch, which now uses the same `if not spec:` guard as its four siblings (behaviour bit-identical: the openai spec's `default_base_url`, `auth_style` and `completions_path` match the old constants and `runtime_headers()` is `{}` for both openai and azure) | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/llm/model_validation.py` | (same) | `unused_param` dropped | `validate_draft(body, *, store=None)` → `validate_draft(body)`. No caller passed `store`. Not an FR-011 shared signature: `validate_saved` genuinely uses its `store` and the two are reached from separate routes with no common dispatch | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/migration_scan.py` | (same) | `bool_flag` → two functions | Confirmed `bool_flag`: `persist_scan_results(profile_id, results, *, preserve_manual_assignments=True)` split into `persist_scan_results(profile_id, results)` (state-DB `save_profile_scan`) and `replace_scan_results(profile_id, results)` (`replace_profile_scan`), sharing `_write_scan_backup`. Callers updated in `ado2gh/api/profile_discovery.py`, `ado2gh/api/settings_scan.py`, `services/accelerator_api/routes/profile_routes.py`, `tests/core/test_discovery_inventory_persistence.py`; the `*args` passthroughs in `services/accelerator_api/routes/_shared.py` forward unchanged | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/migration_scan.py` | (same) | `bool_flag` → two functions, `gt5_params` resolved | Confirmed `bool_flag`: `scan_with_credentials(url, pat, gh_org, max_repos, phase_definitions, *, db_path, run_inventory=True, pipeline_parallel)` (8 params) split into `scan_with_credentials(url, pat, gh_org, max_repos, phase_definitions)` and `attach_pipeline_inventory(raw, url, pat, db_path, *, pipeline_parallel)` — 5 params each, clearing `gt5_params` without grouping into a model. The name `scan_with_credentials` is unchanged, so `services/accelerator_api/main.py`'s re-export and the eight tests in `tests/profile/` that patch `services.accelerator_api.main.scan_with_credentials` needed no edit. `ado2gh/api/settings_scan.py::_execute_profile_scan` now calls the second half under an `if adv.db_path:` guard, preserving the old `run_inventory and db_path` gate | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/migration_status_report.py` | (same) | renamed function | Confirmed `name_review`: `_pipeline_run_repo_outcomes` → `extract_outcomes_from_pipeline_runs`. All nouns with no action verb on a function that extracts per-repo outcomes from run rows | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/migration_work_plan.py` | (same) | renamed function | Confirmed `name_review`: `executable_work_items` → `filter_executable_work_items`, since it filters work items by status. Callers updated in `ado2gh/agents/migration_agent/nodes/executor/node.py` (4 sites), `ado2gh/agents/migration_agent/nodes/executor/plan.py`, `services/agent/routes/form_routes.py`, `tests/core/test_migration_work_plan.py` | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/migration_work_plan.py` | (same) | `bool_flag` → `ExecutionMode` | Confirmed `bool_flag` (CA-001): `plan_narrative_from_work_items(..., *, dry_run: bool, ...)` → `mode: ExecutionMode`, no default because the old parameter had none. Boundary conversions with `ExecutionMode.from_dry_run(dry_run=...)` at `ado2gh/agents/migration_agent/nodes/executor/plan.py` and `services/agent/routes/form_routes.py` (session/DB boolean). The narrative's `include_blocked` boolean was left alone — the reviewer REJECTed it in round two as a rendering filter over one output shape. `sync_work_item_wire_keys` (GAP-015) is untouched at the producer and both blocker-mutation sites | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/platform_rbac.py` | (same) | `bool_flag` → `ExecutionMode` | Confirmed `bool_flag` (CA-001): `operator_requires_live_approval(user, dry_run: bool)` → `(user, mode: ExecutionMode)`; the body is now `if mode is ExecutionMode.DRY_RUN: return False` and the `permissions_for(role)["can_approve_live_execution"]` capability check and the 401-on-anonymous-live path are byte-identical. Every caller reads a boolean off an HTTP body or a stored row and converts at the boundary: `services/accelerator_api/main.py`, `services/accelerator_api/routes/migrate_guard.py`, `services/accelerator_api/routes/pipeline_routes.py` (×2, including the GAP-004 approval branch), `tests/feature/test_agent_pev_coverage_boost.py`. `require_approve_live_execution` still has no `auth_enabled()` short-circuit and the other guards keep theirs (GAP-002/005); no test under `tests/auth/` was edited | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/run_reporting.py` | (same) | renamed functions ×2 | Confirmed `name_review` ×2: `validation_message` → `build_validation_message` and `_scope_detail_message` → `build_scope_detail_message`, both message builders. Applied per call site, not by find-and-replace, because `validation_message` is also a persisted **field** on `LLMModelConfig` in `ado2gh/api/llm/llm_model_store.py` and must not move. Callers updated in `ado2gh/api/pipeline_steps.py` and `tests/core/test_run_reporting.py` | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/settings_store.py` | (same) | `bool_flag` → two methods | Confirmed `bool_flag`: `SettingsStore.update_phases(phases_raw, *, removals, span_to_scan: bool, profile_id)` split into `update_phases(...)` (saves the bands as supplied) and `update_phases_spanning_scan(...)` (widens them over the profile's scanned risk range first), over a shared private `_apply_phase_update`. The validate → migrate-repos-off-removed-phases → persist → rescan body is unchanged, so the background rescan still triggers on exactly the same condition. `services/accelerator_api/routes/settings_routes.py` picks the method from `req.span_to_scan`; the wire field `PhasesUpdateRequest.span_to_scan` is untouched | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/agentic_routes.py` | (same) | `gt5_params` ×3 resolved via an existing model | `history_sessions` (9 params → 4) and `export_history_sessions` (8 → 3) now take `filters: AuditEventFilters = Depends()`, the existing state-layer dataclass, which FastAPI expands into the same query parameters. `_audit_search_filters` was deleted rather than moved — the dependency replaces it, so its own `gt5_params` row went with it. The query-string contract was verified unchanged by generating the OpenAPI schema: `/v1/history/sessions` still declares actor, date_from, date_to, event_type, limit, offset, profile_id, search, and `/v1/history/sessions/export` the same minus offset, all optional. The dropped `or None` normalisation was redundant (`build_audit_filters` tests truthiness) and `actor` still passes through `resolve_audit_actor_filter`, so RBAC scoping is unchanged. Handler docstrings rewritten as consumer-facing OpenAPI descriptions (FR-010a). Not moved out of the package — that is T079 | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/live_approval_store.py` | (same) | `gt5_params` resolved via an existing model | `LiveApprovalStore.create_or_get_pending(requester, scope_type, scope_id, *, profile_id, reason_request, context)` → `(requester: PlatformUser, request: LiveApprovalCreateRequest)`. `LiveApprovalCreateRequest` in `ado2gh/api/contracts.py` already carries exactly those five fields and was not modified; `requester` stays a separate server-side argument on purpose. `services/accelerator_api/routes/pipeline_routes.py::create_live_approval` collapses to `store.create_or_get_pending(user, req)` since it already held the model. `_notify_agent`'s GAP guarantees are intact: non-2xx responses and transport failures still produce an ERROR log plus a `platform.live_execution.notify_failed` audit event with the approver as actor, and only `resp.status_code` and `type(exc).__name__` reach the message (CA-003) | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/accelerator.py` | (same) | unused params dropped (4) | `Accelerator.validate(request, ado_url, ado_pat, gh_token, gh_org)` → `validate(request)`; its only caller, `ado2gh/core/orchestration/worker.py`, passed the request alone. `_build_gh_client(global_cfg, gh_token, gh_org)` → `(global_cfg, gh_token)`: `GHClient.__init__` takes a token manager and a base URL and has never accepted an organisation, so `gh_org` was vestigial rather than a masked defect. Dropping it cascaded to the public SDK methods `Accelerator.run_wave` and `Accelerator.run_phase`, which only forwarded it, and to `ado2gh/api/validation_run.py` and `services/accelerator_api/main.py`; `services/accelerator_api/routes/migrate_routes.py` stopped forwarding it too and still computes and returns `gh_org` for its own callers. `run_phase`'s GAP-009 force path (a blocking gate needs `override_reason` and calls `checker.override(phase, reason)`) is unchanged and now documented | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/credentials/cloud_credentials_store.py` | (same) | unused param dropped | `CloudCredentialsStore.revoke(provider, *, actor)` → `revoke(provider)`. `CloudCredentialSource` has `approved_by` and `rejected_by` but no revoker field, so `actor` was accepted and discarded. The action is still audited: `services/accelerator_api/routes/settings_routes.py::revoke_cloud_credentials` writes `cloud_credentials.revoked` through `write_profile_audit(..., actor=actor)`. Dropping it costs no exception row. Callers updated in that route, `tests/agent/test_agent_models.py` and `tests/cloud_credentials/test_cloud_credentials_store.py`. **Follow-up for the operator, not fixed here**: adding a `revoked_by` field so the persisted record keeps its revoker the way approval and rejection do would change `to_public()`, a wire shape | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/profile_discovery.py` | (same) | `gt5_params` resolved by dropping params | `build_wave_from_profile_phase` 7 params → 5: `parallel: int = 4` and `pipeline_parallel: int = 8` were pure pass-throughs into `WaveConfig`, whose defaults are already 4 and 8, so behaviour is identical for every caller and concurrency became the runner's concern. The one caller that passed them, `ado2gh/api/pipeline_steps.py`, now sets them on the returned wave | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/profile_discovery.py` | (same) | `Any` kwargs made explicit | `require_gh_org(profile, **kwargs: Any)` (ANN401) now names the four keyword-only parameters it was forwarding to `resolve_gh_org` — `global_cfg`, `config_path`, `scan`, `profile_id` — for 5 arguments total. All three call sites already used those keywords | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/pipeline_store.py` | (same) | keyword-only args; exception row | `PipelineRunStore.create`'s leading arguments became keyword-only, which clears FBT001 on `dry_run` without renaming or splitting a boolean the reviewer REJECTed as data. One caller updated (`services/accelerator_api/routes/pipeline_routes.py`); the eight test call sites already used keywords. `PLR0913` (11 > 5) remains and takes the increment's single exception-register row | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/pipeline_steps.py` | (same) | shared step signature documented (FR-011) | All seven step handlers keep one identical signature, `def _step_<name>(self, run: PipelineRun) -> None`. Nothing changed; the uniformity is now stated in the `PipelineStepsMixin` docstring with the reason — `PipelineRunner._execute` dispatches through a single `dict[str, Callable[[PipelineRun], None]]` table, so a divergent step could not be dispatched at all, and the scoped steps are bound as lambdas over `_migrate_scoped` to stay conformant. `_migrate_scoped` still redacts `override_reason` via `redact_payload` before building `PhaseRunRequest`, and both existing `ExecutionMode.from_dry_run` boundary conversions are untouched | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/pipeline_steps.py` | (same) | unused param renamed | The nested `on_repo_done(key, res, repo_cfg)` callback's third argument is fixed by `BatchExecutor.execute_wave`, which declares `Callable[[str, dict, RepoConfig], None]` and calls it positionally, so it cannot be dropped. Renamed to `_repo_cfg` per the standard dummy-argument convention, clearing ARG001 without spending an exception row — the same remedy increment 13 used for a framework-injected `request` | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/phase_definitions.py` | (same) | dead functions deleted ×2 | `phase_rationale` and `slugify_phase_id`, both 0 references. A repo-wide grep across `ado2gh/`, `services/`, `tests/`, `apps/migration-ui/src` and every `.py/.md/.ts/.tsx/.json` found exactly one hit each — its own definition. Pre-deletion rows are at `inventory.json@baee79d` (FR-029). Neither had an exclusive test | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/pipeline_steps.py` | (same) | dead function deleted | `PipelineStepsMixin._sc_note`, 0 references, verified by the same repo-wide grep. Pre-deletion row at `inventory.json@baee79d` (FR-029). No exclusive test | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/settings_profiles.py` | (same) | dead function deleted | `ProfileMixin.get_active_profiles`, 0 references; the only other mentions in the repo are historical task text in `specs/archive/005-profile-onboarding/tasks.md`. Pre-deletion row at `inventory.json@baee79d` (FR-029). No exclusive test | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/contracts.py` | (same) | class docstrings | All 58 pydantic request/response models and the two enums gained a class docstring (D101), written as OpenAPI-facing descriptions per FR-010a: each names the endpoint or SDK operation it belongs to and what it makes happen. 217 insertions, **zero deletions** — no field, validator, default, `model_config`, import or class ordering moved. `PipelineRunStartRequest` keeps `extra="forbid"` and `override_reason` (GAP-004/009), `PhaseRunRequest.override_reason` is intact, and `PipelineRunStartApprovedRequest` stays deleted. Live-execution and destructive-action models say so plainly; `dry_run`'s default-false on the wire models versus the console's default-true is called out; secret-bearing fields say they are masked in responses and logs and show no value, real or fake (CA-003) | yes | pass (910) | 2026-09-09 |
| `ado2gh/api/**` | (same) | docstrings + annotations | Google-style docstrings written across the package: every one of the 141 inventoried functions carrying a `missing_docstring` tag at `inventory.json@baee79d` now has one, along with the private helpers the scanner exempts and the module and dataclass docstrings ruff's `D1` set flags. Roughly 35 missing or `Any` annotations were replaced with concrete types (`PlatformUser`, `StateStore`, `SettingsStore`, `MigrationProfile`, `RepoConfig`, `ADOClient`, `LLMProviderSpec`, `Path`, `type[Exception]`), several imported under `TYPE_CHECKING` so no runtime import or cycle was added. Every non-void function carries a `Returns:` describing the value, and private helpers are documented as well as public ones. `ado2gh/api/audit_access.py`'s scoped RBAC exclusion is documented as deliberate, with a warning against hardening it, rather than "fixed". CA-003 held throughout `ado2gh/api/llm/` and `ado2gh/api/credentials/`: docstrings name credential *fields* and describe masking and truncation behaviour, and no key, token, PAT, endpoint secret or plausible-looking value appears in any docstring, default or example | yes | pass (910) | 2026-09-09 |

- **Tests removed**: 0. Measured on this increment: 910 passed, 30 skipped, 0 failed, coverage TOTAL 59 %, in the shared working tree.
- **Production functions deleted**: 4 dead rows (`phase_definitions.py::phase_rationale`, `phase_definitions.py::slugify_phase_id`, `pipeline_steps.py::PipelineStepsMixin._sc_note`, `settings_profiles.py::ProfileMixin.get_active_profiles`); their pre-deletion rows are at `inventory.json@baee79d` (FR-029). None had exclusive tests, so no test file was deleted. Two further functions disappeared as part of a mandated remediation rather than as dead code: `agentic_routes.py::_audit_search_filters` (superseded by the `AuditEventFilters` dependency) and `llm/model_validation.py::_validate_openai` (folded into the dispatch branch during the confirmed split).
- **`dry_run` conversions**: two, both confirmed `bool_flag` rows — `migration_work_plan.py::plan_narrative_from_work_items` and `platform_rbac.py::operator_requires_live_approval`, each taking `mode: ExecutionMode` with no default because neither old parameter had one. Every external shape stayed boolean and converts at the boundary with `ExecutionMode.from_dry_run(dry_run=...)`: HTTP bodies in `services/accelerator_api/main.py`, `routes/migrate_guard.py` and `routes/pipeline_routes.py`, the stored `run.dry_run` column, and the agent session dict in `services/agent/routes/form_routes.py`. The pre-existing conversions in `ado2gh/api/accelerator.py` and `ado2gh/api/pipeline_steps.py` were left as they were.
- **Shared `pipeline_steps` step signature (FR-011)**: `def _step_<name>(self, run: PipelineRun) -> None`, unchanged and now documented. No module split — `pipeline_steps.py`'s `module_name_review` was REJECTed by the reviewer, and the file remains in `tests/unit/test_file_size_limit.py`'s `US2_ADDRESSED` allowlist, which was not edited.
- **Exception register**: one row added (21 of 29), `ado2gh/api/pipeline_store.py::PipelineRunStore.create` with `# noqa: PLR0913`. Three other candidates were resolved instead of spent: `_build_gh_client`'s `gh_org` and `revoke`'s `actor` were dropped with their callers updated, and `on_repo_done`'s framework-fixed `repo_cfg` became `_repo_cfg`.
- **Coverage ratchet**: `--cov-fail-under` stays **59** in `.github/workflows/ci.yml` (measured TOTAL 59 %, equal to the current gate, not higher — FR-027a/SC-004).
- **File sizes**: two files crossed the 800-line cap mid-increment under the weight of new docstrings and were brought back by tightening prose only, with no executable line changed and no allowlist or cap edit — `llm/model_validation.py` 877 → 788 and `contracts.py` 966 → 716. `pipeline_steps.py` is 1257 lines and stays exempt via `US2_ADDRESSED`; the next largest are `settings_store.py` at 710 and `contracts.py` at 716.
- **Inventory**: `ado2gh/api` is 327 rows (331 before the four dead ones went, plus the rows added by the confirmed splits), 326 `disposition: clean` and 1 `exception`. Two of the clean rows carry a `bool_data` tag with no exception — `migration_work_plan.py::_scope_status` (the operator's ESCALATE resolution) and `pipeline_models.py::PipelineRun`'s stored flags — the same accounting increments 5 and 7 recorded. `--pending --package ado2gh/api` exits 0. The T002 ruff set with `max-args=5` reports nothing for the package; the default `ruff check ado2gh/ services/` reports only the five findings that pre-date this increment in `ado2gh/agents/` (`nodes/executor/node.py` E402 ×3, `migration_agent/utils.py` F401 + F811, owned by increment 10). The public-surface snapshot is unchanged, the orphan guard passes and the 800-line guard is green.
- **Not changed, for later increments**: `agentic_routes.py` stays inside the package — moving HTTP route code out is T079. The `api → cli` import edge (GAP-021) was neither deepened nor fixed; that is T078. `LLMProviderSpec.for_cloud` became provably dead state once the client-factory split removed its only consumer — it is a dataclass field rather than an inventory row, so it was left in place for the follow-up that owns wire shapes. `services/accelerator_api` still reports 254 findings under the T002 set; that is increment 12.
- **Concurrent work**: increment 14 (`apps/migration-ui/`) committed as `50fd533` and the inventory-tool fix `baee79d` landed before any regeneration here, so no TypeScript decision was lost — `tag-decisions.json` kept all 11 of them and the row counts for `apps/migration-ui` (198) and `services/agent` (40) were re-checked before and after every regeneration and never moved.
- **Known stale reference, deliberately not edited**: `tests/pipeline/test_gap_004_client_self_certified_live_approval.py`'s module docstring quotes the source line `if operator_requires_live_approval(user, dry)`, which now reads `... (user, ExecutionMode.from_dry_run(dry_run=dry))`. The test is on the untouchable list, it passes, and only its prose is out of date.

## 2026-09-09 — 013 Increment 11: `ado2gh/cli`

Phase 6 (US2) increment 11, run per the per-increment protocol in
`specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `d11d25e`.

**Run alongside increments 10 and 12**, at the operator's request, and safe because the
package is almost a leaf: nothing in `ado2gh/` or `services/` imports `ado2gh/cli` except
`ado2gh/__main__.py` and the one `api → cli` edge GAP-021 records —
`from ado2gh.cli.helpers import load_repos` in `ado2gh/api/validation_run.py:135,146` and
`ado2gh/api/pipeline_steps.py:1158`. That edge was neither deepened nor removed here; T078
owns it. `load_repos` kept its runtime signature exactly (only its annotations were
corrected), so the importers are unaffected.

The review agent (`cavecrew-reviewer`) decided the package's nine proposals: one CONFIRM,
eight REJECT, no ESCALATE, all matching `review-precheck.md` batch B10 by `state_hash`.
The single CONFIRM was the `module_name_review` on `ado2gh/cli/run_cmd.py`, and it was
executed — see the table. Two rows had to be re-decided rather than reused. Executing the
move created a fresh, undecided `module_name_review` on the new path
`ado2gh/cli/migration.py`; the reviewer rejected it, on the grounds that the name now
matches the seven commands the module registers. And `ado2gh/cli/main.py::cli` changed from
`def cli():` to `def cli() -> None:`, so its `state_hash` no longer matched the pre-check
and the `name_review` verdict was re-taken — REJECT again, unchanged rationale: `cli` is the
Click group callback name and is framework-fixed. Nine decisions are recorded in
`tag-decisions.json`. The tenth, the `run_cmd.py` CONFIRM, deliberately is not: the
generator's `apply_decisions` prunes any decision whose target no longer exists in a package
it walked, so a decision against a module that the confirmed move has just deleted cannot
survive the next regeneration. The same thing happened to `ado2gh/assignments/` in increment
1 and to `agent.ts::patchAgentExecutionMode` in increment 14; this section is the record.

No function was deleted — every row has callers — so there is no FR-029 deletion pointer,
only the move below. The package had no `mutable_default` and no inconsistent return. Its
`--dry-run` flags are untouched and still `is_flag=True`; the four `ExecutionMode.from_dry_run`
conversions that increments 5 and 7 had already placed at the Click boundary
(`ado-cleanup`, `push-workflows`, `pipelines retry-failed`, `rollback`) were left exactly as
they were and none was added or doubled. `run` and `phase run` still forward a plain
`dry_run` bool into `RunWaveRequest` / `PhaseRunRequest`, which is what the boundary contract
requires.

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `ado2gh/cli/run_cmd.py` | `ado2gh/cli/migration.py` | moved (renamed) | FR-013: confirmed `module_name_review`. "run_cmd" named only the `run` command while the module also owns `status`, `report`, `rollback`, `export-failed`, `validate` and `token-status`. Module docstring rewritten to state the full responsibility. Only `ado2gh/cli/main.py` needed updating (`register_run` → `register_migration`); no test, no doc, no orphan-guard entry and no `pyproject.toml` entry referenced the old path. Pre-increment inventory: `inventory.json@d11d25e` | yes | see note | 2026-09-09 |
| `ado2gh/cli/*.py` | (same) | annotations + docstrings | Every function annotated and documented: 118 ruff findings across the T002 set (`D1`, `ANN`, `PLR0913`, `ARG`) reduced to zero. Google-style docstrings with real `Returns:`/`Raises:` on the non-Click helpers in `helpers.py`; `TYPE_CHECKING` block there so the deliberate lazy client imports stay lazy | yes | see note | 2026-09-09 |
| `ado2gh/cli/misc.py`, `phase.py`, `pipelines.py`, `migration.py` | (same) | boolean parameters made keyword-only | Annotating the flags turned them into boolean positional arguments and raised `FBT001` on `dry_run`, `force`, `archive` and `override`. Click invokes a callback as `callback(**ctx.params)`, so making them keyword-only clears the rule with no behaviour change, no `# noqa` and no exception row. The `@click.option` declarations are untouched | yes | see note | 2026-09-09 |
| `ado2gh/cli/migration.py`, `phase.py`, `pipelines.py` | (same) | unused parameter dropped via `expose_value=False` | `report`, `phase dashboard` and `pipelines status` each declared `--config` as required and never read it (`ARG001` ×3). The option is frozen, so it stays; Click's own `expose_value=False` keeps it on the command line and drops it from the callback. Measured byte-identical afterwards: the `cli_commands` snapshot line, the `--help` text and the "Missing option '--config'" error | yes | see note | 2026-09-09 |
| `ado2gh/cli/misc.py` | (same) | `gt5_params` exception | `push_workflows` takes the six frozen `push-workflows` options. `# noqa: PLR0913` with a reason, and one row added to `exception-register.md` (21 → 22, cap 29 at `totals.functions` 1483). `RUF100` confirms the suppression is live, not decorative | yes | see note | 2026-09-09 |

- **`--help` changed on 22 of 24 command pages, all additively.** FR-010a puts the parameter
  documentation in the option `help=` strings rather than in the rendered docstring, so nearly
  every option gained one, and the nine commands that had no docstring at all gained a help
  body: `pipelines inventory`, `pipelines status`, `pipelines retry-failed`, `phase assign`,
  `phase plan`, `phase dashboard`, `pipeline-readiness`, `service-connections`, `ado-cleanup`.
  The three group pages (`ado2gh`, `ado2gh pipelines`, `ado2gh phase`) changed only because
  those new short-helps now appear in their command lists. **`ado2gh phase run` and
  `ado2gh phase gate-check` are byte-identical** — both already carried full docstrings and
  option help from the GAP-009/GAP-017 work, and were touched only on their `def` lines.
  Every existing docstring was preserved verbatim. No test in the repository asserts `--help`
  output; verified by grep over `tests/` before and after.
- **Frozen surface intact.** `tests/contract/test_public_surface_snapshot.py` passes unchanged:
  no option was added, removed, renamed, retyped, re-defaulted, and no `is_flag` or `multiple`
  changed. `expose_value` and `help` are not among the seven fields the collector records.
- **Inventory**: `ado2gh/cli` 10 rows, all tag-free — nine `clean`, one `exception`
  (`misc.py::register`, carrying the nested `push_workflows` finding).
  `--pending --package ado2gh/cli` exits 0.

## 2026-09-12 — 013 Increment 10: `ado2gh/agents/`

Phase 6 (US2) increment 10, run per the per-increment protocol in `specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `d11d25e`.

The package's 224 proposals (165 function/export, 59 module) came from the tracked pre-check in `review-precheck.md` — 15 CONFIRM, 206 REJECT, 3 ESCALATE. **Every one of the 224 `state_hash` values still matched after regeneration**, so all 224 verdicts applied directly with no first-round re-ask; all 59 `module_name_review` proposals were REJECT, so no module was renamed, moved or split. The docstring and signature work then invalidated 80 hashes, which went back to `caveman:cavecrew-reviewer` in one batch with the verbatim handoff instruction from `contracts/artifact-schemas.md`. **The second round returned 77 REJECT and 3 CONFIRM**, all three of the latter `stale_docstring` rows raised by the increment's own new `Args:` blocks and fixed in the same pass. One reversal: `guardrails.py::evaluate_guardrail:bool_flag`, CONFIRM in round one, REJECT in round two — see below.

The three pre-check ESCALATEs were resolved by the **operator** on 2026-09-08 and were recorded in `tag-decisions.json` with `decided_by: operator`. Note where the durable record now lives: the generator prunes a decision once its proposal stops firing, so the two that were remediated away — `enforce_live_mode_request` (the boolean became an enum) and `can_access_agent_session` (the function was deleted) — are no longer rows in that file, and the third was superseded by the round-two reviewer. This section and `review-precheck.md` are therefore the record of all three:

- `policies.py::enforce_live_mode_request:bool_flag` — **CONFIRM**, remediated: `dry_run: bool` → `mode: ExecutionMode`, converted at the two HTTP boundaries in `services/agent/routes/execution_routes.py` (`confirm-live` passes `ExecutionMode.LIVE`, `execution-mode` passes `ExecutionMode.from_dry_run(dry_run=req.dry_run)`). `tests/agent/test_gap_006_*` and `tests/auth/test_gap_002_*`/`test_gap_005_*` call neither the function nor the routes directly and were not edited.
- `policies.py::can_access_agent_session:bool_flag` — **REJECT with resolution** (`bool_data`): the operator's remedy was keyword-only `write: bool = False`, which the signature already had, so no logic change and no exception row. The function turned out to have **zero references repo-wide** and was deleted as a `dead` row; the recorded decision keeps the reasoning on file.
- `guardrails.py::evaluate_guardrail:bool_flag` — recorded **CONFIRM, `decided_by: operator`** at the start of the increment, with the note that the escalation's premise was wrong: the reviewer described a `dry_run` parameter this function does not have (it reads `session["dry_run"]` internally, unchanged), and the only boolean is `plan_approved`, which the operator ruled is data (approval state on record), not a mode. The round-two reviewer, re-asked after the docstring rewrite, independently reached the same conclusion and returned **REJECT**, so the live row in `tag-decisions.json` is now that reviewer's reject and the function's tag is `bool_data`. The reversal is a convergence on the operator's own reasoning, not a conflict — no code changed either way, and the row keeps its `gt5_params` exception.

The pre-check's suspected generator false positive, `tools/orchestrator_tools.py::get_orchestrator_tools:bool_flag`, was **real but misattributed**: `get_orchestrator_tools` itself has no boolean parameter, and the walker attributes tags from nested definitions to the enclosing row (FR-001a) — the booleans are the `dry_run` arguments of the nested `invoke_planner` / `invoke_bulk_planner` tool functions. REJECT stands.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/agents/migration_agent/runtime/orchestrator.py` | (same) | `gt5_params` x7 resolved via an existing model | All seven over-long signatures shared the keyword tail `model_id, accel_get, accel_post, build_plan, session_token`. `model_id` was **dropped**: every one of the six production call sites passed exactly `session.get("selected_model_id")` and `session` is already the first argument, so it is derived inside `_bind_runtime_deps`. The remaining four were grouped into a single `deps` mapping keyed by the names `runtime/deps.py` already declares in `_RUNTIME_KEYS` — the existing per-invocation dependency concept that `graph/state.py`'s `AgentState` docstring explicitly points at. No new type. Final counts: `process_user_message`, `stream_user_message`, `run_turn`, `stream_turn`, `_build_initial_state`, `resume_interrupted_graph`, `stream_interrupted_graph` = 3; `continue_session_graph`, `continue_session_graph_stream` = 4. Callers updated in `services/agent/routes/form_routes.py` (2), `message_routes.py` (2), `session_routes.py`, `route_helpers.py`, `tests/unit/test_streaming.py`. Zero exception rows | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/session/lifecycle.py` | (same) | `gt5_params` resolved by dropping and splitting | `new_isolated_agent_session` 10 params to 5. `assignment_id` dropped (no caller passed it, nothing reads `session["assignment_id"]`); `user_display_name` dropped (its only caller passed `platform_user.display_name` and then immediately called `attach_actor_to_session`, which overwrites it from the same object); both keys still exist on the session as `None`. `selected_model_id`/`llm_degraded`/`llm_unconfigured` moved to a sibling `apply_model_selection(session, *, selected_model_id, llm_degraded=False, llm_unconfigured=False)` — the exact 3-tuple `route_helpers._resolve_model_id` returns, keeping the `llm_available = not llm_unconfigured` derivation in one place. Defaults reproduce the old constructor exactly | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/session/store.py` | (same) | `gt5_params` x2 resolved | `update_session` 6 to 2 via a `_SESSION_PATCH_COLUMNS` map and `**fields: object` (None values and unknown names ignored, as before); `add_message` 6 to 5 by dropping `correlation_ids`, which no caller passed — `correlation_json` is still written `"{}"`. Five identical `SELECT ... fetchall()` getters were routed through a new `_rows()` helper to make room for the docstrings under the 800-line cap. `_messages_json()`'s two GAP-011 masking sites are untouched | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/tools/*.py` | (same) | `gt5_params` + `unused_param` resolved via the existing deps mapping | `get_executor_tools` 7 to 3 (`accel_request` dropped — no caller passed it and it was always rebuilt internally; `accel_get`/`accel_post`/`session_token`/`build_plan` grouped into `deps`, matching `get_runtime_deps()`; `session_getter`/`log_decision` stay explicit as guardrail hooks), `get_orchestrator_tools` 5 to 3 and `get_planner_tools` 4 to 3 by dropping genuinely unused injected callables. **No tool name and no tool parameter name changed**, so `prompts/*.md` (`github_api` 12x, `ado_api_query` 10x, `call_accelerator` 7x), `tests/contract/test_agent_*` and `apps/migration-ui/src` needed no edits. Tool docstrings were rewritten as consumer-facing descriptions with no credential-shaped literals (CA-003) | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/policies.py` | (same) | `bool_flag` to `ExecutionMode` | `enforce_live_mode_request(request, dry_run: bool, session=None)` becomes `(request, mode: ExecutionMode, session=None)`; the body is now `if mode is ExecutionMode.DRY_RUN: return`, and the 401-on-anonymous-live and 403-requires-approval paths are byte-identical. `resolve_execution_dry_run`'s GAP-006 contract, and the absence of an `auth_enabled()` short-circuit in `can_execute_live_without_approval` / `session_requires_live_approval` (GAP-002/005), are unchanged and now documented | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/nodes/executor/` | (same) | `bool_flag` to `ExecutionMode` x4 | The four confirmed rows: `node.py::_execute_deterministic_repo_scopes` (mode derived from `session["dry_run"]`, which `executor_node` resolves and writes before any repo runs), `pipeline.py::ensure_agent_pipeline_run`, `scope.py::_execute_secrets_scope` and `scope.py::execute_migration_scope`. Every external shape stays boolean: the accelerator run body still carries `"dry_run": <bool>` and nothing new was added to it (GAP-004 / `PipelineRunStartRequest(extra="forbid")`). The reviewer-REJECTed `dry_run` booleans elsewhere in these files were deliberately left alone, so a file may legitimately carry both a `mode` and a `dry_run` parameter | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/nodes/validator.py`, `planner_research.py` | (same) | `bool_flag` to `ExecutionMode` x4 | `_failure_is_benign`, `_scope_result_is_benign`, `_validate_scope` and `_run_planner_research_loop` take `mode: ExecutionMode`; `_run_planner_research_loop` keeps `ExecutionMode.DRY_RUN` as its default because the old parameter defaulted to `True` (CA-001). `validator_node` converts once at the boundary. `_all_failures_benign`, a pure wrapper over `_failure_is_benign`, was converted with them rather than calling `from_dry_run` per element. `_validate_scope` also lost its `plan` parameter, which every caller passed and no code path read, taking it 4 to 2 plus keyword-only `mode` | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py` | (same) | `bool_flag` x3 to parameter dropped | `_handle_general_chat`, `_handle_migration_info` and `_handle_migration_action` each lost `llm_unconfigured: bool`; all three already receive `state`, and `AgentState.llm_unconfigured` is declared in `graph/state.py`, so the flag is read from the object the caller already passed. Four call sites updated. The three GAP-011 SSE emit sites stay masked | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/hitl/` | (same) | `bool_flag` x3 resolved three ways | `form_fields.py::resolution_options_from_context` split by intent — `probe_failures` removed and a sibling `resolution_options_for_probe_failures` added, sharing `_blocker_summary_text` / `_repository_target_options` / `_with_fallback_options`; the `recommended: not options` pre-extend evaluation is preserved as `had_specific_options`. `intake.py::consult_planner_context` takes `analysis: OperatorMessageAnalysis | None` instead of `requests_new_migration: bool` and derives the flag. `intake_guardrails.py::apply_analysis_guardrails` dropped `from_form` — no caller anywhere passed it, so its early-return branch was dead | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/session/state.py` | (same) | `bool_flag` to two functions | `maybe_reset_for_migration_request(session, repository_id, force)` split into `reset_for_migration_request(session, repository_id)` (unconditional) and `maybe_reset_for_migration_request(session, repository_id)` (conditional, `force` gone). The one `force=True` call site, `nodes/intent.py`, now calls the unconditional form | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py` | (same) | dead-by-inlining | `execute_repo_migration` (10 params) deleted and its six-line accelerator-availability dispatch inlined into `node.py::_run_repo`, its only caller. That also removed the `scope_executor` injection seam nothing used and an unreachable `RuntimeError`. `execute_repo_via_pipeline` then reached 5 parameters by moving `accel_get`, `accel_post`, `session_token` and `on_progress` into the same `deps` mapping | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/utils.py` | (same) | E402/F401/F811 fixed; `gt5_params` partially resolved | The module-level `import re` was unused (F401) only because `_parse_llm_json` re-imported it locally (F811); deleting the local import fixed both with one line. `_append_and_stream` went 6 to 5 by extracting `_stream_entry(entry, *, kind, subagent)` — the single caller that passed `meta`, `_emit_tool_call`, now calls `_append_event` and `_stream_entry` in sequence, the pattern `_emit_tool_result` already used. `mask_secrets` still delegates to `redact_payload` and `_append_event` still masks and returns the entry (GAP-011) | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/nodes/executor/node.py` | (same) | E402 x3 fixed | `logger = logging.getLogger(__name__)` sat above the import block; moved below it. The GAP-006 wiring is untouched — `resolve_execution_dry_run(session, migration_plan)` is still hoisted above the dry-run/live banner and `session["dry_run"] = dry_run` is still written back — as are the two singular `blocker` reads (GAP-015) | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/policies.py` | (same) | dead functions deleted x2 | `can_access_agent_session` (0 references repo-wide, including `prompts/*.md`, `apps/migration-ui/src` and every test) and, as a direct cascade, `session_owner_username`, whose only caller it was. Neither is on `protected-entry-points.json`. Pre-deletion rows are at `inventory.json@d11d25e` (FR-029). Neither had an exclusive test | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/session/state.py`, `utils.py` | (same) | dead functions deleted x2 | `SessionStateMachine.can_transition` (0 references; every transition was permitted, so it was a no-op predicate) and `utils.py::_append_status_message` (0 references), together with the constant `MAX_STATUS_MESSAGES_PER_TURN` that only it read. `_reset_turn_status_budget` was **kept** — it still has two live callers — although with its only consumer gone the counter it clears is now written and never read; flagged rather than removed because removing it would have meant editing two other increments' files mid-flight. Pre-deletion rows at `inventory.json@d11d25e` | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/migration_agent/graph/builder.py` | (same) | framework signature documented, not "fixed" | The LangGraph `(state, config)` node shape is unchanged. `_with_runtime_deps`'s nested `wrapped` never reads the framework's second argument, so it was renamed `_config` — the `_repo_cfg` precedent from increment 9 — which clears `ARG001` without an exception row. Verified against the installed LangGraph rather than assumed: `RunnableCallable` injects the config only when a parameter is literally named `config`, so `_config` keeps its `None` default and behaviour is identical. `_close_checkpointer` was not restructured | yes | pass (941) | 2026-09-12 |
| `ado2gh/agents/**` | (same) | docstrings + annotations | Google-style docstrings across the package: every one of the 72 rows carrying `missing_docstring` at `inventory.json@d11d25e`, plus the private helpers and dataclasses ruff's `D1` set flags. All 86 `untyped` rows annotated, and every bare `Any` parameter or return replaced with a concrete type — `BaseChatModel`, `CompiledStateGraph`, `BaseCheckpointSaver`, `StructuredTool`, `ModelCapabilities`, `LLMModelConfig`, `PlatformUser`, `AuditWriter`, `OperatorInputRequest`, `RepoConfig`, `SQLiteStateDB | PostgresStateDB`, `Callable[..., Awaitable[Any]]`, or `object` where the value is genuinely opaque — most imported under `TYPE_CHECKING`, so no runtime import or cycle was added. Every non-void function carries a `Returns:` describing the value. CA-003 held: no token, PAT, key or plausible-looking credential appears in any docstring, default or example | yes | pass (941) | 2026-09-12 |

- **Tests removed**: 0. Measured on this increment: 941 passed, 30 skipped, 0 failed, 967 collected, coverage TOTAL 60 %, in the shared working tree.
- **Production functions deleted**: 5. Four `dead` rows — `policies.py::can_access_agent_session`, `policies.py::session_owner_username` (dead only once the first went), `session/state.py::SessionStateMachine.can_transition`, `utils.py::_append_status_message` — each verified at 0 references by a repo-wide `git grep` covering `ado2gh/`, `services/`, `tests/`, `prompts/*.md` and `apps/migration-ui/src`, none `protected`, none with an exclusive test. Pre-deletion rows at `inventory.json@d11d25e` (FR-029). A fifth, `nodes/executor/pipeline.py::execute_repo_migration`, disappeared as part of a mandated `gt5_params` remediation rather than as dead code. No `dead` row was kept: none of the four was dynamically dispatched.
- **`dry_run` conversions**: nine confirmed `bool_flag` rows became `mode: ExecutionMode` — `policies.py::enforce_live_mode_request`, `nodes/executor/node.py::_execute_deterministic_repo_scopes`, `nodes/executor/pipeline.py::ensure_agent_pipeline_run`, `nodes/executor/scope.py::_execute_secrets_scope` and `::execute_migration_scope`, `nodes/planner_research.py::_run_planner_research_loop`, `nodes/validator.py::_failure_is_benign`, `::_scope_result_is_benign` and `::_validate_scope` (plus `_all_failures_benign`, converted with its callee). Defaults that were `True` became `ExecutionMode.DRY_RUN`; the rest have no default, as before. Every external shape stays boolean — the HTTP bodies in `services/agent/routes/execution_routes.py`, the accelerator's `dry_run` field, the persisted `agent_sessions.dry_run` column and the `session["dry_run"]` / `plan["dry_run"]` dict keys are all unchanged, converting at the boundary with the keyword-only `ExecutionMode.from_dry_run(dry_run=...)`. The other six `bool_flag` rows were resolved without an enum: three by dropping the parameter, two by deriving it from an argument already passed, one by splitting into two intent-named functions.
- **Exception register**: five rows added (27 of the cap). `guardrails.py::evaluate_guardrail` (7 params, the CA-002 authorization choke point), `nodes/executor/node.py::_execute_scope` (8 params plus a positional boolean, both frozen by a protected test that binds seven arguments positionally), `nodes/executor/scope.py::execute_migration_scope` (7), `utils.py::_append_event` (6, the CA-003 masking choke point) and `utils.py::IdeAuditBridge.record` (7, the CA-004 audit choke point). Eighteen further `gt5_params` and `unused_param` candidates were resolved instead of spent — seven in `runtime/orchestrator.py` alone — by dropping parameters no caller passed, deriving values from arguments already present, and grouping injected callables into the existing `runtime/deps.py` mapping. No new dataclass, TypedDict or Pydantic model was introduced anywhere in the increment.
- **Coverage ratchet**: `--cov-fail-under` raised from 59 to **60** in `.github/workflows/ci.yml` (measured TOTAL 60 %, FR-027a/SC-004). Never lowered.
- **File sizes**: the docstring pass pushed two files over the 800-line cap mid-increment and both were brought back by tightening prose only, with no executable line changed and no allowlist or cap edit — `nodes/orchestrator.py` 806 to 781 and `session/store.py` 805 to 799. The largest files in the package are now `message_format.py` and `session/store.py` at 799 each. No module was split: every `module_name_review` proposal was REJECTed.
- **Inventory**: `ado2gh/agents` is 402 rows (399 before, less four dead and one inlined, plus the rows added by the confirmed splits and the new private helpers), 397 `disposition: clean` and 5 `exception`. Seven of the clean rows carry a `bool_data` tag with no exception — the reviewer's "this boolean is data" resolution, the same accounting increments 5, 7 and 9 recorded. `--pending --package ado2gh/agents` exits 0. The T002 ruff set with `max-args=5` reports nothing for the package, and the default `ruff check ado2gh/ services/` is clean repo-wide — **the five findings that pre-dated this increment (`nodes/executor/node.py` E402 x3, `utils.py` F401 + F811) are gone**. The public-surface snapshot passes unchanged, the orphan guard passes and the 800-line guard is green.
- **GAP-051 isolation**: `tests/agent/test_gap_051_checkpointer_isolation.py` passes, and `data/agent_checkpoints.db` has the same mtime before and after the full-suite run with no `-wal` file left behind — no test reached the developer's real checkpoint database.
- **Concurrent work**: increments 11 (`ado2gh/cli`) and 12 (`services/accelerator_api`) ran alongside. The row counts for `apps/migration-ui` (198), `services/agent` (40), `ado2gh/api` (327), `ado2gh/cli` (10) and `services/accelerator_api` (146 to 147, increment 12 adding a row) were re-checked before and after every regeneration and never dropped. `tag-decisions.json` was clobbered once by an interleaved unlocked write from increment 11 — `record_decision` is a whole-file read-modify-write with no lock — and all 224 decisions were re-recorded from `review-precheck.md` through the idempotent `.inc10-record.py` / `.inc10-reask.py` scripts afterwards, with the other packages' 415 rows verified intact.
- **Not changed, for later increments**: `route_helpers.py` was cleaned in place and not moved — that is T079 — and its `agents -> api` import (GAP-021) was neither deepened nor fixed, which is T078. The six-member `MigrationScope` enum and the four deletion-operation strings in `guardrails.py` were left as they are (CA-002 residual, recorded under GAP-015). `hitl/operator_input.py::fr036_operator_message` was left alone: its `failure["holder_run_id"]` lookup is **not** the orphan the brief described — producers set that key at `ado2gh/api/pipeline_steps.py:957` and `nodes/executor/pipeline.py:250` — so the remaining question is only its inconclusive-case wording, which is prose and outside a signature-cleanup increment.
- **Known stale reference, deliberately not edited**: `tests/unit/test_streaming.py::test_sse_event_kinds` greps `inspect.getsource` for the literal `"token"`, which used to pass incidentally through `session_token=session_token` before the `deps` grouping removed that parameter. Rather than edit the assertion, the `token` event kind is now named in `_stream_graph_events`'s docstring — accurate, since custom node writer events are relayed verbatim.

## 2026-09-12 — 013 Increment 12: `services/accelerator_api/`

Phase 6 (US2) increment 12, run per the per-increment protocol in `specs/013-clean-code-arch-remediation/tasks.md`. Pre-increment inventory ref: `d11d25e`.

The review agent (`cavecrew-reviewer`) decided the package in two rounds. The tracked pre-check in `review-precheck.md` covered 77 proposals — 7 CONFIRM, 70 REJECT, 0 ESCALATE — and **none of its function verdicts could be applied straight**: writing a docstring changes a row's `state_hash`, so by the time the cleanup was done all 66 function rows had moved. Four of the seven CONFIRMs were executed first, while their hashes still matched, and their rows disappeared with the fix; the remaining 63 function proposals plus 13 `module_name_review` proposals went back to the reviewer in one batch with the verbatim handoff instruction from `contracts/artifact-schemas.md`, told up front that CONFIRM obliges remediation and that FastAPI handler names, parameter names and `Depends()` parameters are the frozen HTTP contract. **Round two: 71 REJECT, 3 CONFIRM, 2 ESCALATE.** A two-row follow-up round judged the rename and the module this increment created; both REJECT. 74 decisions are recorded in `tag-decisions.json`, 73 `decided_by: cavecrew-reviewer` and one `decided_by: operator`.

Five verdicts reversed against the pre-check, all in round two and all explained by information the pre-check did not have:

- `main.py:module_name_review` — CONFIRM to REJECT ("`services.accelerator_api.main:app` is deployment-frozen"). The **operator** had already overridden this one; recorded with `decided_by: operator` and the rationale that the module path is load-bearing in the Dockerfile, both compose files, `deploy/kubernetes`, `ci.yml` and the docs, so the rename is deferred. No exception-register row.
- `routes/_shared.py:module_name_review` — REJECT to **CONFIRM** ("shared states the module's relation to its importers, not any responsibility"). **Not executed, and deliberately left undecided and `pending` for the operator**: the rename means editing the import line of three frozen GAP regression tests (`tests/auth/test_gap_002_*`, `tests/auth/test_gap_005_*`, `tests/pipeline/test_gap_004_*`), which this increment was instructed not to touch, and T078/T079 restructure this package in Phase 8.
- `routes/pipeline_routes.py:module_name_review` — REJECT to **CONFIRM**, and executed (see the table).
- `main.py::_pipeline_readiness_impl:name_review` — REJECT to **CONFIRM**, and executed.
- `routes/profile_routes.py::migration_scan_profile:bool_flag` and `routes/settings_routes.py::list_cloud_credentials:bool_flag` — CONFIRM to **ESCALATE**. Both booleans are public query parameters with live callers (`services/agent/routes/form_routes.py` calls `?sync=true`; `apps/migration-ui/src/lib/cloudCredentials.ts` calls `?scan=true`), so the reviewer ruled the tag substantively right but the fix a contract change. They stay `pending` for the operator (FR-002b) and are the only two rows in the package that are not `clean`.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `services/accelerator_api/routes/_shared.py` | (same) | `bool_flag` to two functions | Confirmed `bool_flag` (FR-012): `_require_profile(profile_id, require_active: bool = False)` split into `_require_profile(profile_id)` (404 if unknown) and `_require_active_profile(profile_id)` (also runs `assert_profile_active_for_run`, mapping `ProfileGovernanceError` to 403/404/409). All 12 call sites are in `routes/profile_routes.py`; two took `require_active=True`, one took the explicit `False` | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/_shared.py` | (same) | renamed function | Confirmed `name_review`: `asdict_adv` to `advanced_settings_as_dict`, typed `(adv: AdvancedSettings) -> dict[str, Any]`. One call site, `routes/settings_routes.py::update_advanced` | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/migrate_routes.py` | `services/accelerator_api/routes/migrate_scope.py` (new) | module split | `migrate_routes.py` was 752 lines against the 800-line cap and needed nine FR-010a handler docstrings, which no amount of prose-trimming fits. The shared plumbing — `_get_clients`, `_load_global_cfg`, `_parse_repo_key`, `_build_scope_context`, `_scope_handler_response`, `_run_scope_migrate`, `_raise_ado_http_error`, `_encrypt_secret` — moved to a new sibling module and is re-exported from `migrate_routes`, so `routes/proxy_routes.py`'s `from ...migrate_routes import _get_clients` and `tests/unit/test_migrate_routes_core.py`'s `migrate_routes._parse_repo_key` still resolve. `_state_db` stayed in `migrate_routes` on purpose: `tests/unit/test_gap_007_migrate_routes_unguarded.py` and `test_migrate_routes_core.py` patch it there by attribute, so `_build_scope_context` now takes the store as a `db` parameter instead of opening one. Both files are in the orphan guard's import graph; no allowlist edit | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/migrate_routes.py` | `services/accelerator_api/routes/migrate_scope.py` | `bool_flag` x2 to `ExecutionMode` | Confirmed `bool_flag` x2 (CA-001): `_build_scope_context(..., *, dry_run: bool, ...)` and `_scope_handler_response(..., *, dry_run: bool, ...)` both take `mode: ExecutionMode`. `_build_scope_context` previously converted internally, so the conversion simply moved out to the three handlers that call it; `_scope_handler_response`'s branch is now `if mode is ExecutionMode.DRY_RUN`. Every call site converts at the boundary with `ExecutionMode.from_dry_run(dry_run=req.dry_run)` off the request body. The six handlers that branch on `req.dry_run` directly were left alone — that is the wire boolean at its own boundary | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/migrate_routes.py` | (same) | unused params dropped (9) | The nine `/v1/migrate/*` handlers each declared `request: Request` and none used it — GAP-007 moved the live-execution check to the router-level `dependencies=[Depends(guard_live_migration)]`, which resolves its own `Request`. Dropped rather than renamed to `_request`, since FastAPI injects `Request` by annotation and the parameter never appears in OpenAPI: 9 x ARG001 cleared, route table byte-identical | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/profile_routes.py` | `services/accelerator_api/routes/profile_credential_routes.py` (new) | module split | Documenting 32 handlers took `profile_routes.py` to 1097 lines. The twelve credential-validation and GitHub-token endpoints moved out verbatim — decorators, docstrings and annotations byte-identical — onto their own `APIRouter`, mounted with `router.include_router(_credential_router)` at the bottom of `profile_routes.py` so `main.py` keeps its single `app.include_router(profile_routes.router)` and the registration order the routes were tested in. 795 + 329 lines; no docstring was trimmed | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/routes/pipeline_routes.py` | `services/accelerator_api/routes/approval_routes.py` (new) | confirmed `module_name_review`, remediated by split | Confirmed `module_name_review` (FR-013): the reviewer found five of the twelve endpoints were the platform-wide live-approval API under a different prefix, `/v1/platform/approvals`, plus the `register_migrate_executor` / `register_pipeline_executor` calls that govern migrate approvals as well as pipeline runs — "the name covers only the pipeline half". Remediated by moving those five handlers and both executor registrations into `approval_routes.py`, mounted as a sub-router the same way, rather than by renaming: `pipeline_routes.py` now holds only `/v1/pipeline` endpoints (275 lines) and `approval_routes.py` only the approval queue (154). GAP-004's `POST /runs` approval branch and the body-less `POST /runs/{run_id}/start` are untouched | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/main.py` | (same) | renamed function | Confirmed `name_review`: `_pipeline_readiness_impl` to `_assess_pipeline_readiness`, since `_impl` named the function's relation to its two route wrappers rather than the work it does. Private, two call sites, both in `main.py`; the two readiness routes are unchanged | yes | pass (967) | 2026-09-12 |
| `services/accelerator_api/**` | (same) | docstrings + annotations | Google-style docstrings on every module, class, function and method in the package, private helpers included, each non-void one carrying a `Returns:` that describes the value. Route-handler docstrings are written as OpenAPI descriptions for an API consumer (FR-010a) — what the endpoint does, what it needs, what comes back and when it fails — and now appear as the `description` of all 107 operations. Every parameter and return annotated, with `PlatformUser`, `MigrationProfile`, `AdvancedSettings`, `LLMModelConfig`, `ADOClient`, `GHClient`, `ScopeHandler`, `StateDBBase`, `Path`, `Callable`/`Awaitable`/`Response` imported under `TYPE_CHECKING` so no runtime import or cycle was added. CA-003 held: `auth_routes.py` describes passwords by constraint and storage and the session as an HTTP-only same-site cookie, `settings_routes.py` and `proxy_routes.py` describe credential *fields* and masking, and no key, token, PAT, password or cookie value — real or fake — appears in any docstring, default or example | yes | pass (967) | 2026-09-12 |

- **Tests removed**: 0. **Tests edited**: 0 — every frozen GAP regression test (`test_gap_002`, `test_gap_004`, `test_gap_005`, `test_gap_007`, `test_gap_008`, `test_gap_012`) is byte-identical and green. Measured on this increment: 967 passed, 30 skipped, coverage TOTAL 61 %, in the shared working tree; the one failure in that run, `tests/unit/test_gap_021_layering.py::test_no_upward_imports_between_layers`, is an untracked test a concurrent agent added and the edge it reports (`ado2gh.state.job_store -> ado2gh.api.contracts`) is present unchanged at HEAD and owned by T078.
- **Production functions deleted**: 0 — the package had no `dead` rows at `inventory.json@d11d25e` and none appeared. No `# noqa` was added anywhere in the package.
- **Response-model annotations, and how the "no wire change" claim was checked.** FastAPI 0.137.2 infers `response_model` from a return annotation whenever the decorator does not set one, so the OpenAPI document was generated before and after and diffed with `description`, `summary` and `title` stripped. Result: **90 paths and 107 operations before and after, zero parameter changes, zero request-body changes, zero changes to any existing component schema.** Of the handlers annotated, 47 were given the concrete model their decorator already declares in `response_model=` (which wins over the annotation, so those are inert), 4 were given a model the decorator did not declare — `GET /v1/pipeline/readiness` to `ReadinessResponse`, `GET /v1/settings` to `SettingsResponse`, `GET /v1/settings/profiles/{profile_id}/scan` to `MigrationScanResponse`, `POST /v1/settings/profiles/{profile_id}/set-default` to `MigrationProfileResponse` — each provably satisfied because the handler constructs exactly that model on every return path, 47 raw-dict handlers were annotated `dict[str, object]`, and 8 were annotated `-> object`. The `object` cases are deliberate: measured on this FastAPI, a handler annotated `dict[str, object]` that returns a `BaseModel` raises `ResponseValidationError` and 500s, which rules that annotation out for `migration_scan_profile` (returns a model on `sync=true` and a dict otherwise) and for the seven GitHub/ADO proxy passthroughs, whose upstream may be a JSON array; `-> Any` is not available because ruff's ANN401 fires in return position too. The only documented change is 51 responses whose schema went from `{}` (undocumented) to a free-form object, plus `SettingsResponse` joining the components — no payload changed. `tests/contract/`, `tests/feature/`, `tests/unit/test_migrate_routes_core.py` and `tests/pipeline/` pass unchanged, and `test_public_surface_snapshot.py`'s 148 `http_routes` lines are untouched.
- **Exception register**: **no row added**; it stays at 27 rows, against a cap of 33 (2 % of 1690). The package ends with zero `tags` outside one `bool_data` marker (`_shared.py::_maybe_audit_model_enabled`, the reviewer's REJECT of a `bool_flag` on two captured prior-state values), the same accounting increments 5, 7 and 9 recorded. The candidates that would have cost rows were resolved instead: the nine unused `request: Request` parameters were dropped outright, and no `> 5 params` row survived — the package's largest handler takes four.
- **Coverage ratchet**: `--cov-fail-under` raised **60 to 61** in `.github/workflows/ci.yml` in this commit (measured TOTAL 61 %, FR-027a/SC-004). Never lowered.
- **File sizes**: three new modules were created purely to stay under the 800-line cap, which the docstring pass would otherwise have breached on two files (`migrate_routes.py` 752 to ~873 projected, `profile_routes.py` 570 to 1097 actual). Final: `main.py` 799, `settings_routes.py` 798, `profile_routes.py` 795, `migrate_routes.py` 691, `auth_routes.py` 544, `proxy_routes.py` 380, `_shared.py` 336, `profile_credential_routes.py` 329, `pipeline_routes.py` 275, `migrate_scope.py` 265, `approval_routes.py` 154, `migrate_routes_models.py` 139, `migrate_guard.py` 134. **`main.py` sits one line under the cap and `settings_routes.py` two** — T079, which moves `ado2gh/api/agentic_routes.py` into `routes/history_routes.py` and will want a registration line in `main.py`, must budget a split or a prose trim rather than assume headroom. No allowlist and no cap was edited.
- **Inventory**: `services/accelerator_api` is 147 rows (146 before; the confirmed `_require_profile` split added one), **145 `clean` and 2 `pending`** — the two operator ESCALATEs. `--pending --package services/accelerator_api` therefore exits 1 by design, listing exactly those two plus the deferred `routes/_shared.py:module_name_review`; FR-002b keeps all three open until the operator answers. The T002 ruff set with `max-args=5` reports 4 findings for the package, the FBT001/FBT002 pair on each of those two query parameters, left unsuppressed on purpose so the operator's decision stays visible. `python -m ruff check ado2gh/ services/` is clean repo-wide. The public-surface snapshot, the orphan guard and the 800-line guard all pass, and `tests/agent/test_gap_051_checkpointer_isolation.py` passes with `data/agent_checkpoints.db`'s mtime unchanged.
- **Not changed, for later increments**: `main.py` keeps its name, its middleware `auth_enabled()` short-circuits (GAP-002/005) and its registration of `ado2gh/api/agentic_routes.py` — moving that module is T079. GAP-008's deliberate GET/write asymmetry in `proxy_routes.py`, GAP-012's `POST /v1/settings/llm-models/catalog` with its `body: dict`, and GAP-004's body-less run-start are documented as deliberate rather than "fixed". `routes/_shared.py`'s module-level singletons and its five patch-forwarding lazy wrappers are unchanged; the wrappers gained `*args: object, **kwargs: object` and the concrete return type of the function each forwards to.
- **Concurrent work**: increments 10 (`ado2gh/agents/`, committed `ca3f3b2`) and 11 (`ado2gh/cli/`, committed `85712d0`) and several GAP repairs ran in the same tree. Per the serialisation rule adopted after a lost-write incident, every decision and regeneration here happened after `ca3f3b2` landed; row counts for `apps/migration-ui` (198), `services/agent` (40), `ado2gh/api` (327), `ado2gh/agents` (402) and `ado2gh/cli` (10) were re-checked before and after every regeneration and never moved, and `tag-decisions.json` is byte-identical to `ca3f3b2` apart from the generator's own re-sort.

## 2026-09-12 — 013 Phase 8 T078: GAP-021 layering

Phase 8 (US4) G-seed 3, run per the per-increment protocol in `specs/013-clean-code-arch-remediation/tasks.md`. Pre-change baseline: `a5fbb01`.

GAP-021 (GAP-ARCH-01) found the package layered bottom-to-top as `models`/`http_utils` → `clients`/`state` → `core`/`phase`/`pipelines` → `api` → `cli`, with the state, clients and api layers reaching upward through top-level and function-local ("deferred") imports. Five symbols closed the three worst-measured edges by moving down a layer; no symbol's behavior changed, only its module.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/api/contracts.py` | `ado2gh/models.py` | moved (`JobTypeEnum`, `JobStatus`, `JobRecord`) | Closes `state → api`: `ado2gh/state/job_store.py` and `ado2gh/core/orchestration/worker.py` took their job record types from the API layer's wire-contract module. `JobRecord` is a `pydantic.BaseModel`; it moved unchanged into `models.py` alongside the plain dataclasses/enums already there. `contracts.py` keeps `JobStatusResponse`, which wraps `JobRecord`, and now imports `JobRecord`/`JobTypeEnum` back from `models.py` for that one field | yes (diff hunks are a pure cut/paste, no line's content changed) | pass (975) | 2026-09-12 |
| `ado2gh/api/profile_discovery.py` | `ado2gh/state/scan_payload.py` (new) | moved (`manual_phase_overrides`) | Closes `state → api`: `ado2gh/state/postgres_risk_gates_scan_mixin.py` and `sqlite_profile_scan_mixin.py` called this via a function-local import to dodge a load-time cycle. The new module's own docstring records why the shaping lives in `ado2gh/state/` | yes | pass (975) | 2026-09-12 |
| `ado2gh/api/migration_scan.py` | `ado2gh/state/scan_payload.py` | moved (`pack_scan_summary_json`, `extract_discovery_fields`, `DISCOVERY_DETAIL_KEY`, `DISCOVERY_DETAIL_FIELDS`) | Same edge, same two mixin callers. `migration_scan.py` keeps using `DISCOVERY_DETAIL_FIELDS` itself and now imports it back from `ado2gh.state.scan_payload` — api importing from state is the intended downward direction | yes | pass (975) | 2026-09-12 |
| `ado2gh/core/sessions.py` (deleted) | `ado2gh/http_utils.py` (function added) | file deleted; function moved (`get_thread_session`) | Closes `clients → core`: `ado2gh/clients/gh_client.py`'s `GHClient._session` property deferred this import specifically because a top-level import collided with `ado2gh/core/__init__.py` eagerly importing `RollbackHandler`, which transitively imports `ado2gh.clients` — the exact cycle GAP-021's evidence named. `get_thread_session` and its backing `threading.local()` moved into `http_utils.py`, which `clients` already sits below; `gh_client.py` now imports it at module level | yes | pass (975) | 2026-09-12 |
| `ado2gh/cli/helpers.py` | `ado2gh/api/repo_input.py` (new) | moved (`load_repos`) | Closes `api → cli`: `ado2gh/api/pipeline_steps.py` and `ado2gh/api/validation_run.py` (two call sites) reached into the CLI layer via function-local imports for the one helper both the CLI commands and the API-layer pipeline/validation steps need. `ado2gh/cli/migration.py` and `ado2gh/cli/misc.py` now import it from `ado2gh.api.repo_input` instead of from their own `helpers` sibling | yes | pass (975) | 2026-09-12 |

- **New test**: `tests/unit/test_gap_021_layering.py` (new). Reuses `_build_import_graph` from `tests/unit/test_no_orphaned_modules.py` — the same AST walk that already catches function-local/deferred imports, not just top-level ones — and asserts zero edges for `ado2gh.state → ado2gh.api`, `ado2gh.clients → ado2gh.core`, `ado2gh.api → ado2gh.cli`. The revert proof below shows it catching all eight edges the five relocations removed.
- **Residual, deliberately not asserted**: `ado2gh.core → ado2gh.api` stays open on the GAP-021 register. `ado2gh/core/orchestration/worker.py` imports `ado2gh.api.accelerator`/`ado2gh.api.contracts` at module level because driving the Accelerator SDK is that module's job, and `ado2gh/core/conflict_detection.py` does two function-local imports of `api.repo_lock`/`api.pipeline_store`. The register's evidence lists this file under its pre-rename name `migration_fr036.py`; `docs/STRUCTURAL_CHANGELOG.md:717` records the `git mv` to `conflict_detection.py` from a prior increment, so this is the same residual under its current name, not a second one. Closing it means moving the worker out of `ado2gh/core/`, which is out of scope here; the new test's own docstring records the decision so it is not quietly re-opened by a future edit to the assertion. Checked but out of T078's three named edges entirely: `ado2gh/auth/service.py:149,255` still does a function-local import of `ado2gh.api.profile_governance` (an `auth → api` edge) — real and still open, but a different layer pair than `state → api` / `clients → core` / `api → cli`, so neither T078 nor `test_gap_021_layering.py` claims to close it. Both residuals stay open on the GAP-021 register after T078.
- **Callers updated (import path only, no logic change)**: production — `ado2gh/state/job_store.py`, `ado2gh/core/orchestration/worker.py`, `ado2gh/clients/gh_client.py`, `ado2gh/state/postgres_risk_gates_scan_mixin.py`, `ado2gh/state/sqlite_profile_scan_mixin.py`, `ado2gh/cli/migration.py`, `ado2gh/cli/misc.py`, `ado2gh/api/pipeline_steps.py`, `ado2gh/api/validation_run.py`; tests — `tests/unit/test_gap_028_dynamo_double_claim.py`, `tests/unit/test_gap_053_worker_inventory_job.py`, `tests/unit/test_module_structure.py` (also drops its assertion that `ado2gh/core/sessions.py` exists), `tests/core/test_lightweight_mode.py`, `tests/core/test_template_and_bicep_smoke.py`.
- **Tests removed**: 0. **Production functions deleted**: 0 — all five symbols were relocated, not deleted; every diff hunk is a pure cut from the old file and paste into the new one with no line's content changed, so there is no FR-029 deletion pointer against `inventory.json@a5fbb01` — the same reasoning Increment 11 recorded for the same kind of move.
- **Revert proof**: `git stash push -- ado2gh/models.py ado2gh/api/contracts.py ado2gh/http_utils.py ado2gh/core/sessions.py ado2gh/core/orchestration/worker.py ado2gh/clients/gh_client.py ado2gh/state/job_store.py ado2gh/api/migration_scan.py ado2gh/api/profile_discovery.py ado2gh/state/postgres_risk_gates_scan_mixin.py ado2gh/state/sqlite_profile_scan_mixin.py ado2gh/cli/helpers.py ado2gh/api/pipeline_steps.py ado2gh/api/validation_run.py ado2gh/cli/migration.py ado2gh/cli/misc.py tests/unit/test_gap_028_dynamo_double_claim.py tests/unit/test_gap_053_worker_inventory_job.py tests/unit/test_module_structure.py tests/core/test_lightweight_mode.py tests/core/test_template_and_bicep_smoke.py` (the two new untracked modules and the new test stay in place); `python -m pytest tests/unit/test_gap_021_layering.py -q` then fails on all eight edges: `ado2gh.api.pipeline_steps -> ado2gh.cli.helpers`, `ado2gh.api.validation_run -> ado2gh.cli.helpers`, `ado2gh.clients.gh_client -> ado2gh.core.sessions`, `ado2gh.state.job_store -> ado2gh.api.contracts`, `ado2gh.state.postgres_risk_gates_scan_mixin -> ado2gh.api.migration_scan`, `ado2gh.state.postgres_risk_gates_scan_mixin -> ado2gh.api.profile_discovery`, `ado2gh.state.sqlite_profile_scan_mixin -> ado2gh.api.migration_scan`, `ado2gh.state.sqlite_profile_scan_mixin -> ado2gh.api.profile_discovery`; `git stash pop` restores the tree exactly (`git status` after matches before). Taken 2026-09-12 by Claude (sonnet subagent, T078 finisher).
- **Guards**: `test_gap_021_layering.py`, `test_no_orphaned_modules.py`, `test_file_size_limit.py`, `test_public_surface_snapshot.py`, `test_module_structure.py` — 10 passed. `python -m ruff check ado2gh/ services/` — 4 pre-existing findings, both `FBT001`/`FBT002` on the two operator-pending query parameters at `services/accelerator_api/routes/profile_routes.py:278` and `settings_routes.py:443`, unrelated to this task and unchanged before and after; zero new findings anywhere this task touched.
- **Full suite / coverage**: measured in the shared working tree with T079 also uncommitted (its own entry follows): 975 passed, 30 skipped, 1 unrelated failure, coverage TOTAL 60.45% against a 61% gate. The coverage shortfall is T079's mechanical side effect, not T078's — see below.
- **Not changed, for later increments**: the `core → api` residual named above stays on the GAP-021 register as `open`.
- **Concurrent work**: several other increments and GAP fixes landed in the same tree while this task ran (commits including `4b161df`, `6e31cf2`, `a5e085a`, `ef7a6c7`, `4999e19`, `617b08f`, `95ddc3f`, visible in `git log`); none touched a T078 file, confirmed by `git status` matching before and after the revert-proof stash cycle.

## 2026-09-12 — 013 Phase 8 T079: HTTP-route code out of the package

Phase 8 (US4) G-seed 11, run per the same protocol, in the same working tree as T078 above. Pre-change baseline: `a5fbb01`.

Two HTTP-route modules moved out of `ado2gh` into the service that actually serves them; `ado2gh` stops holding FastAPI route handlers outside the documented residual below. Both moves are byte-identical relocations, verified by diffing the pre-change blob at `a5fbb01` against the new file with empty output.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/api/agentic_routes.py` | `services/accelerator_api/routes/history_routes.py` | renamed (git-detected) | The audit-history REST routes (`history_sessions`, `history_event_types`) are `services/accelerator_api`'s own HTTP surface, not shared package logic; moving them out is G-seed 11's whole point | yes — `git show a5fbb01:ado2gh/api/agentic_routes.py` diffed against the new file is empty | pass (975) | 2026-09-12 |
| `ado2gh/agents/migration_agent/route_helpers.py` (599 lines, deleted) | `services/agent/routes/_helpers.py` (new) | moved | Same reasoning for the agent service: session/run/form/plan lazy-import wrappers used only by `services/agent`'s own route modules | yes — `git show a5fbb01:ado2gh/agents/migration_agent/route_helpers.py` diffed against the new file is empty | pass (975) | 2026-09-12 |
| `services/accelerator_api/main.py` | (same) | registration updated | `from ado2gh.api.agentic_routes import router as agentic_router` / `app.include_router(agentic_router)` replaced with the equivalent import and call against `history_routes`. Still 799 lines (net-zero swap), under the 800-line cap | yes | pass (975) | 2026-09-12 |
| `services/agent/main.py` | (same) | imports re-pointed | Three `from ado2gh.agents.migration_agent.route_helpers import (...)` blocks replaced with equivalent `from services.agent.routes._helpers import (...)` blocks, same names (`RunStatus`, `_accel_headers`, `_sessions`, `_accel_get_impl as _accel_get`, `_accel_post_impl as _accel_post`), reordered per isort (`ado2gh.*` before `services.*`). 159 lines | yes | pass (975) | 2026-09-12 |
| `services/agent/routes/execution_routes.py`, `form_routes.py`, `message_routes.py`, `model_routes.py`, `plan_routes.py`, `run_routes.py`, `session_routes.py` | (same) | imports re-pointed | Same import swap in all seven route modules; `run_routes.py::metrics()` also updates its one function-local import of `_sessions` | yes | pass (975) | 2026-09-12 |
| `ado2gh/agents/migration_agent/session/lifecycle.py` | (same) | docstring text updated | One reference to `route_helpers._resolve_model_id` corrected to `routes._helpers._resolve_model_id` | yes | pass (975) | 2026-09-12 |

- **Tests updated (import path only)**: `tests/contract/test_gap_015_work_item_field_contract.py` (imports `services.agent.routes._helpers as route_helpers`, keeping the local alias), `tests/unit/test_thinking_isolation.py`, `tests/auth/test_audit_history_api.py`, `tests/auth/test_audit_history_rbac.py`, `tests/core/test_validate_api.py` (import `router` from `services.accelerator_api.routes.history_routes` now), `tests/auth/test_gap_002_auth_disabled_bypasses_live_approval.py` (docstring line-reference only, `route_helpers.py:199,209` to `services/agent/routes/_helpers.py:199,209`).
- **Tests removed**: 0. **Production functions deleted**: 0 — both moves are whole-file relocations verified byte-identical against `a5fbb01`; nothing was deleted, so there is no `inventory.json@a5fbb01` deletion pointer to cite.
- **Orphan allowlist**: not edited. `tests/unit/test_no_orphaned_modules.py` passes with the new module locations (`services/agent/routes/_helpers.py`, `services/accelerator_api/routes/history_routes.py`) and no allowlist entry needed — both are imported by their service's `main.py`/route registrations, same as any other route module.
- **Snapshot**: `tests/contract/test_public_surface_snapshot.py` passes unchanged — the HTTP paths this test freezes never moved, only the Python module serving them.
- **800-line cap**: holds. `services/accelerator_api/main.py` 799 lines, `services/agent/main.py` 159 lines — the cap-adjacent hazard Increment 12 flagged (`docs/STRUCTURAL_CHANGELOG.md:958`) resolved itself because the import-block swap is net-zero length.
- **Guards / ruff / full suite**: identical run to T078 above, in the same working tree — 10 guard tests passed, ruff 4 pre-existing unrelated findings, full suite 975 passed / 30 skipped / 1 unrelated failure.
- **Coverage**: TOTAL 60.45%, against the 61% gate raised at Increment 12 (`docs/STRUCTURAL_CHANGELOG.md:957`) for a package that, at that point, still contained both files this task moves out. Moving `route_helpers.py` (599 lines) and `agentic_routes.py` (~155 lines) — both already well-exercised by tests — out of `--cov=ado2gh`'s measured package removes above-average lines from the numerator's supporting set, which mechanically pulls the package average down even though no line anywhere lost a test. This was foreseeable from this task's own goal and is not a new gap in any moved or touched logic: every relocated function is byte-identical to its pre-move blob at `a5fbb01`, and `get_thread_session` and `load_repos` (both T078) were already thinly covered at their old locations — moving them into smaller files only made an existing gap visible against a smaller denominator, it did not create one. The ratchet itself (`.github/workflows/ci.yml`'s `--cov-fail-under`) is owned by T075/T076/T083/T094-T095 and the operator per GAP-022's register entry, "never lowered" per that entry's own history; this task does not touch `ci.yml` or `pyproject.toml`'s coverage config, and leaves the 60.45%-versus-61% mismatch for the coverage-ratchet-owning task to reconcile against the smaller package.
- **Not changed, for later increments**: none — T079 as scoped is complete; the GAP-022 coverage-ratchet reconciliation above is explicitly flagged for another task, not deferred silently.
- **Concurrent work**: same tree, same commits, as T078's entry above.

## 2026-09-12 — 013 Phase 10 T089: dead console stub export

T089 final regeneration, run per `specs/013-clean-code-arch-remediation/tasks.md`. Baseline: `7a05ca9`. `apps/migration-ui/src/__tests__/nextNavigationStub.ts` is GAP-025 test-support code, added in `617b08f`; the walker had never previously reported any of its exports (`git show 7a05ca9:specs/013-clean-code-arch-remediation/inventory.json` has zero `nextNavigationStub` entries), so this regeneration is the first sighting of a row from that file. It came back tagging `useParams` `dead`, `reference_count: 0`, `disposition: 'pending'` — the one SC-001 finding this task exists to close.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `apps/migration-ui/src/__tests__/nextNavigationStub.ts` | (same) | deleted function + orphaned field | `useParams` had zero references anywhere under `apps/migration-ui/src`, checked both ways: no import of the stub's `useParams` by name, and no production component or page imports `useParams` from `next/navigation` either (grepped every `from 'next/navigation'` import site — `usePathname`, `useRouter`, `useSearchParams` and `redirect` are used, `useParams` never is), so the `vi.mock('next/navigation', ...)` indirection never reaches this export. Not a false positive of the TS walker. A read-only Codex consultation (repo CLAUDE.md rule) confirmed the evidence and additionally flagged `navState.params` as now-orphaned backing state with `useParams` gone; it and its reset line in `resetNavState` were removed too, along with the "no route params" clause in that function's doc comment | yes | pass (166) | 2026-09-12 |

- **Deleted functions**: 1 — `apps/migration-ui/src/__tests__/nextNavigationStub.ts::useParams`. This does not follow the usual FR-029 pointer to a pre-increment commit's `inventory.json` rows with `disposition == "delete"`: no such row exists at any prior commit, since the export first surfaced as `pending`/`dead` in this T089 regeneration rather than carrying forward an earlier confirmed-delete decision. Pointer for the record: `inventory.json@7a05ca9` (pre-commit HEAD; does not contain the row) plus the pre-deletion sighting captured verbatim in `specs/013-clean-code-arch-remediation/run-t089-sc001-detail.txt`. Tests removed: 0 — nothing in the suite called `useParams`.
- **Incidental cleanup, not inventory-tracked**: `navState.params` (an object field, not a function, so the walker never saw it) and the `navState.params = {};` line in `resetNavState` were also removed, since `useParams` was their only reader. Not part of the SC-001 count.
- **Tests**: `npx vitest run` from `apps/migration-ui` — 55 files / 166 tests, all passing after both edits. `npx tsc --noEmit` clean after both edits.
- **Exception register**: not touched, and the 27-row count is unchanged. The alternative reading — a mocked consumer reaching `useParams` through the `next/navigation` mock, making `dead` a walker false positive — does not hold; see the reference check above. No exception row was warranted.
- **SC-001**: before, `tagged-not-excepted: 1 exceptions: 27 cap: 33` (`run-t089-sc001-check.txt`, this task's first regeneration pass). After deleting the export and re-running both generators, `run-t089c-sc001-check.txt` reports `tagged-not-excepted: 0 exceptions: 27 cap: 33`.
- **Inventory**: web console (TypeScript) 205 to 204 exports; the `dead` tag count 1 to 0 (`inventory-summary.md`). `--pending` still exits 1 on exactly the three rows reserved for the operator (`services/accelerator_api/routes/profile_routes.py::migration_scan_profile:bool_flag`, `services/accelerator_api/routes/settings_routes.py::list_cloud_credentials:bool_flag`, `services/accelerator_api/routes/_shared.py:module_name_review`) — unchanged by this task and left for the operator, per T089's own instructions.
- **ChatGPT/Codex consultation**: run before committing per repo `CLAUDE.md`, through the Codex CLI (`gpt-5.6-luna`, `--sandbox read-only --ephemeral`). It confirmed the dead-vs-mock-surface evidence ("No import, alias, re-export, wrapper, or direct call reaches `useParams`... Only computed/reflection-based access or code outside the searched tree could evade the greps, and nothing indicates either") and recommended removing `navState.params` as orphaned state, which was applied.
- **Not changed, for later**: none — T089 as scoped (the SC-001 gate) is closed.


## 2026-09-13 — 013 threat-model remediation: guardrails (GAP-070)

`ado2gh/agents/migration_agent/guardrails.py::evaluate_guardrail` carried an
`approved_plan` keyword whose only behaviour was to imply `plan_approved = True` from
the mere presence of a plan object. Grep over `ado2gh/` and `services/` at `685c624`
found no production caller; the only callers were four test files, all of them
module- or test-level skipped legacy (spec 011) suites.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/agents/migration_agent/guardrails.py` | (same) | parameter deleted | `evaluate_guardrail`'s `approved_plan` alias, its docstring entry and the three-line alias block that set `plan_approved = True` from it. Approval is now expressible only through `plan_approved` (GAP-070) | yes | pass (39, `run-r10a-guardrails.txt`) | 2026-09-13 |
| `tests/unit/test_guardrails_spec011.py` | (deleted) | file deleted | 19 test functions, the whole module skipped at import since spec 012 ("Legacy guardrail API (spec 011) — superseded by test_guardrails.py"). Every test asserted the superseded contract (`ado2gh_*` tool names, role-based access) and 9 of them drove it through `approved_plan=` | yes | pass (39, `run-r10a-guardrails.txt`) | 2026-09-13 |
| `tests/contract/test_agent_pev_flow_contracts.py` | (same) | tests deleted | 3 `@skip`-marked legacy guardrail contract tests that called `evaluate_guardrail(..., approved_plan=plan)`: `test_guardrail_blocks_unauthorized_delete_contract`, `test_guardrail_blocks_repo_not_in_plan_contract`, `test_guardrail_allows_ado_write_with_cleanup_contract`. The file's other skipped legacy tests do not touch the alias and were left alone | yes | pass (14 passed / 12 skipped, `run-r10a-contract.txt`) | 2026-09-13 |

- **Deleted functions**: 22 — 19 in `tests/unit/test_guardrails_spec011.py` (whole file) plus the
  3 named above in `tests/contract/test_agent_pev_flow_contracts.py`. As with the T089 entry,
  this does not follow the usual FR-029 pointer to a pre-increment `inventory.json` row with
  `disposition == "delete"`: the inventory walks `ado2gh/`, `services/` and `apps/`, not `tests/`,
  so no row has ever existed for any of them. Pointer for the record: `inventory.json@8b48f36`
  (1717 rows, none naming `evaluate_guardrail`, `approved_plan` or `test_guardrails_spec011`).
  Production functions deleted: 0 — `evaluate_guardrail` lost a parameter, not its definition.
- **Regression check**: `tests/unit/test_guardrails.py::test_plan_approval_cannot_be_set_through_an_alias`
  asserts the alias keyword now raises `TypeError` and that a plan passed without `plan_approved`
  blocks the write.
- **Not changed, for later**: `tests/unit/test_executor.py` and `tests/integration/test_pev_loop.py`
  also pass `approved_plan=`, but to the deleted legacy `AgentExecutor.invoke`, not to
  `evaluate_guardrail`; both modules are skipped at import and are out of GAP-070's scope.
- **Concurrent work**: same tree as the R1/R4/R10b/R10c threat-model batches; only the paths in
  the table above were touched by this entry.

## 2026-09-13 — 013 batch R10b: `untrusted.py`, the agent's prompt-data envelope

R10b remediation of the threat-model hook artefact
`specs/013-clean-code-arch-remediation/threat-model-2026-09-13-001.md`, registered as
GAP-089 through GAP-096. One new module; no moves, no deletions, no renames.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| (new) | `ado2gh/agents/migration_agent/untrusted.py` | added module | THR-01-004 / THR-02-001 asked for one shared untrusted-data envelope. It could not go in `ado2gh/agents/migration_agent/utils.py`: that module is shared by every node and already stands large, and the envelope has to be importable by `nodes/intent.py`, which `utils.py` itself imports — putting it there would have made the dependency circular in the direction the package layering forbids. 79 lines: `fence_untrusted` (redact through `ado2gh.audit.redaction.redact_payload`, JSON-encode, cap with a visible truncation marker, wrap in delimiters behind a "data, never instructions" notice, and respell any copy of the delimiter token inside the payload so the data cannot close its own block) and `scrub_inline` (collapse control characters, defuse the marker, cap the length) for the one site that must stay a plain inline list | yes | pass (`run-r10b-targeted.txt`, 191 passed / 4 skipped) | 2026-09-13 |

- **Deleted functions**: 0. Nothing was moved, renamed or removed by this batch; the three
  node modules and five tool modules it edits keep every symbol they had.
- **Orphan guard**: `tests/unit/test_no_orphaned_modules.py` needs no allowlist row — the module
  has three real production importers, `nodes/planner_research.py`,
  `nodes/validator_investigation.py` and `nodes/intent.py`, plus
  `tests/unit/test_untrusted_envelope.py`.
- **File-size cap**: all eight edited files stay under 800 lines; the largest,
  `nodes/validator_investigation.py`, is unchanged at 758 because every prompt site swapped a
  three-line `json.dumps(...)[:N]` for a three-line `fence_untrusted(...)` call.
- **Public surface**: unchanged. `tests/contract/test_public_surface_snapshot.py` passes without
  a snapshot edit — no CLI command, HTTP route, DB table or environment variable is involved.
- **Concurrent work**: same tree as the R1/R4/R5/R9/R10a/R10c batches. Only
  `ado2gh/agents/migration_agent/{untrusted.py,tools/*.py,nodes/planner_research.py,
  nodes/validator_investigation.py,nodes/intent.py}` and their tests were touched here;
  `guardrails.py`, `nodes/orchestrator.py` and `nodes/orchestrator_tools.py` belong to R10a and
  were not edited, which is why three halves of THR-05-001, THR-04-001 and THR-02-003 are
  carried as follow-ups on GAP-089, GAP-092 and GAP-096 rather than fixed.

## GAP-110 approval-store split (final-review-findings.md finding 5)

Adding `AGENT_SESSION_SCOPE_TYPE` to `ado2gh/api/live_approval_store.py` for GAP-110 pushed the
file from 876 to 884 lines, past the project's 800-line cap. Split, not trimmed: every moved
function is unchanged prose, just relocated so `LiveApprovalStore`'s storage/decision/dispatch
code and the scope-identifier/context-validation code live in separate files.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| (new) | `ado2gh/api/live_approval_scopes.py` | added module | Holds the scope-identifier and context-validation helpers moved out of `live_approval_store.py`: `ScopeType`, `PIPELINE_RUN_SCOPE_TYPE`, `AGENT_SESSION_SCOPE_TYPE`, `PIPELINE_RUN_CONTEXT_RUN_ID`, `pipeline_run_scope_id`, `migrate_scope_id`, `_migrate_job_params`, `_pipeline_run_params`, `_assert_migrate_context_matches`, `_assert_pipeline_context_matches`. `live_approval_store.py` imports every one of them back and re-exports the public names, so no external importer (`services/accelerator_api/routes/pipeline_routes.py`, `services/accelerator_api/routes/_shared.py`, `ado2gh/api/accelerator.py`, and the GAP-063/071/072/073/098 tests) changed its import statement. | yes | pass (`_pretest_split.txt`, 50 passed) | 2026-09-22 |
| shrunk | `ado2gh/api/live_approval_store.py` | — | Down to 717 lines after the split (cap is 800); keeps `LiveApprovalStore` itself plus `_redacted_context`, `_public_row`, `_db_path`, `_internal_headers`, the executor registry and `_notify_agent` — the storage/decision/dispatch half of the module. | yes | pass (`_pretest_split.txt`, 50 passed) | 2026-09-22 |

## GAP-110 agent-route-helpers split (final-review-findings.md finding 5, second pass)

Implementing the GAP-110 fix itself grew `_enqueue_session_live_approval` in `services/agent/routes/_helpers.py` from a two-line status flip into a real call to the platform approval queue, pushing the file to 850 lines, past the project's 800-line cap. Split, not trimmed: the in-memory session/run caches and their eviction policy are self-contained (no dependency on anything else in `_helpers.py`) and moved out whole.

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| (new) | `services/agent/routes/_session_registry.py` | added module | Holds the process-global `_runs`/`_sessions` caches and their bound: `_session_activity_epoch`, `_forget_session`, `_NON_RECONSTRUCTIBLE_KEYS`, `_is_reconstructible`, `_evict_stale_state`, `_remember_session`, `_remember_run`. `_helpers.py` imports every one of them back and re-exports the public names, so every other route module's `from services.agent.routes._helpers import _sessions, _runs, ...` kept working unchanged. One whitebox test target did move: `tests/agent/test_agent_route_boundary_limits.py` monkeypatched `_helpers.MAX_IN_MEMORY_SESSIONS` to shrink the cap, but `_evict_stale_state` now reads that constant from its own module's namespace, not `_helpers`'s re-exported copy — the two size-cap tests now patch `_session_registry.MAX_IN_MEMORY_SESSIONS` instead. `SESSION_IDLE_TTL_SECONDS` is read-only in that same test and stayed a plain re-export from `_helpers.py`, needing no test change. | yes | pass (`_pretest_gap100_wide3.txt`, 174 passed, 5 skipped) | 2026-09-22 |
| shrunk | `services/agent/routes/_helpers.py` | — | Down to 749 lines after the split (cap is 800); keeps the route request/response models, the accelerator HTTP client helpers, session hydration/payload assembly and `_enqueue_session_live_approval` itself — the request/response half of the module. | yes | pass (`_pretest_gap100_wide3.txt`, 174 passed, 5 skipped) | 2026-09-22 |

## 2026-09-22 — correction: rename the two `GAP-110` test files to their final register ids

The two entries above (`## GAP-110 approval-store split` and `## GAP-110 agent-route-helpers
split`) both cite the provisional id `GAP-110`, which the register scribe's second pass
renumbered to its final id, `GAP-122`, when merging the astra-fixes.md batch (the last three
entries of that batch — provisional `GAP-108`/`GAP-109`/`GAP-110` — became final
`GAP-120`/`GAP-121`/`GAP-122`, since the review-fixes batch already claimed `GAP-108` and
`GAP-109` for two different findings). This is an append-only correcting row; the two entries
above are left as written since they describe file moves that already happened correctly, not
a renumbering. The three regression-test files the register's own GAP-120/GAP-121/GAP-122
entries flagged as not yet renamed have now been renamed to match:

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `tests/auth/test_gap_108_pipeline_approval_scope_match.py` | `tests/auth/test_gap_120_pipeline_approval_scope_match.py` | renamed | Provisional id `GAP-108` in the filename superseded by the final register id `GAP-120` (`GAP-AUTH-13`); the in-file docstring label was updated to match. | yes | pass (`tests/auth/test_gap_120_pipeline_approval_scope_match.py`, targeted run) | 2026-09-22 |
| `tests/auth/test_gap_109_short_and_quoted_secret_values.py` | `tests/auth/test_gap_121_short_and_quoted_secret_values.py` | renamed | Provisional id `GAP-109` in the filename superseded by the final register id `GAP-121` (`GAP-TOKEN-08`); the in-file docstring label was updated to match. | yes | pass (`tests/auth/test_gap_121_short_and_quoted_secret_values.py`, targeted run) | 2026-09-22 |
| `tests/agent/test_gap_110_session_live_request_audited.py` | `tests/agent/test_gap_122_session_live_request_audited.py` | renamed | Provisional id `GAP-110` in the filename superseded by the final register id `GAP-122` (`GAP-AGT-32`); the in-file docstring label was updated to match. | yes | pass (`tests/agent/test_gap_122_session_live_request_audited.py`, targeted run) | 2026-09-22 |
