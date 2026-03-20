# Changelog

## v5.1.0 — ADO-Specific Redesign

### Removed
- `bash/` shell wrapper scripts — Python CLI handles everything natively
- `templates/orchestrator.py` — over-engineered JSON DAG not needed for this migration
- `template-run` and `template-init` CLI commands

### Fixed
- **Git migration actually executes** — `_migrate_git` now runs `git clone --mirror && git push --mirror` via subprocess, including LFS object detection and push
- **Post-migration validation is content-level** — compares HEAD commit SHA between ADO and GitHub (not just branch counts)
- **Rollback is scope-targeted** — `rollback --scopes branch_policies,pipelines` rolls back specific scopes without deleting the repo

### Added
- **`gh gei` integration** — set `migration_strategy: gei` in config for GitHub Enterprise Importer
- **LFS handling** — automatic detection and push of LFS objects during mirror migration
- **`pipeline-readiness` command** — classifies each pipeline as auto/assisted/manual with effort estimate in hours
- **`service-connections` command** — generates ops-team manifest mapping ADO service connections to GitHub secrets/OIDC
- **`ado-cleanup` command** — post-migration ADO cleanup: disable pipelines, push MIGRATION_NOTICE.md redirect, archive repos
- **Commit SHA verification** in `validate` — proves code actually transferred, not just "something exists"

## v5.0.0 — Modular Package

### Changed
- Split monolithic `ado2gh_migrator.py` (4200 lines) into 36-file modular package
- All classes independently importable and testable

### Added
- **Multi-token load balancing** — round-robin across `GH_TOKEN_1..N` with rate-limit awareness
- **GitHub App authentication** — JWT-based auth via cryptography + PyJWT
- **CSV export** — `report --format csv` and `export-failed` for stakeholder reporting
- **Text input format** — simple `project/repo` text files for ad-hoc operations
- **`pyproject.toml`** — proper Python packaging with `pip install -e .`

## v4.0.0 — Phase Orchestration (Original)

- 9-signal risk scoring (0–100)
- Auto-assignment to POC → Pilot → Wave1 → Wave2 → Wave3
- Phase gate enforcement with override + audit trail
- Sub-batch execution with SQLite checkpointing
- Pipeline inventory builder (1000+ pipelines)
- Pipeline transformer (200+ ADO task mappings)
- Rich terminal UI + HTML reports
