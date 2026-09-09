# Data Model: Clean-Code Signature Audit & Critical Architecture Remediation

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-07

No runtime data model changes. Every entity below is a **feature artefact** stored under
`specs/013-clean-code-arch-remediation/` (machine-readable where the spec requires
regeneration, markdown where humans decide). The single production type introduced is
`ExecutionMode` (research R7).

## Artefact entities

### FunctionInventoryEntry — `inventory.json` (array of objects)

| Field | Type | Rule |
|-------|------|------|
| `id` | string | `<path>::<qualname>` — unique; stable across regenerations while the function keeps its name and file |
| `language` | `"py"` \| `"ts"` | |
| `path` | string | repo-relative, forward slashes |
| `line` | int | 1-based definition line |
| `package` | string | increment key from research R6 (e.g. `ado2gh/state`, `services/agent`, `apps/migration-ui`); files directly under `ado2gh/` use the key `ado2gh` (the root-modules increment) |
| `qualname` | string | `Class.method` or bare function name; TS: `export default` recorded as `default` |
| `signature` | string | source text of the `def`/`function` header, one line |
| `is_export` | bool | TS only: exported symbol; Python: always `true` (nested functions are not rows — see Entry granularity) |
| `param_count` | int | receiver excluded; variadic and keyword-only count one each |
| `tags` | string[] | mechanical tags plus **confirmed** judgment tags, from the Tag vocabulary below, sorted; empty = clean |
| `proposed_tags` | string[] | judgment tags the generator suspects (`name_review`, `stale_docstring`, `bool_flag`, `inconsistent_return`) that have no matching `JudgmentTagDecision` yet; a row with any proposal counts as neither tagged nor clean (FR-002b); must be empty at the final regeneration (SC-001) |
| `state_hash` | string | sha1 of the normalised signature text + docstring text; keys `JudgmentTagDecision` reuse |
| `reference_count` | int | occurrences of the bare name outside the definition across the scan roots (research R5) |
| `vulture_confidence` | int \| null | 0–100 when vulture reported the symbol, else null |
| `protected` | bool | true when on the protected entry-point list |
| `disposition` | `"clean"` \| `"delete"` \| `"exception"` \| `"pending"` | `pending` on generation; set by the per-package pass |
| `note` | string | free text; required when `disposition == "exception"` (points at the register entry) |

**Entry granularity (FR-001a)**: one row per *named module-level or class-level*
function, method, or coroutine (Python) and per *named exported* function, hook, or
component (TS). Nested `def`s, lambdas, and inline arrow callbacks get no row; a tag a
tool reports inside one is attributed to the enclosing row, and the same rule is applied
at every regeneration. The Python generator therefore keeps only `ast` nodes whose parent
is a `Module` or a `ClassDef`; the TS walker keeps only exported declarations. The
research baseline of 1,549 was measured with a walk that also counted nested definitions,
so the first real generation may report a slightly lower total — that generation, not the
research number, is the baseline recorded in `inventory-history.jsonl`.

**Validation**: `delete` requires `reference_count == 0 && !protected`; `exception`
requires a matching `ExceptionRegisterEntry`; a regenerated inventory carries over
`disposition`/`note` by `id` and resets them only when the function no longer exists.

**Determinism (FR-004)**: rows are sorted by `id`, tag arrays are sorted, the file
contains no timestamps or run metadata (those live only in `inventory-history.jsonl`),
and mechanical tags depend only on source text plus the pinned `ruff`/`vulture`
versions. Two runs on the same tree with the same `tag-decisions.json` are byte-identical.

### JudgmentTagDecision — `tag-decisions.json` (array of objects)

