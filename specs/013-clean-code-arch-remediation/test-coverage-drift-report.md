# Test Coverage Drift Report: 013 Clean Code & Architecture Remediation

**Purpose**: Record concrete deviations between the current implementation and the repository test-coverage baseline for the active feature slice.
**Created**: 2026-09-13
**Updated**: 2026-09-13
**Feature**: [spec.md](./spec.md)
**Correction Tracking**: Coverage drift remediation tasks are added to [tasks.md](./tasks.md) by `/speckit.test-coverage-drift-control.remediation-plan`.

## Scope

- This report covers test coverage, coverage gates, and test-structure alignment only.
- This report does not cover general coding standards, domain correctness, product behavior, or unrelated constitution-gate evidence.
- Evidence for newly observed findings reflects the current implementation tree (branch `feature/ado-agentic-ai`, HEAD `74d2388`). Evidence retained with historical findings remains a point-in-time record from the review that captured it.
- No prior `test-coverage-drift-report.md` existed, so every finding below is new and `Created` equals `Updated`.
- Coverage figures are reused from the feature's own most recent full measurement, [`run-gap-071-full.txt`](./run-gap-071-full.txt); no fresh coverage run was taken for this review.

## Coverage Baseline

Coverage definition reference files loaded and used as review evidence:

| File | Clauses used |
| --- | --- |
| `.specify/memory/constitution.md` | `:86-92` Principle VI (NON-NEGOTIABLE): every function and meaningful code path MUST be covered by automated tests (unit, integration or contract as appropriate); **at least 85 % line coverage on the `ado2gh` package, enforced in CI**; new code MUST not reduce coverage below the threshold without a documented, approved exception in the plan's Complexity Tracking table. `:103` testing stack is `pytest` with `pytest-cov` reported in CI. `:110` Quality Gate 1 — before merge, all tests pass and coverage >= 85 % on `ado2gh`. `:117` test tasks are mandatory, not optional. |
| `AGENTS.md` | `:48` names `pytest tests/` as the test command. Defines **no** coverage percentage, gate or test-structure rule; it contributes no baseline clause beyond the command name. |
| `CLAUDE.md` | `:265-266` coverage ratchet — CI runs `pytest --cov=ado2gh --cov-fail-under=<N>` at `.github/workflows/ci.yml`, `N` currently **62**, raise when a change lifts real coverage, never lower; the 85 % target is outstanding and tracked as a deferred gap with the ratchet as its compensating control. `:256` names the suite as "~1,000 tests in ~100s". Also `HTTP route code lives in services/, never in the ado2gh package` — the structural rule that scopes finding COV-DRIFT-001. |
| `specs/013-clean-code-arch-remediation/spec.md` | `:242` FR-027 — at completion the full Python suite, lint and type checks MUST pass, and the console's existing type-check, lint and tests MUST pass; **no new coverage threshold is introduced for the console**. `:243` FR-027a — coverage MUST be measured with **no package excluded from measurement**; the enforced threshold MUST equal the honest measured figure, raised after every increment and never lowered; reaching the constitutional 85 % is not in scope and the shortfall is carried as a high gap with a follow-up owner. `:267` SC-004 — full suite passes, coverage at or above the ratchet carried from the previous increment, test count at or above the post-stabilisation baseline minus tests exclusively exercising deleted functions, console checks pass. `:31`, `:37`, `:57`, `:62` clarifications fixing the ratchet decision and the console gate. |
| `specs/013-clean-code-arch-remediation/plan.md` | `:264` post-stabilisation baseline — **828 collected**, 807 passed / 30 skipped at T011 (`9c83a29`), recorded as the SC-004 baseline test count. `:505-524` § Coverage measurement — operator decision 2026-09-07: delete the `omit` list, set `--cov-fail-under` to the honest whole-package figure, ratchet per increment. `:291` Constitution Check records Principle VI as **PASS with documented exception**. `:209-214` latest measurement, ratchet left at 62. |
| `.github/workflows/ci.yml` | `:36` the enforced gate — `pytest --cov=ado2gh --cov-fail-under=62`. `:48` console job runs `npm test` with no coverage flag. |
| `pyproject.toml` | `:83-86` coverage flags live in CI, `addopts = ""`. `:88-90` `[tool.coverage.run]` carries no `omit` key (FR-027a honesty guarantee). |
| `.specify/templates/tasks-template.md` | `:88`, `:111`, `:134` — **derived** test-structure baseline: each user story carries a contract test in `tests/contract/` and an integration test in `tests/integration/` for its user journey; `:157` additional unit tests in `tests/unit/` are "(if requested)", i.e. secondary. Marked **derived** because neither the constitution nor `AGENTS.md` mandates integration-first; the constitution says only "unit, integration, or contract as appropriate". |

