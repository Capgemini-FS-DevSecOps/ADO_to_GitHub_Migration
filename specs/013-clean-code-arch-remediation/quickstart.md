# Quickstart: Validating 013 end-to-end

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-07

Run everything from the repository root unless a step says otherwise. On this machine use
`python -m pytest` (the bare `pytest` shim exits 1 silently — measured).

## Prerequisites

```bash
pip install -e ".[api,agent,dev]"            # already done in .venv
pip install "ruff==0.15.17" mypy              # CI-pinned linter; dev-only, not a runtime dependency
python -m vulture --version                   # 2.16 present in .venv
(cd apps/migration-ui && npm ci)              # typescript 5.9.x + vitest
```

## 0a. Stabilise the baseline (blocking — research R15)

The suite is red before this feature starts (measured: 50 failed, 16 errors, 790 passed).
Clear it first; nothing below is meaningful until this exits 0.

```bash
python -m pytest -q                                              # currently: 50 failed, 16 errors
python -m pytest -q --tb=no -rf | grep -E "^(FAILED|ERROR)" > /tmp/baseline_failures.txt
# fix genuine breakage; delete tests that only exercise endpoints/modules removed by earlier
# commits (FR-009, one changelog row each); commit each fix separately from any cleanup
python -m pytest -q                                              # must be green before 0b
```

## 0b. Record the baseline (once, before increment 1)

```bash
python -m pytest --collect-only -q | tail -1                     # record the post-stabilisation count as the SC-004 baseline
# FR-027a: delete the [tool.coverage.run] omit lines from pyproject.toml, then
python -m pytest -q --cov=ado2gh --cov-report=term | grep ^TOTAL # honest figure → --cov-fail-under=<int> in .github/workflows/ci.yml (T011)
UPDATE_SURFACE_SNAPSHOT=1 python -m pytest tests/contract/test_public_surface_snapshot.py
git add tests/contract/public_surface_snapshot.json && git commit -m "test: freeze public surface for 013"
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py
node   specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs
cat    specs/013-clean-code-arch-remediation/inventory-summary.md   # expect ≤ 1,549 py (FR-001a drops nested defs, excluded-paths.txt drops generated/vendored files — both reported) + ~340 ts rows
```

Expected summary (Python; research counted nested defs too, so each figure is an upper
bound): ≤ 1,549 functions · ≤ 1,026 missing docstring · ≤ 291 untyped · ≤ 62 > 5 params
· ≤ 69 boolean params · 0 mutable defaults. The first real generation is the baseline.

## 1. Architecture assessment (spec US3) — before any cleanup

```bash
# Write gap-register.md following contracts/artifact-schemas.md; start from research.md § R9 seeds.
# Hand off to the review agent with the verbatim instruction in contracts/artifact-schemas.md.
grep -c "^### GAP-" specs/013-clean-code-arch-remediation/gap-register.md   # ≥ 11 (seeds) expected
grep -A3 "^## Disputes" specs/013-clean-code-arch-remediation/gap-register.md
```

Pass when: every critical/high gap has ≥ 1 `path:line` or reproduction evidence, the
Summary table matches the `### GAP-` sections, and every DISPUTE line has an operator
decision before its gap is touched.

## 2. One cleanup increment (repeat for the 14 increments in research R6 order)

```bash
PKG=ado2gh/state
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py --package $PKG
# … clean signatures, update callers, set dispositions (clean/delete/exception) in inventory.json …
ruff check $PKG --select D1,ANN,FBT001,FBT002,PLR0913,B006,ARG,RET501,RET502,RET503 \
     --config "lint.pylint.max-args=5" --config 'lint.pydocstyle.convention="google"'
python -m pytest -q --cov=ado2gh --cov-report=term | grep -E "^TOTAL|passed|failed"   # green; TOTAL ≥ current --cov-fail-under; raise it in ci.yml if higher
python -m pytest tests/contract/test_public_surface_snapshot.py    # unchanged surface
python -m pytest tests/unit/test_no_orphaned_modules.py            # allowlist still valid
```

Judgment-tag proposals for the package are decided by the review agent (FR-002b) before
cleanup starts; the maintainer only records the agent's lines:

```bash
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py --pending      # lists proposals; exit 1 while any remain
# hand the listing to the review agent (contracts/artifact-schemas.md, second handoff); then per reply line:
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py \
       --confirm "ado2gh/state/sqlite_db.py::SQLiteStateDB.data:name_review" \
       --rationale "noun name; body loads rows" --decided-by cavecrew-reviewer
```

Pass when: `ruff` reports zero findings for `$PKG` except lines carrying a
`# noqa` that is listed in `exception-register.md`; `--pending` exits 0 for the package;
the suite is green; the collected count is ≥ the step-0b baseline minus the tests recorded
as removed in the changelog; coverage is ≥ the previous increment's ratchet value; the
changelog has a new `013 Increment N` section.

If the increment cannot be made green (FR-014a):

