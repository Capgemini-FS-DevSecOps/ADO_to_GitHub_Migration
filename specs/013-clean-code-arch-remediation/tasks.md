# Tasks: Clean-Code Signature Audit & Critical Architecture Remediation

**Input**: Design documents from `/specs/013-clean-code-arch-remediation/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED by constitution (Principle VI). New code here (inventory scripts, snapshot test, `ExecutionMode`, gap fixes) gets its own tests; signature cleanup is covered by the existing suite, which must stay green after every increment. Coverage: measured 60 % with the current omit list and 56 % without it, against a gate asserting 85 % (plan § Coverage measurement). Decided 2026-09-07: delete the omit list and enforce a per-increment ratchet starting from the honest post-stabilisation figure (FR-027a — started in T011, raised in every increment's verify step, closed out in T075/T076); 85 % stays as the outstanding target on G-seed 4.

**Organization**: Phases follow the **binding execution order** from the spec's clarifications and plan § Execution order, not raw priority order: US1 (inventory) → US3 (assessment) → US4-critical → US2 (14 cleanup increments) → guards → US4-high → US5 (docs) → final gates. Story labels still map to spec.md user stories.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 inventory, US2 cleanup, US3 assessment, US4 remediation, US5 docs)
- Include exact file paths in descriptions

## Path Conventions

- Feature artefacts: `specs/013-clean-code-arch-remediation/` (abbreviated `FEATURE/` below)
- Python package `ado2gh/`, services `services/`, console `apps/migration-ui/src/`, tests `tests/`
- Run Python tests as `python -m pytest` (bare `pytest` shim is broken on the dev machine — research R12)

## Standing rules (apply to every task below)

- **Red increment (FR-014a)**: if an increment cannot be made green within three fix-and-verify attempts, `git reset --hard` to the last green commit, move the offending function/module to `FEATURE/exception-register.md` or into its own smaller increment, retry; never carry red forward.
- **Exception cap (FR-005a)**: before adding an exception-register row, check `rows ≤ 0.02 × totals.functions` (latest `FEATURE/inventory-history.jsonl` line). If it would exceed, stop cleanup of that rule and ask the operator (AskUserQuestion: raise cap / amend rule / accept behaviour change) before continuing.
- **Public surface (FR-006)**: `tests/contract/test_public_surface_snapshot.py` must pass unchanged in every commit except an approved contract-changing gap fix (FR-024).
- **No shims (FR-006a, R16)**: renames and deletions leave no alias, re-export, or deprecation wrapper.
- **Rendered docstrings (FR-010a, R14)**: Click command and FastAPI route docstrings keep their prose; parameter/return/error docs go into `click.option(help=…)`, `response_model`, `responses`, and Pydantic `Field(description=…)`.
- **Judgment tags (FR-002b)**: only the review agent (`caveman:cavecrew-reviewer`) decides `proposed_tags`; record with `--confirm/--reject … --decided-by cavecrew-reviewer`; `ESCALATE` lines go to the operator.
- **Changelog (FR-029)**: one section per increment in `docs/STRUCTURAL_CHANGELOG.md` using the contracts/artifact-schemas.md entry format: every moved/renamed/deleted file individually; deleted functions as a count plus the pointer `inventory.json@<git ref of the pre-increment commit>` rows with `disposition == "delete"` for the package; plus the number of tests removed with deleted code (needed for SC-004).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Toolchain parity with CI and the artefact skeleton

- [X] T001 Install the CI-pinned linter into the venv: `pip install "ruff==0.15.17" mypy`; verify `python -m ruff --version` prints `0.15.17` and `python -m vulture --version` prints `2.16`; record both in `specs/013-clean-code-arch-remediation/research.md` § R12
- [X] T002 [P] Verify the ruff configuration keys and rule codes assumed in research R1 against the pinned docs (`trafilatura -u https://docs.astral.sh/ruff/rules/ --markdown`, `trafilatura -u https://docs.astral.sh/ruff/settings/ --markdown`): `D1xx`, `ANN`, `FBT001/FBT002`, `PLR0913` + `lint.pylint.max-args` (does it count `self`/`cls`?), `B006`, `ARG`, `RET501–503`, `lint.pydocstyle.convention`; write the verified list and the receiver-count answer into `specs/013-clean-code-arch-remediation/research.md` § R1 "Verified"
- [X] T003 [P] Create the artefact skeleton: `specs/013-clean-code-arch-remediation/scripts/` (empty), `FEATURE/protected-entry-points.manual.txt` (header comment only), `FEATURE/exception-register.md` (table header per data-model.md § ExceptionRegisterEntry), `FEATURE/tag-decisions.json` (`[]`), `FEATURE/inventory-history.jsonl` (empty), `FEATURE/excluded-paths.txt` (FR-003b — seed with the generated/vendored/data-table paths: `ado2gh/pipelines/transform/` mapping tables, any `*_generated.py`, vendored stubs, `apps/migration-ui/src/**/*.d.ts`; one pattern per line with a `#` comment naming why each is excluded)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: A green baseline suite (research R15) and the frozen public surface (research R8). Nothing else starts until this phase is done.

**⚠️ CRITICAL**: FR-014/SC-010 are unverifiable against a red suite; the snapshot must be committed before any signature changes.

- [X] T004 Run `python -m pytest -q -p no:cacheprovider 2>&1 | tee FEATURE/baseline-failures.txt` and write `specs/013-clean-code-arch-remediation/baseline-failures.md` grouping the 50 failures + 16 errors by cause (`no such table: migration_operations`, endpoints removed in `0ec95d2`, `_gate_payload` import, missing `SQLiteStateDB.upsert_dependenc…` method, `AgentState` missing `llm`, `boto3` missing, other) with the test ids per group
- [ ] T005 Fix the `sqlite3.OperationalError: no such table: migration_operations` errors: locate the expected table in `tests/` fixtures vs `ado2gh/state/sqlite_db.py` / `ado2gh/state/postgres_db.py` schema creation; add the missing `CREATE TABLE` (mirrored in both backends) or repair the fixture, whichever the test contract shows; commit `fix(state): restore migration_operations table` alone
  - **Left unchecked deliberately (2026-09-08).** The 16 errors are gone and the suite is green, but not by this task's route and there is no `fix(state)` commit. Measured: the table's owning model (`ado2gh/api/models/`) and its only reader/writer (`migration_router.py`) were both deleted in `0ec95d2`, so no production code reads or writes `migration_operations`. Adding the `CREATE TABLE` would have introduced dead schema and baked a phantom table into the T013 public-surface snapshot. The errors were instead cleared under T006 by deleting `tests/contract/test_migration_api_contracts.py`, whose module-level fixture was the only thing asking for the table. **Operator decision needed** before T012/T013: accept this reading (test was stale) or reinstate the table in both backends (production lost a feature).