Measured state at review time (from `run-gap-071-full.txt`):

- `TOTAL 18116 6773 63%`; `Required test coverage 62% reached. Total coverage: 62.61%`
- `1 failed, 1103 passed, 30 skipped, 3398 warnings in 102.95s` — 1,134 collected against the SC-004 baseline of 828
- Ratchet trail 56 -> 58 -> 59 -> 60 -> 61 -> 62; gate currently 62, measured 62.61 %
- Console: 55 vitest files / 182 tests passing (`run-console-fix-vitest.txt`), no coverage provider installed

## Findings

### COV-DRIFT-001: The `services/` package is outside coverage measurement entirely

**Status**: Pending
**Severity**: High
**Diverges from**:

- `spec.md:243` FR-027a — "Coverage MUST be measured [with] no package excluded [from] measurement"
- `.specify/memory/constitution.md:86-89` Principle VI — "Every function and meaningful code path MUST be covered by automated tests"
- `CLAUDE.md` § Package Structure — "HTTP route code lives in `services/`, never in the `ado2gh` package"

**Evidence**:

- `.github/workflows/ci.yml:36` — `pytest --cov=ado2gh --cov-fail-under=62`; the `--cov` selector names one package
- `pyproject.toml:88-90` — `[tool.coverage.run]` has no `omit` key and no `source` key, so nothing widens the selector
- `services/` — 26 Python modules, 8,609 lines, measured this review; no coverage figure for any of them exists anywhere in the feature's run artefacts
- `git diff --stat 9c83a29..HEAD -- services/` — 23 files changed, +5,582 / -1,776, i.e. 7,358 lines of churn inside this feature, after the coverage baseline was frozen
- `docs/STRUCTURAL_CHANGELOG.md:624` — "013 Increment 13: `services/agent/`"; `:331` adds `services/accelerator_api/routes/migrate_guard.py` as the single live-execution choke point for the nine `/v1/migrate/*` routes (GAP-007, FR-025)
- Modules under `services/` with no direct import reference anywhere in `tests/`: `accelerator_api/auth_routes.py` (595), `accelerator_api/routes/_shared.py` (405), `accelerator_api/routes/approval_routes.py` (154), `accelerator_api/routes/migrate_scope.py` (265), `accelerator_api/routes/pipeline_routes.py` (312), `accelerator_api/routes/profile_credential_routes.py` (329), `accelerator_api/routes/profile_routes.py` (795), `accelerator_api/routes/proxy_routes.py` (380), `accelerator_api/routes/settings_routes.py` (798), `agent/routes/message_routes.py` (175), `agent/routes/model_routes.py` (46), `agent/routes/plan_routes.py` (123)

**Description**:

FR-027a's whole point is that the CI number is honest — no package excluded, no `omit` list, so the ratchet measures a real denominator. The `omit` list was duly deleted at T011, but the selector was never widened past `ado2gh`. The repository's own structural rule puts every HTTP route in `services/`, so the package that carries the entire externally reachable surface of both FastAPI services — including `migrate_guard.py`, the single live-execution choke point installed by this feature for GAP-007/FR-025 — contributes zero statements to the 18,116 the ratchet is computed over. The feature then rewrote 7,358 lines inside that package after freezing its baseline, with no coverage signal to detect what the rewrite left unexercised.

