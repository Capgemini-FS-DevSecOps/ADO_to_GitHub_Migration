# Research: Clean-Code Signature Audit & Critical Architecture Remediation

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-07

All numbers below are **measured** in this session against the working tree at
`feature/ado-agentic-ai` (commit `7b64ac2` + uncommitted `.gitignore`), unless marked
*inferred*.

## Baseline measurements

| Metric | Value | How measured |
|--------|-------|--------------|
| Python functions (`ado2gh/` + `services/`) | 1,549 | stdlib `ast` walk (throwaway script) |
| … with zero heuristic tags | 407 (26%) | same |
| … missing docstring | 1,026 (66%) | same |
| … untyped param or return | 291 | same |
| … > 5 params (receiver excluded) | 62 | same |
| … `bool`-typed param | 69 signatures; `dry_run: bool` alone appears 65× repo-wide | `grep ': bool'` |
| … mutable default | 0 | same |
| Web console `.ts`/`.tsx` files under `src/` | 73 files; ~340 function-like definitions | `find` + `grep` |
| Web console exports vs JSDoc blocks | 227 exports, 28 `/**` blocks | `grep` |
| Python tests collected | 877 (`python -m pytest --collect-only -q`) | CLAUDE.md says ~970 — doc drift |
| Python suite result | **red**: 50 failed, 790 passed, 30 skipped, 16 errors — identical with and without coverage instrumentation, so the failures are real, not `pytest-cov` artefacts | `python -m pytest --cov=ado2gh -q` (90 s) and `python -m pytest -q` (84 s) |
| Coverage, current omit list (`cli`, `state`, `core` omitted) | **60 %** (15,036 statements, 6,088 missed) — CI gate claims 85 % | same run |
| Coverage, no omit list | **56 %** (18,017 statements, 7,885 missed) — the three omitted packages add 2,981 statements and cost 4 points | `python -m pytest --cov=ado2gh --cov-config=<no-omit cfg>` |
| Web console tests | 38 tests in 7 files, all pass in 0.6 s | `npx vitest run` |
| Web console type-check | clean (`tsc --noEmit`, TS 5.9.3 local) | `npx tsc --noEmit` |
| CLI entry points | 21 commands + 2 groups + 1 root (24 decorators) | `grep @…command/group` |
| HTTP routes | 131 unique method+path | `grep @router.<verb>("…")` |
| Env var names referenced in code | 52 | `grep` for `ADO2GH_|GH_|ADO_|LLM_|ACCELERATOR_|AGENT_` |
| Persisted tables | 25 `CREATE TABLE` names | `grep` in `ado2gh/state` |
| Existing contract tests | 14 modules in `tests/contract/` | `ls` |
| Coverage gate | CI: `pytest --cov=ado2gh --cov-fail-under=85`; **`pyproject.toml` omits `ado2gh/cli/*`, `ado2gh/state/*`, `ado2gh/core/*` from measurement**; **measured 60 %** with those omits (15,036 stmts, 6,088 missed) — the gate is failing at baseline | `pyproject.toml`; `python -m pytest --cov=ado2gh` + `coverage report` |
| Type gate | CI: `mypy ado2gh/ … \|\| true` (never fails); `ignore_errors = true` for `ado2gh.state.*`, `ado2gh.cli.*`, `ado2gh.pipelines.*` | `ci.yml`, `pyproject.toml` |
| Locally installed | `vulture 2.16`, `pytest-cov`, `pyright`, `ast-grep`, `ast-outline`; **not** installed: `ruff`, `mypy` | `python -m …` |
| Docstring style in use | Google style (`Args:` / `Returns:`) — only 3 files use it; no `:param` or numpy style anywhere | `grep` |
| Existing enumerations | 19 `(str, Enum)` classes; **no** execution-mode enum (`dry_run` is always a bare `bool`) | `grep class …Enum` |
| Agent tool names referenced outside their definitions | 20 files (tests, prompts, UI); prompts reference `github_api` 12×, `ado_api_query` 10×, `call_accelerator` 7× | `grep` |
| Secret-masking implementations | 3 separate: `migration_agent/utils.py:mask_secrets`, `assignments/audit.py:redact_payload`, `core/scopes/git_scope.py:_redact` | `grep def *mask|redact` |
| Live-execution gate references | spread across 6 files (`policies.py`, `route_helpers.py`, `platform_rbac.py`, `auth/service.py`, `pipeline_routes.py`, `session_routes.py`) | `grep` |
| Package import edges | `state → api`, `clients → core`, `api → cli`, `auth ↔ api`, `agents ↔ api` (layering inversions / cycles) | `grep '^from ado2gh\.'` per package |
| Orphan-guard allowlist | still lists `ado2gh.reporting.boards_gaps`, deleted 2026-08-25 | `test_no_orphaned_modules.py` vs changelog |
| Local `pytest` shim | bare `pytest` exits 1 with no output; `python -m pytest` works | run |

