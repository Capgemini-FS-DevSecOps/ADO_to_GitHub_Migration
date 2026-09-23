# SC-008 rehearsal (dry run of T092)

**Date**: 2026-09-08 · **HEAD**: `79b5673` · **Status**: rehearsal only, not the gate.
Read-only: no docstring, no source and no shared artefact was modified by this run.

## Method

Followed research R13 / quickstart § SC-008 exactly, with one deliberate narrowing:
the population is restricted to the **committed-clean** packages, because the rows of
the packages still in flight change under the sampler.

- Population: `disposition == "clean"` rows of `ado2gh` (root), `ado2gh/audit`,
  `ado2gh/clients`, `ado2gh/state`, `ado2gh/phase`, `ado2gh/pipelines`,
  `ado2gh/reporting` = **443 rows**. Excluded: `auth`, `core`, `api`, `agents`, `cli`,
  `services/*`, `apps/migration-ui`.
- Ordering: R13 fixes no ordering; the quickstart snippet samples the filtered list in
  **inventory.json file order**, so that order was used (not an `id` sort).
  `random.Random(13).sample(rows, 40)`.
- `inventory.json` has no `docstring` field (quickstart's `r.get("docstring","")` is
  always empty), so signature + docstring were extracted from the source with `ast`.
- Prompt: id + signature + docstring only, asking per function for what it takes, what
  it returns (concrete meaning, not just the type) and what it raises, with an explicit
  instruction to answer "cannot determine" rather than guess.
- Reviewer: one `general-purpose` agent, told it has no tools and no repository access;
  zero tool calls made (confirmed: `tool_uses: 0`).
- Scoring rule applied: **fail** when the answer contradicts the body, or when the
  concrete return value cannot be stated at all (only its type). SC-008's wording scores
  takes/returns; `Raises:` mismatches are recorded as observations, not failures.
- CA-003: the 40 bodies were scanned for credential literals — **none present**, nothing
  redacted.

## Score

**38 / 40** — exactly the pass line, with zero margin.

| Package | Sampled | Fail |
|---|---|---|
| `ado2gh/state` | 20 | 1 |
| `ado2gh/clients` | 7 | 0 |
| `ado2gh/pipelines` | 11 | 0 |
| `ado2gh/phase` | 1 | 0 |
| `ado2gh/reporting` | 1 | 1 |

## The 40 sampled ids

Pass unless marked. `†` = abstract stub (body is the docstring only; nothing can
contradict it, so the row passes for free). Paths are relative to `ado2gh/`.

1 `pipelines/resolve/template_resolver.py::_resolve_template_lists` ·
2 `pipelines/transform/job_graph.py::_jobs_for_stage` ·
3 `state/postgres_db.py::PostgresStateDB.inventory_count_for_repo` ·
4 `state/sqlite_profile_scan_mixin.py::ProfileScanMixin.get_profile_scan_repos` ·
5 `state/sqlite_users_mixin.py::PlatformUsersMixin.get_auth_session` ·
6 `phase/batch_executor.py::BatchExecutor.execute_wave` ·
7 `state/postgres_agentic_users_mixin.py::PostgresAgenticUsersMixin.list_platform_users` ·
8 `pipelines/inventory.py::PipelineInventoryBuilder._enrich_build_pipeline` ·
9 `state/postgres_db.py::PostgresStateDB.get_failed_migrations` ·
10 `clients/gh_token_manager.py::TokenManager._get_app_token` ·
11 `pipelines/extractor.py::PipelineMetadataExtractor.extract_release_pipeline` ·
12 `state/postgres_agentic_users_mixin.py::PostgresAgenticUsersMixin.has_repo_in_progress` ·
13 `state/postgres_risk_gates_scan_mixin.py::PostgresRiskGatesScanMixin.upsert_phase_gate` ·
14 `clients/gh_client.py::GHClient.list_branches` ·
15 `clients/ado_token_manager.py::ADOTokenManager.__init__` ·
16† `state/base.py::StateDBBase.update_profile_repo_phases` ·
17 `state/sqlite_users_mixin.py::PlatformUsersMixin.find_pending_live_execution_approval` ·
18 `pipelines/extractor.py::PipelineMetadataExtractor._extract_retention` ·
19 `state/sqlite_agentic_mixin.py::AgenticPlatformMixin.list_audit_events` ·
20 `pipelines/transform/job_graph.py::build_default_jobs` ·
21 `clients/ado_client.py::ADOClient.get_repo_commits` ·
22† `state/base.py::StateDBBase.build_profile_scan_payload` ·
23 `clients/gh_client.py::GHClient.get_repo` ·
24 `state/sqlite_users_mixin.py::PlatformUsersMixin.create_platform_user` ·
25 `state/job_store.py::SQLiteJobStore.fail` ·
26 `clients/ado_client.py::ADOClient._get` ·
27 `pipelines/task_scanner.py::resolve_service_connections` ·
28 `state/sqlite_users_mixin.py::PlatformUsersMixin.create_auth_session` ·
29 `clients/gh_client.py::GHClient._delete` ·
30 `state/sqlite_db.py::SQLiteStateDB.wave_summary` ·
**31 FAIL** `state/sqlite_risk_gates_mixin.py::RiskGatesMixin.count_repos_by_phase` ·
32 `state/sqlite_profile_scan_mixin.py::ProfileScanMixin.save_profile_scan` ·
33 `pipelines/resolve/template_resolver.py::extract_template_refs` ·
34 `state/sqlite_risk_gates_mixin.py::RiskGatesMixin.upsert_batch_checkpoint` ·
35† `state/base.py::StateDBBase.find_pending_live_execution_approval` ·
36† `state/base.py::StateDBBase.count_repos_by_phase` ·
37 `clients/gh_client.py::GHClient.set_branch_protection` ·
38 `pipelines/resolve/template_resolver.py::_merge_extends` ·
**39 FAIL** `reporting/pipeline_readiness.py::PipelineReadinessReport._build_summary` ·
40 `state/sqlite_users_mixin.py::PlatformUsersMixin.get_live_execution_approval`

