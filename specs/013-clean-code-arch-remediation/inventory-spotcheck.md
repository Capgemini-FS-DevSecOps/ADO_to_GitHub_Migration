# Inventory Spot Check — US1 independent test (T019)

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-08
**Inventory under test**: `inventory.json` at commit `2036576` (1686 rows: 1470 py + 216 ts)
**Draw**: `random.Random(13).sample(sorted(rows, key=id), 30)`

Method: for every drawn row, the definition was opened in the source and `path`,
`line`, `signature`, `param_count` and each entry of `tags` / `proposed_tags` were
checked against what the code actually says. Where a tag came from an external tool
(ruff `ANN`/`ARG`, vulture) or from the protected-list generator, that tool was
re-run on the single file to confirm the finding is real and correctly attributed.

**Result: 30 PASS, 0 FAIL. No script change was required.**

| # | Row id | Verdict | Note |
|---|--------|---------|------|
| 01 | `ado2gh/api/llm/model_validation.py::_openai_payload_has_tool_calls` | PASS | L194 exact, p1. No `missing_docstring` is right — `D103` is public-only and this is `_`-private. `name_review` proposed on first token `openai`. |
| 02 | `ado2gh/api/phase_definitions.py::slugify_phase_id` | PASS | `dead` confirmed independently: a repo-wide grep for `slugify_phase_id` returns only the `def` line; vulture agrees at confidence 60. `missing_docstring` correct (public, no docstring). `name_review` on `slugify`. |
| 03 | `apps/migration-ui/src/lib/agentSessions.ts::mergeChatMessagesForLoad` | PASS | L129, p3. Untagged because the JSDoc on L128 documents it. |
| 04 | `apps/migration-ui/src/lib/agentSessions.ts::loadCachedThinking` | PASS | L99, p3, no JSDoc above → `missing_docstring` correct. |
| 05 | `services/accelerator_api/routes/settings_routes.py::update_llm_model` | PASS | `untyped` = missing return annotation; `protected` from the `@router.put` decorator, which is why `reference_count == 0` did **not** become `dead`. |
| 06 | `ado2gh/agents/migration_agent/utils.py::_tool_status_done` | PASS | Private + fully annotated → no mechanical tag. `name_review` on `tool`. |
| 07 | `apps/migration-ui/src/components/EmbedLayout.tsx::EmbedLayout` | PASS | JSDoc closes on L7 → correctly clean. p1 (one destructured props object). |
| 08 | `ado2gh/api/live_approval_store.py::LiveApprovalStore.has_approved` | PASS | p2 confirms `self` is excluded from `param_count`. No `name_review` because `has` is in the verb list. |
| 09 | `apps/migration-ui/src/components/UnifiedNavigation.tsx::UnifiedSubNavigation` | PASS | L60, p1, no JSDoc → `missing_docstring` correct. |
| 10 | `ado2gh/agents/migration_agent/runtime/orchestrator.py::stream_turn` | PASS | `untyped` traces to `**kwargs: Any` (ruff `ANN401`), not to a missing annotation. Docstring present → correctly no `missing_docstring`. |
| 11 | `ado2gh/api/live_approval_store.py::LiveApprovalStore._execute_migrate` | PASS | Private, annotated, `execute` is a verb → correctly clean. |
| 12 | `apps/migration-ui/src/app/settings/profiles/[profileId]/layout.tsx::default` | PASS | `protected` as a Next.js route export; `reference_count == 1` is the documented default-export fiat. |
| 13 | `apps/migration-ui/src/lib/pipelineRunStatus.ts::pipelineRunStatusLabel` | PASS | JSDoc on L14 → clean. |
| 14 | `ado2gh/agents/migration_agent/utils.py::discovery_repo_names` | PASS | Docstring + annotations → no mechanical tag; `name_review` on the noun `discovery`. |
| 15 | `ado2gh/agents/migration_agent/route_helpers.py::_require_operate` | PASS | Clean row: private, documented, annotated, `require` is a verb. |
| 16 | `ado2gh/agents/migration_agent/nodes/executor/node.py::executor_node` | PASS | `untyped` looks wrong on the signature alone but is correct: ruff reports `ANN202` at L183 for the **nested** `_run_repo`, and FR-001a attributes nested findings to the enclosing row (`executor_node` spans 103–435). Verified by re-running ruff on the file and by an `ast` nesting check. |
| 17 | `ado2gh/state/base.py::StateDBBase.get_wave_migrations` | PASS | `@abstractmethod` with an `...` body and no docstring → `missing_docstring` correct. |
| 18 | `ado2gh/api/credentials/cloud_credentials_store.py::CloudCredentialsStore._write_sources` | PASS | `protected: true` on a private method is correct — reason `orphan_allowlist`; `ado2gh.api.credentials.cloud_credentials_store` is listed at `tests/unit/test_no_orphaned_modules.py:72`, so every row in the module is protected. |
| 19 | `services/accelerator_api/main.py::enqueue_job` | PASS | `protected` from `@app.post`. `name_review` fires because `enqueue` is absent from the verb list while `queue` is present — a true-to-rule proposal for the reviewer, not a defect. |
| 20 | `ado2gh/api/pipeline_runner.py::PipelineRunner._cancelled` | PASS | Same shape: `cancel` is a verb, `cancelled` is not, so the proposal is raised for judgment. |
| 21 | `ado2gh/agents/migration_agent/hitl/intake.py::consult_planner_context` | PASS | p5 (kw-only counted, `self` n/a) and no `gt5_params` — the threshold is `> 5`. `bool_flag` from `requests_new_migration: bool = False`; `untyped` from `accel_get: Any`. |
| 22 | `ado2gh/core/rollback.py::RollbackHandler._rollback_branch_protection` | PASS | Both tool tags re-confirmed on the file: `ARG002 Unused method argument: stats` at 157:53 and `ANN202` at 156:9. `bool_flag` from `dry_run: bool`. Multi-line signature captured correctly. |
| 23 | `ado2gh/agents/migration_agent/route_helpers.py::_build_migration_plan` | PASS | p4 across a `*`-separated signature; documented and annotated → clean. |
| 24 | `apps/migration-ui/src/lib/agentSessions.ts::loadSessionIndex` | PASS | L179, p2, no JSDoc → `missing_docstring` correct. |
| 25 | `ado2gh/state/sqlite_db.py::SQLiteStateDB.pipeline_migration_summary` | PASS | Public method, no docstring → tagged; `name_review` on the noun `pipeline`. |
| 26 | `ado2gh/agents/migration_agent/guardrails.py::GuardrailDecision.decision` | PASS | Exercises the property carve-out: a noun name under `@property` correctly raises **no** `name_review`. p0 confirms `self` exclusion. |
| 27 | `ado2gh/api/migration_scan.py::pack_scan_summary_json` | PASS | Documented + annotated → clean; `name_review` on `pack`. |
| 28 | `ado2gh/agents/migration_agent/runtime/orchestrator.py::stream_interrupted_graph` | PASS | p7 → `gt5_params` correct; `untyped` from `resume_value: Any` / `accel_get: Any`; `stream` is a verb so no `name_review`. |
| 29 | `ado2gh/agents/migration_agent/nodes/executor/scope.py::discovery_repo_lookup` | PASS | `name_review` keys off the **first** token (`discovery`), not the trailing verb `lookup` — behaving as specified. |
| 30 | `services/accelerator_api/routes/settings_routes.py::delete_llm_model` | PASS | `protected` from `@router.delete`, so `reference_count == 0` correctly does not become `dead`. |

## Observations (no defect, recorded for the reviewer)

- `missing_docstring` never fires on `_`-private definitions. That is ruff's `D1`
  behaviour, not a gap in the script; the audit's docstring debt is therefore a
  public-surface figure by construction.
- `untyped` covers `ANN401` (`Any`) as well as missing annotations, so a fully
  annotated signature can still carry the tag (rows 10, 21, 28).
- Tool findings inside a nested `def` land on the enclosing row (row 16). Anyone
  reading a tag against a signature alone will occasionally be surprised; the
  attribution is the documented FR-001a rule.
- `name_review` is deliberately generous: rows 19 and 20 are almost certainly
  fine names that only miss the verb list by an inflection. That is the intended
  false-positive direction — a reviewer minute, not a missed rename.