The twelve modules listed above are exercised, if at all, only indirectly through `TestClient(app)`; whether any given branch in them runs is precisely what the missing instrumentation makes unknowable. That is the drift: not a proven low number, but the absence of a number for 8,609 lines of project-owned code.

Concrete correction to apply: change `.github/workflows/ci.yml:36` to `pytest --cov=ado2gh --cov=services --cov-fail-under=<newly measured honest figure>`, take the combined figure once on a green tree, and set the gate to it under FR-027a's never-lowered rule. Then add black-box route tests where the new number shows holes, starting with the four largest uninstrumented modules: `tests/contract/test_settings_routes_contract.py` and `tests/contract/test_profile_routes_contract.py` asserting status code and response schema for each registered route, `tests/integration/test_auth_routes_journey.py` covering register -> login -> session -> capability-denied for `services/accelerator_api/auth_routes.py`, and `tests/integration/test_proxy_routes.py` asserting the proxy forwards and masks credentials. Note the combined figure will be lower than 62.61 % on first measurement; FR-027a's never-lowered rule governs the same denominator, so widening the denominator requires re-baselining the gate rather than treating the drop as a regression.

### COV-DRIFT-002: The constitutional 85 % coverage floor is unmet by 22.4 points

**Status**: Pending
**Severity**: High
**Diverges from**:

- `.specify/memory/constitution.md:89-90` Principle VI — "at least **85 %** line coverage on the `ado2gh` package, enforced in CI"
- `.specify/memory/constitution.md:110` Quality Gate 1 — "Before merge: All tests pass; coverage >= 85 % on `ado2gh`"

**Evidence**:

- `run-gap-071-full.txt` — `Required test coverage 62% reached. Total coverage: 62.61%`; `TOTAL 18116 6773 63%`
- `.github/workflows/ci.yml:36` — the enforced gate is 62, not 85
- `plan.md:291` Constitution Check — Principle VI recorded as "**FAIL at baseline, not caused by this feature** ... PASS with documented exception"
- `plan.md:505-524` § Coverage measurement — operator decision 2026-09-07 scoping the climb to 85 % out of this feature
- `gap-register.md:552` GAP-022 (GAP-TOOL-01), `status: deferred`, follow-up owner `operator`, compensating control = the never-lowered ratchet

**Description**:

6,773 of 18,116 statements in `ado2gh` have no test exercising them. Principle VI is NON-NEGOTIABLE and the merge gate names 85 %, so the shortfall is recorded here as required drift even though it predates the feature. The constitution's own escape hatch was used correctly: `plan.md:291` carries the documented, approved exception in the Constitution Check, and GAP-022 carries the deferral with a named compensating control.

This finding exists to keep the shortfall visible in the coverage record, not to reopen the scoping decision. Remediation bounded by that decision is: keep `--cov-fail-under` monotonically non-decreasing, raise it whenever a measured figure exceeds it, and leave the climb to 85 % with GAP-022's follow-up owner. The findings below name where the missing 6,773 statements actually are, which is the practical route to raising the number.

### COV-DRIFT-003: External-service client modules are the least-covered code on the critical migration path