| Field | Type | Rule |
|-------|------|------|
| `id` | string | `FunctionInventoryEntry.id` |
| `tag` | `name_review` \| `stale_docstring` \| `bool_flag` \| `inconsistent_return` \| `module_name_review` | the proposed judgment tag (the four judgment classes of FR-002a), or the per-module rename proposal of FR-013 — for `module_name_review` the `id` is the module path (e.g. `ado2gh/assignments/`) and `state_hash` hashes that path |
| `decision` | `confirm` \| `reject` \| `escalate` | `confirm` moves the tag into `tags` (for `bool_flag` rejection, `bool_data` is recorded instead); `reject` clears the proposal; `escalate` keeps it pending for the operator (FR-002b) |
| `rationale` | string | one sentence from the reviewer |
| `state_hash` | string | `state_hash` of the function when decided; the decision is reused on every regeneration while the hash matches and is dropped (proposal re-raised) when the signature or docstring changes |
| `decided_by` | string | the review agent's id (FR-002b: never the generator, never the maintainer who wrote the cleanup); `operator` for escalations resolved by the operator |

Written only through the inventory script's `--confirm` / `--reject` flags
(contracts/artifact-schemas.md) so the file stays sorted and valid; checked in.

**Tag vocabulary** (Python source → rule in research R1; TS source → research R2):

| Tag | Meaning | Python source | TS source |
|-----|---------|---------------|-----------|
| `missing_docstring` | no docstring / no JSDoc on export | `D1xx` | walker |
| `stale_docstring` | `Args:` names ≠ signature | walker | walker (`@param` names) |
| `untyped` | any param or return without annotation | `ANN` | walker (`any`) |
| `gt5_params` | more than five, receiver excluded | `PLR0913` / walker | walker |
| `bool_flag` | boolean parameter that selects behaviour (candidate until confirmed) | `FBT001`/`FBT002` + walker for keyword-only | walker |
| `bool_data` | boolean parameter confirmed as data, not a switch — clears `bool_flag` | judgement | judgement |
| `name_review` | first token not a verb (candidate) | walker | walker |
| `mutable_default` | list/dict/set default | `B006` | n/a |
| `unused_param` | parameter never read | `ARG` | `noUnusedParameters` |
| `inconsistent_return` | return type varies by branch without documentation — **judgment tag** (FR-002a): proposed from the `RET50x` heuristic, decided by the review agent | `RET50x` → proposal | walker → proposal |
| `dead` | zero references and not protected | vulture ∧ scan | scan |
| `module_name_review` | module/folder name does not describe its responsibility — **judgment proposal per module** (FR-013), not a function tag; confirmed → the module is moved in its increment | walker (one per module) | walker (one per file under `src/`) |

### ExcludedPaths — `excluded-paths.txt`

Gitignore-style patterns for generated, vendored, and data-table files (FR-003b). Files
matching a pattern produce no `FunctionInventoryEntry`, but the generator counts their
definitions and reports them under `excluded` in the history line and the summary, so an
exclusion is always visible against the zero-tag denominator. Read by both the Python
generator and the TS walker; checked in and reviewed like any other artefact.

### InventorySummary — `inventory-summary.md` + `inventory-history.jsonl`

One JSON line per regeneration: `{"generated_at", "git_head", "totals": {"functions",
"clean", "<tag>": n …}, "per_package": {"<package>": {same}}, "excluded": {"<pattern>": n}}`. The markdown summary is
rendered from the latest line and shows the delta against the previous line (spec US1
scenario 5).

### ProtectedEntryPoint — `protected-entry-points.json` + `protected-entry-points.manual.txt`

| Field | Type | Rule |
|-------|------|------|
| `id` | string | same key as `FunctionInventoryEntry.id` |
| `reason` | enum | `cli_command` \| `http_route` \| `main_guard` \| `orphan_allowlist` \| `langchain_tool` \| `graph_node` \| `pytest_fixture` \| `next_route_export` \| `manual` |

Generated file is overwritten on every inventory run; the manual file (one `id` per
line, `#` comments allowed) is merged in with `reason: manual`.

### ExceptionRegisterEntry — `exception-register.md` (table)

| Column | Rule |
|--------|------|
| Inventory id | must exist in `inventory.json` with `disposition == "exception"` |
| Unmet rule(s) | tag names |
| Reason | why compliance would change behaviour (one sentence) |
| Follow-up owner | person or spec id |
| `noqa` code(s) | the ruff codes suppressed at the definition (research R4) |

