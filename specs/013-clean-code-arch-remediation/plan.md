# Implementation Plan: Clean-Code Signature Audit & Critical Architecture Remediation

**Branch**: `feature/ado-agentic-ai` (spec id `013-clean-code-arch-remediation`) | **Date**: 2026-09-07 | **Spec**: `specs/013-clean-code-arch-remediation/spec.md`

**Input**: Feature specification from `/specs/013-clean-code-arch-remediation/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Bring every existing function in `ado2gh/`, `services/`, and `apps/migration-ui/src` to the
constitution's clean-code bar (typed, ≤ 5 params, no boolean switches, documented, no dead
code) without changing any external contract, and close every critical and high
architecture gap found by a whole-system assessment. Order is fixed by clarification:
baseline stabilisation (green suite, honest coverage ratchet) → assessment → confirmed
critical gaps fixed immediately → 14 per-package cleanup increments (leaf packages first; a
red increment is reverted, never carried) → high-gap remediation → documentation. Tooling is what the repo already has: the inventory is `ruff` (CI-pinned)
+ `vulture` + one stdlib merge script for Python and one TypeScript-compiler-API script for
the console; the same ruff rule set becomes the regression guard. The only production type
added is `ExecutionMode`, replacing the 65 `dry_run: bool` parameters. Public surface is
frozen by a snapshot test committed before the first increment. Deletion of zero-reference
functions is automatic, braked by a generated protected entry-point list and by the rule
that a name appearing as a string anywhere in the repository counts as a reference
(FR-003a). Inventory rows are named module- and class-level definitions only (FR-001a);
docstrings that render as `--help` or OpenAPI text keep their operator-facing prose and
document parameters through option help and response models instead (FR-010a, R14).

### Completion summary (2026-09-13, T095)

Final measured state at the completion commit. Every figure below was re-measured on the
branch tip; the raw logs are the `run-t09*.txt` files in this directory.

**Inventory (`inventory-summary.md`, git head `9a24b38`).** Python: 1,493 inventoried
functions, 1,459 clean (no tags, no proposals), 2 rows carrying an open proposal. Tag
counts: `bool_data` 14, `bool_flag` 1, `gt5_params` 20, `proposed_bool_flag` 2. Module
rename proposals (FR-013): 220 decided, 1 open. TypeScript console: 204 exports, 196 clean,
29 protected as `next_route_export`; tags `bool_data` 7, `bool_flag` 1. Nothing is suppressed
under FR-003b (0 definitions excluded), so the zero-tag denominator is the whole inventoried
surface.

**Exception register (FR-005a / SC-001).** 27 rows against a cap of 29
(`floor(1493 x 0.02)`) when the cap is taken over the Python inventory alone; the SC-001
script in `quickstart.md` § 5 counts every `inventory.json` row — Python plus the 204
TypeScript exports — and so prints `cap: 33` (`floor(1697 x 0.02)`,
`run-t094-sc001-check.txt`); the 27 rows sit under both caps. The SC-001 check reports
`tagged-not-excepted: 0` — every remaining tagged row is a registered exception and none is
silent.

**Guards at completion.** `ruff check ado2gh/ services/` — 4 findings, all FBT001/FBT002 on
the two route parameters held back for the operator's FR-024 decision
(`services/accelerator_api/routes/profile_routes.py:278` `sync: bool = False`;
`services/accelerator_api/routes/settings_routes.py:443` `scan: bool = False`). The `E9,F821`
hard gate passes clean. `mypy ado2gh/ --ignore-missing-imports` — **0 errors across 195 source
files**, so mypy is a real gate now rather than an advisory run.
`pytest --cov=ado2gh --cov-fail-under=61` — 1,019 passed, 30 skipped, **coverage 61.49 %**
against the ratchet of **61**. The one local failure,
`tests/unit/test_scripts_cleanup.py::test_only_scripts_dev_remains`, is caused by four
untracked developer scratch files in `scripts/dev/`; a clean checkout has only the three
tracked files, so it cannot fail in CI.

**Docstring quality (SC-008).** 40 / 40 on the final seeded sample (`sc-008-sample.md`), up
from 33 / 40 before the round-2 docstring fixes.

**Safeguard-preservation gate (CA-001…CA-004).** All seven checks PASS (`safeguard-gate.md`);
two carry a documented note rather than a bare numeric pass.

**Gap register.** 54 gaps: 16 critical, 22 high, 11 medium, 5 low. **All 16 critical gaps are
remediated.** Of the 22 high gaps, 16 are remediated, 2 are deferred by decision (GAP-018 —
CLI migration commands live by default; GAP-022 — coverage tooling) and 4 remain **open**
pending an operator decision: GAP-019 (`/sessions/{id}/provision` and `/remediate` trust a
client-supplied `actor`), GAP-024 (boolean `recommended_value` stringified server-side inverts
the `confirm_execute` safe default in the browser), GAP-031 (ADO variable-group variables never
reach the generated workflow) and GAP-054 (`DynamoDBJobStore.complete()`/`.fail()` assign
`updated_at` on a `JobRecord` that declares no such field). The "zero critical or high gaps
open" bar is therefore **not** met, and `spec.md` § Status says so explicitly.

**Outstanding operator decisions carried past completion.**

1. ~~The four FR-024 contract changes still awaiting sign-off — GAP-007, GAP-008, GAP-003 and
   GAP-017.~~ **Resolved 2026-09-13**: signed off by operator instruction and now entries 5–8
   of § Approved contract changes. None drifts the four frozen snapshot keys.
2. ~~The two `bool_flag` route parameters named above.~~ **Resolved 2026-09-13**: `sync` on
   `POST /v1/settings/profiles/{id}/scan` is recorded as `bool_data` with a
   `# noqa: FBT001,FBT002` and an exception-register row; `scan` on
   `GET /v1/settings/cloud-credentials` was removed outright (entry 12 of § Approved
   contract changes) rather than excepted.
3. GAP-054: whether `JobRecord` gains `created_at`/`updated_at` fields or the DynamoDB job
   store stops writing them.
4. ~~`services/accelerator_api/routes/_shared.py` — module rename proposal still open.~~
   **Resolved 2026-09-13**: the operator rejected the rename and kept the name
   (`tag-decisions.json`, `--decided-by operator`); the docstring and the contents agree and a
   rename churns 14 import sites for no behaviour change. GAP-036's remaining module-naming
   concern is the `__import__`-based singletons, not the name.
5. ~~`services/agent/routes/_helpers.py` — rename **confirmed** by the review agent.~~
   **Resolved 2026-09-13**: the operator kept the name, superseding the reviewer's confirm
   (`tag-decisions.json`, `--decided-by operator`). A rename does not fix what the reviewer
   objected to — `_build_migration_plan`, `_enqueue_session_live_approval` and
   `_try_start_pev_run` being business logic in a helpers module — so the extraction is filed
   as GAP-080 instead.

**CI.** `.github/workflows/ci.yml` triggers only on `push` to `main`/`master` and on
`pull_request`, so pushing `feature/ado-agentic-ai` produces no workflow run and none could be
watched (`run-t095-ci-1.txt`); the file is not yet on `origin/main` and no pull request exists
for the branch. Both jobs were therefore run locally with the identical commands, recorded in
`run-t095-local-ci.txt`. That stands in for the Python 3.11 requirement honestly rather than
replacing it: `ruff` is pinned to `target-version = "py311"` and `mypy` to
`python_version = "3.11"` in `pyproject.toml`, so both analyses already evaluate the added
annotations under 3.11 semantics whatever interpreter runs them. Firing the workflow itself
still requires opening a pull request.

**Addendum (2026-09-13) — the register grew after T095 measured it.** The gap-register figures
above were accurate at the completion commit and are left as measured; three later review
passes appended eight entries, so the register now holds **62 gaps: 16 critical, 26 high,
13 medium, 7 low**. `902f27e` registered GAP-055…GAP-058 from the T077 behaviour review,
`aa6d17a` registered GAP-059…GAP-061 from the console safeguard review and `493b325`
registered GAP-062 from the T090 and T077-review test runs. Counted from the register's own
`status:` lines, the **26 high** entries are **20 remediated, 2 deferred** (GAP-018, GAP-022)
**and 4 open** (GAP-019, GAP-024, GAP-031, GAP-054) — the same four operator-blocked gaps named
above; all 16 critical entries remain remediated. The eight additions:

| Id | Title | Severity | Status |
|----|-------|----------|--------|
| GAP-055 (GAP-CLI-06) | `ado2gh service-connections` writes an empty manifest: the generator is handed repo objects, not project names | high | remediated |
| GAP-056 (GAP-ACC-07) | The step-label fallback imports `_PIPELINE_STEP_INDEX` from a module that does not have it | low | remediated |
| GAP-057 (GAP-ACC-08) | The Vertex credential probe could never pass: `google.auth.transport.requests` used without importing it | high | remediated |
| GAP-058 (GAP-ACC-09) | T077 regression: the single-repo dry run probes credentials that were never merged and can report COMPLETED | high | remediated |
| GAP-059 (GAP-UI-04) | Live and destructive console actions fire on a single click, three of them recording no reason | high | remediated |
| GAP-060 (GAP-UI-05) | Agent chat transcripts and thinking events persist in localStorage and are never purged on logout | medium | remediated |
| GAP-061 (GAP-UI-06) | A stored proxy password cannot be cleared from the console: a blank field always re-sends the keep sentinel | medium | open |
| GAP-062 (GAP-TOOL-08) | Agent-service tests can deadlock in the Starlette test client under concurrent suite runs | low | open |