**Status**: Pending
**Severity**: High
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `.specify/memory/constitution.md:94-95` Principle VI rationale — "Migration bugs can corrupt thousands of repositories; test depth is the primary safety net"
- `.specify/templates/tasks-template.md:111` (derived) — integration tests are the default for external-service-facing workflows

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/clients/ado_client.py` 233 statements, **188 missing, 19 %**; `ado2gh/clients/gh_client.py` 149 statements, **103 missing, 31 %**; `ado2gh/clients/gh_token_manager.py` 144 statements, **90 missing, 38 %**
- `git diff --numstat 9c83a29..HEAD` — 283 lines churned in `ado_client.py`, 419 in `gh_client.py` during this feature
- `ado2gh/clients/ado_client.py` has **no** dedicated test module anywhere under `tests/`; the 8 test files that reference it reach it incidentally through higher-level flows
- `tests/core/test_gh_client.py` is the only dedicated client test module and is **10 lines holding a single test**, `test_gh_client_token_manager_property`, which asserts a property accessor and nothing about HTTP behaviour
- `ado2gh/clients/gh_token_manager.py` has no dedicated test module despite being the rotation and rate-limit component

**Description**:

These two modules are every byte the platform exchanges with Azure DevOps and GitHub — repo enumeration, mirror push targets, branch policy reads, rate-limit handling. Four fifths of `ado_client.py` and two thirds of `gh_client.py` are unexercised, and this feature rewrote 702 lines across them while the only dedicated client test in the repository remained a ten-line property assertion. `gh_token_manager.py` at 38 % compounds it: `CLAUDE.md` § Key Patterns names it as the component that rotates tokens per API call and updates rate limits from response headers, which is exactly the kind of header-parsing logic that fails silently.

Tests to add, highest value first: a new `tests/unit/test_ado_client.py` and an expansion of the existing `tests/core/test_gh_client.py`, both driving every public method against `requests_mock`/`responses` fixtures and asserting URL, method, pagination continuation, and the raise-vs-return branch for 401/403/404/409/429; `tests/unit/test_gh_token_manager_rate_limits.py` asserting rotation on `X-RateLimit-Remaining: 0`, correct parsing of `X-RateLimit-Reset`, and behaviour when every token is exhausted; `tests/integration/test_client_retry_policy.py` asserting the shared `ado2gh/http_utils.py` session retries the statuses it claims to and gives up where it should.

### COV-DRIFT-004: The destructive `ado-cleanup` path sits at 23 % coverage

**Status**: Pending
**Severity**: High
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `spec.md:186-187` CA-003 / CA-004 — every state change auditable at start MUST remain auditable
- `.specify/templates/tasks-template.md:111` (derived) — integration tests are the default for user journeys

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/core/ado_cleanup.py` 133 statements, **103 missing, 23 %**
- `git diff --numstat 9c83a29..HEAD` — 109 lines churned in this feature
- Only two test files reference `ado_cleanup` at all: `tests/core/test_rollback.py` and `tests/unit/test_gap_018_dry_run_default.py`
- `CLAUDE.md` § CLI Commands — `ado-cleanup` disables ADO pipelines, pushes a `MIGRATION_NOTICE.md` redirect, and optionally archives the ADO repo; § ADO-Specific Design Decisions confirms it is a real, irreversible post-migration step

**Description**:

This is the most destructive command the CLI ships and it is step 10 of the documented execution workflow. 103 of its 133 statements are unexercised. The one safeguard test that touches it, `test_gap_018_dry_run_default.py`, asserts the dry-run default; nothing asserts what the live path actually does, nothing asserts the audit record is written, and nothing asserts the archive step is skipped when the preceding validation failed. Coverage this thin on an irreversible operation is the specific failure mode Principle VI's rationale is written about.

Tests to add: `tests/integration/test_ado_cleanup_journey.py` driving disable-pipelines -> push-notice -> archive against a faked `ADOClient`, asserting call order, that a failure at any step aborts the remaining steps, and that `--dry-run` performs zero mutating calls; `tests/core/test_ado_cleanup_audit.py` asserting an audit event is emitted for each of the three sub-operations with the repo identifier redacted per `ado2gh/audit/redaction.py`; `tests/unit/test_ado_cleanup_archive_guard.py` asserting archive is refused when validation has not recorded a successful commit-SHA match.

### COV-DRIFT-005: The PostgreSQL storage backend is effectively untested at 17-20 %