```bash
git reset --hard <last-green-commit>          # or git revert <increment-commits>
# move the offending function/module to exception-register.md with a reason, OR split it into its own increment
# retry; the retry must pass before the next package starts
```

Increment 14 (web console) additionally:

```bash
cd apps/migration-ui && npx tsc --noEmit && npx vitest run       # 38+ tests; exports-documented test included
```

## 3. Guards on (after the last increment)

```bash
ruff check ado2gh/ services/          # pyproject now selects the R1 rule set; must be clean
mypy ado2gh/                          # must exit 0 — CI no longer appends "|| true" (GAP fix)
python -m pytest --cov=ado2gh --cov-fail-under=<ratchet>   # omit list deleted from pyproject.toml; <ratchet> starts at the honest
#   figure measured at the end of step 0a (56 % on the red suite; re-measure green) and is raised to the new measured value after
#   every increment, never lowered. 85 % stays recorded as the outstanding target on G-seed 4, not as this feature's gate.
```

## 4. Remediation proof per gap (spec US4)

```bash
python -m pytest -q -k GAP_017                        # the gap's regression check passes
git stash push -- <files of the fix> && python -m pytest -q -k GAP_017; git stash pop
#                                                     ↑ must FAIL with the fix reverted (revert_proof)
```

Record the exact commands in the register entry's `revert_proof`.

## 5. Completion checks (spec SC-001 … SC-010)

```bash
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py
node   specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs
python - <<'EOF'
import json; rows=json.load(open("specs/013-clean-code-arch-remediation/inventory.json"))
tagged=[r for r in rows if set(r["tags"])-{"bool_data"} and r["disposition"]!="exception"]  # bool_data is a resolution, not an open tag (data-model.md)
exc=[r for r in rows if r["disposition"]=="exception"]
print("tagged-not-excepted:",len(tagged),"exceptions:",len(exc),"cap:",int(0.02*len(rows)))
EOF
#   expect: tagged-not-excepted: 0, exceptions ≤ cap
python -m pytest -q && python -m pytest --collect-only -q | tail -1
grep -E "^\| (GAP|critical|high)" specs/013-clean-code-arch-remediation/gap-register.md   # no critical/high in status open
# SC-007: every backticked path in docs resolves
python - <<'EOF'
import re,pathlib
docs = [pathlib.Path("CLAUDE.md"), pathlib.Path("README.md"), *sorted(pathlib.Path("docs").rglob("*.md"))]
docs = [d for d in docs if d.name != "STRUCTURAL_CHANGELOG.md"]  # append-only ledger of paths that no longer exist
for doc in docs:   # completed specs 001-012 are history, excluded by construction (FR-028)
    for m in re.findall(r"`([\w./-]+\.(?:py|md|ts|tsx|yml))`", doc.read_text(encoding="utf-8")):
        # MIGRATION_NOTICE.md is generated by `ado-cleanup` and pushed to the migrated repo, not tracked here — a MISSING hit for it is expected
        if not pathlib.Path(m).exists(): print(doc, "->", m, "MISSING")
EOF
```

Safeguard preservation (CA-001…CA-004, task T093) — compare against the counts recorded
in `FEATURE/baseline-failures.md` at step 0b; any decrease blocks completion:

```bash
git diff <baseline>..HEAD -- ado2gh services | grep -E "^-.*dry_run.*=.*True"     # each must have an ExecutionMode.DRY_RUN counterpart
grep -rn "can_approve_live_execution" ado2gh services --include=*.py | wc -l       # ≥ 6 (measured 2026-09-07, CA-002)
grep -rn "require_confirmation\|confirm_execute\|plan_confirmed" ado2gh services --include=*.py | wc -l   # ≥ baseline (CA-002)
python -m pytest tests/ -k "mask or redact or secret" -q                          # green (CA-003)
git diff <baseline>..HEAD | grep -E "^\+.*(ghp_|github_pat_|password\s*=\s*\")"    # must print nothing (CA-003)
grep -rn "AuditWriter\|audit_event(" ado2gh services --include=*.py | wc -l        # ≥ baseline (CA-004; `audit_event(` was 4 on 2026-09-07)
```

Write the five results to `FEATURE/safeguard-gate.md`.

SC-008 (research R13):

```bash
python - <<'EOF'
import json,random; rows=json.load(open("specs/013-clean-code-arch-remediation/inventory.json"))
rows=[r for r in rows if r["disposition"]=="clean"]; sample=random.Random(13).sample(rows,40)
for r in sample: print(r["id"]); print(r["signature"]); print(r.get("docstring",""),"\n---")
EOF
# paste the 40 blocks into one prompt for a tool-less general-purpose agent; score each answer against the body;
# save ids + answers + scores to specs/013-clean-code-arch-remediation/sc-008-sample.md; pass = ≥ 38/40
```

Pass when all checks print nothing unexpected, `--pending` exits 0 (zero judgment
proposals left), the exception count is under the cap or carries an operator decision,
the structural changelog lists every move and deletion, and the Summary section of the
gap register shows zero critical/high gaps in `open` or `disputed`.