No addition opens a new critical or high gap — the two of the eight still `open` are
GAP-061 (medium) and GAP-062 (low). The **"zero critical or high gaps open" bar is therefore
still not met**, for exactly the four operator-blocked gaps already listed: GAP-019, GAP-024,
GAP-031 and GAP-054.

The ratchet moved with them: the 14 tests added by the T077 review lift whole-package coverage
to **62.39 %** (`run-ratchet-62.txt`, 1,033 passed / 30 skipped and, as at T095, 1 failed —
the same local-only `tests/unit/test_scripts_cleanup.py::test_only_scripts_dev_remains`
against the operator's untracked `scripts/dev/` scratch files, which cannot fail in a clean
checkout), so `--cov-fail-under` in
`.github/workflows/ci.yml` is raised 61 → **62** under FR-027a.

**Addendum (2026-09-13) — the Astra branch review appended eight more entries.** The figures
in the previous addendum are superseded: the register now holds **70 gaps: 19 critical, 30
high, 13 medium, 8 low**, counted from its own `- severity:` and `- status:` lines (70 of
each, one per entry). Three review passes since that addendum added `10b1250` GAP-063
(GAP-AUTH-08, a client-quoted `live_approval_id` verified by status alone), `e57eecb`
GAP-064 (GAP-TOKEN-06, exception objects and tracebacks reaching the log handler unmasked) —
neither of which had reached this section — and, from the Astra branch review closed out on
2026-09-13, six more:

| Id | Title | Severity | Status |
|----|-------|----------|--------|
| GAP-065 (GAP-ACC-10) | The `/v1/migrate/*` live guard reads `dry_run` with `bool()`, so a string-spelled `false` is a dry run to the guard and a live migration to the handler | critical | remediated |
| GAP-066 (GAP-AUTH-09) | A refused live pipeline run is persisted before it is authorized, and `/start` takes the orphan live with no operate check | critical | remediated |
| GAP-067 (GAP-AUTH-10) | Approving a `/v1/migrate/*` live run raises `ValidationError` after the decision is committed and before it is audited | high | remediated |
| GAP-068 (GAP-ENG-09) | `MigrationEngine` and `rollback_wave` default their `ExecutionMode` parameter to `LIVE` | high | open |
| GAP-069 (GAP-STATE-06) | The DynamoDB claim-conflict audit can never be written: its writer is built from a factory that raises for that backend | high | open |
| GAP-070 (GAP-AGT-06) | A dead `approved_plan` backward-compatibility alias in the agent guardrails sets `plan_approved = True`, kept alive only by its own tests | low | open |

GAP-065 and GAP-066 are the register's first new criticals since T095; both are remediated,
so **all 19 critical entries remain remediated**. Of the 30 high entries, 22 are remediated,
2 are deferred by decision (GAP-018, GAP-022) and **6 are open**: the four operator-blocked
gaps named throughout this section — GAP-019, GAP-024, GAP-031, GAP-054 — plus GAP-068 and
GAP-069. Both new ones are open for the same reason as the original four rather than a new
one: GAP-068 is the same default-execution-mode policy question the operator already holds
for GAP-018, and GAP-069 turns on where a DynamoDB deployment's audit database should live.
The **"zero critical or high gaps open" bar is still not met**, and for the same cause it has
never been met — an operator decision, not an unfixed defect. The verdict recorded at T095
and in `spec.md` § Status is unchanged.

Measured at the close-out commit: `ruff check ado2gh/ services/` — the same 4 FBT001/FBT002
findings on the two route parameters held for the FR-024 decision, nothing new;
`mypy ado2gh/ --ignore-missing-imports` — `Success: no issues found in 195 source files`;
`pytest --cov=ado2gh --cov-fail-under=62` — **1,056 passed, 30 skipped, coverage 62.47 %**
(`run-close-full.txt`) against the ratchet of 62, with the same single local-only failure,
`tests/unit/test_scripts_cleanup.py::test_only_scripts_dev_remains`, against the operator's
untracked `scripts/dev/` scratch files. The ratchet is left at 62.

**Addendum (2026-09-13) — the Astra security review of the post-fix tree appended five
more entries.** The figures in the previous addendum are superseded: the register now holds
**75 gaps: 20 critical, 33 high, 14 medium, 8 low**, counted from its own `- severity:` and
`- status:` lines (75 of each, one per entry). The review re-read the tree after the
GAP-063…GAP-067 fixes landed and reproduced three findings end to end, plus two it did not
fix:

| Id | Title | Severity | Status |
|----|-------|----------|--------|
| GAP-071 (GAP-AUTH-11) | An approved `migrate_job` executed the client's context, not the scope the approver was shown | critical | remediated |
| GAP-072 (GAP-AUTH-12) | Approval scopes dropped falsy identifiers, so wave 0 was every wave and an empty profile was the default one | high | remediated |
| GAP-073 (GAP-TOKEN-07) | The live-approval context was persisted without masking, so a credential posted into the queue survived verbatim | high | remediated |
| GAP-074 (GAP-AUTH-13) | `_is_https_deployment` lets a client-supplied `X-Forwarded-Proto` drop the session cookie's `Secure` flag | medium | open |
| GAP-075 (GAP-ACC-11) | Feature-route live approvals were profile-blind, so a grant under one profile released the same route under every other | high | remediated |

GAP-071 is the first new critical since GAP-065 and GAP-066; it is remediated, so **all 20
critical entries remain remediated**. Of the 33 high entries, 26 are remediated, 2 are
deferred by decision (GAP-018, GAP-022) and **6 are open** — the same six as the previous
addendum: GAP-019, GAP-024, GAP-031, GAP-054, GAP-068, GAP-069. None of the four new
remediations adds to that list and GAP-074 is medium, so the open-high set is unchanged.
The **"zero critical or high gaps open" bar is still not met**, and for the same cause it
has never been met — operator decisions on FR-024 contract changes, not unfixed defects.
GAP-074 is held for that same reason: a trusted-proxy environment variable is a new name on
the frozen surface, and the alternative "downgrade only, never upgrade" still trusts the
same header in one direction, so which is right depends on the deployment topology the
operator intends. The verdict recorded at T095 and in `spec.md` § Status is unchanged.

Two residuals are recorded inside remediated entries rather than as separate ids, so
neither is lost: the `db_path` key an approved migrate job still takes from its context
(GAP-071 — already client-chosen on the unguarded `POST /v1/plan`, and it names no
migration target), and the `profile_id` that `POST /v1/platform/approvals` still takes from
the client (GAP-075 — it steers attribution and the scope label, not the executor).

Measured at the close-out commit: `ruff check ado2gh/ services/` — the same 4 FBT001/FBT002
findings on the two route parameters held for the FR-024 decision, nothing new;
`mypy ado2gh/ --ignore-missing-imports` — `Success: no issues found in 195 source files`;
`pytest --cov=ado2gh --cov-fail-under=62` — **1,103 passed, 30 skipped, coverage 62.61 %**
(`run-gap-071-full.txt`) against the ratchet of 62, with the same single local-only failure,
`tests/unit/test_scripts_cleanup.py::test_only_scripts_dev_remains`, against the operator's
untracked `scripts/dev/` scratch files. The public-surface snapshot, the 800-line file cap
and the orphan-module guard all pass (`run-gap-071-guards.txt`). The ratchet is left at 62:
62.61 % is the same rounded point it already names.

**Addendum (2026-09-13) — the Sol `ExecutionMode` review appended four more entries.** The
figures above are superseded: the register now holds **79 gaps: 20 critical, 34 high, 16
medium, 9 low**, counted from its own `- severity:` and `- status:` lines (79 of each, one
per entry). The review classified 433 `ExecutionMode` sites — 114 boundary conversions, 98
plumbing, 176 persistence and 45 defaults — and cleared all but the four below; its one
pre-merge blocker was a null `dry_run` reaching the agent's live/dry-run reconciliation:

| Id | Title | Severity | Status |
|----|-------|----------|--------|
| GAP-076 (GAP-AGT-07) | A plan whose `dry_run` key was present and null read as a request to run live, and the value came from the planner model | high | remediated |
| GAP-077 (GAP-AGT-08) | `resolve_dry_run` read a present-but-null flag as live, so a dry run could be validated as though it had written to GitHub | medium | remediated |
| GAP-078 (GAP-ENG-10) | Ten more `ExecutionMode` parameters still default to `LIVE`, beyond the two GAP-068 records | medium | open |
| GAP-079 (GAP-ACC-12) | Two request models pin `dry_run` to a boolean, so the configured `dry_run_default` behind them can never apply | low | open |

GAP-076 is remediated, so **all 20 critical entries remain remediated**. Of the 34 high
entries, 27 are remediated, 2 are deferred by decision (GAP-018, GAP-022) and **6 are open**
— the same six as the previous addendum: GAP-019, GAP-024, GAP-031, GAP-054, GAP-068,
GAP-069. GAP-078 and GAP-079 are medium and low, so the open-high set is unchanged and the
**"zero critical or high gaps open" bar is still not met for the same cause it has never
been met** — operator decisions on FR-024 contract changes, not unfixed defects. GAP-078 is
held against that same decision: it extends GAP-068 from `MigrationEngine` and
`rollback_wave` to the other ten `ExecutionMode` parameter defaults, and deciding it
separately would be deciding one policy three times. Its evidence also records the
`wave_runs.dry_run` column's schema default of `0` (live) in both backends, unexercised
because every `INSERT` supplies the value.