## Decisions

### R1 — Python inventory tags come from ruff, not a custom tagger

**Decision**: The Python inventory is produced by a single stdlib script
(`specs/013-clean-code-arch-remediation/scripts/function_inventory.py`) that merges three
inputs into `inventory.json`:

1. `ruff check ado2gh/ services/ --output-format json` with an *inventory-only* rule set
   (passed on the command line, not written to `pyproject.toml` until cleanup is done):
   `D1` (missing docstring), `ANN` (missing annotations), `FBT001`/`FBT002` (positional
   boolean), `PLR0913` (too many arguments, `--config lint.pylint.max-args=5`), `B006`
   (mutable default), `ARG` (unused argument), `RET501`–`RET503` (inconsistent return).
2. `vulture ado2gh/ services/ --min-confidence 60` for zero-reference candidates.
3. The script's own `ast` pass for the two tags ruff has no rule for: *stale docstring*
   (an `Args:` section whose names differ from the signature) and *keyword-only boolean*
   (FBT only flags positional booleans; the spec flags any behaviour-switch boolean) —
   plus the function list itself with location and signature text.

Three judgement tags — `name_review` (non-verb first token), `stale_docstring` (heuristic
`Args:` mismatch) and `bool_flag` confirmation (data boolean vs behaviour switch) — are
emitted as `proposed_tags`. The reviewer decides each once via
`--confirm/--reject <id>:<tag>`; the decision is stored in the checked-in
`tag-decisions.json` keyed by the function's `state_hash` and reused by every later
regeneration until the signature or docstring changes (clarified 2026-09-07; data-model
§ JudgmentTagDecision). Mechanical tags are deterministic by construction: same source,
same pinned `ruff`/`vulture`, same output (FR-004).

**Rationale**: ruff is already the project's linter (CI pins `ruff==0.15.17`), so the same
rule set becomes the regression guard once cleanup finishes (R4) — the inventory and the
guard cannot drift apart. Installing ruff locally is a dev install matching CI, not a
runtime dependency (SC-003 is about runtime dependencies). The prototype `ast` tagger
written for this research (~80 lines) agreed with `ruff`'s categories but would have to be
maintained separately.

**Alternatives considered**: (a) custom `ast` tagger only — rejected, duplicate of ruff
rules and no CI guard; (b) `pyright --outputjson` for typing — rejected, reports type
*errors* not missing annotations; (c) agent-lsp `blast_radius` per file — kept for
interactive caller lookup during cleanup, not scriptable for the inventory.

