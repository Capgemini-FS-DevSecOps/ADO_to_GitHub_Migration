# ADO2GH Execution Manual

Complete operational guide for running Azure DevOps to GitHub migrations using the ado2gh CLI.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Environment Setup](#2-environment-setup)
3. [Configuration File](#3-configuration-file)
4. [Input File Formats](#4-input-file-formats)
5. [Phase 1 — Discovery](#5-phase-1--discovery)
6. [Phase 2 — Pipeline Inventory & Assessment](#6-phase-2--pipeline-inventory--assessment)
7. [Phase 3 — Planning & Phase Assignment](#7-phase-3--planning--phase-assignment)
8. [Phase 4 — Execution](#8-phase-4--execution)
9. [Phase 5 — Validation](#9-phase-5--validation)
10. [Phase 6 — Reporting](#10-phase-6--reporting)
11. [Phase 7 — ADO Cleanup](#11-phase-7--ado-cleanup)
12. [Recovery & Rollback](#12-recovery--rollback)
13. [Monitoring](#13-monitoring)
14. [Complete End-to-End Example](#14-complete-end-to-end-example)
15. [Command Quick Reference](#15-command-quick-reference)

---

## 1. Prerequisites

### Software

| Software | Version | Required For |
|----------|---------|-------------|
| Python | >= 3.9 | All commands |
| Git | >= 2.30 | `phase run` (mirror strategy) |
| git-lfs | latest | Repos with LFS objects |
| GitHub CLI (`gh`) | >= 2.0 | GEI strategy only |
| gh-gei extension | latest | GEI strategy only |

### ADO PAT Scopes

| Scope | Access | Required By |
|-------|--------|------------|
| Code | Read | `discover`, `phase run`, `validate` |
| Code | Read & Write | `ado-cleanup --add-redirect` |
| Build | Read | `pipelines inventory`, `pipeline-readiness` |
| Build | Read & Execute | `ado-cleanup --disable-pipelines` |
| Release | Read | `pipelines inventory` |
| Work Items | Read | `phase run` (work_items scope) |
| Variable Groups | Read | `service-connections`, `phase run` (secrets scope) |
| Service Connections | Read | `service-connections` |
| Wiki | Read | `phase run` (wiki scope) |
| Project and Team | Read | `discover` |

### GitHub Token Scopes

Classic PAT: `repo`, `admin:org`, `workflow`, `delete_repo` (rollback only)

---

## 2. Environment Setup

### Install

```bash
cd c:\Users\snanjan\Downloads\ADO2GH
python -m venv venv
venv\Scripts\activate          # Windows
pip install -e .
ado2gh --version               # Should print: 5.0.0
```

### Set Credentials

```bash
# Required — always
export ADO_PAT="your-ado-personal-access-token"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"

# Single GitHub token
export GH_TOKEN="ghp_your_github_token"

# OR multi-token for rate limit load balancing (recommended for 100+ repos)
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"
export GH_TOKEN_3="ghp_token_three"
```

### Verify Connectivity

```bash
# Check token health
ado2gh token-status -c migration.yaml

# Quick discovery test (proves ADO PAT works)
ado2gh discover -c migration.yaml -o output/test_discovery
```

### Optional: GitHub App Auth

```bash
export GH_APP_ID="123456"
export GH_APP_INSTALLATION_ID="78901234"
export GH_APP_PRIVATE_KEY_PATH="C:\path\to\private-key.pem"
pip install cryptography PyJWT
```

### Optional: GEI Strategy

```bash
gh extension install github/gh-gei
```

Then set in `migration.yaml`:

```yaml
global:
  migration_strategy: gei
```

---

## 3. Configuration File

File: `migration.yaml`

This file contains ONLY connection settings and phase thresholds. Repo lists come from input files.

```yaml
global:
  ado_org_url: "https://dev.azure.com/CONTOSO"
  gh_org: "contoso-github"
  migration_strategy: mirror       # "mirror" or "gei"
  parallel: 4                      # concurrent repo migrations
  pipeline_parallel: 12            # pipeline transform threads
  default_scopes:
    - repo
    - pipelines
    - work_items
    - wiki
    - secrets
    - branch_policies

phases:
  poc:
    repo_cap: 10
    risk_max: 25
    batch_size: 10
    repo_parallel: 2
    pipeline_parallel: 4
    gate_repo_success_pct: 0.90
    gate_pipeline_success_pct: 0.80
  pilot:
    repo_cap: 100
    risk_max: 45
    batch_size: 25
    repo_parallel: 4
    pipeline_parallel: 8
    gate_repo_success_pct: 0.95
    gate_pipeline_success_pct: 0.90
  wave1:
    repo_cap: 500
    risk_max: 65
    batch_size: 50
    repo_parallel: 6
    pipeline_parallel: 12
    gate_repo_success_pct: 0.97
    gate_pipeline_success_pct: 0.95
  wave2:
    repo_cap: 1000
    risk_max: 80
    batch_size: 100
    repo_parallel: 8
    pipeline_parallel: 16
    gate_repo_success_pct: 0.98
    gate_pipeline_success_pct: 0.97
  wave3:
    repo_cap: 999999
    risk_max: 100
    batch_size: 500
    repo_parallel: 8
    pipeline_parallel: 16
    gate_repo_success_pct: 0.98
    gate_pipeline_success_pct: 0.97
```

---

## 4. Input File Formats

Repos to migrate are specified in input files, NOT in migration.yaml.

### Text Format (`in/repos.txt`)

One repo per line. Lines starting with `#` are comments.

```
# project/repo                        — uses gh_org from migration.yaml
# project/repo::gh_org/gh_repo        — explicit GitHub target

MyProject/backend-api
MyProject/frontend-app
SharedInfra/terraform-modules
Legacy/old-name::contoso-github/new-name
```

### CSV Format (`in/repos.csv`)

Per-repo scope control. Scopes are pipe-separated.

```csv
ado_project,ado_repo,gh_org,gh_repo,scopes
MyProject,backend-api,contoso-github,backend-api,repo|pipelines|branch_policies
MyProject,frontend-app,contoso-github,frontend-app,repo|pipelines|work_items
SharedInfra,terraform-modules,contoso-github,terraform-modules,repo|pipelines
Legacy,old-service,contoso-github,old-service,repo
```

### How to Create Input Files

1. Run `ado2gh discover` (see Phase 1 below)
2. Review `output/discovery/repos.csv`
3. Copy `output/discovery/repos_template.txt` to `in/repos.txt`
4. Uncomment the repos you want to migrate

---

## 5. Phase 1 — Discovery

Scan the entire ADO organization to understand what exists.

### Command

```bash
ado2gh discover -c migration.yaml
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | Path to migration.yaml |
| `-o, --output` | `output/discovery` | Output directory |

### Output Files

| File | Contents |
|------|----------|
| `output/discovery/repos.csv` | All repos: project, name, size, branches, last commit |
| `output/discovery/pipelines.csv` | All pipelines: project, name, type, linked repo |
| `output/discovery/repos_template.txt` | Pre-formatted input file — uncomment repos to migrate |
| `output/discovery/discovery.json` | Full JSON detail |

### Example

```bash
ado2gh discover -c migration.yaml -o output/discovery

# Review the output
# Windows:
start output\discovery\repos.csv
# Linux/macOS:
open output/discovery/repos.csv
```

### Next Step

Copy `output/discovery/repos_template.txt` to `in/repos.txt` and uncomment the repos you want to migrate.

---

## 6. Phase 2 — Pipeline Inventory & Assessment

Deep-scan pipeline definitions and assess migration readiness.

### 6.1 Pipeline Inventory

```bash
ado2gh pipelines inventory -c migration.yaml -i in/repos.txt
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | migration.yaml |
| `-i, --input` | (none) | Input file with repos to scan |
| `-p, --projects` | (none) | Scan entire projects instead of input file |
| `--parallel` | 12 | Enrichment threads |
| `--no-releases` | false | Skip classic release pipelines |
| `--clear` | false | Clear existing inventory before scan |
| `--dry-run` | false | Scan without writing to DB |
| `--db` | migration_state.db | State database path |

Examples:

```bash
# Scan pipelines for repos in input file
ado2gh pipelines inventory -c migration.yaml -i in/repos.txt

# Higher parallelism
ado2gh pipelines inventory -c migration.yaml -i in/repos.txt --parallel 16

# Scan specific projects
ado2gh pipelines inventory -c migration.yaml -p MyProject -p SharedInfra

# Clear and rescan
ado2gh pipelines inventory -c migration.yaml -i in/repos.txt --clear
```

Duration: ~20 minutes per 1000 pipelines at `--parallel 12`.

### 6.2 Pipeline Readiness Assessment

```bash
ado2gh pipeline-readiness -c migration.yaml -i in/repos.txt
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | migration.yaml |
| `-i, --input` | (none) | Input file with repos |
| `-o, --output` | output/pipeline_readiness.csv | Output path |
| `--db` | migration_state.db | State database |

Output classifies each pipeline:

| Level | Meaning | Typical Effort |
|-------|---------|---------------|
| **auto** | YAML pipeline, simple, no blockers | 0.5h |
| **assisted** | Has warnings (var groups, environments) | 2-8h |
| **manual** | Has blockers (unsupported tasks, self-hosted pools) | 8-24h |

Example:

```bash
ado2gh pipeline-readiness -c migration.yaml -i in/repos.txt -o output/readiness.csv
```

### 6.3 Service Connection Manifest

```bash
ado2gh service-connections -c migration.yaml -i in/repos.txt
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | migration.yaml |
| `-i, --input` | (none) | Input file — scans their ADO projects |
| `-o, --output` | output/service_connection_manifest.json | Output path |

Generates a JSON + CSV manifest mapping each ADO service connection to:
- Suggested GitHub secret names
- OIDC setup instructions (for Azure/AWS)
- Link to relevant GitHub Actions docs

Send the CSV to your ops/platform team for manual secret provisioning.

Example:

```bash
ado2gh service-connections -c migration.yaml -i in/repos.txt -o output/svc_manifest.json
```

---

## 7. Phase 3 — Planning & Phase Assignment

### 7.1 Score Repos & Assign to Phases

```bash
ado2gh phase assign -c migration.yaml -i in/repos.txt --output migration_phase.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | migration.yaml |
| `-i, --input` | (none) | Input file with repos to score |
| `--gh-org` | from config | Override GitHub org |
| `--output` | migration_phase.yaml | Output config with phase assignments |
| `--dry-run` | false | Score but don't write |
| `--db` | migration_state.db | State database |

Each repo is scored on 9 signals (0-100) and assigned to a phase:

| Phase | Risk Range | Repo Cap | Gate: Repos | Gate: Pipelines |
|-------|-----------|----------|------------|----------------|
| POC | 0-25 | 10 | 90% | 80% |
| Pilot | 25-45 | 100 | 95% | 90% |
| Wave 1 | 45-65 | 500 | 97% | 95% |
| Wave 2 | 65-80 | 1000 | 98% | 97% |
| Wave 3 | 80-100 | unlimited | 98% | 97% |

Examples:

```bash
# Score and assign
ado2gh phase assign -c migration.yaml -i in/repos.txt --output migration_phase.yaml

# Dry run to preview scores
ado2gh phase assign -c migration.yaml -i in/repos.txt --dry-run
```

### 7.2 Review Assignments

```bash
# All phases
ado2gh phase plan -c migration_phase.yaml

# Specific phase
ado2gh phase plan -c migration_phase.yaml -p poc
ado2gh phase plan -c migration_phase.yaml -p wave1

# Pipeline breakdown per wave
ado2gh pipelines plan -c migration_phase.yaml
ado2gh pipelines plan -c migration_phase.yaml -w 1

# Full wave-level plan
ado2gh plan -c migration_phase.yaml
```

---

## 8. Phase 4 — Execution

### 8.1 Dry Run (Always Do This First)

```bash
ado2gh phase run -p poc -c migration_phase.yaml --dry-run
```

Verifies connectivity, permissions, and config without creating anything on GitHub.

### 8.2 Execute POC

```bash
ado2gh phase run -p poc -c migration_phase.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `-p, --phase` | (required) | poc, pilot, wave1, wave2, wave3 |
| `-c, --config` | (required) | migration_phase.yaml |
| `--dry-run` | false | Simulate without changes |
| `--force` | false | Skip gate check from previous phase |
| `--db` | migration_state.db | State database |

What happens during execution:
1. Creates GitHub repos (if they don't exist)
2. `git clone --mirror` from ADO, `git push --mirror` to GitHub
3. Pushes LFS objects (if any)
4. Transforms ADO pipelines to GitHub Actions YAML
5. Creates GitHub Issues from ADO work items
6. Writes wiki pages to output directory
7. Generates secrets mapping manifest
8. Applies branch protection rules

### 8.3 Gate Check

After each phase, check if it passed the success thresholds:

```bash
ado2gh phase gate-check -p poc -c migration_phase.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `-p, --phase` | (required) | Phase to check |
| `-c, --config` | (required) | migration_phase.yaml |
| `--override` | false | Force gate to PASS |
| `--reason` | (none) | Required with --override, stored in DB |
| `--db` | migration_state.db | State database |

If the gate FAILS:
- Fix the failing repos and re-run the phase (it resumes automatically)
- Or override: `--override --reason "Approved by migration lead"`

```bash
# Override example
ado2gh phase gate-check -p poc --override --reason "2 repos excluded by design" -c migration_phase.yaml
```

### 8.4 Execute Remaining Phases

```bash
# Pilot — 100 repos
ado2gh phase run -p pilot -c migration_phase.yaml
ado2gh phase gate-check -p pilot -c migration_phase.yaml

# Wave 1 — 500 repos
ado2gh phase run -p wave1 -c migration_phase.yaml
ado2gh phase gate-check -p wave1 -c migration_phase.yaml

# Wave 2 — 1000 repos
ado2gh phase run -p wave2 -c migration_phase.yaml
ado2gh phase gate-check -p wave2 -c migration_phase.yaml

# Wave 3 — remaining repos
ado2gh phase run -p wave3 -c migration_phase.yaml
```

### 8.5 Skip Previous Gate (Force)

```bash
ado2gh phase run -p pilot -c migration_phase.yaml --force
```

### 8.6 Run Specific Wave (v3 Style)

```bash
ado2gh run -c migration_phase.yaml -w 1
ado2gh run -c migration_phase.yaml -w 1 --dry-run
ado2gh run -c migration_phase.yaml           # all waves
```

### 8.7 Interrupted Runs

If a run is interrupted (network issue, machine restart), just re-run the same command. The tool tracks completed batches in SQLite and resumes from the last checkpoint.

```bash
# Safe to run multiple times — skips completed repos
ado2gh phase run -p wave1 -c migration_phase.yaml
```

---

## 9. Phase 5 — Validation

Compare ADO source against GitHub target at the content level.

```bash
ado2gh validate -c migration_phase.yaml -o output/validation.csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | Config with repos |
| `-i, --input` | (none) | Input file (alternative to config waves) |
| `-o, --output` | validation_report.csv | Output path |
| `--db` | migration_state.db | State database |

### What It Checks Per Repo

| Check | What It Validates |
|-------|------------------|
| `repo_exists` | GitHub repo was created |
| `default_branch` | Branch name matches (e.g., both `main`) |
| `head_commit` | HEAD commit SHA matches between ADO and GH (proves code transferred) |
| `branches` | Branch count comparison |
| `workflows` | GitHub Actions workflow files present (if pipelines migrated) |
| `branch_protection` | Protection rules applied (if branch_policies migrated) |

### Verdicts

| Verdict | Meaning |
|---------|---------|
| PASS | Check passed |
| WARN | Minor discrepancy (e.g., 1-2 branches missing) |
| FAIL | Significant issue (e.g., commit SHA mismatch) |

### Output

- `validation_report.csv` — one row per repo with per-check verdicts
- `validation_report.json` — full detail including SHAs

### Examples

```bash
# Validate all repos from phase config
ado2gh validate -c migration_phase.yaml

# Validate specific repos
ado2gh validate -c migration.yaml -i in/repos.txt -o output/validation.csv
```

---

## 10. Phase 6 — Reporting

### HTML Report (Interactive)

```bash
ado2gh report -c migration_phase.yaml --format html --output output/report.html
```

Tabbed view: repo migrations + pipeline migrations. Dark theme. Open in browser.

### CSV Report (Stakeholders)

```bash
ado2gh report -c migration_phase.yaml --format csv --output output/report.csv
```

One row per repo/scope with status. Import into Excel or Google Sheets.

### JSON Report (Programmatic)

```bash
ado2gh report -c migration_phase.yaml --format json --output output/report.json
```

---

## 11. Phase 7 — ADO Cleanup

Run ONLY after validation passes for all repos.

```bash
ado2gh ado-cleanup -c migration_phase.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | Config with repos |
| `-i, --input` | (none) | Input file (alternative) |
| `--disable-pipelines / --no-disable-pipelines` | enabled | Disable ADO build pipelines |
| `--add-redirect / --no-redirect` | enabled | Push MIGRATION_NOTICE.md to ADO repo |
| `--archive / --no-archive` | disabled | Make ADO repo read-only |
| `--dry-run` | false | Simulate |
| `-p, --phase` | (none) | Cleanup only repos in this phase |
| `--db` | migration_state.db | State database |

### What Each Flag Does

| Action | Effect |
|--------|--------|
| `--disable-pipelines` | Sets ADO pipeline `queueStatus` to disabled |
| `--add-redirect` | Pushes `MIGRATION_NOTICE.md` with link to GitHub repo and `git remote set-url` instruction |
| `--archive` | Sets ADO repo `isDisabled: true` (no more pushes) |

### Examples

```bash
# Dry run first (always)
ado2gh ado-cleanup -c migration_phase.yaml --dry-run

# Default: disable pipelines + add redirect
ado2gh ado-cleanup -c migration_phase.yaml

# Full cleanup including repo archival
ado2gh ado-cleanup -c migration_phase.yaml --archive

# Cleanup only POC repos
ado2gh ado-cleanup -c migration_phase.yaml -p poc

# Cleanup specific repos from input file
ado2gh ado-cleanup -c migration.yaml -i in/repos.txt

# Only disable pipelines
ado2gh ado-cleanup -c migration_phase.yaml --no-redirect --no-archive

# Only add redirect notice
ado2gh ado-cleanup -c migration_phase.yaml --no-disable-pipelines --no-archive
```

---

## 12. Recovery & Rollback

### 12.1 Export Failed Repos

```bash
# All failed repos
ado2gh export-failed -o failed.txt

# Failed in specific phase
ado2gh export-failed -p wave1 -o failed_wave1.txt

# Custom database
ado2gh export-failed --db custom.db -o failed.txt
```

Output: text file with one `project/repo` per line. Use as `--input` for targeted retries.

### 12.2 Retry Failed Pipelines

```bash
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1 --dry-run
```

### 12.3 Rollback

```bash
# Full rollback — deletes GitHub repos (destructive)
ado2gh rollback -c migration_phase.yaml -w 1
ado2gh rollback -c migration_phase.yaml -w 1 --dry-run

# Scope-targeted rollback (keeps repos, undoes specific scopes)
ado2gh rollback -c migration_phase.yaml -w 1 -s branch_policies
ado2gh rollback -c migration_phase.yaml -w 1 -s pipelines
ado2gh rollback -c migration_phase.yaml -w 1 -s "branch_policies,pipelines"
```

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | (required) | Config file |
| `-w, --wave` | (required) | Wave number to rollback |
| `--dry-run` | false | Simulate |
| `-s, --scopes` | (none = full rollback) | Comma-separated scopes |
| `--db` | migration_state.db | State database |

Valid scope values: `repo`, `work_items`, `pipelines`, `wiki`, `secrets`, `branch_policies`

---

## 13. Monitoring

### Live Dashboard

```bash
ado2gh phase dashboard -c migration_phase.yaml
```

Shows all phases, gate status, batch progress, velocity.

### Migration Status

```bash
ado2gh status -c migration_phase.yaml          # all waves
ado2gh status -c migration_phase.yaml -w 1     # specific wave
```

### Pipeline Status

```bash
ado2gh pipelines status -c migration_phase.yaml -w 1
```

### Token Health

```bash
ado2gh token-status -c migration.yaml
```

Shows remaining rate limit for each configured token.

---

## 14. Complete End-to-End Example

```bash
# ── 1. Setup ────────────────────────────────────────
export ADO_PAT="your-ado-pat"
export ADO_ORG_URL="https://dev.azure.com/CONTOSO"
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"

cd c:\Users\snanjan\Downloads\ADO2GH
pip install -e .

# ── 2. Discovery ────────────────────────────────────
ado2gh discover -c migration.yaml

# ── 3. Create input file ────────────────────────────
# Review output/discovery/repos.csv
# Copy output/discovery/repos_template.txt to in/repos.txt
# Uncomment the repos you want to migrate
cp output/discovery/repos_template.txt in/repos.txt
# Edit in/repos.txt — uncomment repos

# ── 4. Pipeline scan + assessment ───────────────────
ado2gh pipelines inventory -c migration.yaml -i in/repos.txt --parallel 16
ado2gh pipeline-readiness -c migration.yaml -i in/repos.txt -o output/readiness.csv
ado2gh service-connections -c migration.yaml -i in/repos.txt

# ── 5. Phase assignment ─────────────────────────────
ado2gh phase assign -c migration.yaml -i in/repos.txt --output migration_phase.yaml
ado2gh phase plan -c migration_phase.yaml

# ── 6. POC (10 repos) ───────────────────────────────
ado2gh phase run -p poc -c migration_phase.yaml --dry-run
ado2gh phase run -p poc -c migration_phase.yaml
ado2gh validate -c migration_phase.yaml -o output/poc_validation.csv
ado2gh phase gate-check -p poc -c migration_phase.yaml

# ── 7. Pilot (100 repos) ────────────────────────────
ado2gh phase run -p pilot -c migration_phase.yaml
ado2gh validate -c migration_phase.yaml
ado2gh phase gate-check -p pilot -c migration_phase.yaml

# ── 8. Waves ────────────────────────────────────────
ado2gh phase run -p wave1 -c migration_phase.yaml
ado2gh phase gate-check -p wave1 -c migration_phase.yaml

ado2gh phase run -p wave2 -c migration_phase.yaml
ado2gh phase gate-check -p wave2 -c migration_phase.yaml

ado2gh phase run -p wave3 -c migration_phase.yaml

# ── 9. Monitor (run anytime) ────────────────────────
ado2gh phase dashboard -c migration_phase.yaml
ado2gh token-status -c migration.yaml

# ── 10. Validation ──────────────────────────────────
ado2gh validate -c migration_phase.yaml -o output/final_validation.csv

# ── 11. Reports ─────────────────────────────────────
ado2gh report -c migration_phase.yaml --format html --output output/report.html
ado2gh report -c migration_phase.yaml --format csv --output output/report.csv

# ── 12. ADO Cleanup ─────────────────────────────────
ado2gh ado-cleanup -c migration_phase.yaml --dry-run
ado2gh ado-cleanup -c migration_phase.yaml --archive
```

---

## 15. Command Quick Reference

| # | Command | Sample |
|---|---------|--------|
| 1 | `discover` | `ado2gh discover -c migration.yaml` |
| 2 | `pipelines inventory` | `ado2gh pipelines inventory -c migration.yaml -i in/repos.txt` |
| 3 | `pipeline-readiness` | `ado2gh pipeline-readiness -c migration.yaml -i in/repos.txt` |
| 4 | `service-connections` | `ado2gh service-connections -c migration.yaml -i in/repos.txt` |
| 5 | `phase assign` | `ado2gh phase assign -c migration.yaml -i in/repos.txt` |
| 6 | `phase plan` | `ado2gh phase plan -c migration_phase.yaml` |
| 7 | `phase run` | `ado2gh phase run -p poc -c migration_phase.yaml` |
| 8 | `phase gate-check` | `ado2gh phase gate-check -p poc -c migration_phase.yaml` |
| 9 | `phase dashboard` | `ado2gh phase dashboard -c migration_phase.yaml` |
| 10 | `validate` | `ado2gh validate -c migration_phase.yaml` |
| 11 | `report` | `ado2gh report -c migration_phase.yaml --format csv` |
| 12 | `ado-cleanup` | `ado2gh ado-cleanup -c migration_phase.yaml --archive` |
| 13 | `export-failed` | `ado2gh export-failed -p wave1 -o failed.txt` |
| 14 | `rollback` | `ado2gh rollback -c migration_phase.yaml -w 1 -s branch_policies` |
| 15 | `pipelines plan` | `ado2gh pipelines plan -c migration_phase.yaml -w 1` |
| 16 | `pipelines status` | `ado2gh pipelines status -c migration_phase.yaml -w 1` |
| 17 | `pipelines retry-failed` | `ado2gh pipelines retry-failed -c migration_phase.yaml -w 1` |
| 18 | `status` | `ado2gh status -c migration_phase.yaml -w 1` |
| 19 | `plan` | `ado2gh plan -c migration_phase.yaml` |
| 20 | `run` | `ado2gh run -c migration_phase.yaml -w 1` |
| 21 | `token-status` | `ado2gh token-status -c migration.yaml` |

---

## Output Directory Structure

After a complete migration run:

```
ADO2GH/
├── migration.yaml                    # Connection settings
├── migration_phase.yaml              # Generated — risk scores + phases
├── migration_state.db                # SQLite state (auto-managed)
├── in/
│   └── repos.txt                     # Your input file
├── output/
│   ├── discovery/
│   │   ├── repos.csv
│   │   ├── pipelines.csv
│   │   ├── repos_template.txt
│   │   └── discovery.json
│   ├── workflows/                    # Generated GitHub Actions YAML
│   │   └── {gh_org}/{gh_repo}/.github/workflows/
│   ├── wikis/                        # Exported wiki pages
│   │   └── {gh_org}/{gh_repo}/
│   ├── secrets/                      # Secrets mapping manifests
│   │   └── {gh_org}/{gh_repo}/secrets_mapping.json
│   ├── pipeline_readiness.csv
│   ├── service_connection_manifest.json
│   ├── validation.csv
│   ├── report.html
│   └── report.csv
└── failed_repos_*.txt                # Auto-generated retry lists
```