Measured at the GAP-076 fix commit `7d53d9a`: `ruff check ado2gh/ services/` — the same 4
FBT001/FBT002 findings on the two route parameters held for the FR-024 decision, nothing
new; `mypy ado2gh/` — `Success: no issues found in 195 source files`; the targeted run over
`tests/agent` and `tests/unit` for the touched areas — **281 passed, 6 skipped**
(`run-gap-076-targeted.txt`); the public-surface snapshot, the 800-line file cap and the
orphan-module guard all pass (`run-gap-076-guards.txt`). No full-suite run was taken in this
pass — the concurrent GAP-071…GAP-075 pass ran one at `run-gap-071-full.txt` and two
full runs against the same tree deadlock — so the coverage figure and the ratchet are
unchanged from the addendum above.


**Addendum (2026-09-13, remediation batch R1 — GAP-018, GAP-068, GAP-078).** The operator
approved option A of `operator-decisions.md` § 1 by instruction, and that one decision closed
all three default-execution-mode gaps; it is recorded as § Approved contract changes entry 9.
`run`, `phase run` and `ado-cleanup` now declare the Click boolean pair `--dry-run/--live` with
`default=True` (`b9eb89c`); `MigrationEngine.__init__` and `RollbackHandler.rollback_wave`
default to `ExecutionMode.DRY_RUN` (`121a772`); and the remaining ten `ExecutionMode` parameter
defaults follow (`754a82a`), with `mode=` made explicit at the five in-repo call sites whose
behaviour had to stay live — the two `PipelineInventoryBuilder` constructions in
`ado2gh/api/accelerator.py` and `ado2gh/api/migration_scan.py`, whose persisted inventory is the
entire product of the call, and the `mark_wave_run` calls in `ado2gh/core/rollback.py` and
`ado2gh/phase/batch_executor.py`, which previously reached the live default from inside a live
guard. Three `cli_commands` lines moved in `tests/contract/public_surface_snapshot.json`
(`opts=--dry-run` → `opts=--dry-run/--live`, `default=False` → `default=True`); the entry count
stays 96 and the other three frozen keys are untouched. 55 worked examples across `README.md`,
`docs/COMMAND_REFERENCE.md`, `docs/EXECUTION_MANUAL.md`, `docs/MIGRATION_RUNBOOK.md` and
`docs/TROUBLESHOOTING.md` gained `--live`; none was deleted.

Measured at `754a82a`: the targeted run over `tests/core`, `tests/pipeline`, the three new
regression modules, `tests/unit/test_gap_026_execute_phase.py` and the three guard tests —
**271 passed** (`run-r1-targeted.txt`); `ruff check` over every package directory this batch
touched and over `services/` — `All checks passed!`; `mypy ado2gh/ --ignore-missing-imports` —
clean over this batch's files. One full run was taken
(`.venv/Scripts/python.exe -m pytest --cov=ado2gh --cov-fail-under=62 -q`, `run-r1-full.txt`):
**1361 passed, 11 failed, 26 skipped, coverage 64.65 % ≥ 62**. None of the eleven is in this
batch's surface: four belong to `34dce99` (GAP-069), two to the route-test commit `2886cf1`,
four to the concurrent uncommitted agent-package work (`guardrails.py`, `hitl/blockers.py`,
`nodes/orchestrator_tools.py`), and the last is the known local-only
`test_scripts_cleanup.py::test_only_scripts_dev_remains`. Two pre-existing findings outside this
batch were observed and left alone: the working-tree `ado2gh/audit/redaction.py` currently fails
`ruff` (I001, W292) and `mypy` (`return-value` at `:23`), and a stalled full-suite pytest from
06:13 was cleared with `Stop-Process` before this run could start.

**Addendum (2026-09-13, remediation batch R10a — the agent guardrail and orchestrator
threat-model findings).** The figures above are superseded: the register now holds **88 gaps:
20 critical, 38 high, 19 medium, 11 low**, counted from its own `- severity:` lines. R10a took
the eight findings the threat-model hook artefact `threat-model-2026-09-13-001.md` raised
against `ado2gh/agents/migration_agent/guardrails.py`, `nodes/orchestrator.py` and
`nodes/orchestrator_tools.py`, re-measured each one in this tree before editing, and registered
them as GAP-081…GAP-088. Seven are remediated in two commits; GAP-086 is recorded open because
both of its sites live in `ado2gh/agents/migration_agent/tools/`, which a concurrent batch owns.
GAP-070 is closed in the same pass at the coordinator's instruction.

| Id | Title | Severity | Status |
|----|-------|----------|--------|
| GAP-081 (GAP-AGT-10) | The approved-plan scope check was skipped whenever the plan's repo set was empty or its key was misspelled | high | remediated |
| GAP-082 (GAP-AGT-11) | The guardrail's terminal branch allowed any tool absent from all three classification sets | high | remediated |
| GAP-083 (GAP-AGT-12) | The CA-002 confirmation for rollback was never read, so a form submission deleted GitHub resources unconfirmed | high | remediated |
| GAP-084 (GAP-AGT-13) | A falsy session skipped the CA-001 dry-run write blocks entirely | medium | remediated |
| GAP-085 (GAP-AGT-14) | The planner handoffs seeded the session's execution mode from the model's own tool argument | medium | remediated |
| GAP-086 (GAP-AGT-15) | `call_accelerator` defaults its HTTP method to POST, so a tool call that omits `method` writes | medium | open |
| GAP-087 (GAP-AGT-16) | The guardrail had one call site, and the orchestrator's inline tool dispatcher was not one of them | low | remediated |
| GAP-088 (GAP-AGT-17) | ADO-sourced repository names were interpolated into the orchestrator's system prompt | high | remediated |

The common shape across the three high guardrail findings is a control that fails **open**: a
scope check skipped on the degenerate input, an allowlist whose fall-through permits, and a
confirmation nothing server-side reads. All three now fail closed, and the tool-classification
sets are tied to the four tool builders by
`tests/unit/test_guardrails.py::test_every_bound_tool_has_a_guardrail_classification`, so
registering a tool without classifying it fails a test rather than shipping unguarded. All four
of R10a's high entries are remediated, so the batch adds nothing to the open-high set and the
**"zero critical or high gaps open" bar is unaffected** by it.

Two structural consequences are recorded rather than absorbed silently. `nodes/orchestrator.py`
stood at 792 of its 800 permitted lines, and the THR-01-001 fix took it to 837, so the
orchestrator's LLM-turn assembly was split into the new
`ado2gh/agents/migration_agent/nodes/orchestrator_prompt.py` (53 lines; `orchestrator.py` is
789 after the split), with a `docs/STRUCTURAL_CHANGELOG.md` row. The GAP-070 deletion removed
22 test functions — the whole of the import-skipped `tests/unit/test_guardrails_spec011.py`
plus three `@skip`-marked legacy contract tests — and 0 production functions; that changelog
section carries the count and the inventory pointer.

