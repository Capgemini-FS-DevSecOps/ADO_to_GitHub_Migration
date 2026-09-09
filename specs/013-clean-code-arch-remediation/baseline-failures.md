# Baseline failure taxonomy (T004)

Command: `python -m pytest -q -p no:cacheprovider` (run as `.venv/Scripts/python.exe`, research R12).
Raw output: [`baseline-failures.txt`](./baseline-failures.txt).

**Baseline result**: `49 failed, 791 passed, 30 skipped, 256 warnings, 16 errors in 77.67s` — 65 items to clear.

## Precondition: developer-local state made the suite non-deterministic

Before any grouping was possible the suite had to be made runnable. Two environment
faults, both fixed in `tests/conftest.py` (no production change):

1. **Live LLM calls.** `$ADO2GH_DATA_DIR` defaults to the current directory, so the
   agent read the developer's real `data/llm_models.json` — three *enabled* Ollama
   models pointing at `127.0.0.1:11434`, with Ollama actually listening on this
   machine. `POST /v1/sessions` therefore issued real `qwen3:14b` generations and the
   suite appeared to hang (>10 min, no progress). CI has no such file. The session
   fixture points `ADO2GH_DATA_DIR` at an empty tmp dir, restoring CI parity;
   `tests/agent` went from "hangs" to 15 s.
2. **Non-exiting process.** After the last test, `threading._shutdown` blocked forever
   on a leaked non-daemon `aiosqlite._connection_worker_thread` — the cached LangGraph
   checkpointer connection (`graph/builder.py`), whose worker blocks on `tx.get()`
   until the connection is closed. The same fixture calls `reset_compiled_graph()` on
   teardown.

**Known flake, not fixed (escalate).** Running `tests/contract` *in isolation*
deadlocks intermittently inside `POST /v1/sessions/{id}/form-cancel`. Diagnosis
(py-spy): the request thread waits on the AnyIO portal while a second event loop,
spun up by `clear_langgraph_thread_sync` (`graph/builder.py:378-389`,
`asyncio.run(...)` when no loop is running), contends with the portal loop over the
process-global `_CHECKPOINTER` aiosqlite connection. Full-suite runs have not
reproduced it (three consecutive clean runs at 77 s). Fixing it means changing
production code in `builder.py`, which is out of Phase 2 scope.

## Groups

| Group | Cause | Count | Task |
|---|---|---|---|
| A | `sqlite3.OperationalError: no such table: migration_operations` | 16 (errors) | T005 |
| B | Endpoints removed in `0ec95d2` — routes now 404 | 23 | T006 |
| C | `ImportError: cannot import name '_gate_payload'` | 1 | T007 |
| D | `AttributeError: 'SQLiteStateDB' object has no attribute 'upsert_dependency_edge'` | 2 | T008 |
| E | `AgentState missing required field: llm` | 2 | T009 |
| F | `ModuleNotFoundError: No module named 'boto3'` | 1 | T010 |
| G | Other (no assigned task; must still reach zero for T011) | 20 | T011 |
| | **Total** | **65** | |

### Group A — `no such table: migration_operations` (16 errors, T005)

All are errors in the module-level `client` fixture of
`tests/contract/test_migration_api_contracts.py`, which runs
`DELETE FROM migration_operations` before every test.