**Verify at task time** (per CLAUDE.md rule on config schemas, using
`trafilatura -u https://docs.astral.sh/ruff/rules/ --markdown`): exact rule codes for
ruff 0.15.17, whether `PLR0913` excludes `self`/`cls` from `max-args` (spec requires
receiver excluded — if it counts the receiver, set `max-args=6` for methods via the
script's own count instead), and the `lint.pydocstyle.convention = "google"` key.

**Verified** (T002, 2026-09-07, against `ruff 0.15.17` installed in `.venv` and
`trafilatura -u https://docs.astral.sh/ruff/rules/ --markdown` +
`trafilatura -u https://docs.astral.sh/ruff/settings/ --markdown`):

*Rule codes* — every code assumed by R1 exists and is stable in 0.15.17. The whole
selector string resolves without an "unknown rule" error (measured):

```
ruff check --isolated --select 'D1,ANN,FBT001,FBT002,PLR0913,B006,ARG,RET501,RET502,RET503' \
           --config 'lint.pylint.max-args=5'
```

| Assumed | Resolves to | Status in docs |
|---|---|---|
| `D1` | `D100`–`D107` (`undocumented-public-module/class/method/function/package`, `undocumented-magic-method`, `undocumented-public-nested-class`, `undocumented-public-init`) | all stable since v0.0.70 |
| `ANN` | `ANN001`, `ANN002`, `ANN003`, `ANN201`, `ANN202`, `ANN204`, `ANN205`, `ANN206`, `ANN401` | stable |
| `FBT001` | `boolean-type-hint-positional-argument` | stable |
| `FBT002` | `boolean-default-value-positional-argument` | stable |
| `PLR0913` | `too-many-arguments` | stable since v0.0.238 |
| `B006` | `mutable-argument-default` | stable |
| `ARG` | `ARG001`–`ARG005` (function / method / classmethod / staticmethod / lambda) | stable since v0.0.168 |
| `RET501`–`RET503` | `unnecessary-return-none`, `implicit-return-value`, `implicit-return` | stable |

**Removed codes — do not select individually**: `ANN101` (`missing-type-self`) and
`ANN102` (`missing-type-cls`) were **removed in ruff 0.8.0**. R1 selects the `ANN`
prefix, which simply no longer includes them, so no change is needed — but T016/T071
must never name `ANN101`/`ANN102` explicitly, which would fail the run.

*Receiver count — the open question, answered: `self`/`cls` are **excluded***.
The docs do not state it: the settings page says only "Maximum number of arguments
allowed for a function or method definition (see `PLR0913`)" (`### lint.pylint` →
`#### max-args`, **Default value**: `5`), and the `too-many-arguments` rule page
discusses only the `@typing.override` exemption. The answer is therefore **measured**
against installed `ruff 0.15.17` with `lint.pylint.max-args=5`:

| Probe | Params excl. receiver | Flagged? |
|---|---|---|
| `def m5(self, a, b, c, d, e)` | 5 | no |
| `def m6(self, a, b, c, d, e, f)` | 6 | **yes** — `(6 > 5)` |
| `@classmethod def c5(cls, a, b, c, d, e)` | 5 | no |
| `@staticmethod def s5(a, b, c, d, e)` | 5 | no |
| `def f6(a, b, c, d, e, f)` | 6 | **yes** — `(6 > 5)` |

`m6` reports `6 > 5` while carrying seven parameters including `self`, and `m5`/`c5`
(six including the receiver) do not fire at all. **`max-args` stays at `5`** and the
`gt5_params` tag can be taken straight from `PLR0913`; the R1 contingency of setting
`max-args=6` for methods is **not** needed. The inventory script's own
`param_count` (data-model: "receiver excluded") therefore agrees with ruff by
construction — but T016 still computes it independently, because it is a field on
every row, not just on flagged ones.

*`lint.pydocstyle.convention`* — key path confirmed (`### lint.pydocstyle` →
`#### convention`, values Google / NumPy / PEP 257). Doc warning, load-bearing for
R4/T071: "Enabling a convention will disable all rules that are not included in the
specified convention." Measured: `D100`/`D103` fire identically with and without
`convention = "google"`, so the `D1` family survives the convention — but any later
`D2xx`/`D4xx` addition must be re-checked against it.

*Two firing conditions that confirm R1 item 3 is required* (measured):
`FBT001` fires **only on an annotated `bool` param** — `def f(flag=False)` (no
annotation) yields `FBT002` alone. And a keyword-only boolean
(`def f(*, flag: bool = False)`) fires **neither** rule. In a partly-unannotated
codebase, ruff alone therefore under-reports `bool_flag`; the script's `ast` pass
must supply keyword-only and unannotated boolean parameters, as R1 already specifies.

### R2 — Web console inventory uses the installed TypeScript compiler API

**Decision**: `specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs`
walks `apps/migration-ui/src` with the `typescript` package already in
`devDependencies` (5.9.3 resolved locally) and emits the same `inventory.json` row shape
(`language: "ts"`). Tags: `missing_doc` (exported function/component/hook without a
leading JSDoc block), `untyped` (parameter typed `any` or implicitly any — the latter is
already an error under `strict`), `gt5_params`, `bool_flag` candidate, `unused_param`
(from `tsc --noEmit --noUnusedParameters`), `dead` (export with zero imports across `src/`).

