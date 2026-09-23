# Contract: Structural Change Log

**Feature**: 010-enterprise-audit-simplification | **Date**: 2026-06-24

## Format

The structural change log is a markdown file at `docs/STRUCTURAL_CHANGELOG.md`. It records every file move, rename, split, merge, deletion, and gitignore action performed during the simplification.

## Entry Format

Each entry is a markdown table row under a dated section header:

```markdown
## 2026-06-24 — Session Orchestrator Decomposition

| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |
|-----------|----------|-------------|--------|----------|-------------|-----------|
| `ado2gh/agents/session_orchestrator.py` | `ado2gh/agents/orchestration/loop.py` | split | Decompose 2253-line monolith into focused modules (FR-014) | yes | pass | 2026-06-24T14:30:00Z |
| `ado2gh/agents/session_orchestrator.py` | `ado2gh/agents/orchestration/prompts.py` | split | LLM prompt management extracted | yes | pass | 2026-06-24T14:30:00Z |
```

## Required Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| File Path | `str` | yes | Original path relative to repo root |
| New Path | `str` | no | New path if moved/renamed/split; omit for deletions |
| Change Type | `enum` | yes | `moved`, `renamed`, `split`, `merged`, `deleted`, `gitignored` |
| Reason | `str` | yes | Brief rationale referencing the FR that drove the change |
| Verified | `bool` | yes | `yes` if import graph verified no live references, `no` otherwise |
| Test Status | `enum` | yes | `pass`, `fail`, `skipped` |
| Timestamp | `str` | yes | ISO 8601 timestamp of the change |

## Invariants

- Every deletion MUST have `Verified = yes` and `Test Status = pass`
- Every `gitignored` entry MUST include the `.gitignore` line added
- Entries are grouped by date and logical change batch (e.g., "State Layer Consolidation", "Session Orchestrator Decomposition")
- No entry may be removed or modified after creation (append-only audit trail)