**Status**: Pending
**Severity**: Medium
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `spec.md:117` critical test (c) — a gap qualifies as critical if it can lose or corrupt migration state such that a run cannot be resumed or its outcome verified

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/state/postgres_db.py` 186 statements, **149 missing, 20 %**; `ado2gh/state/postgres_risk_gates_scan_mixin.py` 163 statements, **135 missing, 17 %**; `ado2gh/state/postgres_agentic_users_mixin.py` 164 statements, **134 missing, 18 %**; `ado2gh/state/job_store.py` 242 statements, **103 missing, 57 %**
- `git diff --numstat 9c83a29..HEAD` — 456 lines churned across the three Postgres modules in this feature
- `tests/unit/test_gap_029_backend_parity.py` exists and asserts backend parity, but no test in `tests/` connects to or fakes a Postgres cursor; there is no skip marker either, so the gap is silent rather than declared

**Description**:

`CLAUDE.md` § State Persistence names PostgreSQL as the production backend (`docker-compose.prod.yml`), while SQLite is the local one. The suite exercises SQLite and leaves 418 statements of the production backend unexecuted, so a SQL-level defect introduced by this feature's 456 lines of churn — a column renamed in one backend only, a mixin method whose parameter order drifted — would pass CI and surface first in a production migration run. `test_gap_029_backend_parity.py` compares surfaces, not behaviour, so it will not catch a query that is well-formed but wrong.

Tests to add: `tests/core/test_postgres_backend_behaviour.py` running the same assertions as the SQLite state tests against a fake DB-API connection (`psycopg2` is not needed; a stub cursor recording executed SQL is enough) to assert the emitted SQL and parameter binding per method; extend `tests/unit/test_gap_029_backend_parity.py` to compare method signatures and emitted column sets between `sqlite_db.py` and `postgres_db.py` including the mixins; `tests/core/test_postgres_mixins_round_trip.py` for `postgres_risk_gates_scan_mixin` and `postgres_agentic_users_mixin` asserting each write method's SQL names the columns its read counterpart selects.

### COV-DRIFT-006: The pipeline inventory -> extract -> transform chain leaves 587 statements unexercised

**Status**: Pending
**Severity**: Medium
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `.specify/templates/tasks-template.md:88` (derived) — contract tests for the surfaces a user journey crosses

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/api/pipeline_steps.py` 618 statements, **453 missing, 27 %** (the largest single uncovered module in the package); `ado2gh/pipelines/extractor.py` 170 statements, **137 missing, 19 %**; `ado2gh/pipelines/inventory.py` 163 statements, **136 missing, 17 %**; `ado2gh/pipelines/transform/transformer.py` 268 statements, **141 missing, 47 %**; `ado2gh/pipelines/resolve/template_resolver.py` 186 statements, **82 missing, 56 %**
- `git diff --numstat 9c83a29..HEAD` — 392 lines churned in `pipeline_steps.py`, 121 in `extractor.py`, 123 in `inventory.py`, 104 in `transformer.py`
- Existing tests referencing `pipeline_steps` (`tests/core/test_pipeline_steps_profile_merge.py`, `tests/unit/test_pipeline_plan.py`, three agent gate tests) cover the profile-merge and plan-shape slices only

**Description**:

This chain is steps 2-3 of the documented execution workflow and produces the YAML that ops teams run after migration. `pipeline_steps.py` alone accounts for 453 of the package's 6,773 missing statements — 6.7 % of the entire coverage shortfall in one file — and this feature rewrote 392 of its lines. The transform path is where a silent defect is most expensive: a wrongly converted task emits a workflow that runs and does the wrong thing, rather than failing loudly.

Tests to add: `tests/pipeline/test_pipeline_steps_conversion_matrix.py` as a table-driven test with one row per supported ADO task type, asserting the emitted Actions step for each, plus the explicit assisted/manual classification for unsupported types; `tests/pipeline/test_extractor_variants.py` covering classic-vs-YAML pipeline definitions, templates, and the malformed-definition branch; `tests/pipeline/test_inventory_pagination.py` asserting the inventory walk pages correctly and writes one `pipeline_inventory` row per pipeline; `tests/contract/test_transform_output_schema.py` freezing the shape of the generated workflow YAML so a transformer change cannot silently alter it.

### COV-DRIFT-007: Agent PEV orchestration nodes range from 20 % to 41 %