**Rationale**: zero new dependencies; the compiler API gives exact export and JSDoc
positions, which regex cannot for arrow-function components. ~80 lines.

**Alternatives considered**: `ts-morph` (new dev dependency — rejected, FR-008);
`ast-grep` rules (`sg --lang tsx -p 'export function $F($$$A) { $$$ }'`) — workable for
listing but cannot see JSDoc attachment or resolve imports; ESLint + `eslint-plugin-jsdoc`
— ESLint is not installed in the console and would be two new dev dependencies.

### R3 — Docstring convention: Google style

**Decision**: Python docstrings use Google style (`Args:`, `Returns:`, `Raises:`), one
summary line, no type repetition (types live in the signature). Web console exports get a
JSDoc block with a summary line and `@param`/`@returns` only where the name alone is not
self-explanatory.

**Rationale**: the only existing convention in the repo is Google style (3 files); ruff's
pydocstyle convention setting supports it; shortest of the three styles.

**Alternatives considered**: reST `:param:` and numpy — neither present in the repo.

### R4 — Regression guards for the zero-tag state: ruff config + one vitest test

**Decision**: When the last Python increment is green, extend `[tool.ruff.lint] select`
in `pyproject.toml` with the R1 rule set and `lint.pydocstyle.convention = "google"`,
listing the exception-register entries as `# noqa: <code>` with a `ponytail:`/reason
comment (never a blanket `per-file-ignores`). For the console, add
`apps/migration-ui/src/__tests__/exports-documented.test.ts` (vitest, uses the same
compiler-API walker as R2) asserting every export has a JSDoc block, and turn on
`noUnusedParameters` in `tsconfig.json`. Both run in the existing CI jobs.

**Rationale**: the guard reuses tooling already in CI; the "clean" state is enforced, not
just measured (SC-001 cannot silently regress). Lint config keys are guard configuration,
not the runtime configuration keys FR-008 forbids.

**Alternatives considered**: pre-commit only — rejected, not enforced in CI; a custom
pytest that re-runs the inventory and asserts zero tags — rejected, slower and duplicates
ruff.

### R5 — Dead-code detection and the protected entry-point list

**Decision**: A function is a deletion candidate when **both** `vulture` (≥ 60 %
confidence) and the inventory's cross-reference scan find zero references. The
cross-reference scan greps the bare name across `ado2gh/ services/ tests/
apps/migration-ui/src ado2gh/agents/migration_agent/prompts docker-compose*.yml
Dockerfile* scripts/ .github/` so string-based dynamic uses count as references.
The protected entry-point list is **generated** into `protected-entry-points.json` from:
Click-decorated functions, FastAPI route-decorated functions, `__main__` guards,
`test_no_orphaned_modules.py` `_ENTRY_POINTS`/`_DYNAMIC_IMPORTS`/`_STANDALONE_EXECUTABLES`,
inner functions wrapped by `StructuredTool.from_function` in `migration_agent/tools/`,
LangGraph node callables registered in `graph/builder.py`, pytest `conftest.py` fixtures
and hooks, Next.js route-file exports (`page.tsx`, `layout.tsx`, `route.ts`,
`generateMetadata`), and a hand-maintained `protected-entry-points.manual.txt` for
anything the generator cannot see. Candidates on the list are never deleted.

**Rationale**: the previous dead-code pass (changelog 2026-08-25) used exactly
"vulture + cross-reference scan" and deleted ~55 functions without regressions, so the
method is proven here. The user chose automatic deletion; the protected list is the only
brake, so it is generated (complete) rather than typed (forgetful).

**Alternatives considered**: agent-lsp `find_references` per candidate — precise but
interactive-only; keep for spot checks when the two scans disagree.

### R6 — Increment order for cleanup

**Decision**: Fourteen increments, leaf packages first so that callers are updated at most
once per signature:

