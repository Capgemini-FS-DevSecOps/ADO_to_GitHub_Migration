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
