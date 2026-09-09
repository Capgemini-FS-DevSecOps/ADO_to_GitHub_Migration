# Contract: Feature Artefacts & Scripts

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-07

The consumers of these artefacts are the maintainer, the review agent, and the
regression guards. Field definitions live in [data-model.md](../data-model.md); this
file fixes the file names, locations, and the command-line contract of the two scripts.

## Files (all under `specs/013-clean-code-arch-remediation/`)

| File | Producer | Consumer | Committed |
|------|----------|----------|-----------|
| `scripts/function_inventory.py` | hand-written once | maintainer | yes |
| `scripts/function_inventory_ts.mjs` | hand-written once | maintainer, vitest guard (imports the walker) | yes |
| `inventory.json` | scripts | maintainer, per-package pass | yes (regenerated per increment) |
| `inventory-summary.md` | `function_inventory.py` | operator, completion report | yes |
| `inventory-history.jsonl` | `function_inventory.py` (append) | summary delta | yes |
| `protected-entry-points.json` | `function_inventory.py` | deletion step | yes (regenerated) |
| `protected-entry-points.manual.txt` | maintainer | `function_inventory.py` | yes |
| `tag-decisions.json` | `function_inventory.py --confirm/--reject` | `function_inventory.py`, `function_inventory_ts.mjs` | yes |
| `exception-register.md` | maintainer | SC-001 check, ruff `noqa` review | yes |
| `gap-register.md` | assessment, review agent, remediation | operator, tasks | yes |
| `excluded-paths.txt` | maintainer | `function_inventory.py`, `function_inventory_ts.mjs` | yes |
| `baseline-failures.md` | maintainer (stabilisation, FR-027b) | operator, SC-004 baseline | yes |
| `inventory-spotcheck.md` | maintainer (US1 independent test) | operator | yes |
| `sc-008-sample.md` | maintainer (SC-008 run) | operator | yes |
| `safeguard-gate.md` | maintainer (CA-001…CA-004 gate, T093) | operator, completion report | yes |
| `tests/contract/public_surface_snapshot.json` | snapshot test run with `UPDATE_SURFACE_SNAPSHOT=1` | snapshot test | yes (repo path, not spec dir) |

## `scripts/function_inventory.py`

```
python specs/013-clean-code-arch-remediation/scripts/function_inventory.py
        [--roots ado2gh services]        # default: both
        [--package ado2gh/state ...]     # restrict regeneration to one increment; repeatable; `ado2gh` = root-level modules
        [--no-ruff] [--no-vulture]       # skip an input (for speed while iterating)
        [--out specs/013-clean-code-arch-remediation]
        [--confirm <id>:<tag> --rationale "…"]   # record a JudgmentTagDecision, then regenerate
        [--reject  <id>:<tag> --rationale "…"]
        [--pending]                      # list rows with non-empty proposed_tags and exit 1 if any
        [--excluded excluded-paths.txt]  # default: FEATURE/excluded-paths.txt (FR-003b)
```

`excluded-paths.txt`: one gitignore-style path pattern per line, `#` comments allowed,
covering generated, vendored, and data-table files (e.g. `ado2gh/pipelines/transform/*_map.py`).
Matching files produce **no** inventory rows; the generator still counts their definitions
and writes `excluded: {"<pattern>": <count>}` into the history line and a matching section
in `inventory-summary.md`, so the zero-tag denominator cannot shrink unnoticed (FR-003b).
The TS walker reads the same file.

- Exit 0 on success; exit 2 if `ruff` or `vulture` is missing and not skipped (prints
  the install line from research R12).
- Runs, in order: `ruff check <roots> --output-format json --select <R1 rule set>
  --config "lint.pylint.max-args=5"`; `vulture <roots> --min-confidence 60`; its own
  `ast` walk; the reference scan; the protected-list generation; the merge.