| # | Increment | Depends on (measured import edges) |
|---|-----------|------------------------------------|
| 1 | root modules `models.py`, `http_utils.py`, `logging_config.py`, `output_dirs.py` + `assignments/` | — |
| 2 | `clients/` | core (inversion, see G-seed) |
| 3 | `state/` | api (inversion), models |
| 4 | `phase/` | models, state |
| 5 | `pipelines/` | clients, models, state |
| 6 | `reporting/` | clients, models, pipelines, state |
| 7 | `core/` | api, clients, phase, pipelines, state |
| 8 | `auth/` | api, state |
| 9 | `api/` | agents, assignments, auth, cli, clients, core, phase, pipelines, reporting, state |
| 10 | `agents/` | api, assignments, auth, core, pipelines, state |
| 11 | `cli/` | api, clients, core, phase, pipelines, reporting, state |
| 12 | `services/accelerator_api/` | api, auth, core, reporting, state |
| 13 | `services/agent/` | agents, api, auth, core |
| 14 | `apps/migration-ui/` | HTTP contracts only |

Each increment: regenerate inventory for the package → apply cleanup → update callers
everywhere → `ruff check` (inventory rule set) on the package → `python -m pytest` →
record coverage and raise `--cov-fail-under` to the new value if it went up (FR-027a) →
commit → append changelog entries. Increment 14 additionally runs `tsc --noEmit` and
`vitest run`.

**Red-increment rule (FR-014a)**: an increment that cannot be made green is reverted to
the last green commit (`git revert`/`reset` of that increment only), the offending
function or module is either added to the exception register with its reason or carved
into its own smaller increment, and the retry must pass before the next package starts.
Nothing red is carried forward.

**Exception-cap rule (FR-005a)**: when adding an exception would push the register past
2 % of the latest `totals.functions`, cleanup of the rule driving the excess stops and
the operator decides (raise cap / amend rule / accept behaviour change) before any further
entry is added. The `--pending` flag and the SC-001 check in quickstart § 5 surface this.

**Rationale**: the dependency map is cyclic in places (`state → api`, `api ↔ agents`,
`api ↔ auth`, `api → cli`), so a perfect topological order does not exist; this order
minimises re-touching. The cycles themselves are architecture gap seeds (G-seed 3).

### R7 — `dry_run: bool` becomes `ExecutionMode`

**Decision**: The one deliberate type addition of this feature: `ExecutionMode(str, Enum)`
with `DRY_RUN` and `LIVE` in the existing `ado2gh/models.py`, replacing `dry_run: bool`
across its 65 signatures. External surfaces keep their shape: `--dry-run` CLI flags, the
`dry_run` HTTP payload field, and persisted `dry_run` columns are converted at the boundary
(`ExecutionMode.DRY_RUN if dry_run else ExecutionMode.LIVE`) and never renamed. Every other
boolean behaviour switch (the remaining ~4 signatures) is split into two intent-named
functions per the clarified default; the shared body, if large, moves to a private helper
that takes the *existing* domain object, never a fresh flag.