Measured at the two fix commits `e0fe573` and `f8ab25b`:
`ruff check` over the four files this batch owns — `All checks passed!`; `mypy ado2gh/` —
`Success: no issues found in 197 source files`; the targeted run over `tests/agent` and
`tests/unit` for the touched areas — **173 passed, 6 skipped** (`run-r10a-targeted.txt`), with
one unrelated failure, `tests/unit/test_hitl_form_guardrails.py::test_human_input_node_labels_an_unlabelled_payload`,
which belongs to a concurrent batch's untracked test file and passes both in isolation and
alongside this batch's own tests; the public-surface snapshot, the 800-line file cap and the
orphan-module guard all pass (`run-r10a-guards.txt`). No full-suite run was taken in this pass —
batch R1 ran one against the same tree. A read-only Codex `gpt-5.6-terra` review of the diff ran
before the first commit and produced four findings; two were applied (`plan_scope` stringified
non-dict entries so `repos: [None]` authorised the literal target `"None"`; the dispatcher read
`plan_approved` with `bool()` rather than `is True`), one was answered in a comment (the
session-local control tools' read classification) and one is recorded as a residual on GAP-088
(context-window trimming preserves system messages unconditionally but non-system ones only
from the tail).

**2026-09-13 — batch R10b, the agent's tool package and its prompt-building nodes.** Nine
findings from the same threat-model hook artefact, remediated across
`ado2gh/agents/migration_agent/tools/*.py`, `nodes/planner_research.py`,
`nodes/validator_investigation.py` and `nodes/intent.py`. Registered as GAP-089 through
GAP-096, all `remediated`; GAP-086, which R10a had to leave `open` because the file belongs
to this batch, is closed here. Three fix commits: `4b154ba` puts every join of a fixed API
prefix and a model-chosen endpoint through one `join_api_path` helper that normalises the
path the way httpx and uvicorn will and refuses anything that no longer resolves under the
prefix (THR-05-001, reproduced against the installed httpx 0.28.1), encodes `ref`,
`workflow_path` and ADO project names (THR-05-002), replaces every `return {"error":
str(e)}` with a typed, redacted error (THR-02-003) and caps decoded repository file content
(THR-01-002, tool half). `ad218c5` adds
`ado2gh/agents/migration_agent/untrusted.py`, the single envelope that redacts through
`ado2gh.audit.redaction`, caps and fences externally-sourced data before it enters a prompt,
and applies it at the planner research loop, the validator investigation context and loop,
and the discovery repo-name line (THR-02-001, THR-01-001 data half, THR-01-002 prompt half,
THR-01-004); it also replaces the `/discovery` substring test that seeded durable session
state with an exact, percent-decoded route match plus a response-shape check (THR-04-001).
`3fb44df` flips the `call_accelerator` schema default from POST to GET (THR-06-007). Each
fix has a failing-then-passing test and a path-limited stash revert proof
(`run-r10b-revert-tools.txt` 18 failed/10 passed, `run-r10b-revert-nodes.txt` 18 failed/33
passed, `run-r10b-revert-get.txt` 2 failed). Verification on the post-fix tree:
`run-r10b-targeted.txt` (`tests/agent tests/unit -k "tool or planner or validator or intent
or untrusted"`, 191 passed / 4 skipped), `run-r10b-regression.txt` (executor, guardrail,
validator and PEV-loop suites, 81 passed / 2 skipped), ruff clean on `ado2gh/` and
`services/`, mypy `ado2gh/` Success, and the snapshot, 800-line and orphan guards all pass
(`run-r10b-guards.txt`). No full-suite run was taken in this pass. Two read-only Codex
`gpt-5.6-terra` reviews ran before the first commit (`run-r10b-codex.txt`,
`run-r10b-codex2.txt`); four findings were applied — URL userinfo and opaque query
credentials that `redact_text` does not name, a `%2F` mismatch in the discovery gate, the
raw `str(exc)` still left in the validator's tool loop, and the same three defects in
`_execute_planner_tool_call`'s dormant duplicate branches — and one was accepted as a
documented residual on GAP-089 (a reverse proxy performing a second percent-decode would
invalidate the containment check, since the returned path is deliberately left encoded).
Three halves stay open for the batches that own those files and are recorded as follow-ups:
`nodes/orchestrator_tools.py`'s duplicate prefix joins and `/discovery` substring test,
`nodes/planner.py`'s unfenced first-turn prompt, and the agent routes' raw exception
returns.


**Addendum (2026-09-22) — register-pending merge, the register scribe's closing pass.** Eight
hand-off batches queued in `register-pending/` since the last addendum were merged into
`gap-register.md` and this file in one pass: ten new ids, four existing entries closed from
`open` to `remediated`, one existing entry's status corrected from `open` to `deferred`, and
three existing entries' detail blocks brought current with no status change.

Ten new ids, one line each: GAP-098 (GAP-PHASE-05, medium, remediated) — `BatchExecutor`
accepted an executor mode and an injected engine mode that could silently disagree; GAP-099
(GAP-ACC-14, medium, remediated) — `RunWaveRequest`/`PhaseRunRequest.dry_run` defaulted to a
live run on any caller that omitted the field, closed by entry 15 above; GAP-100 (GAP-CLI-07,
medium, remediated) — `ado-cleanup --live` disabled ADO pipelines and could archive the
repository with no confirmation; GAP-101 (GAP-STATE-07, low, remediated) — the connectivity,
settings and LLM-catalog stores resolved their data directory once at construction instead of
per call; GAP-102 (GAP-AGT-26, high, remediated) — plan approval is sticky and survives every
replan (THR-09-002); GAP-103 (GAP-AGT-27, medium, remediated) — form `required` is advertised
but never enforced (THR-09-003); GAP-104 (GAP-AGT-28, medium, remediated) — the HITL resume
payload is relabelled instead of matched (THR-09-004); GAP-105 (GAP-AGT-29, low, remediated) —
a declined blocker stays declined across every later plan (THR-09-005); GAP-106 (GAP-AGT-30,
medium, remediated) — the agent service's `/health` route disclosed the accelerator URL and raw
connection error to any caller (THR-02-004), a new entry distinct from the pre-existing GAP-062
test-client deadlock; GAP-107 (GAP-TOOL-09, low, deferred) — the Vurnix 0.3.0 honest gate
cannot reach PASS on this repository layout: `gate.py:60` hardcodes a `.deps` directory layout
the vurnix CLI has no flag to override, so it reports 224 phantom findings against this tree
without an NTFS junction and 35 residual findings with one, every one of the 35 confirmed
correct as written (24 synthetic test fixtures, 5 sibling-script imports the layout does not
expect, 6 guarded optional imports); the tree itself compiles clean (426 Python files, 7
JavaScript files) and carries 1,157 non-trivial tests, so the residual findings are a tool
limitation, not a code defect, and PASS needs either an upstream vurnix flag or reshaping this
repository to the `.deps` layout the tool assumes — not recommended solely to satisfy the tool.
The register's own ruff/mypy/coverage-ratchet guards remain the compensating control while this
is deferred.

Four existing entries closed from `open` to `remediated` in this pass: GAP-019 (`e5d11c4`, entry
10 above), GAP-024 (entry 11 above), GAP-031 (`8764115`, resolved with no contract change), and
GAP-054 (`d457ff0`, entry 13 above). One existing entry's status corrected from `open` to
`deferred` rather than closed: GAP-069 (`34dce99`) — its DynamoDB claim-conflict audit now logs
`job.audit_unavailable` instead of silently discarding the conflict, but the underlying
question, where such a deployment's audit database should live, is a configuration decision the
operator has not yet made, so R3's fix made the gap visible rather than closing it; its
`compensating_control` and `follow_up` fields record that. Three already-remediated entries'
detail blocks were refreshed with no status change: GAP-086, GAP-089 and GAP-096 (the R10a/R10b
follow-up text merged from `register-pending/r10a-followup.md`).

The mutation re-run on `ado2gh/audit/redaction.py` reproduces `fdeaaa6`'s own 35/41 (85 %)
figure with the same six survivors, confirming the coverage-drift work holds steady; five
commits (`2886cf1`, `5aedb39`, `fdeaaa6`, `178a733`, `a2ce47d`) added roughly 6,500 lines of
test code across 19 test files since the last addendum. R5 closed GAP-074 by gating every
forwarding header behind the new `ADO2GH_TRUSTED_PROXY` setting (entry 14 above) so no client
can decide whether its own session cookie is `Secure`, and GAP-061 by giving operators a
confirmed "Clear stored password" control over the empty-string sentinel the connectivity store
already honoured, with the clear now audited by field name and never by value; it also fixed
the connectivity store's import-time data-directory binding that made route-driven state leak
across a whole process (commit `a863d4b`, folded into GAP-101 above), and folded in five
findings from two read-only reviews (commits `9be088f`, `71efb61`). Batch R10c2 closed GAP-024
by carrying boolean form recommendations as JSON booleans end to end (entry 11 above), and
remediated THR-09-002 through THR-09-005 in the HITL package (GAP-102 through GAP-105 above) —
binding plan approval to a plan-revision fingerprint, enforcing `required` server-side on
submit, rejecting resume payloads that name a different form, and scoping declined blockers to
the revision that raised them. Of the two residuals R10c2 recorded inside otherwise-remediated
entries, the one on `graph/builder.py`/`guardrails.py` (GAP-102) is closed by the R10a
follow-up batch; the one on `services/agent/routes/form_routes.py` — GAP-103's `form_incomplete`
status has no route-level mapping to a 4xx response yet, so an incomplete submission gets a
clarifying re-ask rather than the threat model's named status code — remains, owned by the
batch that touches that file. GAP-103 itself stays `remediated`: the security property, that a
blank required field is never applied to the session, holds regardless of which response shape
carries the refusal.

The combined `ado2gh` + `services` coverage floor remains open for the final gate pass, as
`r9.md` recorded it: `.github/workflows/ci.yml` is owned by a concurrent agent and stays
untouched by this merge.

The open critical-or-high set after this pass is one entry: GAP-069, `deferred`. `spec.md` §
Status is updated in the same pass to match.


## Technical Context

**Language/Version**: Python ≥ 3.11 (`pyproject.toml`; CI runs 3.11); TypeScript 5.9.3 (console, `strict`), Node 22

**Primary Dependencies**: unchanged — Click, FastAPI, Pydantic 2, LangGraph/LangChain, Next.js 14, React 18, vitest 2. Dev-only additions to the local venv: `ruff==0.15.17` (CI pin), `mypy`. Zero new runtime or console dependencies (SC-003).

**Storage**: N/A for runtime (no schema change; `dry_run` columns keep shape). Feature artefacts are files under `specs/013-clean-code-arch-remediation/` (see data-model.md).

**Testing**: `python -m pytest` (`pytest-cov` present), `ruff check`, `mypy`, `npx tsc --noEmit`, `npx vitest run` (38 tests). New: `tests/contract/test_public_surface_snapshot.py`, `apps/migration-ui/src/__tests__/exports-documented.test.ts`, one regression test per remediated gap.

**Baseline suite state (measured 2026-09-07, blocking)**: the Python suite is **red** before this feature starts — 50 failed, 16 errors, 790 passed, 30 skipped in 90 s. Causes are in-flight refactor fallout on `feature/ado-agentic-ai`, not cleanup: 16 errors from `sqlite3.OperationalError: no such table: migration_operations`; contract tests hitting endpoints removed in `0ec95d2`; `ImportError: cannot import name '_gate_payload'`; `SQLiteStateDB` missing an `upsert_dependenc…` method; `AgentState missing required field: llm`; one `ModuleNotFoundError: No module named 'boto3'`. Execution-order step 0 stabilises this before increment 1, because FR-014/SC-010 are unverifiable against a red baseline (research R15).

**Green baseline after step 0 (measured 2026-09-08, T011)**: `807 passed, 30 skipped, 258 warnings in 75.03s` — **0 failed, 0 errors**. **828 tests collected** (`python -m pytest --collect-only -q | tail -1`) — this is the **SC-004 baseline test count**; every later increment compares its collected count against 828 and accounts for the difference in `docs/STRUCTURAL_CHANGELOG.md`. All 65 baseline items were cleared by test-side fixes only; no production file was modified in step 0. Details and the per-group disposition are in [`baseline-failures.md`](./baseline-failures.md).

**Target Platform**: unchanged (Linux containers; Windows local dev — note `python -m pytest`, bare `pytest` shim broken locally)

**Project Type**: CLI + two web services + web console; this feature is a codebase-wide refactor plus architecture remediation, not a product feature

**Performance Goals**: inventory regeneration ≤ 60 s for the whole repo; per-increment verification (`ruff` + `python -m pytest`) ≤ 3 min; no runtime performance change

**Constraints**: every increment leaves the suite green (FR-014); public surface snapshot unchanged except via approved gap fixes (FR-006/FR-024); coverage measured with no omit list and at or above the per-increment ratchet (FR-027a; 85 % remains the outstanding target on G-seed 4, not this feature's gate); exception register ≤ 2 % of functions (SC-001)

**Scale/Scope**: ≤ 1,549 Python functions — an upper bound, because the research walk counted nested `def`s that FR-001a excludes from inventory rows; the first generation sets the real number — (1,142 tagged by heuristic: 1,026 missing docstring, 291 untyped, 62 > 5 params, 69 boolean params) in 195 files, minus whatever the FR-003b exclusion file removes; ~340 console functions in 73 files (227 exports, 28 documented); 24 CLI decorators, 131 HTTP routes, 52 env vars, 25 tables frozen; 13 architecture gap seeds; 14 cleanup increments; 66 baseline test failures to clear in step 0

**Coverage note** (measured 2026-09-07): **60 %** with the current omit list, **56 %** without it — the CI gate of 85 % is already failing at baseline either way. See § Coverage measurement for the ratchet decision this forces.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) | Pre-research | Post-design |
|-----------|-------------------------|--------------|-------------|
| I. Clean Code | Plan describes readable structure; no unjustified complexity | PASS — the feature *is* the clean-code pass; one type added (Complexity Tracking) | PASS |
| II. Documentation | New modules/functions will include purpose/inputs/outputs docstrings | PASS — FR-010 covers every retained function; scripts documented | PASS |
| III. Deprecation | No silent legacy paths; deprecations marked or removed | PASS — dead functions deleted, not deprecated; changelog records each; orphan allowlist stale entry fixed (G-seed 7) | PASS |
| IV. Architecture & Naming | Folder/module names match domain | PASS — FR-013 renames opaque modules; layering inversions assessed (G-seed 3, 11) | PASS |
| V. Enterprise Safeguards | Dry-run/HITL/audit/secrets handling addressed for destructive scope | PASS — CA-001..004 preserved; `ExecutionMode` keeps `DRY_RUN` defaults; masking choke point and live-gate paths are assessment targets (G-seed 1, 2) | PASS |
| VI. Testing (85%+) | Test strategy defined; coverage gate will not regress below 85% on `ado2gh` | **FAIL at baseline, not caused by this feature** — measured: suite red (50F/16E) and coverage 60 % with omits, 56 % without, against a gate asserting 85 % | PASS with documented exception — step 0 restores green (FR-027b); the omit list is deleted and the gate becomes an honest per-increment ratchet (FR-027a, operator decision 2026-09-07); the 85 % shortfall is carried as high gap G-seed 4 with a follow-up owner and recorded in Complexity Tracking |

**Result**: [x] PASS post-design — five principles pass outright; Principle VI passes only
with the documented exception below (honest ratchet instead of 85 %, with the shortfall
registered as a high gap). The pre-research reading of VI is a genuine FAIL against the
current repository, which is what this feature exists to surface.

## Project Structure

### Documentation (this feature)

```text
specs/013-clean-code-arch-remediation/
├── plan.md                          # This file
├── research.md                      # Phase 0: measurements, decisions R1–R16, gap seeds
├── data-model.md                    # Phase 1: artefact entities, ExecutionMode
├── quickstart.md                    # Phase 1: validation runbook
├── contracts/
│   ├── public-contract-freeze.md    # frozen surfaces + snapshot-test contract + ExecutionMode boundary rules
│   └── artifact-schemas.md          # file layout, script CLIs, register layout, review-agent handoff, changelog entry format
├── checklists/requirements.md
├── scripts/                         # created in implementation
│   ├── function_inventory.py        # ruff + vulture + ast merge → inventory.json (stdlib only)
│   └── function_inventory_ts.mjs    # TypeScript compiler API walker (no new deps)
├── inventory.json · inventory-summary.md · inventory-history.jsonl
├── tag-decisions.json               # review-agent judgment decisions (FR-002b, FR-013)
├── protected-entry-points.json · protected-entry-points.manual.txt
├── exception-register.md
├── gap-register.md
├── baseline-failures.md · inventory-spotcheck.md · sc-008-sample.md   # evidence artefacts (T004, T019, T092)
└── tasks.md                         # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

No new packages. Files touched, by increment (research R6):

```text
ado2gh/
├── models.py                    # + ExecutionMode (R7); increment 1
├── http_utils.py · logging_config.py · output_dirs.py · assignments/   # increment 1 (assignments/ → audit/ candidate, FR-013)
├── clients/                     # increment 2
├── state/                       # increment 3  (+ factory.py backend-selection review, G-seed 6)
├── phase/                       # increment 4
├── pipelines/                   # increment 5
├── reporting/                   # increment 6
├── core/                        # increment 7  (git_scope._redact → shared masking choke point if G-seed 1 confirmed)
├── auth/                        # increment 8
├── api/                         # increment 9  (agentic_routes.py placement, G-seed 11)
├── agents/migration_agent/      # increment 10 (tools/ signatures free to change; prompts + fixtures updated together)
└── cli/                         # increment 11 (--dry-run flags stay; boundary conversion)
services/
├── accelerator_api/             # increment 12 (routes/_shared.py __import__ singletons, G-seed 8)
└── agent/                       # increment 13
apps/migration-ui/src/           # increment 14 (+ __tests__/exports-documented.test.ts; tsconfig noUnusedParameters)

tests/
├── contract/test_public_surface_snapshot.py + public_surface_snapshot.json   # before increment 1
├── unit/test_function_inventory_script.py · unit/test_execution_mode.py      # tests for new code (US1, increment 1)
├── unit/test_no_orphaned_modules.py                                          # allowlist updates per move/delete
└── <domain>/test_gap_NNN_*.py                                                # one per remediated gap

# Conditional on register outcomes (gap fixes, FR-013 moves): services/accelerator_api/routes/history_routes.py,
# services/agent/routes/_helpers.py, ado2gh/audit/ (from ado2gh/assignments/) — each logged in the structural changelog

pyproject.toml                   # coverage omit list deleted at step 0 (FR-027a, T011); ruff rule set + pydocstyle convention after last increment (R4)
.github/workflows/ci.yml         # --cov-fail-under = honest ratchet, raised per increment (FR-027a); mypy no longer "|| true" (per GAP-fix)
docs/STRUCTURAL_CHANGELOG.md · docs/ARCHITECTURE.md · CLAUDE.md               # final increment
```

**Structure Decision**: Existing layout retained; moves happen only through FR-013
(opaque names) or registered gap fixes, each logged in the structural changelog. Feature
tooling lives inside the spec directory so it does not become a runtime module or trip the
orphan-module guard.

## Execution order (binding — from clarifications)

0. **Stabilise the baseline** (blocking, research R15): fix or delete the 66 failing/erroring tests so `python -m pytest` is green before anything else. Tests that exercise only endpoints or modules removed by earlier commits are deleted under FR-009 with a changelog row; genuine breakage (`migration_operations` table, missing state method, `AgentState.llm`, `_gate_payload` import) is fixed; `boto3` is either added to the optional extra it belongs to or its test is skipped when absent. Each fix is its own commit, separate from cleanup. Exit gate: green suite, the collected count recorded as the real baseline for SC-004, the coverage `omit` list deleted from `pyproject.toml`, and `--cov-fail-under` in `ci.yml` set to the honest figure measured on the green suite (FR-027a) — the ratchet is live before increment 1 and is raised in every increment's verify step.
1. **Baseline & freeze**: install CI-pinned ruff; commit `public_surface_snapshot.json`; generate inventory (py + ts); the review agent decides the first judgment-tag and `module_name_review` proposals, recorded into `tag-decisions.json` (FR-002b); record the post-stabilisation Python test count and the 38 console tests.
2. **Assessment** (US3): write `gap-register.md` from the FR-016 component list, the FR-016a deployment/CI artefacts (compose, Dockerfiles, workflows, `.env.example` — high at most), and research R9 seeds; review-agent pass; operator resolves disputes.
3. **Critical remediation** (US4, FR-022): every CONFIRMED critical gap is fixed now — regression check + one-time revert proof recorded — before cleanup touches that code.
4. **Cleanup increments 1–14** (US1/US2): per research R6, each ending green with a changelog section; a red increment is reverted, re-scoped, and retried (FR-014a); the 2 % exception cap stops the offending rule until the operator decides (FR-005a). `ExecutionMode` lands in increment 1 and is threaded through as each package is reached. Critical-gap regression checks stay green throughout.
5. **Guards on** (R4): ruff rule set into `pyproject.toml`, vitest export-doc test, `noUnusedParameters` (the coverage ratchet has been live since step 0).
6. **High remediation** (US4): high gaps in register order, one regression check each, revert proof recorded once; contract-changing fixes only after being listed under § Approved contract changes and plan re-approval.
7. **Documentation & final gates** (US5): ARCHITECTURE.md, CLAUDE.md, README.md, the rest of `docs/`, changelog, orphan allowlist (completed specs 001–012 are history and are not rewritten); SC-007 script and SC-008 agent sample (R13) from quickstart § 5; final inventory regeneration with zero pending proposals.

## Approved contract changes

Any remediation needing a public-contract change is appended here with its `GAP-NNN`, the
exact change, and the migration note, and the plan is re-approved before the change is
applied (FR-024). The full text of each entry lives in
`contracts/public-contract-freeze.md` § Approved contract changes; this section is the
plan-level record of what the operator agreed to and when.

**1. Live execution requires an authenticated approver (GAP-002, GAP-005). Decision
(operator, 2026-09-08): approve as-is.** The live-execution approval guards no longer
consult `auth_enabled()`. Previously they stood down whenever `ADO2GH_AUTH_ENABLED` was
unset — the shipped default — which left the approval gate inert in the configuration most
deployments actually run, so an identity-less request could start an irreversible migration
with no human approval. Live execution now requires `ADO2GH_AUTH_ENABLED=true` **and** a
signed-in ADMIN or APPROVER; a live request with no identity is refused with 401 where it
previously proceeded, and a signed-in caller who can operate but not approve — OPERATOR and
now COORDINATOR, which used to escape the role-name check — is routed into the approval
queue. Affects `ado2gh/api/platform_rbac.py` (`operator_requires_live_approval`,
`require_approve_live_execution`) and `ado2gh/agents/migration_agent/policies.py`
(`can_execute_live_without_approval`, `session_requires_live_approval`).

Dry-run is unaffected: both guards return before the identity check when the request or
session is a dry run, so auth-disabled local and CI environments keep working for
everything reversible. Every ordinary read/operate guard keeps its `auth_enabled()`
short-circuit; only the two live-execution guards became identity-independent.

*Migration note.* A deployment running with `ADO2GH_AUTH_ENABLED=false` that never executes
live needs no action. One that does execute live must set `ADO2GH_AUTH_ENABLED=true` and
create an ADMIN or APPROVER account before upgrading; otherwise a live migration that ran
before the upgrade now fails with 401 and nothing is migrated. Set `ADO2GH_INTERNAL_TOKEN`
in the same pass — with auth on, the agent's `/v1/internal/` approval-resume routes fail
closed without it (GAP-003).

*Snapshot impact: none.* No route path, CLI command, environment variable name or table name
changes, so `tests/contract/public_surface_snapshot.json` needs no edit for this change.

**2. `/v1/settings/llm-models/catalog` moves from GET to POST (GAP-012). Decision (operator,
2026-09-08): approved.** The provider API key was travelling in a URL query string, where it
is written to browser history, captured in HAR exports and recorded in the access log of
every proxy on the path, in clear text (CWE-598). `get_llm_catalog` in
`services/accelerator_api/routes/settings_routes.py` now takes `provider`, `api_key` and
`base_url` in a JSON body instead, matching the sibling
`POST /v1/settings/llm-models/validate`. The migration console was updated in the same
change — `apps/migration-ui/src/lib/llmSettings.ts` issues the POST — so the in-repo caller
needs no further action.

*Migration note.* A caller still issuing
`GET /v1/settings/llm-models/catalog?provider=...&api_key=...` now gets **404** and must
switch to `POST /v1/settings/llm-models/catalog` with a
`{"provider", "api_key", "base_url"}` body. The response shape and the `manage_models`
capability requirement are unchanged. External scripts and saved request collections that
drive the catalog endpoint have to be updated.

*Snapshot impact: yes — the only drift in the working tree, on `http_routes`.*
`accelerator GET /v1/settings/llm-models/catalog` is removed and
`accelerator POST /v1/settings/llm-models/catalog` is added; the entry count stays at 148.
With this approval the snapshot edit is authorised, and T040 applies it in the same commit
as the fix. The exact two-line edit is written out in
`contracts/public-contract-freeze.md` § Snapshot edit authorised for T040.

**3. Forcing past a blocked phase gate becomes a two-step CLI workflow (GAP-009). Decision
(operator, 2026-09-08): approved, and no `--reason` flag is added to `phase run`.** Adding
an option would drift the frozen `cli_commands` surface, so the operator chose to keep the
CLI surface clean and accept two steps instead. This is deliberate, not an oversight: it
makes overriding a gate its own separately audited act rather than a side effect of running
a phase, so the audit log shows a named person overriding a specific gate for a stated
reason rather than a `--force` buried among a run's flags. To get past a blocking gate, the
operator now runs `ado2gh phase gate-check --phase <prior> --override --reason "..."` first,
then `ado2gh phase run --phase <next> --config ...`. Underneath,
`ado2gh/api/accelerator.py` always evaluates the prior-phase gate and `force` escalates it
through the audited `PhaseGateChecker.override` path rather than skipping it;
`ado2gh/api/contracts.py` gains `PhaseRunRequest.override_reason`.

*Migration note.* Any runbook that uses `ado2gh phase run --force` to push past a red gate
must change to the two-step form. `--force` alone now fails closed — the run is refused with
`Gate blocked for prior phase <name>: forcing past it requires override_reason` and no
migration starts. The refusal is safe, but an unattended pipeline relying on `--force` stops
there until the override is recorded. `--force` still works where the prior gate is not
blocking.

*Snapshot impact: none.* No CLI option was added, removed or retyped, and `override_reason`
is a request-model field, which is not one of the four frozen keys.

**4. Pipeline-run request bodies: `agent_live_approved` removed, unknown fields rejected
(GAP-004). Decision (operator, 2026-09-08): approved**, as two decisions taken after the
implementing agent finished and measured the result. `agent_live_approved` let a caller
assert its own live authority in a request body and skip the approval queue before its
capability was consulted; live authority is now derived only from server-side state.
`PipelineRunStartRequest` additionally sets `extra="forbid"`, so a caller still sending the
field to `POST /v1/pipeline/runs` gets **422** with `detail[].loc == ["body",
"agent_live_approved"]` and `type: "extra_forbidden"` — a loud failure was chosen over
silently ignoring the field, which would have left callers believing they were still
skipping the queue. That strictness applies to **any** undeclared field on that endpoint,
not just this one, so every future field addition there is a coordinated server-and-caller
change. `PipelineRunStartApprovedRequest` held nothing but the removed field and was deleted
outright with no shim, so `POST /v1/pipeline/runs/{run_id}/start` now declares no body
parameter at all: `{}`, a body carrying the old field, and no body are identical and none is
rejected, and `extra="forbid"` does not apply there.

`override_reason: str = ""` was added to `PipelineRunStartRequest` in the same change — the
console had already begun sending it as the GAP-009 gate justification, so under
`extra="forbid"` every console-initiated pipeline run would have returned 422. It is
optional, defaulted and backward-compatible, and is deliberately excluded from
`PipelineRun.to_dict()`: the value is free operator text redacted only at the persist point,
so echoing it in the run response would return unredacted text (CA-003). That coupling is
permanent — any future field on this model that can carry free text or a secret must be
checked against `to_dict()` and the redaction point before it is added.

*Migration note.* A client still sending `agent_live_approved` to `POST /v1/pipeline/runs`
must stop, and must obtain a real approval instead; sending any other undeclared field there
now also fails with 422. A client sending it to `POST /v1/pipeline/runs/{run_id}/start` keeps
working and the field simply has no effect.

*Snapshot impact: none.* Request-model fields, model deletions and validation strictness are
not among the four frozen keys.

**5. Nine `/v1/migrate/*` routes gain a live-execution guard (GAP-007). Decision (operator,
2026-09-13): approved by operator instruction.** An unauthenticated live request to any
`/v1/migrate/*` route now gets **401**, and a signed-in OPERATOR or COORDINATOR gets **403**
carrying an `awaiting_approval` `approval_id` instead of the route proceeding; dry-run requests
pass through unchanged. Applied in `36edbc3` —
`services/accelerator_api/routes/migrate_guard.py` hangs `guard_live_migration` off the
`/v1/migrate` router. Full text and migration note:
`contracts/public-contract-freeze.md:291-305`.

*Snapshot impact: none.* The guard changes who gets an answer, not any route path.

**6. GitHub write proxy requires `can_approve_live_execution` (GAP-008). Decision (operator,
2026-09-13): approved by operator instruction.** Proxy writes — POST, PATCH, PUT and DELETE —
now require `can_approve_live_execution` and are audited before being forwarded; reads are
unaffected. Applied in `a6309ea` — `services/accelerator_api/routes/proxy_routes.py` is split
by verb. Full text and migration note: `contracts/public-contract-freeze.md:319-333`.

*Snapshot impact: none.* The split changes who gets an answer, not any route path.

**7. Agent `/v1/internal/` fails closed without `ADO2GH_INTERNAL_TOKEN` (GAP-003). Decision
(operator, 2026-09-13): approved by operator instruction.** `ADO2GH_INTERNAL_TOKEN` becomes
mandatory wherever `ADO2GH_AUTH_ENABLED=true`. A deployment that forgets it sees approvals
accepted in the console while the run silently never resumes, which is the failure mode the
operator is accepting in exchange for the routes no longer standing open. Applied in `8285f0c`
— `services/agent/main.py:100-103` reads the variable and warns when it is unset. Full text and
migration note: `contracts/public-contract-freeze.md:353-368`.

*Snapshot impact: none.* `ADO2GH_INTERNAL_TOKEN` is already in the frozen `env_vars` list.

**8. `phase gate-check --override` rejects an empty `--reason` (GAP-017). Decision (operator,
2026-09-13): approved by operator instruction.** `--override` with an empty `--reason` is now a
clean `click.UsageError` where it previously raised `TypeError` and wrote no gate row at all.
Applied in `ceb6b0b` (fix) and `aefea23` (test) — `ado2gh/cli/phase.py:166-168` raises, and
`tests/unit/test_gap_017_gate_check_signature.py` covers it. Full text and migration note:
`contracts/public-contract-freeze.md:372-378`.

*Snapshot impact: none.* The change adds, removes and retypes no CLI option.

**9. `run`, `phase run` and `ado-cleanup` default to dry-run and require `--live` to execute
(GAP-018, GAP-068, GAP-078). Decision (operator, 2026-09-13): approved by operator instruction.**
The three migration commands declared `--dry-run` as `is_flag=True, default=False`, so each
mutated unless the operator opted out, and none prompted for confirmation. On the CLI path that
default was the only guard in existence: the approval-token check landed in `5f799e7` fires only
when a request quotes a `live_approval_id`, and `ado2gh/cli/migration.py` never sends one. Each
option becomes `--dry-run/--live` with `default=True` in `ado2gh/cli/migration.py`,
`ado2gh/cli/phase.py` and `ado2gh/cli/misc.py`; `--dry-run` keeps its name and its meaning, and
`ExecutionMode.from_dry_run(dry_run=…)` still converts at the boundary. The same decision settles
the twelve `ExecutionMode` parameter defaults held against it: `MigrationEngine.__init__` and
`RollbackHandler.rollback_wave` (GAP-068) and the ten in `ado2gh/core/ado_cleanup.py`,
`core/scopes/base.py`, `core/wave_runner.py`, `phase/batch_executor.py` (two),
`pipelines/inventory.py`, `pipelines/push_workflows.py` (two), `state/base.py`,
`state/sqlite_db.py` and `state/postgres_db.py` (GAP-078) all move from `ExecutionMode.LIVE` to
`ExecutionMode.DRY_RUN`. Applied in `b9eb89c` (GAP-018), `121a772` (GAP-068) and `754a82a`
(GAP-078).

*Migration note.* Any script or runbook that ran these three commands without `--dry-run` and
expected a real migration must add `--live`. Until it does, the command reports what it would
do and changes nothing — a safe failure, but an unattended pipeline will appear to succeed
while migrating nothing. Scripts that already pass `--dry-run` are unaffected. For the twelve
signatures, every shipped in-repo call site already passed `mode=` explicitly or was changed to
do so in the same commit, so nothing in this repository changes at runtime; an out-of-tree SDK
consumer that omitted the argument now previews instead of migrating. The persisted
`wave_runs.dry_run` column keeps its polarity exactly — only what an omitted argument records
changed, never how a stated mode is serialised.

*Snapshot impact: yes — three lines on `cli_commands`.* The `dry_run` param line for
`ado2gh run`, `ado2gh phase run` and `ado2gh ado-cleanup` each change `opts=--dry-run` to
`opts=--dry-run/--live` and `default=False` to `default=True`. The entry count stays at 96;
no command or option is added or removed. The twelve signature defaults touch no frozen key.
Verified by `tests/contract/test_public_surface_snapshot.py`; behaviour is covered by
`tests/unit/test_gap_018_dry_run_default.py`, `tests/unit/test_gap_068_execution_mode_defaults.py`
and `tests/unit/test_gap_078_execution_mode_defaults.py`.

**10. `ProvisionRequest.actor` is removed; `/sessions/{id}/provision` and `/remediate` derive
the actor server-side (GAP-019). Decision (operator, 2026-09-13): approved by operator
instruction.** `provision_session` and `remediate_session` took no `Request` parameter and
granted the write provisioning tier based solely on a client-supplied `req.actor` string with
no binding to an authenticated identity; `remediate_session`'s escalation counter came from the
equally client-supplied `req.retry_count`, so a client could reset its own escalation count by
resending a low value. Both routes now take the `Request`, derive the actor and role from the
authenticated platform user through `_audit_actor`, and require live-approval authority for the
write tier; the escalation counter is read from the session's own `remediation_attempts`
instead. `ProvisionRequest.actor` is removed from the request model entirely, closing the trust
gap by removing the field rather than validating it. Applied in `e5d11c4`.

*Migration note.* A caller that sent `actor` in the `ProvisionRequest` body now gets it
silently ignored — the model does not set `extra="forbid"`, so the request still succeeds, but
the actor recorded is always the authenticated platform user, never the value the client sent.
Any external caller relying on setting an arbitrary `actor` string must instead authenticate as
the identity it wants recorded.

*Snapshot impact: none.* Neither route path, method, CLI command, environment variable nor
database table changes; a request-model field removal is not one of the four frozen keys.
Verified: `tests/contract/test_public_surface_snapshot.py` passes unchanged; the fix is covered
by `tests/auth/test_gap_019_provision_actor_server_side.py` (8 tests).

**11. Form fields carry boolean `recommended_value` as a JSON boolean (GAP-024). Decision
(operator, 2026-09-13): approved by operator instruction.** `field_dict_from_spec` coerced
every recommended value through `str(...)`, so the planner's `recommended_value: False` for
`confirm_execute` reached the console as the string `"False"`, and `Boolean("False")` is
`true` — the live-execution confirmation checkbox rendered pre-checked against the backend's
intent. `ado2gh/agents/migration_agent/hitl/form_fields.py` and `.../hitl/forms.py` now pass a
`bool` through unchanged and stringify only non-boolean values, through the single
`normalize_recommended_value` helper both files share; `build_field_recommendations` emits the
real boolean for `dry_run` instead of `"true"`/`"false"`.
`apps/migration-ui/src/lib/agent.ts` widens `AgentFormField.recommended_value` back to
`string | boolean` — GAP-059 had narrowed it to `string` to match the wire shape of the time —
and `apps/migration-ui/src/lib/agentChat.ts` keeps `parseBooleanValue` as defence in depth
while casting a boolean to its string form for a select, because `dry_run` is a boolean field
drawn as a select whose option values are strings. The `confirm_execute` fallback for a field
with no recommendation already returned unchecked, under GAP-059.

*Migration note.* Any client rendering agent HITL forms must accept `true`/`false` as well as
`"true"`/`"false"` for `recommended_value`; one that applied `Boolean(...)` to the string form
will now, correctly, see an unchecked confirmation box. No server-side authority changes: live
execution was and remains re-checked from server-held state before any run, independent of the
submitted `confirm_execute`.

*Snapshot impact: none.* No route path, CLI command, environment variable or table name is
touched; the payload field type is not one of the four frozen keys. Verified:
`tests/contract/test_public_surface_snapshot.py` passes unchanged.

**12. `GET /v1/settings/cloud-credentials` no longer accepts `scan`. Decision (operator,
2026-09-13): approved by operator instruction.** The listing endpoint accepted `?scan=true` to
re-probe the host for cloud credential sources before answering. It performed exactly the probe
that `POST /v1/settings/cloud-credentials/scan` performs, but wrote no audit record, so the
console's page load — `fetchCloudCredentials(true)` on every render and every refetch — was
probing the host outside the audit trail (CA-004). The parameter and its branch are removed from
`services/accelerator_api/routes/settings_routes.py` with no shim (FR-006a);
`apps/migration-ui/src/lib/cloudCredentials.ts` drops the argument and the console's existing
Rescan button, already wired to the audited POST, becomes the only way to re-probe.

*Migration note.* A client still sending `?scan=true` gets the listing without a probe —
FastAPI ignores an undeclared query parameter, so nothing errors, but the response reflects the
last recorded detection rather than a fresh one. Any caller that relied on the flag must issue
`POST /v1/settings/cloud-credentials/scan` first, which needs the same `can_manage_models`
capability and additionally writes a `cloud_credentials.scanned` audit event.

*Snapshot impact: none.* The route path and method are unchanged; query parameters are not
recorded in `http_routes` and are not among the four frozen keys. Verified by
`tests/contract/test_public_surface_snapshot.py`; the removal is covered by
`tests/contract/test_cloud_credentials_contracts.py::test_listing_never_probes_the_host`.

**13. `JobRecord` gains `created_at` and `updated_at` (GAP-054). Decision (operator,
2026-09-13): approved.** Every job store already passed both timestamps to the `JobRecord`
constructor and `DynamoDBJobStore._save` already read `record.created_at`, but the model
declared neither field, so pydantic dropped them at construction and raised on assignment.
`DynamoDBJobStore.enqueue` therefore failed on its first write, and `complete()`/`fail()` each
raised `ValueError` — which, through the worker's catch-then-`fail()` fallback in
`ado2gh/core/orchestration/worker.py`, killed the worker process instead of recording one
failed job. `ado2gh/models.py` now declares both fields with UTC `default_factory` defaults,
and the ten `# type: ignore` comments added across `ado2gh/state/job_store.py` to suppress the
symptom are removed.

*Migration note.* The job payload returned by `POST /v1/jobs` and `GET /v1/jobs/{job_id}` gains
two ISO-8601 timestamp keys, `created_at` and `updated_at`, when serialized through
`model_dump(mode="json")` or returned by a FastAPI response — a plain `model_dump()` keeps them
as `datetime` objects (`tests/unit/test_gap_054_job_record_timestamps.py:175`). No in-repo
client reads these routes; an external client that asserts an exact key set on the job object
must be widened. No stored schema changes — the SQLite and Postgres `jobs` tables already carry
both columns, and this change only stops discarding them on the way out.

*Snapshot impact: none.* Both route paths are unchanged, no table is added or renamed, and
model fields are not among the four frozen keys.

**14. New environment variable `ADO2GH_TRUSTED_PROXY` (GAP-074). Decision (operator,
2026-09-13): approved by operator instruction ("Remediate all findings").** GAP-074 found that
`_is_https_deployment` let a client-supplied `X-Forwarded-Proto` decide whether the
platform session cookie carries `Secure`, so any client could downgrade its own session
credential to one a network attacker can read. The scheme the request actually arrived on
cannot be overridden by a header any more; a deployment that terminates TLS at a reverse
proxy opts in with `ADO2GH_TRUSTED_PROXY` (boolean, `1`/`true`/`yes`, default false), and
only then is the last forwarded hop believed — the last element of the last
`X-Forwarded-Proto` field, or of RFC 7239 `Forwarded` when that header is absent. Default
false keeps every existing local and compose deployment on exactly the behaviour it had.
Recorded in `tests/contract/public_surface_snapshot.json` `env_vars`, `.env.example` and
`docs/LOCAL_DEVELOPMENT.md` § 8; commit `e6129a8`.

*Snapshot impact: yes — one name added to `env_vars`.* `ADO2GH_TRUSTED_PROXY` joins the frozen
list; no route, CLI command or table changes. Verified:
`tests/contract/test_public_surface_snapshot.py` passes with the added entry recorded.

**15. `RunWaveRequest.dry_run` and `PhaseRunRequest.dry_run` default to true (GAP-099).
Decision (operator, 2026-09-13): approved by operator instruction ("Remediate all
findings") — the same blanket approval already recorded for the other findings-driven
contract changes in this batch.** Both fields previously defaulted to `false`, so a
`POST /v1/migrate` or `POST /v1/phase/run` body that omitted `dry_run`, or a `POST /v1/jobs`
body naming the migrate-repo job type whose payload the background worker forwards into
`RunWaveRequest` unchanged (`ado2gh/core/orchestration/worker.py`), ran for real. Entry 9 above
already flipped the three CLI flags and twelve internal `ExecutionMode` signature defaults to
preview-first; this closes the same gap on the two Pydantic request bodies those internal
signatures sit behind, which entry 9 did not touch. `ado2gh/api/contracts.py:38`
(`RunWaveRequest.dry_run`) and `:67` (`PhaseRunRequest.dry_run`) now default to `true`. Applied
in commit `bd5a03c`.

*Migration note.* An external caller that builds either body from the OpenAPI schema or a
hand-written request and relied on an omitted `dry_run` field running for real must now add
`"dry_run": false` explicitly. Every shipped in-repo caller (the CLI, the console's step
executor) already stated the field, so nothing in this repository changes behaviour at runtime;
a queued migrate-repo job whose stored payload omitted the field previously ran live and now
previews.

*Snapshot impact: none.* `tests/contract/test_public_surface_snapshot.py` freezes CLI commands,
HTTP routes (path and method), environment variables and DB table names; it does not freeze a
Pydantic request field's default, so no snapshot edit is needed. Confirmed green in the
targeted verify run (commit `bd5a03c`).

**Contract changes still awaiting sign-off.** None. The four that were — GAP-007, GAP-008,
GAP-003 and GAP-017 — were signed off by operator instruction on 2026-09-13 and are entries
5–8 above.

## Coverage measurement

| Configuration | Result (measured 2026-09-07, red baseline suite) |
|---------------|--------|
| Current `pyproject.toml` (omits `cli/`, `state/`, `core/`) — what CI's `--cov-fail-under=85` sees | **60 %** (15,036 statements, 6,088 missed) |
| No omit list (`--cov-config` pointing at a config with `source = ado2gh` and no `omit`) | **56 %** (18,017 statements, 7,885 missed) — the omitted `cli/`, `state/`, `core/` add 2,981 statements and cost 4 points |

Two consequences. (1) The constitutional 85 % gate is **already failing** at baseline
(60 %, matching the 60.98 % recorded in the structural changelog on 2026-06-24), so CI's
coverage job cannot be green today regardless of this feature — that is G-seed 4, rated at
least *high*.

**Decision (operator, 2026-09-07): ratchet from the honest baseline.** The `omit` list is
deleted from `pyproject.toml`; `--cov-fail-under` is set to the true figure measured on
the green post-stabilisation suite (56 % is the red-suite reading, so the real starting
gate is measured again at the end of step 0) and raised to the new measured value after
each increment, never lowered. Reaching 85 % is **not** in this feature's scope: G-seed 4
stays in the register as a high gap with a time-boxed follow-up owner, its resolution being
"honest measurement restored plus a ratchet", and FR-027's threshold is read as "the
ratchet value at completion, with the constitutional 85 % recorded as the outstanding
target". Every increment must therefore leave coverage at or above the previous
increment's number.

**Ratchet starting value (measured 2026-09-08 on the green post-step-0 suite): 56 %**
(18,017 statements, 7,855 missed, 56.40 % exact). The `omit` list is gone from
`pyproject.toml` and `.github/workflows/ci.yml` now runs
`pytest --cov=ado2gh --cov-fail-under=56`. Reading versus the red-baseline measurement:
the same 18,017 statements, 30 fewer missed — clearing the 65 failures moved the honest
figure by less than half a point, which is expected, since the repairs were test-side.

Measurement note (Windows local only, does not affect CI): under coverage instrumentation
on Python 3.14 the suite intermittently deadlocks in a `TestClient` request — the anyio
blocking portal's loop goes idle while the calling thread waits on its future (observed at
`tests/agent/test_agent_ide_sessions.py::test_session_message` and
`tests/feature/test_agent_pev_quickstart_scenarios.py::test_health_includes_remediation`,
a lost `call_soon_threadsafe` wakeup on the Windows Proactor loop). Setting
`COVERAGE_CORE=sysmon` and forcing `WindowsSelectorEventLoopPolicy` via a throwaway
`-p` plugin makes the run reliable (82.71 s). CI is Linux/Python 3.11 and is unaffected;
neither setting is committed.

**Ratchet trail (T075), as of HEAD `7396829`.** The value of `--cov-fail-under` in
`.github/workflows/ci.yml` after each step, checked against the `013 Increment N`
sections of `docs/STRUCTURAL_CHANGELOG.md` and against
`git log -p -- .github/workflows/ci.yml`:

| Point | `--cov-fail-under` | Evidence |
|-------|--------------------|----------|
| pre-013 | 85, asserted over an `omit` list that hid `cli/`, `state/` and `core/` | `34f872b` |
| T011 — honest baseline, `omit` deleted | **56** | `9c83a29` |
| Increment 1 (`ado2gh` root + `audit/`) | 56 → **58** | `0ab1ac9`, changelog line 362 |
| Increment 2 (`clients/`) | **58** — measured 58, so not raised | changelog line 397 |
| Increment 3 (`state/`) | 58 → **59** | `7eba3d7`, changelog line 432 |
| Increments 4, 5, 6, 8, 7, 13, 14, 9 | **59** at each; increment 8 measured 58 and was correctly not lowered | changelog lines 460, 492, 523, 561, 619, 703, 810 |
| HEAD `7396829` | **59** (`ci.yml:36`) | — |
| Increment 10 (`agents/`) | 59 → **60** | `ca3f3b2` |
| Increment 12 (`services/accelerator_api/`) | 60 → **61** | `ef7a6c7` |
| T077 review tests (2026-09-13) | 61 → **62** | `run-ratchet-62.txt`, measured 62.39 % |

The gate has never been lowered, which is the compensating control recorded against
GAP-022 (GAP-TOOL-01). Increments 10, 11 and 12 were still in flight when this trail
was taken and may raise the value further: coverage measured on 2026-09-09 over the
whole package, on a working tree that carries those in-flight edits, is **60.27 %**,
already a point above the gate. T094 and T095 re-measure and raise `--cov-fail-under`
to the settled figure at completion.

Why that decision rather than closing the gap here: 29 points on 18 k statements is roughly
5,200 statements no test executes, a test-writing effort of a different order than
signature cleanup, and it would dominate the feature. Signature cleanup and dead-code
deletion move the number a little in both directions — deleting untested dead code raises
it, and the deleted tests lower it — which is exactly why the ratchet is re-measured per
increment rather than fixed once.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New type `ExecutionMode(str, Enum)` in `ado2gh/models.py` | Replaces `dry_run: bool` at 65 signatures — the clarified default (split into two functions) would duplicate the bodies of the most safety-critical migration code; no existing enum covers execution mode (measured: 19 enums, none applicable) | Keyword-only `dry_run: bool` + exception register: 65 entries = 4.2 % of functions, over the 2 % cap; two functions per site: body duplication on CA-001 paths |
| Dev-only `ruff`/`mypy` install in the local venv | Inventory and guard reuse the CI-pinned linter (R1/R4) | Custom AST tagger: duplicates ruff rules and has no CI enforcement |
| Constitution Principle VI: completion gate is a measured ratchet, not 85 % | Measured coverage is 56 % with no exclusions; reaching 85 % means ~5,200 newly covered statements, an effort larger than the whole cleanup. The ratchet is monotonic and honest, where the status quo (85 % asserted over an omit list hiding `cli/`, `state/`, `core/`) is neither. Approved by the operator 2026-09-07; tracked as high gap G-seed 4 with a follow-up owner | Writing tests to 85 % inside this feature: reshapes the feature into a test-writing project and delays every safeguard fix behind it. Keeping the omit list and deferring: leaves the gate lying about coverage, the exact condition Principle VI exists to prevent |