- Merge rule: existing `inventory.json` is loaded first; `disposition` and `note` are
  carried over by `id`; rows whose `id` no longer exists are dropped; new rows start
  `pending`. Judgment tags: each suspicion becomes a `proposed_tags` entry unless
  `tag-decisions.json` holds a decision for the same `id`+`tag` whose `state_hash`
  matches the current row — then `confirm` moves it into `tags` (or `bool_data`) and
  `reject` drops it. Stale decisions (hash mismatch) are deleted from the file and the
  proposal is re-raised.
- Determinism: output sorted by `id`, no timestamps in `inventory.json`; the script
  refuses to run with a `ruff` version other than the CI pin and prints the pin.
- Appends one line to `inventory-history.jsonl` and rewrites `inventory-summary.md`
  with the delta against the previous line.
- Stdlib only (`ast`, `json`, `subprocess`, `pathlib`, `re`, `argparse`).

## `scripts/function_inventory_ts.mjs`

```
node specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs
        [--src apps/migration-ui/src] [--out specs/013-clean-code-arch-remediation]
```

- Uses `apps/migration-ui/node_modules/typescript` (resolved relative to `--src`); no
  other imports.
- Emits rows with `language: "ts"` merged into the same `inventory.json` (Python rows
  untouched) and the TS block of the summary.
- Exports `walkExports(sourceRoot)` so `apps/migration-ui/src/__tests__/exports-documented.test.ts`
  can import the same walker (research R4).

## `gap-register.md` layout

```
# Architecture Gap Register
## Summary            ← counts per severity + table of critical/high (id, title, status)
## Disputes           ← RatingDispute table (may be empty)
## Gaps
### GAP-001 <title>
- components: …
- violates: …
- evidence:
  - path:line — what it shows
- severity: high (critical_test: —)
- blast_radius: …
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —
### GAP-002 …
```

Bullet keys are the `ArchitectureGap` fields verbatim so the review agent and a grep can
parse them.

## Review-agent handoff contract

Input: the constitution and `gap-register.md`. Instruction (verbatim, so runs are
comparable): *"For each `### GAP-` section rated critical or high, read only the cited
evidence and the constitution. Reply one line per gap: `GAP-NNN CONFIRM` or
`GAP-NNN DISPUTE <severity you can support> — <one-sentence reason>`. Do not propose
fixes."* Output is pasted into the Disputes table; CONFIRM lines are not recorded.

Second handoff type — judgment-tag proposals (FR-002b) and `module_name_review`
proposals (FR-013), same agent, per package: input is the `--pending` listing for the
package (id, signature, docstring, proposed tag; for modules: path and the module's
docstring/first 20 lines) plus read access to the file. Instruction: *"For each line reply `<id>:<tag> CONFIRM —
<reason>`, `<id>:<tag> REJECT — <reason>`, or `<id>:<tag> ESCALATE — <reason>` when the
code alone cannot decide or the fix would change behaviour. Do not propose renames or
edits."* The maintainer feeds each line to `--confirm/--reject` with
`--decided-by <agent id>`; ESCALATE lines go to the operator and stay pending until
answered.

## Structural changelog entry contract

Append to `docs/STRUCTURAL_CHANGELOG.md` per increment:

```
## 2026-MM-DD — 013 Increment N: <package>

| File | New Path | Change Type | Reason | Verified | Test Status | Date |
|------|----------|-------------|--------|----------|-------------|------|
| `path/module.py` | — | deleted functions | 2 functions — zero references (vulture + scan); see `inventory.json@<pre-increment sha>` rows with `disposition == "delete"`, package `<pkg>`; 3 tests removed | yes | pass (874) | 2026-MM-DD |
| `ado2gh/assignments/` | `ado2gh/audit/` | moved | GAP-036 / FR-013 | yes | pass | 2026-MM-DD |
```

"Test Status" carries the collected-test count after the increment so SC-004's adjusted
baseline (post-stabilisation count − tests removed with deleted code) is auditable from
the log alone. Deleted functions are a count plus the `inventory.json@<sha>` pointer
(FR-029): regenerated inventories drop rows for functions that no longer exist, so the
pre-increment ref is the only place the rows survive.