```
tests/contract/test_migration_api_contracts.py::TestOnDemandMigrationAPI::test_repo_migration_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestOnDemandMigrationAPI::test_repo_migration_requires_form
tests/contract/test_migration_api_contracts.py::TestOnDemandMigrationAPI::test_repo_migration_dry_run_supported
tests/contract/test_migration_api_contracts.py::TestOnDemandMigrationAPI::test_repo_migration_live_requires_confirmation
tests/contract/test_migration_api_contracts.py::TestOnDemandMigrationAPI::test_concurrent_migration_rejected
tests/contract/test_migration_api_contracts.py::TestWaveCreationAPI::test_wave_creation_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestWaveCreationAPI::test_wave_creation_requires_name
tests/contract/test_migration_api_contracts.py::TestWaveCreationAPI::test_wave_creation_with_multiple_repos
tests/contract/test_migration_api_contracts.py::TestWaveExecutionAPI::test_wave_execution_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestWaveExecutionAPI::test_wave_execution_dry_run
tests/contract/test_migration_api_contracts.py::TestWaveExecutionAPI::test_wave_execution_nonexistent_returns_404
tests/contract/test_migration_api_contracts.py::TestWaveStatusAPI::test_wave_status_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestWaveStatusAPI::test_wave_status_nonexistent_returns_404
tests/contract/test_migration_api_contracts.py::TestPreMigrationFormAPI::test_form_generation_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestPreMigrationFormAPI::test_form_submission_endpoint_exists
tests/contract/test_migration_api_contracts.py::TestPreMigrationFormAPI::test_form_generation_requires_repository_id
```

### Group B — endpoints removed in `0ec95d2` (23, T006)

`0ec95d2` deleted `ado2gh/api/discovery_routes.py`, `ado2gh/api/migration_routes.py`,
`ado2gh/api/models.py` and the workflow-readiness route; every test below asserts a
2xx from a path that no longer exists.

```
tests/agent/test_agentic_api.py::test_create_assignment                                  assert 404 == 200
tests/agent/test_agentic_api.py::test_workflow_readiness                                 assert 404 == 200
tests/agent/test_agentic_api.py::test_rollback_dry_run                                   assert 404 == 200
tests/agent/test_agentic_api.py::test_list_assignments_and_audit                         assert 404 == 200
tests/contract/test_agent_interface_contracts.py::TestAgentInterface::test_discovery_results_accessible        assert 404 == 200
tests/contract/test_agent_interface_contracts.py::TestAgentInterface::test_migration_wave_creation_accessible  assert 404 == 201
tests/contract/test_discovery_api_contracts.py::TestDiscoveryScanAPI::test_scan_endpoint_exists                assert 404 == 400
tests/contract/test_discovery_api_contracts.py::TestDiscoveryScanAPI::test_scan_with_force_refresh             assert 404 == 400
tests/contract/test_discovery_api_contracts.py::TestDiscoveryScanAPI::test_scan_returns_scan_id_when_orgs_provided
tests/contract/test_discovery_api_contracts.py::TestDiscoveryResultsAPI::test_results_endpoint_returns_200
tests/contract/test_discovery_api_contracts.py::TestDiscoveryResultsAPI::test_results_have_required_fields
tests/contract/test_discovery_api_contracts.py::TestDiscoveryResultsAPI::test_results_filter_by_organization
tests/contract/test_discovery_api_contracts.py::TestDiscoveryResultsAPI::test_results_filter_by_status
tests/contract/test_discovery_api_contracts.py::TestDiscoveryResultsAPI::test_results_no_wave_assignment
tests/contract/test_unified_tabs_contracts.py::TestUnifiedTabNavigation::test_discovery_api_endpoint_exists
tests/contract/test_unified_tabs_contracts.py::TestUnifiedTabNavigation::test_migration_api_endpoint_exists
tests/contract/test_unified_tabs_contracts.py::TestUnifiedTabNavigation::test_discovery_scan_endpoint_exists
tests/feature/test_services_coverage.py::test_agent_health_llm_and_session                assert 404 == 200
tests/integration/test_agent_interface_integration.py::TestAgentConversationWorkflow::test_full_migration_workflow_via_api
tests/integration/test_agent_interface_integration.py::TestAgentConversationWorkflow::test_wave_execution_requires_draft_status
tests/integration/test_unified_tabs_integration.py::TestUnifiedNavigationWorkflow::test_discovery_results_empty_initially
tests/integration/test_unified_tabs_integration.py::TestUnifiedNavigationWorkflow::test_migration_wave_lifecycle
tests/integration/test_unified_tabs_integration.py::TestUnifiedNavigationWorkflow::test_wave_name_validation
```

