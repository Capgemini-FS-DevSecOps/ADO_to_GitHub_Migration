# Exception Register

Functions that keep a tag because compliance would change behaviour (FR-005).
Every row must have a matching `inventory.json` entry with
`disposition == "exception"` and a `note` pointing here; the suppressed ruff codes
must be present as `# noqa: <code>` at the definition (research R4).

**Cap (FR-005a / SC-001)**: row count must stay `≤ 2 %` of `totals.functions` in the
latest line of `inventory-history.jsonl`. Before adding a row, check the cap; if the
row would exceed it, stop cleanup of that rule and ask the operator (raise the cap /
amend the rule / accept the behaviour change) before continuing.

Schema: data-model.md § ExceptionRegisterEntry. Columns are fixed — do not add,
reorder, or rename them.

| Inventory id | Unmet rule(s) | Reason | Follow-up owner | `noqa` code(s) |
|---|---|---|---|---|
| `ado2gh/models.py::ExecutionMode.from_dry_run` | `bool_flag` (FR-012) | This is the boundary converter that data-model.md § ExecutionMode and contracts/public-contract-freeze.md mandate: its parameter is the external `dry_run` boolean being converted, so the only enum that could replace it is the one it returns, and "two intent-named functions" would be the enum members themselves, scattering the conversion across the 65 boundary call sites. The review agent confirmed `bool_flag` at increment 1 (`tag-decisions.json`); the operator may overrule by deleting this row and re-deciding. | operator (feature 013) | — (walker-derived tag; no ruff code fires on a keyword-only bool) |
