# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Enterprise-grade Python CLI for Azure DevOps to GitHub migrations at scale (5000+ repos). Features risk-based phasing, real git mirroring (+ gh gei support), multi-token load balancing, ADO pipeline → GitHub Actions transformation, commit-level post-migration validation, and ADO-side post-migration cleanup.

## Running

```bash
pip install -e .
ado2gh <command> [options]

# Or directly
python -m ado2gh <command> [options]

# Legacy single-file mode (v4)
python ado2gh_migrator.py <command> [options]
```

## Environment Variables

```bash
ADO_PAT=<ado-personal-access-token>
ADO_ORG_URL=https://dev.azure.com/YOUR_ORG
GH_TOKEN=<github-token>              # Single token mode

# Multi-token load balancing (recommended at scale)
GH_TOKEN_1=<token1>
GH_TOKEN_2=<token2>

# GitHub App auth (optional, for orgs that mandate it)
GH_APP_ID=<app-id>
GH_APP_INSTALLATION_ID=<install-id>
GH_APP_PRIVATE_KEY_PATH=<path-to-pem>
```

## Dependencies

```bash
pip install -r requirements.txt       # click, requests, pyyaml, rich, urllib3
pip install cryptography PyJWT        # Only if using GitHub App auth
```

Also requires `git` on PATH. For GEI migration strategy: `gh` CLI with `gh-gei` extension.

## Package Structure

```
ado2gh/
├── cli.py                 # All Click commands
├── models.py              # Enums, dataclasses, shared types
├── logging_config.py      # Rich console + logging setup
├── http_utils.py          # Session factory with retry/backoff
├── clients/
│   ├── ado_client.py      # Azure DevOps REST API (7.1)
│   ├── gh_client.py       # GitHub API with multi-token rotation
│   └── token_manager.py   # Round-robin PAT + GitHub App JWT
├── state/
│   └── db.py              # SQLite (WAL mode, 7 tables)
├── pipelines/
│   ├── extractor.py       # Normalize ADO pipelines → PipelineMetadata
│   ├── transformer.py     # PipelineMetadata → GitHub Actions YAML
│   └── inventory.py       # Parallel pipeline scanning
├── phase/
│   ├── risk_scorer.py     # 9-signal risk scoring (0–100)
│   ├── wave_assigner.py   # Auto-assign repos to phases
│   ├── gate_checker.py    # Phase gate validation + override
│   ├── batch_executor.py  # Sub-batch execution + checkpointing
│   └── progress_tracker.py
├── core/
│   ├── migration_engine.py # Git mirror/GEI + 6 scope handlers
│   ├── wave_runner.py     # Parallel wave execution
│   ├── config_loader.py   # YAML + text input format
│   ├── discovery.py       # ADO org scanner
│   ├── rollback.py        # Scope-targeted rollback
│   └── ado_cleanup.py     # Post-migration ADO cleanup
└── reporting/
    ├── reporter.py        # Rich tables + HTML report
    ├── csv_exporter.py    # CSV export + failed repo lists
    ├── post_migration_validator.py  # Commit SHA + content verification
    ├── pipeline_readiness.py        # Conversion difficulty assessment
    └── service_connection_manifest.py  # SC → GitHub secrets mapping
```

## CLI Commands

```
ado2gh
├── discover              Scan ADO org, generate wave config
├── plan                  Preview migration plan
├── run --wave N          Execute wave(s)
├── status                Migration status
├── report --format html|json|csv
├── validate              Commit-SHA-level source vs target verification
├── rollback --wave N [--scopes branch_policies,pipelines]
├── export-failed         Failed repos → text file for retries
├── token-status          GitHub token rate limits
├── pipeline-readiness    Auto/assisted/manual assessment + effort estimate
├── service-connections   ADO service connections → GitHub secrets manifest
├── ado-cleanup           Disable ADO pipelines, add redirect, archive repos
├── pipelines/
│   ├── inventory         Scan ADO pipelines into StateDB
│   ├── plan              Pipeline breakdown per wave
│   ├── status            Pipeline migration status
│   └── retry-failed      Re-attempt failed pipelines
└── phase/
    ├── assign            Risk-score + auto-assign to phases
    ├── plan              Phase breakdown with gates
    ├── run --phase poc   Execute with batching + gate enforcement
    ├── gate-check        Validate thresholds (--override --reason)
    └── dashboard         Live progress dashboard
```

## Migration Strategies

**`migration_strategy: mirror`** (default) — `git clone --mirror` + `git push --mirror`. Handles all branches, tags, LFS objects. Requires `git` on PATH.

**`migration_strategy: gei`** — Uses `gh gei migrate-repo`. Handles PRs, issues, releases natively. Requires `gh` CLI + `gh-gei` extension. Set in `migration.yaml` under `global.migration_strategy`.

## ADO-Specific Design Decisions

- **Git migration actually executes**: `_migrate_git` runs `git clone --mirror && git push --mirror` via subprocess, including LFS push. Not just metadata recording.
- **Post-migration validation is content-level**: Compares HEAD commit SHA between ADO and GitHub (not just branch counts). Proves code actually transferred.
- **Service connections can't be migrated**: Only names are readable via API. The `service-connections` command generates a manifest with GitHub secrets names + OIDC setup instructions for ops teams.
- **Pipeline readiness is assessed before migration**: `pipeline-readiness` classifies each pipeline as auto/assisted/manual with estimated hours — lets teams plan before committing.
- **ADO cleanup is a separate post-migration step**: `ado-cleanup` disables pipelines, pushes a MIGRATION_NOTICE.md redirect, and optionally archives the ADO repo.
- **Rollback is scope-targeted**: `rollback --scopes branch_policies,pipelines` only undoes those scopes without deleting the repo.

## Execution Workflow

```
1. discover           → discovered_repos.yaml
2. pipelines inventory → populate StateDB pipeline_inventory
3. pipeline-readiness  → assess conversion effort
4. service-connections → generate ops manifest
5. phase assign        → migration_phase.yaml (risk-scored)
6. phase run --phase poc [--dry-run]
7. validate            → commit SHA verification
8. phase gate-check --phase poc
9. phase run --phase pilot → wave1 → wave2 → wave3
10. ado-cleanup        → disable pipelines, add redirect, archive
```

## State Persistence

SQLite `migration_state.db` (WAL mode). 7 tables:
- `migrations`, `wave_runs` — per-repo/wave tracking
- `pipeline_inventory`, `pipeline_migrations` — pipeline metadata + status
- `repo_risk_scores`, `phase_gates`, `batch_checkpoints` — risk + phase gates

## Key Patterns

- `TokenManager` rotates tokens per API call; updates rate limits from response headers
- Interrupted runs auto-resume from last SQLite checkpoint
- Gate checks enforce success thresholds; `--override --reason` for operator escalation
- All parallelism via `ThreadPoolExecutor` with separate repo-level and pipeline-level knobs
- Failed repos auto-exported to `failed_repos_{phase}.txt` after each phase run
