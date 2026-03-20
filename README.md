# ado2gh — Azure DevOps to GitHub Migration Accelerator

Enterprise-grade CLI tool for migrating repositories, pipelines, work items, and metadata from **Azure DevOps** to **GitHub** at scale (5000+ repos). Risk-based phasing with automatic checkpointing, gate enforcement, and post-migration validation.

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Migration Workflow](#migration-workflow)
- [Migration Strategies](#migration-strategies)
- [Configuration](#configuration)
- [Commands Reference](#commands-reference)
- [Phase Model](#phase-model)
- [Pipeline Transformation](#pipeline-transformation)
- [Post-Migration](#post-migration)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)

---

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Set credentials
export ADO_PAT="your-ado-pat"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="your-github-token"

# 3. Discover repos in your ADO org
ado2gh discover --config migration.yaml

# 4. Build pipeline inventory
ado2gh pipelines inventory --config migration.yaml

# 5. Assess pipeline readiness (before committing)
ado2gh pipeline-readiness --config migration.yaml

# 6. Score repos and assign to phases
ado2gh phase assign --config migration.yaml --output migration_phase.yaml

# 7. Dry-run the POC phase
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run

# 8. Execute POC for real
ado2gh phase run --phase poc --config migration_phase.yaml

# 9. Validate — checks commit SHAs, not just counts
ado2gh validate --config migration_phase.yaml

# 10. Advance through phases
ado2gh phase gate-check --phase poc
ado2gh phase run --phase pilot --config migration_phase.yaml
# ... wave1, wave2, wave3

# 11. Post-migration ADO cleanup
ado2gh ado-cleanup --config migration_phase.yaml
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | >= 3.9 | |
| Git | >= 2.30 | Must be on PATH. `git lfs` for LFS repos |
| GitHub CLI | >= 2.0 | Only if using `migration_strategy: gei` |
| GEI extension | latest | `gh extension install github/gh-gei` (GEI strategy only) |

### Required Permissions

**Azure DevOps PAT** scopes:
- `Code (Read)` — clone repos
- `Build (Read)` — pipeline definitions
- `Release (Read)` — release pipelines
- `Work Items (Read)` — work item migration
- `Variable Groups (Read)` — secrets mapping
- `Service Connections (Read)` — service connection manifest
- `Wiki (Read)` — wiki migration
- `Code (Write)` — only for `ado-cleanup --add-redirect`
- `Build (Read & Execute)` — only for `ado-cleanup --disable-pipelines`

**GitHub Token** scopes:
- `repo` — create repos, push code, manage settings
- `admin:org` — team management
- `workflow` — GitHub Actions workflow files
- `delete_repo` — only for rollback

---

## Installation

```bash
git clone <this-repo>
cd ADO2GH

# Create virtual environment
python -m venv venv
source venv/bin/activate    # Linux/macOS
# venv\Scripts\activate     # Windows

# Install
pip install -e .

# Verify
ado2gh --version
```

### Multi-Token Setup (Recommended for Scale)

At 5000 repos, you'll hit GitHub rate limits with a single token. Set multiple:

```bash
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"
export GH_TOKEN_3="ghp_token_three"
```

The tool auto-detects `GH_TOKEN_1` through `GH_TOKEN_19` and rotates them with rate-limit awareness.

### GitHub App Authentication (Optional)

For organizations that mandate App-based auth:

```bash
export GH_APP_ID="123456"
export GH_APP_INSTALLATION_ID="78901234"
export GH_APP_PRIVATE_KEY_PATH="/path/to/private-key.pem"
pip install cryptography PyJWT
```

---

## Migration Workflow

```
Phase 1: Discovery & Assessment
├── discover              → Enumerate all ADO repos + pipeline counts
├── pipelines inventory   → Deep-scan pipeline definitions
├── pipeline-readiness    → Auto/assisted/manual classification + effort estimate
└── service-connections   → Ops manifest for manual secret setup

Phase 2: Planning
├── phase assign          → Risk-score repos, assign to POC/Pilot/Wave1-3
├── phase plan            → Review assignments + gate thresholds
└── pipelines plan        → Pipeline breakdown per wave

Phase 3: Execution (per phase, gated)
├── phase run --phase poc [--dry-run]
├── validate              → Commit SHA verification
├── phase gate-check      → Must pass before next phase
└── (repeat for pilot, wave1, wave2, wave3)

Phase 4: Post-Migration
├── validate              → Full source vs target comparison
├── ado-cleanup           → Disable pipelines, add redirect, archive repos
└── report                → HTML/CSV/JSON final report
```

---

## Migration Strategies

### Mirror (Default)

```yaml
global:
  migration_strategy: mirror   # or omit — this is the default
```

Executes `git clone --mirror` from ADO + `git push --mirror` to GitHub. Handles all branches, tags, and LFS objects. Fastest for pure code migration.

### GEI (GitHub Enterprise Importer)

```yaml
global:
  migration_strategy: gei
```

Uses `gh gei migrate-repo`. Migrates code, PRs, issues, and releases natively through GitHub's migration API. Requires `gh` CLI with `gh-gei` extension and blob storage on ADO side.

**When to use GEI:**
- You need PR history on GitHub (mirror doesn't transfer PRs)
- GitHub provides GEI access for your org
- ADO has blob storage configured (AWS S3 or Azure Blob)

---

## Configuration

### `migration.yaml` — Main Config

```yaml
global:
  ado_org_url: "https://dev.azure.com/YOUR_ORG"
  gh_org: "your-github-org"
  parallel: 4                # repo-level parallelism
  pipeline_parallel: 12      # pipeline transform threads
  migration_strategy: mirror # or "gei"
  default_scopes:
    - repo
    - work_items
    - pipelines
    - wiki
    - secrets
    - branch_policies

phases:
  poc:
    repo_cap: 10
    risk_max: 25
    batch_size: 10
    gate_repo_success_pct: 0.90
    gate_pipeline_success_pct: 0.80
  pilot:
    repo_cap: 100
    risk_max: 45
    # ...
  wave1:
    repo_cap: 500
    risk_max: 65
  wave2:
    repo_cap: 1000
    risk_max: 80
  wave3:
    repo_cap: 999999
    risk_max: 100

waves: []   # populated by `phase assign`
```

### Scopes

| Scope | What It Does |
|---|---|
| `repo` | Git clone --mirror + push --mirror (or GEI) |
| `work_items` | ADO work items → GitHub Issues with labels |
| `pipelines` | ADO pipelines → GitHub Actions YAML (auto-transform) |
| `wiki` | ADO wiki pages → output directory |
| `secrets` | Variable groups → secrets mapping manifest (names only) |
| `branch_policies` | ADO branch policies → GitHub branch protection rules |

### Text Input Format

For ad-hoc operations, use a simple text file instead of YAML:

```
# project/repo (one per line)
MyProject/my-repo
MyProject/another-repo
OtherProject/service-api::my-gh-org/service-api-renamed
```

---

## Commands Reference

| Command | Description |
|---|---|
| `discover` | Scan ADO org, enumerate repos + pipelines |
| `plan` | Preview migration plan |
| `run --wave N` | Execute wave(s) |
| `status` | Show migration status |
| `report --format html\|json\|csv` | Generate report |
| `validate` | Commit-SHA-level source vs target verification |
| `rollback --wave N [--scopes ...]` | Scope-targeted rollback |
| `export-failed` | Export failed repos for retries |
| `token-status` | GitHub token rate limits |
| `pipeline-readiness` | Auto/assisted/manual assessment |
| `service-connections` | Service connection → GitHub secrets manifest |
| `ado-cleanup` | Disable ADO pipelines, add redirect, archive |
| `pipelines inventory` | Scan ADO pipelines into StateDB |
| `pipelines plan` | Pipeline breakdown per wave |
| `pipelines status` | Pipeline migration status |
| `pipelines retry-failed` | Re-attempt failed pipelines |
| `phase assign` | Risk-score repos, assign to phases |
| `phase plan` | Phase breakdown with gates |
| `phase run --phase poc` | Execute phase with batching |
| `phase gate-check` | Validate thresholds |
| `phase dashboard` | Live progress dashboard |

---

## Phase Model

Repos are automatically assigned to phases based on a 9-signal risk score (0–100):

| Phase | Risk Band | Repo Cap | Gate: Repo % | Gate: Pipeline % |
|---|---|---|---|---|
| POC | 0–25 | 10 | 90% | 80% |
| Pilot | 25–45 | 100 | 95% | 90% |
| Wave 1 | 45–65 | 500 | 97% | 95% |
| Wave 2 | 65–80 | 1000 | 98% | 97% |
| Wave 3 | 80–100 | unlimited | 98% | 97% |

### Risk Signals

| Signal | Max Points | Description |
|---|---|---|
| repo_size_kb | 15 | Log scale, 5 GB = max |
| pipeline_count | 15 | >= 50 pipelines = max |
| complex_pipeline_ratio | 15 | % classified as COMPLEX |
| classic_pipeline_ratio | 10 | % classic (GUI) build pipelines |
| release_pipeline_count | 10 | Classic release pipelines |
| variable_group_count | 10 | >= 10 variable groups = max |
| service_connection_count | 10 | >= 8 service connections = max |
| days_since_last_commit | 10 | Active = high risk, stale = low |
| branch_count | 5 | >= 50 branches = max |

### Gate Enforcement

Each phase must pass its gate before the next phase can start:

```bash
# Check gate
ado2gh phase gate-check --phase poc --config migration_phase.yaml

# Override with documented reason (stored in DB for audit)
ado2gh phase gate-check --phase poc --override --reason "Approved by CTO"
```

---

## Pipeline Transformation

The tool transforms ADO pipelines to GitHub Actions workflows:

| ADO Pipeline Type | Conversion Level | Notes |
|---|---|---|
| YAML pipelines | Auto/Assisted | Syntax transform, task mapping (200+ tasks) |
| Classic build | Assisted/Manual | No source YAML — best-effort conversion |
| Classic release | Manual | Map stages to GitHub Environments |

### Readiness Assessment

Run **before** migration to understand effort:

```bash
ado2gh pipeline-readiness --config migration.yaml --output readiness.csv
```

Produces per-pipeline classification:
- **Auto** — can be converted without manual intervention
- **Assisted** — converted with warnings, needs review
- **Manual** — has blockers (unsupported tasks, self-hosted pools), needs rewrite

---

## Post-Migration

### Validation

```bash
ado2gh validate --config migration_phase.yaml --output validation.csv
```

Checks per repo:
- GitHub repo exists
- Default branch name matches
- **HEAD commit SHA matches** (proves code actually transferred)
- Branch count comparison
- Workflow files present (if pipelines migrated)
- Branch protection applied (if policies migrated)

### ADO Cleanup

Run only after successful validation:

```bash
# Disable pipelines + add redirect (safe)
ado2gh ado-cleanup --config migration_phase.yaml

# Full cleanup: disable + redirect + archive ADO repo
ado2gh ado-cleanup --config migration_phase.yaml --archive

# Dry run first
ado2gh ado-cleanup --config migration_phase.yaml --dry-run
```

What it does:
1. **Disables ADO build pipelines** — prevents stale CI from running
2. **Pushes MIGRATION_NOTICE.md** — tells developers where the repo moved, with `git remote set-url` command
3. **Archives ADO repo** (optional) — makes the repo read-only

### Service Connection Manifest

Service connection **values** cannot be read via ADO API. This generates an ops-team-actionable manifest:

```bash
ado2gh service-connections --config migration.yaml
```

Output includes per-connection:
- Suggested GitHub secret names
- OIDC setup instructions (for Azure/AWS — keyless is preferred)
- Link to relevant GitHub Actions documentation

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `ADO_ORG_URL + ADO_PAT required` | Set environment variables |
| `GH_TOKEN required` | Set `GH_TOKEN` or `GH_TOKEN_1..N` |
| Rate limiting (HTTP 429) | Add more tokens: `GH_TOKEN_1`, `GH_TOKEN_2`, etc. |
| `git clone --mirror failed` | Check ADO PAT has Code (Read) scope |
| `git push --mirror failed` | Check GH token has `repo` scope; repo may already have content |
| `gh gei` not found | Install: `gh extension install github/gh-gei` |
| LFS push failed | Install `git-lfs`: `git lfs install` |
| Gate blocked | Run `phase gate-check` to see failures; fix or `--override` |
| Interrupted run | Just re-run the same command — resumes from last checkpoint |
| Rollback only branch policies | `ado2gh rollback --wave 1 --scopes branch_policies` |

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │           CLI (cli.py)           │
                    │  Click commands + _load_clients  │
                    └──────────────┬──────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
    ┌──────▼──────┐    ┌──────────▼──────────┐    ┌───────▼───────┐
    │   Phase     │    │   Core Migration    │    │   Reporting   │
    │ Orchestration│    │                     │    │               │
    │             │    │  MigrationEngine    │    │  Reporter     │
    │ RiskScorer  │    │  ├── git mirror/GEI │    │  CSVExporter  │
    │ WaveAssigner│    │  ├── work items     │    │  Validator    │
    │ GateChecker │    │  ├── pipelines      │    │  Readiness    │
    │ BatchExec.  │    │  ├── wiki           │    │  SvcConnManif.│
    │ ProgressTrk.│    │  ├── secrets        │    └───────────────┘
    └─────────────┘    │  └── branch policies│
                       │                     │
                       │  WaveRunner         │
                       │  ConfigLoader       │
                       │  DiscoveryScanner   │
                       │  RollbackHandler    │
                       │  ADOCleanup         │
                       └──────────┬──────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
       ┌──────▼──────┐   ┌───────▼───────┐   ┌──────▼──────┐
       │  ADOClient  │   │   GHClient    │   │  StateDB    │
       │  (REST 7.1) │   │  (multi-token)│   │  (SQLite)   │
       └─────────────┘   │  TokenManager │   │  7 tables   │
                         └───────────────┘   │  WAL mode   │
                                             └─────────────┘
```

---

## License

MIT