### Group C — `_gate_payload` import (1, T007)

```
tests/contract/test_rollback_gates_contract.py::test_gate_status_shape
  ImportError: cannot import name '_gate_payload' from 'ado2gh.api.agentic_routes'
```

### Group D — missing `SQLiteStateDB.upsert_dependency_edge` (2, T008)

```
tests/core/test_batch_executor_topo.py::test_plan_repo_order
tests/core/test_batch_executor_topo.py::test_sort_repos_topo
```

### Group E — `AgentState` missing `llm` (2, T009)

```
tests/contract/test_langgraph_contracts.py::test_agent_state_has_pev_fields   AgentState missing required field: llm
tests/contract/test_langgraph_contracts.py::test_all_required_nodes_present   node-set mismatch
```

### Group F — `boto3` missing (1, T010)

```
tests/cloud_credentials/test_cloud_credential_probe.py::test_probe_aws_passed
  ModuleNotFoundError: No module named 'boto3'
```

### Group G — other (20, no assigned task)

Research R15's "~17 remaining assorted assertion failures"; measured at 20.

| Sub-cause | Count | Test ids |
|---|---|---|
| `SettingsStore.__init__() got an unexpected keyword argument 'db_path'` | 5 | `tests/unit/test_operator_resolutions.py::TestOperatorResolutionPersistence::{test_set_operator_resolutions_persists_mappings, test_get_operator_resolutions_loads_mappings, test_get_operator_resolutions_returns_empty_dict_when_none, test_resolutions_per_profile_isolation, test_set_operator_resolutions_overwrites_existing}` |
| `TypeError: '<' not supported between instances of 'MagicMock' and 'int'` | 4 | `tests/unit/test_workflow_integrity.py::TestWorkflowIntegrityCheck::{test_pass_when_files_exist_and_shas_match, test_fail_when_files_missing, test_no_integrity_check_when_no_workflow_files, test_integrity_check_uses_github_contents_api}` |
| `LiveApprovalStore.create_or_get_pending() got an unexpected keyword argument 'assignment_id'` | 2 | `tests/agent/test_agent_pev_live_gate.py::test_approve_calls_enforce_live_gate_before_resume`, `tests/feature/test_agent_pev_quickstart_scenarios.py::TestQuickstartScenario9DualAndGates::test_gate_blocks_after_platform_approval` |
| `ModuleNotFoundError: No module named 'ado2gh.api.models'` (deleted in `0ec95d2`) | 1 | `tests/integration/test_unified_tabs_integration.py::TestUnifiedNavigationWorkflow::test_concurrent_migration_rejected` |
| `ModuleNotFoundError: No module named 'ado2gh.agents.migration_agent.operator_input'` | 1 | `tests/unit/test_planner_node.py::test_blockers_from_baseline_probes` |
| Postgres schema string missing `migration_assignments` | 1 | `tests/core/test_storage_config.py::test_postgres_schema_includes_agentic_tables` |
| `ADOCleanup.__init__() got multiple values for argument 'dry_run'` | 1 | `tests/core/test_rollback.py::test_enable_pipelines_dry_run` |
| `assert True is False` (validator baseline probe) | 1 | `tests/unit/test_validator_node.py::test_validator_baseline_probe_escalates_operator_input` |
| `'Secret mappings' not in` plan markdown | 1 | `tests/unit/test_pipeline_plan.py::test_finalize_agent_migration_plan_attaches_pipeline_metadata` |
| actionlint argv assertion (`'actionlint' in ['/usr/bin/actionlint', ...]`) | 1 | `tests/unit/test_workflow_validator.py::TestWorkflowValidatorWithActionlint::test_subprocess_call_to_actionlint` |
| `DID NOT WARN` DeprecationWarning | 1 | `tests/feature/test_services_coverage.py::test_agent_deprecated_approvals_list` |
| MCP plan tool subprocess exit 1 | 1 | `tests/agent/test_local_host_parity.py::test_mcp_plan_tool_accepts_dry_run` |

