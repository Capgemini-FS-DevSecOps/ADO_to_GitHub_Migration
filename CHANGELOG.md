# Changelog

## v6.0.0 — Enterprise Planner–Executor–Validator

### Added

- Organization-level PEV control plane with content-addressed immutable plans,
  exact `plan_id` approval, immutable repository ID plus complete branch/tag
  SHA snapshots, task DAGs, durable run receipts, resume, evidence-backed
  validation, and bounded repair.
- Deterministic full-organization source-to-target mapping with project prefixes,
  explicit overrides, case-insensitive collision rejection, and existing-target
  preflight policy.
- Nested pipeline PEV conversion for YAML, classic-build, and classic-release
  pipelines. Known constructs remain deterministic; eligible ambiguity can use
  a redacted, schema-constrained LLM provider.
- Independent workflow structural, semantic, and security validation with
  source/plan/workflow/evidence digests and production-readiness enforcement.
- Idempotent pull-request delivery for converted workflows and evidence. Open
  PRs remain `needs_review`; the agent never auto-merges.
- Versioned SQLite schema with mapping ownership, PEV runs/tasks, validation
  evidence, LLM decision records, and pipeline conversion attempts.
- Content-addressed manual pipeline approval manifests bound to pipeline source
  fingerprints and ambiguity IDs, with typed target mappings, approver/change
  metadata, evidence digests, and fail-closed stale/duplicate detection.
- Plan-time pipeline inventory/source snapshots with execution-time drift
  reconciliation; partial or stale inventory cannot produce completion.
- Renewable cross-process plan leases and atomic task leases for crash-safe,
  single-executor execution against one durable state database.
- A reviewed built-in GitHub Action catalog that emits full commit SHAs, with
  validated content-addressed organization overrides.

### Security

- Environment-only credentials by default; Git credentials are no longer
  embedded in remote URLs or process arguments.
- Safe-method-only automatic HTTP retries, fail-closed API reads, paginated
  discovery, thread-local HTTP sessions, and bounded rate-limit waiting.
- LLM prompt redaction, strict structured output, local allow-list validation,
  prompt/response digests, bounded retries/timeouts, and provider storage
  disabled for conversion requests.
- Live ADO cleanup now requires a completed plan-bound run, external approval
  ticket, source-drift check, and interactive confirmation. Redirect commits
  require a separate source-mutation acknowledgment.
- ADO, GitHub API/web, and optional LLM service endpoints require absolute
  HTTPS URLs; ADO and GitHub URL fields additionally reject embedded
  credentials, query strings, and fragments.
- Live rollback derives targets from the immutable plan and requires a terminal
  bound run plus ticket. Repository deletion requires created-by-run provenance
  and a matching immutable GitHub repository ID; adopted targets are blocked.

### Changed

- `ado2gh agent plan|run|validate|status` is the production migration control
  plane. Legacy `run`, `phase run`, `pipelines retry-failed`, and
  `push-workflows` mutations are disabled; only their `--dry-run` compatibility
  previews remain.
- Pipeline conversion is complete only after production-ready validation and
  remote verification on the target default branch.
- Wiki exports, secret manifests, unresolved external requirements, and open
  workflow PRs report `needs_review` instead of false success.
- Post-migration validation compares branch SHAs and tags and fails closed on
  incomplete source or target evidence.
- Dry runs use isolated state and report `dry_run_passed`,
  `dry_run_needs_review`, or `dry_run_failed`; they never create live resume
  receipts.
- Cleanup flags can no longer expand authority: every requested source mutation
  must already be authorized in the immutable plan policy and repository scope.

### Removed

- The scheduled cross-repository sync workflow with a hard-coded destination.
  It bypassed immutable-plan approval and placed a PAT in Git arguments. Use
  `ado2gh agent plan` and `ado2gh agent run` for all repository transfers.

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
- **Web console** (`apps/migration-ui`) — profile-scoped discovery, migrate/monitor, PEV agent chat
- **Accelerator + Agent APIs** — FastAPI services; PostgreSQL/DynamoDB state backends
- **Tool-driven PEV orchestrator** — guarded planner/executor/validator via `session_orchestrator.py`

### Documentation & hygiene (2026-06)
- Rewrote `docs/ARCHITECTURE.md`; README is now a documentation index
- Removed duplicate `.windsurf/` Speckit copies; fixed 59+ unused Python imports
- Version aligned to 5.1.0 across package, APIs, and docs

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