### Failures

- **31** — the docstring is `Count repositories assigned to a phase; see
  :meth:StateDBBase.count_repos_by_phase`. The body returns a two-key dict
  (`risk_scores`, `profile_scan`); those keys exist only in the *base* method's
  docstring, which a reviewer holding this row alone cannot read. The reviewer only
  produced them because row 36 (the base method) happened to be in the same 40-row
  prompt — a sampling accident, not a working docstring.
- **39** — has **no docstring at all**, yet the inventory row carries `disposition:
  clean`, `tags: []`, no `missing_docstring` tag. The reviewer correctly answered
  "cannot determine"; the body returns a nine-key summary dict (`total_pipelines`,
  `auto`, `assisted`, `manual`, `auto_pct`, `by_type`, `by_complexity`,
  `total_effort_hours`, `total_effort_days`).

## Pattern analysis

Two systematic defects, one per package, and neither is a prose-quality problem — the
prose that exists is good. **`ado2gh/state` writes delegating docstrings** (`"…; see
:meth:StateDBBase.X"`): harmless when the method returns `None`, but it silently
deletes the return contract when it does not. 34 clean `state` rows use the pattern and
**24 of them have a non-void return** (`job_store` `enqueue`/`claim_next`,
`get_pipelines_for_repo`, `get_migration_repo_counts`, `mark_wave_run`,
`count_repos_by_phase`, `update_profile_repo_phases`, …) — every one is a latent SC-008
failure. **`ado2gh/reporting` left private helpers undocumented and the inventory scored
them clean anyway**: 12 of the 15 clean-but-docstringless rows across all committed
packages are in `reporting` (`reporter.py` `_html_*`/`_colour`/`_fmt_ts`,
`post_migration_validator.py` `_check_*`/`_validate_one`, both `_build_summary`s), plus
one in `audit`. Separately, four of the 40 rows are abstract stubs whose body is the
docstring — they cannot fail, so the observed 38 is flattered by roughly one row. Minor
and non-scoring: undocumented side effects (`GHClient._delete` also refreshes rate
limits) and omitted units/defaults (`_extract_retention` sets `retention_days`,
defaulting to 30, and says only "retention period").

## Verdict on T092

**The real gate is at risk.** Across the committed-clean population, 39 of 443 rows
(8.8 %) carry one of the two defects above. Treating the draw as binomial, a 40-row
sample expects ~3.5 failures and clears the ≤ 2 bar only ~30 % of the time — and that is
the *best* case, since it excludes `api`, `agents`, `cli` and `services/*`, which are
not written yet. This rehearsal scored 38 because it drew two of the 39 rather than the
expected three or four; that is luck, not headroom.

Two cheap corrections, both to be decided separately from this rehearsal: give the 24
non-void delegating docstrings their own `Returns:` line (the cross-reference can stay
alongside it), and either document the 15 undocumented clean rows or establish why the
inventory does not tag them `missing_docstring` — if that tagging gap is a scanner bug,
the same rows will pass unnoticed in the four packages still to be written.

## Fixes applied (2026-09-09)

Both defects above were corrected, docstrings only — no signature, name, body or
import changed.

- **`ado2gh/state`** — 26 delegating docstrings with a non-void return gained a real
  `Returns:` section describing the value, keeping the `see :meth:` cross-reference.
  SQLite and PostgreSQL twins of the same base method carry identical wording (FR-011).
  Two more than the 24 this rehearsal counted: the `JobStore` delegations
  (`enqueue`, `claim_next` × three backends) were undercounted here.
- **Undocumented private helpers** — 18 Google-style docstrings added: 14 in
  `ado2gh/reporting` (`reporter.py` × 7, `post_migration_validator.py` × 5, and both
  `_build_summary`s), 1 in `ado2gh/audit`, and 3 nested closures in `ado2gh/pipelines`
  (`inventory._score`, `template_resolver.fetch`, `expressions._macro`) that carry no
  inventory row of their own. The reporting count is 14, not the 12 stated above.

An `ast` walk over the ten committed-clean paths now reports zero functions without a
docstring. `ruff check --select D1,ANN,FBT001,FBT002,PLR0913,B006,ARG,RET501,RET502,RET503`
is clean on all 16 edited files, as is the default rule set; no file approaches the
800-line guard.

**Re-score**: the same 40 ids, the same prompt method, a fresh tool-less
`general-purpose` reviewer (`tool_uses: 0`) — **40 / 40**. Rows 31 and 39 now state
their concrete return values without needing a neighbouring row.