**Rationale**: splitting 65 migration/pipeline functions into `x()` / `x_dry_run()` pairs
would duplicate large bodies (the spec's stated fallback condition), and no existing
enumeration covers execution mode (measured: 19 enums, none for this). One type used at
65 call sites satisfies FR-008's ≥ 3-call-site rule; the agent already exposes an
"execution mode" concept (`PATCH /v1/sessions/{id}/execution-mode`), so the name matches
domain vocabulary (Principle IV). Putting 65 entries in the exception register would blow
the 2 % cap (SC-001). Recorded in the plan's Complexity Tracking.

**Alternatives considered**: keyword-only `dry_run: bool` + exception register — rejected
(4.2 % > 2 % cap, and CA-001 paths deserve an explicit type); two functions per site —
rejected (body duplication on the most safety-critical code).

### R8 — Public-contract freeze is enforced by a snapshot test

**Decision**: `tests/contract/test_public_surface_snapshot.py` collects, at test time,
the CLI command tree (from the Click group), every registered HTTP route (method + path)
from both FastAPI apps, the set of env var names referenced in code, and the persisted
table names, and compares them to `tests/contract/public_surface_snapshot.json` committed
**before** the first cleanup increment. Any diff fails; an intentional change (only via an
approved gap fix, FR-024) updates the snapshot in the same commit with the migration note.
The snapshot file is a contract artefact (see `contracts/public-contract-freeze.md`).

**Rationale**: FR-006/SC-002 need a guard that catches an accidental rename of a route or
flag across ~1,900 signature changes; the existing 14 contract test modules cover payload
shapes, not the full surface list. One test, no new dependency.

**Alternatives considered**: relying on the existing contract tests only — rejected, they
do not enumerate the surface; OpenAPI snapshot diff — the accelerator exposes OpenAPI, but
the agent service and CLI do not, so a single mechanism was preferred.

### R9 — Architecture assessment method

**Decision**: The assessment is a reading pass over each component listed in FR-016
against a fixed checklist (six principles × six migration-safety properties), producing
`gap-register.md`. Evidence is a `path:line` or a reproduction command. Ratings use the
spec's critical tests (a)–(e) with (d) restricted to in-code failure handling, then the
high/medium/low definitions. The register is then handed to a **separate review agent**
(`caveman:cavecrew-reviewer` — read-only, cannot edit) with the constitution and the
register as input and the instruction "re-derive each critical/high rating from the cited
evidence; output CONFIRM or DISPUTE with reasoning per gap". Disputes are appended to the
register's "Disputes" table and presented to the operator; remediation of a disputed gap
waits for the decision.

**Scope (FR-016 + FR-016a)**: the components listed in FR-016, plus the deployment and
CI artefacts — `docker-compose*.yml`, `Dockerfile*`, `.github/workflows/*.yml`, and the
`.env.example` template. Findings there are rated **high at most**, under the same rule
that keeps deployment topology out of the critical tests; the one thing that would still
rate critical is a real secret value committed in one of those files, which is a
containment failure rather than a topology limit and is checked explicitly during the
pass.

**Timing (FR-022)**: a gap the reviewer CONFIRMS as *critical* is remediated immediately
after the review pass — before or alongside cleanup — and the affected functions are
cleaned afterwards with the gap's regression check still passing. *High* gaps wait until
increment 14 is green.

**Seeds** (measured facts that the assessment must examine — not pre-rated):

| # | Observation | Principle / property | Likely tier (inferred) |
|---|-------------|----------------------|------------------------|
| G-seed 1 | Three independent secret-masking implementations; no single choke point for logs, audit, and git output | V / secret containment (CA-003) | high; critical if any path writes secrets unmasked |
| G-seed 2 | Live-execution gate logic referenced in 6 files across 3 layers | V / human approval | must trace every path to non-dry-run execution; critical if a bypass exists |
| G-seed 3 | Layering inversions and cycles: `state → api`, `clients → core`, `api → cli`, `api ↔ auth`, `api ↔ agents` | IV | high (violates Principle IV in default config) |
| G-seed 4 | Coverage measurement omits `cli/`, `state/`, `core/` (the migration engine and state layer) while CI claims 85 %; measured 60 % with the omits and 56 % without, so the gate is failing either way | VI | high; closing it means ~5,200 uncovered statements — sized in plan § Coverage measurement, and too large to absorb silently |
| G-seed 5 | `mypy … \|\| true` in CI and `ignore_errors` for `state`, `cli`, `pipelines` | I / VI | high |
| G-seed 6 | `create_state_db()` accepts only a path; backend selection is hidden inside; DynamoDB job store parity with SQLite/Postgres state DB unverified | V / resumability, auditability | assess per spec US3 scenario 5 |
| G-seed 7 | Orphan-guard allowlist references a module deleted 2026-08-25 | III | low |
| G-seed 8 | `services/accelerator_api/routes/_shared.py` builds module-level singletons via `__import__` | IV | medium |
| G-seed 9 | Web console: no linter, 38 tests for ~340 functions, 28 JSDoc blocks for 227 exports | II / VI | medium |
| G-seed 10 | Doc drift: CLAUDE.md test count (~970 vs 877), local `pytest` shim broken | II | low (fixed by FR-028) |
| G-seed 11 | Layout: `ado2gh/assignments/` contains only the audit writer; `ado2gh/api/agentic_routes.py` and `migration_agent/route_helpers.py` hold HTTP-route code outside `services/` | IV | medium/high — candidates for FR-013 moves |
| G-seed 13 | The suite is red on the feature branch (50 failed, 16 errors) and honest coverage is 60 % against a CI gate that asserts 85 % — the gate passes only because `cli/`, `state/`, `core/` are omitted | VI | high — the omit list is what makes a failing gate look green |
| G-seed 12 | Deployment/CI artefacts (FR-016a): compose files, Dockerfiles, workflow files, `.env.example` — check for committed secret values, credentials passed as build args, and workflows that could reach a live migration path without approval | V / secret containment, human approval | high at most; critical only for a committed secret value |

**Rationale**: a fixed checklist makes the second reviewer's job mechanical (SC-006); the
seed table stops the assessment from starting cold but does not pre-empt the rating.

**Alternatives considered**: automated architecture-rule tooling (import-linter,
pydeps) — new dependencies for a one-off assessment; the measured import map above was
produced with `grep` in one call.

### R10 — Regression check per remediated gap

**Decision**: Every remediated gap gets one automated check whose docstring names the gap
id (`GAP-###`) and whose failure mode is described in the register entry. Python gaps →
one test under the matching `tests/<domain>/`; guard-type gaps (lint/type/coverage
configuration) → the CI job itself is the check, with the register pointing at the
`ci.yml` line; console gaps → vitest.

**Rationale**: SC-005 demands a check whose failure against the unfixed code was
observed. The proof is performed **once, at authoring time** (revert the fix, run the
check, see it fail, restore) and the register entry records the command, the date, and
who ran it (FR-023); it is not re-run in CI.

### R11 — Structural changelog and documentation updates

**Decision**: Append one section per increment to `docs/STRUCTURAL_CHANGELOG.md` using
the existing table format (`File | New Path | Change Type | Reason | Verified | Test
Status | Date`); deleted functions are listed one row per module with the function names
in the Reason column and the number of tests removed with them (needed for SC-004's
adjusted test-count rule). `docs/ARCHITECTURE.md`, `CLAUDE.md`, `README.md`, and every other
markdown file under `docs/` are updated in the final increment (FR-028); SC-007 is
checked by a one-off script that extracts every backticked path/`module.py` token from
that document set and asserts it exists. Completed specs under `specs/001-*`…`012-*` are
historical records and are excluded from both the update and the check — a stale path in
one of them is not a defect (clarified 2026-09-07).

### R12 — Local toolchain notes

- Run Python tests as `python -m pytest`; the bare `pytest` shim on this machine exits 1
  silently (measured).
- Install the CI-pinned linter once: `pip install "ruff==0.15.17" mypy` (dev-only).
- `pytest-cov` is already installed locally despite the CLAUDE.md note saying otherwise
  (measured: `import pytest_cov` succeeds).
- Console: `npx tsc --noEmit` and `npx vitest run` from `apps/migration-ui/`.
- The bare `pip` shim is broken the same way as `pytest`; install with
  `.venv/Scripts/python.exe -m pip`.

**Installed toolchain** (T001, 2026-09-07 — `.venv` only, no global installs).
Versions as printed by `.venv/Scripts/python.exe -m <tool> --version`:

| Tool | Printed | Pin | Match |
|---|---|---|---|
| `ruff` | `ruff 0.15.17` | `0.15.17` (CI) | yes |
| `vulture` | `vulture 2.16` | `2.16` (R1) | yes — was already installed |
| `mypy` | `mypy 2.3.1 (compiled: yes)` | unpinned (`pip install … mypy`) | n/a |

`ruff` and `mypy` were absent before T001; `vulture 2.16` was already present.
`mypy` resolved to **2.3.1**, which is not pinned anywhere in the repo — CI pins
only `ruff`. If a later task diffs `mypy` output against CI, pin it first.
Transitive installs: `ast-serialize 0.10.0`, `librt 0.15.0`, `mypy_extensions 1.1.0`,
`pathspec 1.1.1`.

### R13 — SC-008 comprehension sample is scored by an LLM agent with no repository access

**Decision**: After the final regeneration, draw 40 rows from `inventory.json` with a
fixed seed (`random.Random(13).sample`), extract only the signature and docstring text,
and hand them in a single prompt to a `general-purpose` agent that is told it has no
tools and must answer, per function, "what it takes and what it returns". The maintainer
scores each answer against the function body; ≥ 38/40 passes. The sample ids, the
prompt, and the scores are saved as `sc-008-sample.md` in the feature directory.

**Rationale**: clarified 2026-09-07 — an agent, not a human, is the evaluator; denying
tools makes "no access to the repository" enforceable by construction.

### R14 — Docstrings that render to operators keep their prose

**Decision**: For Click-decorated commands and FastAPI route handlers the docstring is
user-visible (`--help` text and the OpenAPI `description`). Cleanup keeps that prose
verbatim and satisfies FR-010 through the surrounding declarations instead: parameter
documentation goes into each `click.option(..., help="…")`, return and error
documentation into the route's `response_model`/`responses` and the Pydantic field
descriptions. `D`-rule compliance is unaffected (the summary line already exists); the
`ANN` and `ARG` rules still apply to these functions normally.

Concretely: 24 Click decorators and 131 route handlers are covered by this rule
(measured baseline). They are **not** exception-register entries (FR-010a) — the
documentation requirement is met, only its location differs. Where a command has no
docstring at all today, the new summary line is written as operator-facing help text,
not as a developer note.

**Rationale**: adding `Args:`/`Returns:`/`Raises:` blocks to these docstrings would print
them in `ado2gh <cmd> --help` and in the API docs, a visible regression for operators
that FR-006's spirit (and the CLI tests that assert help output) forbids.

**Alternatives considered**: freezing help text as a public contract — rejected, it would
push 155 functions toward the exception register; applying the full docstring rule anyway
— rejected, degrades operator UX.

**Verify at task time**: whether any existing CLI test asserts exact `--help` text
(`grep -rn "help" tests/ --include=test_*cli*`); if so, those assertions are the guard
and must stay green unchanged.

### R15 — The baseline is red; stabilise it before increment 1

**Decision** (operator, 2026-09-07): the 66 failing/erroring tests are fixed or deleted as
step 0 of this feature, in commits separate from any cleanup, before the public-surface
snapshot is taken. Only then does "each increment ends green" (FR-014, SC-010) mean
anything, and only then is the SC-004 test-count baseline real.

**Measured failure clusters** (`python -m pytest --cov=ado2gh -q`, 2026-09-07):

| Cluster | Count | Evidence | Likely disposition |
|---------|-------|----------|--------------------|
| `sqlite3.OperationalError: no such table: migration_operations` | 16 errors | `tests/contract/test_migration_api_contracts.py` | fix: table missing from the schema the fixture builds |
| Endpoints removed in `0ec95d2` still asserted | ~11 | `test_discovery_api_contracts.py`, `test_unified_tabs_contracts.py`, `'detail': 'Not Found'` | delete under FR-009 with a changelog row |
| Agent-module restructure fallout | ~15 | `ImportError: cannot import name '_gate_payload'`, `AgentState missing required field: llm`, `LiveApprovalStore.create_or_get_pending()` signature | fix or delete per case |
| State-layer drift | ~6 | `SQLiteStateDB` has no `upsert_dependenc…`; `test_operator_resolutions.py` | fix |
| Optional dependency absent | 1 | `ModuleNotFoundError: No module named 'boto3'` | add to the extra it belongs to, or skip when absent |
| Remaining assorted assertions | ~17 | workflow integrity, batch executor topo, rollback, streaming | triage individually |

**Rationale**: cleanup touches ~1,900 signatures; a red baseline makes every later red
indistinguishable from cleanup damage, and the exception-register and revert-proof
mechanics all assume a green reference point.

**Alternatives considered**: a recorded known-red list with "no new failures" as the gate
— rejected by the operator; deferring to the remediation phase — same objection, the
increments in between would have no usable gate.

**Note**: the red suite and the 60 % honest coverage are also assessment input — they
become G-seed 13 (Principle VI) rather than being silently fixed, so the register records
why the gate said 85 % while measurement said otherwise. Stabilisation therefore ends by
starting the coverage ratchet (operator decision 2026-09-07, plan § Coverage measurement):
the `omit` list is deleted, `--cov-fail-under` is set to the figure measured on the newly
green suite, and every later increment raises it to its own measured value and never
lowers it (FR-027a).

### R16 — No deprecation shims for renamed or removed internals

**Decision**: Renamed or deleted functions and moved modules leave no alias, re-export,
or `DeprecationWarning` wrapper. The structural changelog row is the only record.

**Rationale**: Principle III offers two branches (mark or remove); the clarification
picks removal. Shims would also be dead code by the inventory's own definition and get
deleted in the next regeneration anyway.