- [X] T006 [P] Delete or rewrite contract tests that only exercise routers removed in `0ec95d2` (identify from `FEATURE/baseline-failures.md`, expected in `tests/contract/test_discovery_api_contracts.py` and `tests/contract/test_migration_api_contracts.py`); for deleted tests add rows to `docs/STRUCTURAL_CHANGELOG.md`; commit alone
- [X] T007 [P] Fix `ImportError: cannot import name '_gate_payload'`: grep the import site in `tests/` and the former definition (likely `ado2gh/api/` or `services/accelerator_api/routes/`); restore the helper or update the test to the current API; commit alone
- [X] T008 [P] Fix the missing `SQLiteStateDB.upsert_dependenc…` method in `ado2gh/state/sqlite_db.py` (implement to match the Postgres counterpart in `ado2gh/state/postgres_db.py` or update the test if the method was intentionally removed); commit alone
- [X] T009 [P] Fix `AgentState missing required field: llm` in the failing `tests/agent/` fixtures (align with `ado2gh/agents/migration_agent/graph/state.py`); commit alone
- [X] T010 [P] Make the DynamoDB job-store test skip when `boto3` is absent (`pytest.importorskip("boto3")` in the test module under `tests/` that imports `ado2gh/state/job_store.py`); commit alone
- [X] T011 Re-run `python -m pytest -q -p no:cacheprovider`; require 0 failed / 0 errors; record the collected count (`python -m pytest --collect-only -q | tail -1`) as the SC-004 baseline in `specs/013-clean-code-arch-remediation/plan.md` § Technical Context and in `FEATURE/baseline-failures.md`; then start the coverage ratchet (FR-027a): delete the `[tool.coverage.run] omit` lines from `pyproject.toml`, run `python -m pytest -q --cov=ado2gh --cov-report=term | grep ^TOTAL`, set `--cov-fail-under=<that integer>` in `.github/workflows/ci.yml`, and record the starting value in `plan.md` § Coverage measurement; also record in `FEATURE/baseline-failures.md` the safeguard baseline counts that T093 compares against (live-approval/gate-override call sites, audit-write call sites, `--dry-run` flag count from the snapshot); commit `test: green baseline and honest coverage ratchet for 013`
- [X] T012 Write `tests/contract/test_public_surface_snapshot.py` per `contracts/public-contract-freeze.md`: collect CLI commands (walk `ado2gh.cli.main:cli`), HTTP routes (`services.accelerator_api.main:app.routes` + `services.agent.main:app.routes`, method + path), env var names (regex over `ado2gh/ services/ apps/migration-ui/src`), table names (`CREATE TABLE` in `ado2gh/state/`); compare to `tests/contract/public_surface_snapshot.json`; support `UPDATE_SURFACE_SNAPSHOT=1` to rewrite; fail with a unified diff
- [X] T013 Generate and commit `tests/contract/public_surface_snapshot.json` on its own commit `test: freeze public surface for 013` (expected ≈ 21 commands + 2 groups, 131 routes, 52 env vars, 25 tables); confirm `python -m pytest tests/contract/test_public_surface_snapshot.py` passes
  - **Measured 2026-09-08 at `a01fea5`** (recorded per constraint "record reality, not the estimate"): CLI **21 commands + 2 sub-groups + root group** (exact match; 96 snapshot lines including one per parameter). HTTP **148** `(app, method, path)` entries vs expected 131 — different counting convention, not new routes: the snapshot counts one entry per HTTP *method*, expected counted route *objects* excluding the four auto docs routes per app (that convention measures **132** today, i.e. research's 131 ± 1). Env vars **68** vs 52 — the collector also catches `os.environ[...]`, `setdefault`/`pop`, multi-line calls and `process.env["X"]`; 19 of the 68 are third-party SDK credential conventions (`AWS_*`, `AZURE_*`, `GOOGLE_*`, `PG*`, `MSI_ENDPOINT`, `IDENTITY_ENDPOINT`) read in `cloud_credential_detector.py` / `platform_managed_model.py`, which a narrower grep would miss; every one of the 68 was verified to be a real read (0 comment-only, 0 false positives). Tables **25** (exact match) — reached by scanning `ado2gh/` + `services/`, not just `ado2gh/state/`: 14 are in the state backends and 11 in `ado2gh/agents/migration_agent/session/store.py`; the task text's "`CREATE TABLE` in `ado2gh/state/`" alone yields only 14. `migration_operations` is **absent**, consistent with the T009 note (its model and only reader/writer were deleted in `0ec95d2`) — the freeze records the surface as it is, so reinstating the table later is an additive snapshot change, not a break.

**Checkpoint**: suite green, surface frozen — user stories can begin

---

## Phase 3: User Story 1 — Function Inventory & Signature Audit (Priority: P1) 🎯 MVP

**Goal**: A regenerable, deterministic inventory of every Python and console function with mechanical tags, judgment-tag proposals, reference counts, and a generated protected entry-point list.

**Independent Test**: regenerate twice → byte-identical `inventory.json`; spot-check 30 random rows; total within 2 % of an independent count.

### Tests for User Story 1 (REQUIRED) ⚠️

> Write first; they fail until T016/T017 exist.

- [X] T014 [P] [US1] Write `tests/unit/test_function_inventory_script.py` loading `specs/013-clean-code-arch-remediation/scripts/function_inventory.py` via `importlib.util.spec_from_file_location`; cases on a tmp fixture package: nested `def`/lambda produce no row (FR-001a); `self`/`cls` excluded from `param_count`; keyword-only `bool` → `bool_flag` proposal; two runs byte-identical (FR-004); a `tag-decisions.json` entry with matching `state_hash` is applied and one with a stale hash is dropped and re-proposed; Click-decorated and `__main__`-guarded functions land in the protected list; one `module_name_review` proposal is emitted per module and a recorded decision for it is reused (FR-013); a file matched by `excluded-paths.txt` yields no rows but its definition count appears under `excluded` in the summary (FR-003b); `--pending` exits 1 while proposals exist
- [X] T015 [P] [US1] Write `apps/migration-ui/src/__tests__/exports-documented.test.ts` (vitest) importing `walkExports` from `specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs` via relative path; cases: an exported arrow-function component with a leading `/** */` is `documented: true`; an undocumented `export function` is `false`; an inline callback inside a component is not a row; `default` export of `app/**/page.tsx` is marked `next_route_export`

### Implementation for User Story 1

- [X] T016 [US1] Write `specs/013-clean-code-arch-remediation/scripts/function_inventory.py` (stdlib only) per `contracts/artifact-schemas.md`: run `ruff check <roots> --output-format json --select <T002-verified set> --config "lint.pylint.max-args=5"`, `vulture <roots> --min-confidence 60`, own `ast` walk (rows per FR-001a, `signature`, `param_count`, `state_hash`, stale-`Args:` and keyword-only-bool proposals, `name_review` via a small verb list, `inconsistent_return` proposals from the `RET50x` findings — all four judgment classes of FR-002a go to `proposed_tags`, never straight to `tags`), reference scan over `ado2gh/ services/ tests/ apps/migration-ui/src ado2gh/agents/migration_agent/prompts docker-compose*.yml Dockerfile* scripts/ .github/ docs/ in/ .env.example` plus every root-level `*.yml *.yaml *.toml *.json *.md` (FR-003a: config, docs, workflows, containers all count), one `module_name_review` proposal per module in the package (id = module path, decided by the review agent — FR-013), `--package` repeatable with `ado2gh` as the key for root-level modules, protected-list generation (Click, FastAPI route decorators, `__main__`, orphan-guard sets in `tests/unit/test_no_orphaned_modules.py`, `StructuredTool.from_function` inner functions in `ado2gh/agents/migration_agent/tools/`, node callables in `ado2gh/agents/migration_agent/graph/builder.py`, `conftest.py` fixtures, plus `protected-entry-points.manual.txt`), merge with carry-over, `--package`, `--confirm/--reject/--rationale/--decided-by`, `--pending`, `--excluded` honouring `FEATURE/excluded-paths.txt` (FR-003b: matching files yield no rows, but their definition counts go into the `excluded` block of the history line and the summary), history append, `inventory-summary.md` with delta; refuse to run on a ruff version ≠ `0.15.17`
- [X] T017 [US1] Write `specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs` using `apps/migration-ui/node_modules/typescript`: walk `apps/migration-ui/src`, rows for exported functions/hooks/components only (FR-001a), tags `missing_docstring`, `untyped` (`any`), `gt5_params`, `bool_flag` proposal, `unused_param` (parse `tsc --noEmit --noUnusedParameters` output), `dead` (zero imports across `src/`), `next_route_export` protection; export `walkExports(sourceRoot)`; merge TS rows into the same `inventory.json` without touching `py` rows
- [X] T018 [US1] Run both scripts from the repo root; commit `FEATURE/inventory.json`, `FEATURE/inventory-summary.md`, `FEATURE/inventory-history.jsonl` (line 1 = baseline), `FEATURE/protected-entry-points.json` as `chore(013): baseline inventory`; confirm the Python total is within 2 % of an independent count made at the same FR-001a granularity (one-line `ast` count of `Module`/`ClassDef`-level `def`s over `ado2gh/ services/`; research's 1,549 included nested defs and is only an upper bound) and the TS total within 2 % of `grep -c "^export"`-style count of exported declarations (≈227 exports + hooks/components)
- [X] T019 [US1] Independent test: draw 30 rows with `random.Random(13)`, verify location/signature/tags by reading each definition, and write `specs/013-clean-code-arch-remediation/inventory-spotcheck.md` (row id, verdict, note); fix the script and regenerate if any tag is wrong; re-run T014/T015
- [X] T020 [US1] Pilot the judgment-tag flow on increment-1 packages only (`--package ado2gh --package ado2gh/assignments`, i.e. root-level modules plus `assignments/`, including their `module_name_review` proposals): hand the `--pending` listing to `caveman:cavecrew-reviewer` with the verbatim second-handoff instruction from `contracts/artifact-schemas.md`, record every reply with `--confirm/--reject --decided-by cavecrew-reviewer`, escalate `ESCALATE` lines via AskUserQuestion; regenerate and confirm `--pending --package …` exits 0; commit `FEATURE/tag-decisions.json`

**Checkpoint**: US1 delivers a usable audit on its own (`inventory-summary.md` is the progress dashboard)

---

## Phase 4: User Story 3 — Architecture Gap Assessment (Priority: P2)

**Goal**: `FEATURE/gap-register.md` covering every FR-016/FR-016a component against six principles × six migration-safety properties, rated, reviewed by a separate agent, disputes settled.

**Independent Test**: the review agent reproduces every critical/high rating from the cited evidence or files a dispute; every dispute has an operator decision.

- [X] T021 [US3] Create `specs/013-clean-code-arch-remediation/gap-register.md` with the `## Summary`, `## Disputes`, `## Gaps` layout and the per-gap bullet keys from `contracts/artifact-schemas.md`; add a "Checklist" appendix listing the 6 principles × 6 properties grid used for every component (FR-017, FR-018); record any gap that baseline stabilisation (T005–T010) incidentally closed as a `### GAP-` entry with `status: remediated`, the stabilisation commit as its resolution, and the restored test as its regression check (FR-015)
- [X] T022 [P] [US3] Assess **CLI** (`ado2gh/cli/`): `--dry-run` defaults and help text on every destructive command (`run`, `phase run`, `rollback`, `ado-cleanup`), gate override `--reason` enforcement, secrets in `--help`/logs; write `### GAP-` sections with `path:line` evidence
- [X] T023 [P] [US3] Assess **accelerator service** (`services/accelerator_api/`, `ado2gh/api/`): every route that can start a non-dry-run pipeline passes `live_approval_store.py`/`platform_rbac.py`; `routes/_shared.py` `__import__` singletons (G-seed 8); `agentic_routes.py` placement (G-seed 11); audit events for state changes
- [X] T024 [P] [US3] Assess **agent service** (`services/agent/`, `ado2gh/agents/migration_agent/`): trace every path to non-dry-run execution through `policies.py`, `guardrails.py`, `route_helpers.py`, `routes/session_routes.py`, `/v1/internal/sessions/{id}/resume-live` (G-seed 2 — critical test (a) if any bypass); `mask_secrets` usage on SSE/messages (G-seed 1); checkpoint resume after restart (property: resumability)
- [X] T025 [P] [US3] Assess **web console** (`apps/migration-ui/src/`): live-execution confirmation UI cannot skip the approval endpoint; secrets never rendered or logged (`lib/`); embed mode auth; test/lint posture (G-seed 9, medium)
- [X] T026 [P] [US3] Assess **migration engine** (`ado2gh/core/`, `ado2gh/core/scopes/`): idempotency of `git_scope`/GEI on re-run, `_redact` vs the other two masking functions (G-seed 1), validation after transfer (`reporting/post_migration_validator.py`), rollback scope targeting, `clients → core` inversion (G-seed 3)
- [X] T027 [P] [US3] Assess **phase orchestration** (`ado2gh/phase/`): gate override audit trail, batch checkpoint/resume, `failed_repos_{phase}.txt` export on interruption
- [X] T028 [P] [US3] Assess **pipeline transformation** (`ado2gh/pipelines/`): secret names vs values in generated workflow YAML and manifests, `mypy ignore_errors` for `ado2gh.pipelines.*` (G-seed 5)
- [X] T029 [P] [US3] Assess **state persistence** (`ado2gh/state/`): SQLite vs Postgres schema parity (25 tables), `create_state_db()` hidden backend selection (G-seed 6), `DynamoDBJobStore` parity for audit/resume/idempotency (spec US3 scenario 5), `state → api` inversion (G-seed 3), WAL/checkpoint durability
- [X] T030 [P] [US3] Assess **auth & RBAC** (`ado2gh/auth/`, `ado2gh/api/platform_rbac.py`): `can_approve_live_execution` enforced server-side on every approval route, session cookie flags, bootstrap admin path, `auth ↔ api` cycle (G-seed 3)
- [X] T031 [P] [US3] Assess **token management and audit writing** (`ado2gh/clients/token_manager.py`, `ado2gh/assignments/audit.py`): tokens never logged (headers, exceptions), `redact_payload` coverage of every audit write path (G-seed 1), rate-limit failure mode fail-safe
- [X] T032 [P] [US3] Assess **integration seams**: accelerator ↔ agent HTTP calls (`ado2gh/api/accelerator.py`, `session_token` handling), SSE event shapes, `tests/contract/` coverage of each seam; critical test (e) for any silent contract disagreement
- [X] T033 [P] [US3] Assess **deployment & CI artefacts** (FR-016a, G-seed 12): `docker-compose.yml`, `docker-compose.prod.yml`, `Dockerfile*`, `.github/workflows/ci.yml` (`mypy || true` G-seed 5; coverage omit list G-seed 4/13), `.github/workflows/migrate-repo.yml` (can it reach a live migration without approval?), `.env.example`; rated high at most except a committed secret value
- [X] T034 [P] [US3] Assess **tooling & guards**: `pyproject.toml` coverage omit + measured 60 % (G-seed 4/13), `tests/unit/test_no_orphaned_modules.py` stale allowlist entry (G-seed 7), doc drift (G-seed 10), layering cycles summary (G-seed 3) — one GAP each with severity per spec US3 scenarios 2–3
- [X] T035 [US3] Fill `## Summary` (counts per severity; table of critical/high with id, title, status) and verify every critical entry names its `critical_test` letter (FR-019, FR-020) and every gap has ≥ 1 `path:line` or reproduction command
- [X] T036 [US3] Review-agent pass: spawn `caveman:cavecrew-reviewer` with `.specify/memory/constitution.md` + `FEATURE/gap-register.md` and the verbatim instruction from `contracts/artifact-schemas.md`; paste every `DISPUTE` line into `## Disputes`; set those gaps to `status: disputed`
- [X] T037 [US3] Present each dispute to the operator (AskUserQuestion: keep / lower / raise) and record decisions in `## Disputes` with date; return gaps to `open`. If the operator does not answer a dispute, apply the spec's fallback: record the gap at the **higher** of the two proposed severities, leave `status: disputed`, and defer its remediation until a decision exists — never remediate a disputed gap (FR-021). Commit `docs(013): architecture gap register`

**Checkpoint**: register is complete; critical set is known and confirmed

---

## Phase 5: User Story 4 (part A) — Critical Gap Remediation (Priority: P2, runs before cleanup per FR-022)

**Goal**: Every CONFIRMED critical gap closed at its choke point, with a regression check proven once by revert.

**Independent Test**: for each critical gap, the reproduction no longer reproduces and `python -m pytest -k GAP_NNN` fails with the fix reverted.

- [ ] T038 [US4] For each `severity: critical` gap in `FEATURE/gap-register.md`, write the failing regression test first at `tests/<domain>/test_gap_NNN_<slug>.py` (docstring names `GAP-NNN`; reproduction from the register's evidence); confirm it fails on current code
- [ ] T039 [US4] Fix each critical gap at the shared choke point (FR-025). Expected candidates from research R9 — implement only those the register confirms as critical: (a) G-seed 1 → one masking function (keep `ado2gh/assignments/audit.py:redact_payload` as the choke point; make `ado2gh/agents/migration_agent/utils.py:mask_secrets` and `ado2gh/core/scopes/git_scope.py:_redact` delegate to it; cover log handlers via `ado2gh/logging_config.py`); (b) G-seed 2 → route every non-dry-run entry (`services/agent/routes/session_routes.py`, `/v1/internal/.../resume-live`, `services/accelerator_api/routes/pipeline_routes.py`) through a single `ado2gh/agents/migration_agent/policies.py` check; (c) any (e)-type contract mismatch → align producer and consumer plus a `tests/contract/` assertion; every critical fix also adds the audit event (CA-004) and preview path (CA-001) the register says is missing, at the same choke point. If two critical fixes conflict — closing one weakens the other — stop before applying either, present both options with their trade-offs to the operator (AskUserQuestion), and record the decision in both register entries (spec edge case)
- [ ] T040 [US4] For each critical fix (FR-023): revert proof (`git stash push -- <fix files>`; `python -m pytest -k GAP_NNN` must fail; `git stash pop`), then update the register entry (`status: remediated`, `resolution`, `regression_check`, `revert_proof` command + date + who, `closed_on`, `contract_change`); if `contract_change: true`, STOP — add the change and migration note to `plan.md` § Approved contract changes and `contracts/public-contract-freeze.md` § Approved contract changes, get plan re-approval via AskUserQuestion, then update `tests/contract/public_surface_snapshot.json` in the same commit; commit one `fix(GAP-NNN): …` per gap

**Checkpoint**: zero critical gaps `open`; their regression tests are part of the suite from here on

---

## Phase 6: User Story 2 — Signature Cleanup Without Behaviour Change (Priority: P1) — 14 increments

**Goal**: Every inventoried function reaches zero tags (or an exception-register row), dead functions are deleted, opaque modules are moved, public surface unchanged, suite green after every increment.

**Independent Test**: after increment 14, `function_inventory.py --pending` exits 0, the SC-001 check in quickstart § 5 prints `tagged-not-excepted: 0`, `test_public_surface_snapshot.py` passes unchanged, Python and console suites green.

**Per-increment protocol** (referenced by every "clean" task below): commit any pending work so the pre-increment `inventory.json` has a git ref; regenerate `--package`; review agent decides function proposals and the package's `module_name_review` proposals (FR-013 — confirmed moves are executed in this increment); clean signatures (Google-style docstrings — R3; annotate params/returns; > 5 params → group into an *existing* domain object; `dry_run: bool` → `ExecutionMode` with boundary conversion per `contracts/public-contract-freeze.md`; other confirmed `bool_flag` → two intent-named functions, else an existing enum/option object (FR-012); drop unused params; fix inconsistent returns; rename per confirmed `name_review`); delete every `dead` row not `protected` together with its exclusive tests (note the count and the pre-increment git ref for the changelog pointer — FR-029); apply FR-010a to Click/FastAPI docstrings; update **all** callers (FR-007: tests, prompts, fixtures, console); set `disposition` per row; exception rows → `FEATURE/exception-register.md` + `# noqa: <code>` with reason; verify (`python -m ruff check <pkg> --select <T002 set> --config "lint.pylint.max-args=5"`, `python -m pytest -q --cov=ado2gh --cov-report=term` — TOTAL must be ≥ the current `--cov-fail-under` in `.github/workflows/ci.yml`, and if it is higher, raise that value to the new integer in the same commit, never lower it (FR-027a/SC-004); snapshot test; `tests/unit/test_no_orphaned_modules.py`; `--pending --package <pkg>` exit 0); append `## <date> — 013 Increment N: <pkg>` to `docs/STRUCTURAL_CHANGELOG.md`; commit `refactor(<pkg>): 013 increment N`.

### Increment 1 — root modules + `ado2gh/assignments/`

- [X] T041 [P] [US2] Write `tests/unit/test_execution_mode.py`: `ExecutionMode.DRY_RUN.value == "dry_run"`, `ExecutionMode("live") is ExecutionMode.LIVE`, boundary helper `ExecutionMode.from_dry_run(flag: bool)` maps `True → DRY_RUN`; fails until T042
- [X] T042 [US2] Add `ExecutionMode(str, Enum)` with `DRY_RUN`, `LIVE`, and `from_dry_run()` to `ado2gh/models.py` (docstring per data-model.md); no call sites yet
- [X] T043 [US2] If the review agent confirmed the `module_name_review` proposal for `ado2gh/assignments/` in T020 (expected: the package holds only the audit event writer), rename `ado2gh/assignments/` → `ado2gh/audit/` (FR-013); update imports in `ado2gh/api/`, `ado2gh/agents/`, `services/`, `tests/`; add changelog row; confirm orphan guard passes
- [X] T044 [US2] Clean increment 1 per protocol: `ado2gh/models.py`, `ado2gh/http_utils.py`, `ado2gh/logging_config.py`, `ado2gh/output_dirs.py`, `ado2gh/audit/` (callers: every package); verify, changelog, commit

### Increment 2 — `ado2gh/clients/`

- [X] T045 [US2] Review-agent decisions for `--package ado2gh/clients`; record; escalations to operator
- [X] T046 [US2] Clean `ado2gh/clients/` per protocol (80 functions; 68 without docstrings; `token_manager.py` must keep every token value out of docstrings and log lines — CA-003); callers in `core/`, `pipelines/`, `reporting/`, `cli/`, `api/`; verify, changelog, commit

### Increment 3 — `ado2gh/state/`

- [X] T047 [US2] Review-agent decisions for `--package ado2gh/state`; record
- [X] T048 [US2] Clean `ado2gh/state/` per protocol (239 functions; 228 without docstrings; 47 untyped; 22 with > 5 params — group column arguments into the existing record models in `ado2gh/models.py` / `ado2gh/api/pipeline_models.py`, never a new type; persisted `dry_run` columns keep shape with `ExecutionMode.from_dry_run` at read/write; `sqlite_db.py` and `postgres_db.py` signatures change together — FR-011; `job_store.py` abstract methods likewise); callers in `phase/`, `pipelines/`, `reporting/`, `core/`, `api/`, `agents/`, `cli/`, `services/`; verify, changelog, commit

### Increment 4 — `ado2gh/phase/`

- [X] T049 [US2] Review-agent decisions for `--package ado2gh/phase`; record
- [X] T050 [US2] Clean `ado2gh/phase/` per protocol (15 functions; 3 `dry_run` booleans → `ExecutionMode`; gate-override signatures keep `reason` mandatory — CA-002); callers in `core/`, `api/`, `cli/`; verify, changelog, commit

### Increment 5 — `ado2gh/pipelines/`

- [X] T051 [US2] Review-agent decisions for `--package ado2gh/pipelines`; record
- [X] T052 [US2] Clean `ado2gh/pipelines/` per protocol (77 functions; the task-mapping tables in `ado2gh/pipelines/transform/` are data, not functions — list them as excluded in `inventory-summary.md`); callers in `core/`, `reporting/`, `api/`, `agents/`, `cli/`; verify, changelog, commit

### Increment 6 — `ado2gh/reporting/`

- [X] T053 [US2] Review-agent decisions for `--package ado2gh/reporting`; record
- [X] T054 [US2] Clean `ado2gh/reporting/` per protocol (39 functions); callers in `api/`, `cli/`, `services/accelerator_api/`; verify, changelog, commit

### Increment 7 — `ado2gh/core/`

- [ ] T055 [US2] Review-agent decisions for `--package ado2gh/core`; record
- [ ] T056 [US2] Clean `ado2gh/core/` per protocol (77 functions; 10 `dry_run` booleans → `ExecutionMode` with `DRY_RUN` defaults preserved — CA-001; scope handlers in `core/scopes/` share one signature — FR-011; keep any masking consolidation from T039); callers in `api/`, `agents/`, `cli/`, `services/`; verify, changelog, commit

### Increment 8 — `ado2gh/auth/`

- [X] T057 [US2] Review-agent decisions for `--package ado2gh/auth`; record
- [X] T058 [US2] Clean `ado2gh/auth/` per protocol (26 functions; session/password handling functions must not gain docstring examples containing credentials); callers in `api/`, `agents/`, `services/`; verify, changelog, commit

### Increment 9 — `ado2gh/api/`

- [ ] T059 [US2] Review-agent decisions for `--package ado2gh/api`; record
- [ ] T060 [US2] Clean `ado2gh/api/` per protocol (331 functions; 264 without docstrings; `pipeline_steps.py` (48 KB) step functions share one signature — FR-011; `agentic_routes.py` handlers follow FR-010a; `llm/` provider functions must keep API keys out of docstrings and defaults); callers in `agents/`, `cli/`, `services/`, `tests/`; verify, changelog, commit

### Increment 10 — `ado2gh/agents/`

- [ ] T061 [US2] Review-agent decisions for `--package ado2gh/agents`; record
- [ ] T062 [US2] Clean `ado2gh/agents/` per protocol (430 functions; 23 with > 5 params; 29 booleans incl. `dry_run`, `plan_confirmed`, `confirm_execute`, `start_pev` — decide split vs `ExecutionMode` per confirmed tag; tool inner functions in `tools/*.py` may change name/params — then update `prompts/*.md` (`github_api` 12×, `ado_api_query` 10×, `call_accelerator` 7×), `tests/agent/`, `tests/contract/test_agent_*`, and `apps/migration-ui/src` references in the same commit; LangGraph node signatures `(state, config)` are framework-fixed — document, do not "fix"); verify incl. `python -m pytest tests/agent tests/contract`, changelog, commit

### Increment 11 — `ado2gh/cli/`

- [ ] T063 [US2] Review-agent decisions for `--package ado2gh/cli`; record
- [ ] T064 [US2] Clean `ado2gh/cli/` per protocol (32 functions, 31 untyped: annotate every Click handler parameter; `--dry-run` flags unchanged, convert with `ExecutionMode.from_dry_run` on the first line — `contracts/public-contract-freeze.md`; command docstrings are help text — FR-010a; confirm `ado2gh <cmd> --help` output unchanged for every command via the existing CLI tests); verify, changelog, commit

### Increment 12 — `services/accelerator_api/`

- [ ] T065 [US2] Review-agent decisions for `--package services/accelerator_api`; record
- [ ] T066 [US2] Clean `services/accelerator_api/` per protocol (142 functions, 118 untyped route handlers: annotate params and `-> ResponseModel`; docstrings per FR-010a; `routes/_shared.py` singletons stay unless a gap fix changed them); verify incl. `tests/contract/`, changelog, commit

### Increment 13 — `services/agent/`

- [ ] T067 [US2] Review-agent decisions for `--package services/agent`; record
- [ ] T068 [US2] Clean `services/agent/` per protocol (52 functions, 35 untyped; SSE route handlers keep event shapes — frozen seam); verify incl. `tests/agent tests/contract`, changelog, commit

### Increment 14 — `apps/migration-ui/src/`

- [ ] T069 [US2] Review-agent decisions for `--package apps/migration-ui` (TS proposals); record
- [ ] T070 [US2] Clean `apps/migration-ui/src/` per protocol (≈340 functions; JSDoc on all 227 exports — FR-010; replace `any` params with the types in `src/lib/types/`; > 5 props → existing props interface; boolean behaviour props confirmed as switches → two components or an existing union type; delete dead exports with their tests; `next_route_export` rows are protected); verify with `cd apps/migration-ui && npx tsc --noEmit && npx vitest run`; changelog, commit

**Checkpoint**: all 14 increments green; `inventory-summary.md` shows zero tags outside the exception register

---

## Phase 7: Guards On (research R4)

**Purpose**: Turn the clean state into an enforced state using the tooling already in CI.

- [ ] T071 Extend `[tool.ruff.lint] select` in `pyproject.toml` with the T002-verified set (`D1`, `ANN`, `FBT001`, `FBT002`, `PLR0913`, `B006`, `ARG`, `RET501`, `RET502`, `RET503`), add `[tool.ruff.lint.pylint] max-args = 5` and `[tool.ruff.lint.pydocstyle] convention = "google"`; run `python -m ruff check ado2gh/ services/` — zero findings except `# noqa` lines listed in `FEATURE/exception-register.md`
- [ ] T072 [P] Audit every `# noqa:` added during cleanup against `FEATURE/exception-register.md` (one row per noqa; remove orphans; add missing rows); confirm register rows ≤ 2 % of `totals.functions`
- [ ] T073 [P] Turn on `"noUnusedParameters": true` in `apps/migration-ui/tsconfig.json` and add the assertion to `apps/migration-ui/src/__tests__/exports-documented.test.ts` that every export returned by `walkExports("src")` is documented; `npx tsc --noEmit && npx vitest run` green
- [ ] T074 Run `.pre-commit-config.yaml` hooks locally (`pre-commit run --all-files` if installed, else `python -m ruff check --diff ado2gh/ services/`, which must print no diff) to confirm the new rule set is clean under the pinned `ruff-pre-commit` rev; commit `chore(013): enforce clean-code rule set`

---

## Phase 8: User Story 4 (part B) — High Gap Remediation (Priority: P2, after cleanup per FR-022)

**Goal**: Every `severity: high` gap closed with a regression check and revert proof; medium/low left in the register with a follow-up.

**Independent Test**: no `high` gap in `open`/`disputed`; each `remediated` entry has `regression_check` and `revert_proof`.

- [ ] T075 [US4] Close the coverage gap entry per the operator decision of 2026-09-07 (plan § Coverage measurement, FR-027a): in `FEATURE/gap-register.md` set the G-seed 4 gap to `status: deferred`, `resolution` = "exclusions removed and honest ratchet active since T011 (starting value <n> %, current value <m> %); 85 % outstanding, follow-up owner <name>", compensating control = the never-lowered ratchet; copy the ratchet trail (value after each increment, from the changelog sections) into `plan.md` § Coverage measurement. No new AskUserQuestion — the decision is made
- [ ] T076 [US4] Verify the ratchet is enforced: `--cov-fail-under` in `.github/workflows/ci.yml` equals the highest value measured so far and `pyproject.toml` has no `[tool.coverage.run] omit`; regression check = that `ci.yml` line (register `regression_check` points at `ci.yml:<line>`); revert proof = temporarily restore the omit lines or raise the threshold above the measured value and observe `python -m pytest --cov=ado2gh --cov-fail-under=<value>` fail, then restore
- [ ] T077 [P] [US4] G-seed 5 (if high): remove `|| true` from the mypy step in `.github/workflows/ci.yml`, delete the `ignore_errors` override block in `pyproject.toml` `[[tool.mypy.overrides]]`, fix resulting `mypy ado2gh/` errors (cleanup already typed the signatures); regression check = CI step
- [ ] T078 [P] [US4] G-seed 3 (if high): break the measured inversions — move what `ado2gh/state/` imports from `ado2gh/api/` into `ado2gh/models.py` or `ado2gh/state/`; move what `ado2gh/clients/` needs from `ado2gh/core/` into `ado2gh/http_utils.py`/`ado2gh/models.py`; remove `ado2gh/api → ado2gh/cli` by relocating the shared helper into `ado2gh/api/`; add `tests/unit/test_gap_NNN_layering.py` asserting the import graph (reuse the walker in `tests/unit/test_no_orphaned_modules.py`) has no `state→api`, `clients→core`, `api→cli` edges; changelog rows for moves
- [ ] T079 [P] [US4] G-seed 11 (if high): move HTTP-route code out of the package — `ado2gh/api/agentic_routes.py` → `services/accelerator_api/routes/history_routes.py`, `ado2gh/agents/migration_agent/route_helpers.py` → `services/agent/routes/_helpers.py`; update `main.py` router registration in both services; orphan allowlist; changelog; snapshot test must stay unchanged (paths are frozen, code location is not)
- [X] T080 [P] [US4] G-seed 12 (if high): fix deployment/CI findings from T033 (e.g. secrets passed as build args → runtime env; `migrate-repo.yml` requires an approval gate/environment before any non-dry-run step); regression check = a `tests/unit/test_gap_NNN_ci_artifacts.py` asserting the workflow YAML shape with `yaml.safe_load`
- [ ] T081 [P] [US4] G-seed 6 (if high): make backend selection explicit — `create_state_db(backend: StorageBackend, db_path: str)` in `ado2gh/state/factory.py` using the existing `StorageBackend` enum; parity test `tests/unit/test_gap_NNN_backend_parity.py` asserting SQLite and Postgres create the same 25 tables and that `DynamoDBJobStore` implements every `JobStore` abstract method with the same audit fields
- [ ] T082 [US4] Any remaining `high` gaps from the register not covered above: failing test first at `tests/<domain>/test_gap_NNN_<slug>.py`, fix at the choke point, adding missing audit coverage (CA-004) or preview path (CA-001) where the register names one; contract-changing fixes go through `plan.md` § Approved contract changes + AskUserQuestion re-approval before application (FR-024). Then, for **every** high fix in T076–T082: revert proof once (`git stash push -- <fix files>`; run the check — the CI command for T076/T077, the test for the others — observe failure; `git stash pop`), and update the register entry (`status`, `resolution`, `regression_check`, `revert_proof` command + date + who, `closed_on`). A high gap that cannot be closed here is set to `deferred` with the named blocker and an in-scope compensating control (FR-022); the coverage shortfall (G-seed 4) is deferred this way with the ratchet as its control (FR-027a)
- [ ] T083 [US4] Update `FEATURE/gap-register.md` `## Summary` (FR-020, FR-026) — zero critical/high in `open`/`disputed`; every medium/low has a `follow_up` line; commit `fix(013): high-severity architecture gaps`

**Checkpoint**: SC-005/SC-006 satisfiable from the register alone

---

## Phase 9: User Story 5 — Documentation & Guard Rails Stay Truthful (Priority: P3)

**Goal**: Docs, changelog, and guards reflect the post-cleanup, post-remediation tree.

**Independent Test**: quickstart § 5 SC-007 script prints no `MISSING`; orphan guard passes; changelog has a section per increment and per gap fix.

- [ ] T084 [P] [US5] Update `docs/ARCHITECTURE.md`: package tree (`ado2gh/audit/` if renamed in T043, moved route modules, any G-seed 3 relocations), `ExecutionMode` in the migration-engine section, corrected designs for every remediated gap (masking choke point, live-gate path, state factory), remove descriptions of remediated behaviour
- [ ] T085 [P] [US5] Update `CLAUDE.md`: package structure block, test count and `python -m pytest` note, the coverage ratchet rule and its current value (FR-027a; 85 % named as outstanding), new guard rules (ruff set, snapshot test, exports-documented test), `docs/STRUCTURAL_CHANGELOG.md` reminder, orphan-guard allowlist note
- [ ] T086 [P] [US5] Update `README.md` and the rest of `docs/` (`LOCAL_DEVELOPMENT.md`, `COMMAND_REFERENCE.md`, `SETUP_GUIDE.md`, `TROUBLESHOOTING.md`) for renamed modules and the coverage/lint gates; completed specs 001–012 are not rewritten
- [ ] T087 [US5] Update `tests/unit/test_no_orphaned_modules.py` allowlists (FR-030): remove the stale `ado2gh.reporting.boards_gaps` entry (G-seed 7), add/rename entries for every module moved by T043/T078/T079; `python -m pytest tests/unit/test_no_orphaned_modules.py` green
- [ ] T088 [US5] Run the SC-007 script (FR-028) from `quickstart.md` § 5 over `docs/ARCHITECTURE.md`, `CLAUDE.md`, `README.md`, `docs/*.md`; fix every `MISSING`; verify `docs/STRUCTURAL_CHANGELOG.md` has one `013 Increment N` section per increment plus one row per gap-fix move/delete, and that SC-009 holds end to end: for one increment chosen at random, resolve its `inventory.json@<sha>` pointer with `git show <sha>:specs/013-clean-code-arch-remediation/inventory.json` and confirm every function it lists as `delete` for that package is absent from the tree; commit `docs(013): architecture and guidance refresh`

---

## Phase 10: Final Gates & Polish

**Purpose**: Measure every success criterion against the final tree (FR-014b: the last regeneration is the gate).

- [ ] T089 Final regeneration: `python FEATURE/scripts/function_inventory.py && node FEATURE/scripts/function_inventory_ts.mjs`; require `--pending` exit 0; run the SC-001 check from `quickstart.md` § 5 (`tagged-not-excepted: 0`, exceptions ≤ cap or operator decision recorded in `FEATURE/exception-register.md`)
- [ ] T090 [P] SC-002/SC-004: `python -m pytest -q` green; `python -m pytest tests/contract/test_public_surface_snapshot.py` unchanged since T013 except approved changes; collected count ≥ T011 baseline − tests removed per changelog; `cd apps/migration-ui && npx tsc --noEmit && npx vitest run` green
- [ ] T091 [P] SC-003 (FR-008): `git diff <baseline>..HEAD -- pyproject.toml apps/migration-ui/package.json` shows no new runtime/console dependency; `grep -rn "ADO2GH_" ado2gh services | sort -u` matches the snapshot env list (no new config keys); FR-006a: `git diff <baseline>..HEAD -- ado2gh services apps/migration-ui/src | grep -E "^\+.*(DeprecationWarning|warnings\.warn|# re-export|__all__ \+= )"` returns nothing — no shims or aliases were introduced
- [ ] T092 [P] SC-008 (research R13): sample 40 clean rows with `random.Random(13)`, build the prompt, run a tool-less `general-purpose` agent, score against bodies, save `specs/013-clean-code-arch-remediation/sc-008-sample.md`; pass ≥ 38/40 — if below, fix the docstrings of the failed rows and re-run the sample
- [ ] T093 [P] Safeguard-preservation gate (CA-001…CA-004), measured against the stabilised baseline commit, using anchors verified to exist on 2026-09-07: (CA-001) every `--dry-run`/preview flag in `tests/contract/public_surface_snapshot.json` is still present, and each removed `dry_run=True` default has a matching `ExecutionMode.DRY_RUN` addition — `git diff <baseline>..HEAD -- ado2gh services | grep -E "^-.*dry_run.*=.*True"`; (CA-002) approval and confirmation call sites have not shrunk — `grep -rn "can_approve_live_execution" ado2gh services --include=*.py | wc -l` (baseline **6**) and `grep -rn "require_confirmation\|confirm_execute\|plan_confirmed" ado2gh services --include=*.py | wc -l` (baseline measured at T011); (CA-003) `python -m pytest tests/ -k "mask or redact or secret" -q` green and `git diff <baseline>..HEAD | grep -E "^\+.*(ghp_|github_pat_|password\s*=\s*\")"` prints nothing; (CA-004) audit-write call sites have not shrunk — `grep -rn "AuditWriter\|audit_event(" ado2gh services --include=*.py | wc -l` (baseline `audit_event(` **4** plus `AuditWriter` uses, recorded at T011). A count that drops is a blocker unless the drop is explained by a rename recorded in `docs/STRUCTURAL_CHANGELOG.md` and the new name's count covers the difference. Record all five results plus the explanation of any delta in `FEATURE/safeguard-gate.md`
- [ ] T094 Run `quickstart.md` § 3 guards (`python -m ruff check ado2gh/ services/`, `mypy ado2gh/`, `python -m pytest --cov=ado2gh --cov-fail-under=<current ratchet from ci.yml>`) and § 5 completely; fix anything red
- [ ] T095 Push the branch and confirm the CI workflow (`.github/workflows/ci.yml`, Python 3.11 — the declared minimum, so annotations added on a newer local interpreter are proven valid there) is green; then update `specs/013-clean-code-arch-remediation/spec.md` `**Status**` to `Implemented` with the date, add the final `inventory-summary.md` totals, ratchet value, and gap counts to `plan.md` § Summary, final commit `chore(013): complete clean-code audit and architecture remediation`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none — start immediately
- **Foundational (Phase 2)**: after Setup; **blocks everything** (green suite T011, frozen surface T013)
- **US1 (Phase 3)**: after Foundational
- **US3 (Phase 4)**: after US1 (assessment cites inventory rows; FR-015 requires it before any cleanup)
- **US4-critical (Phase 5)**: after US3 T037 (only CONFIRMED critical gaps; FR-022)
- **US2 (Phase 6)**: after Phase 5; increments strictly sequential 1 → 14 (research R6); each increment's verify task gates the next
- **Guards (Phase 7)**: after increment 14
- **US4-high (Phase 8)**: after Phase 7 (FR-022: high gaps wait for cleanup)
- **US5 (Phase 9)**: after Phase 8
- **Final gates (Phase 10)**: after Phase 9

### User Story Dependencies

- **US1** → none beyond Foundational; deliverable on its own (audit dashboard)
- **US3** → US1 (evidence pointers use inventory ids; stabilised-baseline code per FR-015)
- **US4** → US3 (register); part A precedes US2, part B follows it
- **US2** → US1 (inventory), US4-A (critical fixes landed first so cleanup does not touch unfixed safeguard code)
- **US5** → all of the above

### Within Each Increment (Phase 6)

decide proposals (review agent) → clean + callers → verify (ruff, suite with coverage ≥ ratchet — raise if higher, snapshot, orphan guard, `--pending`) → changelog → commit; red → revert after three attempts (FR-014a)

### Parallel Opportunities

- T002 ∥ T003 (Setup)
- T006–T010 ∥ (distinct failure groups, distinct files) after T005
- T014 ∥ T015 (tests); T016 ∥ T017 (two scripts)
- T022–T034 ∥ (thirteen component assessments, each appends its own `### GAP-` sections — merge by appending, ids assigned sequentially at T035)
- Within Phase 5, critical fixes touching different files ∥
- T041 ∥ T043 inside increment 1; otherwise increments are sequential
- T072 ∥ T073 (Guards)
- T077–T081 ∥ (different files) after T076
- T084–T086 ∥ (docs)
- T090–T093 ∥ (final gates; T094 and T095 run last, in order)

---

## Parallel Example: User Story 3 (assessment)

```bash
# Thirteen read-only assessments, each writing its own GAP sections:
Task: "Assess CLI (ado2gh/cli/) …"                       # T022
Task: "Assess accelerator service …"                     # T023
Task: "Assess agent service … live-execution paths"      # T024
Task: "Assess state persistence … backend parity"        # T029
Task: "Assess deployment & CI artefacts …"               # T033
# then sequentially: T035 summary → T036 review agent → T037 operator disputes
```

## Parallel Example: User Story 1 (inventory)

```bash
Task: "Write tests/unit/test_function_inventory_script.py"                      # T014
Task: "Write apps/migration-ui/src/__tests__/exports-documented.test.ts"        # T015
Task: "Write FEATURE/scripts/function_inventory.py"                             # T016
Task: "Write FEATURE/scripts/function_inventory_ts.mjs"                         # T017
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → Phase 2 Foundational (green suite, frozen surface)
2. Phase 3 US1 → **STOP and VALIDATE**: `inventory-summary.md` is a complete, regenerable audit with a spot-checked accuracy record — usable for planning even if nothing else ships

### Incremental Delivery

1. US3 register + review → decision artefact for stakeholders (risk disclosure) — deliverable on its own
2. US4-A critical fixes → safety improvements land before any refactor risk
3. US2 increments 1–14 → each increment is a shippable, green, reviewable commit; stop after any increment with a consistent tree
4. Guards → the clean state is enforced from this point
5. US4-B high fixes → each gap its own commit
6. US5 docs + final gates

### Sizing notes (measured)

- ~1,550 Python + ~340 console rows; ~1,140 Python rows carry at least one tag; `state/` (239) and `agents/` (430) are the largest increments — expect them to be split per FR-014a if they run red
- 65 `dry_run: bool` sites cross increments 3–13; `ExecutionMode` lands in increment 1 so every later increment converts only its own sites
- Coverage: the honest ratchet (FR-027a) starts in T011 and rises with each increment; reaching 85 % is out of scope (G-seed 4 deferred in T075), so no test-writing programme is hidden in this feature

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Commit after each task or logical group; increments are single commits
- Never edit `tests/contract/public_surface_snapshot.json` outside an approved contract-changing gap fix
- Stop at any checkpoint to validate the story independently