**Status**: Pending
**Severity**: Medium
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `spec.md:186-187` CA-003 / CA-004 — secret masking and audit coverage must hold across the agent surface

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/agents/migration_agent/nodes/orchestrator.py` 367 statements, **292 missing, 20 %**; `runtime/orchestrator.py` 245 statements, **155 missing, 37 %**; `nodes/validator_investigation.py` 301 statements, **179 missing, 41 %**; `nodes/orchestrator_tools.py` 203 statements, **137 missing, 33 %**; `tools/validator_tools.py` 121 statements, **87 missing, 28 %**; `nodes/planner.py` 254 statements, **112 missing, 56 %**; `nodes/executor/node.py` 174 statements, **93 missing, 47 %**; `nodes/executor/pipeline.py` 164 statements, **85 missing, 48 %**
- `git diff --numstat 9c83a29..HEAD` — 374 lines churned in `runtime/orchestrator.py`, 178 in `validator_investigation.py`, 116 in `nodes/orchestrator.py`
- 19 test files exist under `tests/agent/`, but 1,140 statements remain unexecuted across the eight modules above

**Description**:

The four-agent PEV loop is the feature surface `CLAUDE.md` describes as enforcing dry-run-by-default (CA-001), destructive-operation confirmation (CA-002) and secret masking (CA-003). The guardrail modules themselves are better covered — the GAP regression tests under `tests/agent/` and `tests/auth/` target them specifically — but the orchestration and validation nodes that decide *when* those guardrails are consulted are not. `nodes/orchestrator.py` at 20 % with 292 missing statements means the routing logic between orchestrator, planner, executor and validator is largely unproven, including the retry and iteration-cap branches (max 20 iterations, 3 PEV retries) that stop a runaway loop.

Tests to add: `tests/agent/test_orchestrator_routing_matrix.py` driving the conditional edge function with each `AgentState` shape and asserting the next node, including both cap branches; `tests/agent/test_validator_investigation_paths.py` covering each investigation outcome (pass, retry-recommended, blocked) and asserting the recommendation the validator returns; `tests/agent/test_orchestrator_tools_errors.py` asserting each orchestrator tool's failure branch returns a structured error rather than raising; `tests/agent/test_executor_node_resume.py` asserting a mid-run interruption resumes from the last checkpoint rather than restarting.

### COV-DRIFT-008: Reporting and scan output paths sit at 15 % and 34 %

**Status**: Pending
**Severity**: Medium
**Diverges from**:

- `.specify/memory/constitution.md:86-89` Principle VI — every meaningful code path covered
- `spec.md:186` CA-003 — no secret value may appear in a report or persisted artefact

**Evidence**:

- `run-gap-071-full.txt` — `ado2gh/reporting/reporter.py` 171 statements, **146 missing, 15 %** (the lowest percentage in the package); `ado2gh/api/migration_scan.py` 227 statements, **150 missing, 34 %**; `ado2gh/reporting/post_migration_validator.py` 201 statements, **95 missing, 53 %**
- `git diff --numstat 9c83a29..HEAD` — 54 lines churned in `reporter.py`, 293 in `migration_scan.py`
- Only `tests/unit/test_gap_018_dry_run_default.py` references `ado2gh.reporting.reporter`

**Description**:

`reporter.py` backs `ado2gh report --format html|json|csv` and is the artefact operators archive and circulate; at 15 % coverage, two of its three output formats are almost certainly never rendered in the suite. That matters beyond formatting: CA-003 forbids a secret value reaching a persisted artefact, and a report writer that is never executed cannot demonstrate its redaction path runs. `post_migration_validator.py` at 53 % is the commit-SHA verification that `CLAUDE.md` calls the proof code actually transferred — half of it unexercised.

Tests to add: `tests/unit/test_reporter_formats.py` rendering the same fixture through html, json and csv and asserting each output parses and contains the expected row count; `tests/unit/test_reporter_redaction.py` asserting a fixture carrying a PAT-shaped value emits the mask in all three formats; `tests/core/test_post_migration_validator_mismatch.py` asserting a SHA mismatch, a missing target branch and an empty source repo each produce the documented validation verdict rather than a pass.

### COV-DRIFT-009: The feature added 47 test files and none under `tests/integration/`

**Status**: Pending
**Severity**: Medium
**Diverges from**:

- `.specify/templates/tasks-template.md:88`, `:111`, `:134` (**derived** baseline) — each user story carries a contract test in `tests/contract/` and an integration test in `tests/integration/` for its user journey; `:157` places additional unit tests as "(if requested)"
- `.specify/memory/constitution.md:114-115` — user stories MUST remain independently testable

**Evidence**:

- `git diff --name-status 9c83a29..HEAD -- tests/` — 47 files added: 18 `tests/unit/`, 9 `tests/auth/`, 7 `tests/core/`, 5 `tests/pipeline/`, 4 `tests/agent/`, 4 `tests/contract/`, **0 `tests/integration/`**; zero test files deleted
- `tests/integration/` holds 6 files in total against 77 in `tests/unit/`
- `git diff --stat 9c83a29..HEAD -- services/` — 7,358 lines of HTTP service churn over the same window

**Description**:

Marked derived because the constitution permits "unit, integration, or contract as appropriate" and does not mandate integration-first; the expectation comes from the local Spec Kit task template, which is the only artefact in the repository that states a default. Against that default, the feature's test additions skew to unit scope: 18 unit files and 4 contract files, no integration files, while rewriting the entire service route layer. This is the test-structure counterpart of COV-DRIFT-001 — the code with the least coverage instrumentation also received the fewest journey-level tests.

The concrete corrections are the integration tests already named in COV-DRIFT-001, -003, -004 and -006 (`tests/integration/test_auth_routes_journey.py`, `test_proxy_routes.py`, `test_client_retry_policy.py`, `test_ado_cleanup_journey.py`). Landing those closes this finding as a side effect; no separate work is implied.

### COV-DRIFT-010: `pyproject.toml` still tells readers CI enforces 85 %

**Status**: Pending
**Severity**: Low
**Diverges from**:

- `spec.md:243` FR-027a — the enforced threshold must equal the honest measured figure
- `.specify/memory/constitution.md:103` — coverage is measured via `pytest-cov` and reported in CI

**Evidence**:

- `pyproject.toml:83` — "Coverage flags live in CI (`pytest --cov=ado2gh --cov-fail-under=85`), not here"
- `.github/workflows/ci.yml:36` — the actual gate is `--cov-fail-under=62`
- `CLAUDE.md:265-266` correctly states 62, so the two files disagree

**Description**:

The inline comment names a value the repository abandoned at T011 when the ratchet replaced the aspirational gate. A contributor reading `pyproject.toml` to find the coverage policy — the natural place to look — gets 85, runs locally, sees a failure that CI would not produce, and either files a false regression or edits the gate. The correction is a one-line edit to the comment naming the ratchet rather than a number, e.g. "Coverage flags live in CI (`pytest --cov=ado2gh --cov-fail-under=<ratchet>` in `.github/workflows/ci.yml`)". This is distinct from GAP-048, which is registered against `CLAUDE.md` only.

### COV-DRIFT-011: Four task checkboxes covering regression-test and revert-proof work are unticked though the work is evidenced complete

**Status**: Pending
**Severity**: Low
**Diverges from**:

- `.specify/memory/constitution.md:117` — "Test tasks are mandatory, not optional; each user story includes tests"
- `spec.md:132` US4 Independent Test — for each critical gap, an automated check exists that fails if the gap is reintroduced
- `.specify/templates/tasks-template.md` task-state syntax — only `- [ ]` and `- [X]` are defined; there is no marker for "done but unticked"

**Evidence**:

- `tasks.md:52` T005 (restore the `migration_operations` table) — unticked; `run-gap-071-full.txt` contains zero occurrences of `migration_operations`, so the failure class it names no longer exists
- `tasks.md:127-129` T038 / T039 / T040 (write the failing regression test first, fix at the choke point, take the revert proof for every critical gap) — unticked; all 20 `severity: critical` entries in `gap-register.md` carry `status: remediated` with a populated `regression_check` and `revert_proof`, several naming "the T040 implementation agent"
- `gap-register.md` status totals: 49 remediated, 28 open, 2 deferred, 1 disputed — no critical entry is open
- `tasks.md:369` `- [P]` is a legend line, not a task

**Description**:

The four boxes are clerically stale rather than open work, and the evidence for that is in this feature's own artefacts. The cost is not cosmetic in this context: this hook's own prerequisite gate reads `tasks.md` to decide whether implementation is finished, and on a strict reading of the unticked boxes it must refuse to run. The same stale state will block `/speckit.test-coverage-drift-control.remediation-plan` and any later audit that treats the task file as the completion record, and it makes the regression-test trail for the 20 critical gaps look unfinished when the register shows it is not. The correction is to tick T005, T038, T039 and T040 in `tasks.md`, or to add a short note under each recording where its completion evidence lives.

## Notes

- **Prerequisite gate.** `tasks.md` carried four unticked task boxes (T005, T038, T039, T040) at review time. The gate was treated as satisfied on the documented evidence above rather than refusing the run: the failure class T005 names is absent from the latest full suite, and every critical gap T038-T040 govern is `status: remediated` with a regression check and revert proof recorded. The stale state is itself reported as COV-DRIFT-011 rather than being silently accepted. The full gate evaluation is in [`run-hook-coverage-drift.txt`](./run-hook-coverage-drift.txt).
- **Script variant.** The skill prescribes `.specify/scripts/bash/check-prerequisites.sh`. This repository ships only the PowerShell variant; `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks` was run instead and returned `FEATURE_DIR=D:\GitHub\Work\ADO_to_GitHub_Migration\specs\013-clean-code-arch-remediation` with `AVAILABLE_DOCS` covering research, data-model, contracts, quickstart and tasks.
- **No fresh coverage run.** Figures are reused from `run-gap-071-full.txt`, the feature's most recent full measurement, to avoid a concurrent pytest run against a tree other agents are editing. Every percentage, statement count and missing-line count cited above is read from that file.
- **The single failing test is local-only, confirmed.** `tests/unit/test_scripts_cleanup.py::test_only_scripts_dev_remains` asserts `scripts/dev/` holds at most three files. `git ls-files scripts/` returns exactly three tracked files, so the assertion holds in CI; the local working tree carries four additional untracked `.mjs` agent-bridge files. It is not raised as a finding.
- **Console coverage is deliberately absent, not drift.** `apps/migration-ui` runs 55 vitest files / 182 tests with no coverage provider in `package.json` and no threshold in `vitest.config.ts`. `spec.md:242` FR-027 and the clarification at `spec.md:37` explicitly state that no new coverage threshold is introduced for the console, so this is recorded as a scope note rather than a finding.
- **Already-registered items not duplicated here.** `CLAUDE.md:256`'s stale test count ("~1,000 tests" against 1,134 collected) is covered by `gap-register.md:1033` GAP-048 (GAP-TOOL-04), `status: open`; its coverage-threshold claim at `CLAUDE.md:265-266` has since been corrected to 62 and is accurate. The 85 % shortfall on `ado2gh` is `gap-register.md:552` GAP-022 (GAP-TOOL-01), `status: deferred`, and is restated here as COV-DRIFT-002 only to keep the coverage record complete. No register entry covers the `services/` exclusion reported as COV-DRIFT-001.
- **SC-004 passes on both of its measurable criteria.** Test count 1,134 collected against the post-stabilisation baseline of 828 (`plan.md:264`), and coverage 62.61 % against the carried ratchet of 62 (`.github/workflows/ci.yml:36`).
- **ChatGPT consultation did not complete.** The project's `CLAUDE.md` asks for a Codex consultation on substantive analysis. One attempt was made with the delegated read-only recipe and failed with the verbatim error: `ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 10:26 AM.` No reply file was produced and no external review is claimed for this report. The prompt that was sent is reproduced in the run log.