**Constraint**: row count ≤ 2 % of `totals.functions` in the latest history line (SC-001).

### ArchitectureGap — `gap-register.md` (one `### GAP-NNN` section per gap)

| Field | Type | Rule |
|-------|------|------|
| `id` | `GAP-NNN` | sequential, never reused |
| `title` | string | one line |
| `components` | list | from the FR-016 component list |
| `violates` | list | constitution principle (I–VI) and/or migration-safety property (idempotency, resumability, scope-targeted rollback, validation after transfer, secret containment, human approval) |
| `evidence` | list | `path:line` or a reproduction command with expected vs actual |
| `severity` | `critical` \| `high` \| `medium` \| `low` | per spec US3 scenarios 2–3; `critical_test` field names which of (a)–(e) applies |
| `blast_radius` | string | what breaks or who is harmed if left open |
| `status` | `open` \| `disputed` \| `remediated` \| `deferred` | see transitions |
| `resolution` | string | required when `remediated`/`deferred`; for `deferred` names the blocker (external dependency, infrastructure change, or — coverage shortfall only — the effort scoped out under FR-027a) and the compensating control |
| `regression_check` | string | `path::test_name` or `ci.yml:<line>`; required when `remediated` |
| `revert_proof` | string | command run to show the check fails with the fix reverted (SC-005) |
| `contract_change` | bool | true when the fix alters a public contract → must be listed in plan.md before application (FR-024) |
| `closed_on` | date | required when `remediated`/`deferred` |

**State transitions**:

```
open ──(reviewer disputes)──▶ disputed ──(operator decides)──▶ open
open ──(fix + check + revert proof)──▶ remediated
open ──(external blocker + compensating control)──▶ deferred
```

Only `critical` and `high` gaps may leave `open`; `medium`/`low` stay `open` with a
`follow_up` recommendation (FR-026). A gap in `disputed` cannot be remediated (FR-021).

### RatingDispute — `gap-register.md` "Disputes" table

| Column | Rule |
|--------|------|
| Gap id | must exist |
| Original rating | severity + critical test letter |
| Reviewer rating | severity the review agent could reproduce |
| Reviewer reasoning | quoted from the review output |
| Operator decision | `keep` \| `lower` \| `raise`, with date; empty while pending |

Pending disputes are treated at the higher of the two ratings and block remediation of
that gap (spec edge case).

### GapRegisterSummary — first section of `gap-register.md`

Counts per severity, then a table of every critical and high gap: id, title, status.
Regenerated by hand whenever a gap changes state (small enough — tens of rows).

### PublicContractSnapshot — `tests/contract/public_surface_snapshot.json`

```json
{
  "cli_commands": ["discover", "phase assign", "phase run", "pipelines inventory", "..."],
  "http_routes": ["GET /health", "POST /v1/sessions", "..."],
  "env_vars": ["ADO2GH_STORAGE_BACKEND", "GH_TOKEN", "..."],
  "tables": ["migrations", "wave_runs", "..."]
}
```

Sorted, deduplicated. Written once before increment 1 (research R8); modified only in the
same commit as an approved contract-changing gap fix, with the migration note.

## Production type change

### `ExecutionMode` — `ado2gh/models.py`

```python
class ExecutionMode(str, Enum):
    """How a migration action runs: preview only, or against the real targets."""
    DRY_RUN = "dry_run"
    LIVE = "live"

    @classmethod
    def from_dry_run(cls, *, dry_run: bool) -> "ExecutionMode":
        """Map an external ``dry_run`` boolean (CLI flag, HTTP field, persisted column) to a mode."""
        return cls.DRY_RUN if dry_run else cls.LIVE
```

Permitted by FR-012's shared-enumeration clause (the flag recurs in 65 signatures) and
recorded in plan § Complexity Tracking.

Replaces `dry_run: bool` in internal signatures (65 measured). Boundary conversion rule:
CLI `--dry-run` flag, HTTP `dry_run` field, and persisted `dry_run` column keep their
shape and are mapped at the boundary; no persisted value changes.
`ExecutionMode.DRY_RUN` remains the default everywhere a default existed (CA-001).
