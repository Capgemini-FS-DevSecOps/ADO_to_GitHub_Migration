# Data Model: Enterprise Audit & Framework Simplification

**Date**: 2026-06-24 | **Feature**: 010-enterprise-audit-simplification

## Entities

### AuditReport

Categorized file inventory produced by the agent-driven audit. This is a transient in-memory structure, not persisted to a database.

| Field | Type | Description |
|-------|------|-------------|
| `required_files` | `list[FileEntry]` | Files imported by live code, with import evidence |
| `redundant_files` | `list[FileEntry]` | Files duplicating another file's functionality, with rationale |
| `dead_files` | `list[FileEntry]` | Files with zero inbound imports (excluding entry points), with verification |
| `artifacts` | `list[ArtifactEntry]` | Runtime-generated files that should be gitignored |
| `summary` | `AuditSummary` | Counts by category |

### FileEntry

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | Relative file path from repo root |
| `classification` | `str` | One of: required, redundant, dead |
| `evidence` | `str` | Import graph evidence or duplicate detection rationale |
| `inbound_imports` | `list[str]` | List of files that import this module (empty for dead files) |
| `is_entry_point` | `bool` | True if file is a CLI entry point or `__main__.py` |
| `has_dynamic_import` | `bool` | True if referenced via `importlib` or `__import__` |

### ArtifactEntry

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | Relative file path from repo root |
| `artifact_type` | `str` | One of: venv, database, generated_json, temporary_config |
| `gitignored` | `bool` | Whether the file is already in `.gitignore` |
| `tracked_in_git` | `bool` | Whether the file is tracked by git |
| `recommended_action` | `str` | One of: delete, gitignore, gitignore_and_remove_from_tracking |

### AuditSummary

| Field | Type | Description |
|-------|------|-------------|
| `total_files` | `int` | Total files scanned |
| `required_count` | `int` | Count of required files |
| `redundant_count` | `int` | Count of redundant files |
| `dead_count` | `int` | Count of dead files |
| `artifact_count` | `int` | Count of artifacts |
| `estimated_reduction` | `int` | Estimated file count reduction after cleanup |

### ChangeRecord

Entry in `docs/STRUCTURAL_CHANGELOG.md`. Persisted as markdown, not as a database entity.

| Field | Type | Description |
|-------|------|-------------|
| `file_path` | `str` | Original file path (before change) |
| `new_path` | `str?` | New file path (if moved/renamed), null if deleted |
| `change_type` | `str` | One of: moved, renamed, split, merged, deleted, gitignored |
| `reason` | `str` | Rationale for the change |
| `timestamp` | `str` | ISO 8601 timestamp |
| `verified` | `bool` | Whether live import verification was performed |
| `test_status` | `str` | Test suite status after change (pass/fail/skipped) |

### SpecLifecycleEntry

Entry in `specs/README.md`. Persisted as markdown.

| Field | Type | Description |
|-------|------|-------------|
| `spec_id` | `str` | Spec directory name (e.g., "001-agentic-migration-platform") |
| `title` | `str` | Human-readable spec title |
| `status` | `str` | One of: active, archived, superseded |
| `implementation_pointer` | `str?` | Path to implementation file(s) for archived specs |
| `one_line_summary` | `str` | Brief description of the spec's scope |

## State Transitions

### Spec Lifecycle

```
Draft → Clarified → (implemented) → Archived
                ↓
            Active (kept in specs/)
```

### File Classification

```
Scanned → Classified (required | redundant | dead | artifact)
                ↓
         Verified (dynamic import check, entry point check)
                ↓
         Actioned (kept | consolidated | deleted | gitignored)
                ↓
         Logged (ChangeRecord in STRUCTURAL_CHANGELOG.md)
```

## Validation Rules

- A file classified as `dead` MUST have `inbound_imports` empty AND `has_dynamic_import` false AND `is_entry_point` false
- A file classified as `artifact` MUST have `recommended_action` set
- Every `ChangeRecord` with `change_type=deleted` MUST have `verified=true` and `test_status=pass`
- Every `SpecLifecycleEntry` with `status=archived` MUST have `implementation_pointer` set