## Green baseline (T011)

`python -m pytest -q -p no:cacheprovider`:

```
807 passed, 30 skipped, 258 warnings in 75.03s (0:01:15)
```

**0 failed, 0 errors.** Raw output: [`run-t011.txt`](./run-t011.txt).

**Collected count — the SC-004 baseline: 828** (`python -m pytest --collect-only -q | tail -1`
→ `828 tests collected in 4.39s`). The 828 executed items break down as 807 `.` plus 21 `s`
in the progress line; the summary's remaining 9 skips are module-level collection skips that
produce no progress character. Per directory: unit 423, core 107, contract 79, agent 41,
llm 34, auth 28, feature 28, profile 28, integration 25, pipeline 25, cloud_credentials 9,
dashboard 1.

Every one of the 65 baseline items was cleared by a **test-side** fix. **No production file
was modified in step 0.** Group dispositions:

| Group | Disposition |
|---|---|
| A (16) | Cleared by T006 — the whole `tests/contract/test_migration_api_contracts.py` module was deleted. The `migration_operations` table was never restored: its owning model (`ado2gh/api/models/`) and its only reader/writer (`migration_router.py`) were both deleted in `0ec95d2`, so a `CREATE TABLE` would have added dead schema. No separate `fix(state)` commit exists — see the T005 note in the step-6 report. |
| B (23) | Deleted with the two contract modules (T006). |
| C (1) | `tests/contract/test_rollback_gates_contract.py` deleted (T007): `_gate_payload` and `PhaseGateChecker.check_for_assignment` went in `859bb6b`, not `0ec95d2`; restoring the helper would create a function with zero production callers (FR-006a). |
| D (2) | `tests/core/test_batch_executor_topo.py` deleted (T008): `upsert_dependency_edge`, `BatchExecutor.plan_repo_order` and `_sort_repos_topo` all went in `859bb6b`; no dependency-edge code survives in either state backend. |
| E (2) | `tests/contract/test_langgraph_contracts.py` updated (T009): `llm` is deliberately absent from `AgentState` (`graph/state.py:27-30` — unserializable deps travel via contextvars), and `ALL_NODES` is six nodes, not five. |
| F (1) | `pytest.importorskip("boto3")` in `tests/cloud_credentials/test_cloud_credential_probe.py::test_probe_aws_passed` (T010). |
| G (20) | Repaired test-side across three commits; the dominant root cause is `859bb6b`, which removed the assignments feature, the assignment gate routes and the `/v1/approvals` alias, and split `nodes.py` into a `nodes/` package, while leaving the tests behind. |

## Coverage ratchet start (T011, FR-027a)

The `[tool.coverage.run] omit` list (`ado2gh/cli/*`, `ado2gh/state/*`, `ado2gh/core/*`) is
deleted from `pyproject.toml`. Measured on the green suite:

```
TOTAL   18017   7855   56%
```

`.github/workflows/ci.yml` now runs `pytest --cov=ado2gh --cov-fail-under=56`
(56.40 % exact). Raw output: [`run-cov.txt`](./run-cov.txt). See plan.md
§ Coverage measurement for the Windows-only measurement workaround.

## Safeguard baseline counts (for T093)

Measured 2026-09-08 over `ado2gh/` and `services/`, `--include=*.py`, occurrence counts
(not unique files). T093 re-runs these and must not find a lower number.

| Safeguard | Pattern | Count |
|---|---|---|
| Live-execution approval | `can_approve_live_execution` | 6 |
| Gate / override | `override_reason\|gate_override\|\.override(\|check_gate` | 12 |
| Audit writes | `audit_event(` | 4 |
| Audit writer wiring | `AuditWriter` | 7 |
| Execution confirmation | `require_confirmation\|confirm_execute\|plan_confirmed` | 28 |
| `--dry-run` CLI flag declarations | `--dry-run` | 6 (`cli/misc.py` 2, `cli/phase.py` 1, `cli/pipelines.py` 1, `cli/run_cmd.py` 2) |
