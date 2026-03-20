# Command Reference — All ado2gh Commands

Complete list of every command with flags, examples, and expected output.

**Prerequisites for all commands:**
```bash
export ADO_PAT="your-ado-pat"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="your-github-token"
```

---

## Global Options

```bash
ado2gh --version          # Show version (5.0.0)
ado2gh --help             # List all commands
ado2gh <command> --help   # Help for a specific command
```

---

## Phase 1: Discovery & Assessment

### `discover` — Scan ADO Org

```bash
# Basic scan
ado2gh discover -c migration.yaml

# Custom output path
ado2gh discover -c migration.yaml --output my_discovery.yaml
```

**What it does:** Enumerates all ADO projects, repos, and pipeline counts. Writes JSON inventory.
**Output:** `discovered_repos.yaml` (or specified path)

---

### `pipelines inventory` — Deep Pipeline Scan

```bash
# Scan all projects from config
ado2gh pipelines inventory -c migration.yaml

# Scan specific projects only
ado2gh pipelines inventory -c migration.yaml -p ProjectA -p ProjectB

# Higher parallelism for faster scanning
ado2gh pipelines inventory -c migration.yaml --parallel 16

# Skip release pipelines
ado2gh pipelines inventory -c migration.yaml --no-releases

# Clear existing inventory and rescan
ado2gh pipelines inventory -c migration.yaml --clear

# Dry run (no DB writes)
ado2gh pipelines inventory -c migration.yaml --dry-run

# Custom DB path
ado2gh pipelines inventory -c migration.yaml --db custom_state.db
```

**What it does:** Fetches full pipeline definitions (YAML content, variables, environments, run history). Stores in SQLite.
**Duration:** ~20 min per 1000 pipelines at --parallel 12

---

### `pipeline-readiness` — Conversion Assessment

```bash
# Assess all pipelines
ado2gh pipeline-readiness -c migration.yaml

# Custom output
ado2gh pipeline-readiness -c migration.yaml -o output/readiness.csv
```

**What it does:** Classifies each pipeline as auto/assisted/manual with effort estimate.
**Output:** CSV report + JSON detail + console summary table

---

### `service-connections` — Ops Manifest

```bash
# Generate manifest
ado2gh service-connections -c migration.yaml

# Custom output
ado2gh service-connections -c migration.yaml -o output/svc_manifest.json
```

**What it does:** Maps every ADO service connection to GitHub secrets/OIDC with setup instructions.
**Output:** JSON manifest + CSV for ops teams

---

### `token-status` — Token Health Check

```bash
ado2gh token-status -c migration.yaml
```

**What it does:** Checks rate limit remaining for all configured GitHub tokens.
**Output:** Table showing each token's remaining quota + status

---

## Phase 2: Planning

### `phase assign` — Risk Score & Auto-Assign

```bash
# Score and assign
ado2gh phase assign -c migration.yaml --output migration_phase.yaml

# Override GitHub org
ado2gh phase assign -c migration.yaml --gh-org my-github-org

# Dry run (score but don't write)
ado2gh phase assign -c migration.yaml --dry-run
```

**What it does:** Scores every repo (9 signals, 0-100), assigns to POC/Pilot/Wave1-3.
**Output:** `migration_phase.yaml` with risk scores and phase assignments

---

### `phase plan` — Review Assignments

```bash
# All phases
ado2gh phase plan -c migration_phase.yaml

# Specific phase
ado2gh phase plan -c migration_phase.yaml -p poc
ado2gh phase plan -c migration_phase.yaml -p wave1
```

**What it does:** Shows per-phase breakdown: repo count, risk range, pipelines, gate thresholds.

---

### `plan` — Wave-Level Plan

```bash
ado2gh plan -c migration_phase.yaml
```

**What it does:** Shows every wave with repos, scopes, and pipeline counts.

---

### `pipelines plan` — Pipeline Breakdown

```bash
# All waves
ado2gh pipelines plan -c migration_phase.yaml

# Specific wave
ado2gh pipelines plan -c migration_phase.yaml -w 1
```

**What it does:** Shows per-repo pipeline counts by type (YAML/Classic/Release) and complexity.

---

## Phase 3: Execution

### `phase run` — Execute a Phase

```bash
# Dry run first (always recommended)
ado2gh phase run -p poc -c migration_phase.yaml --dry-run

# Execute POC
ado2gh phase run -p poc -c migration_phase.yaml

# Execute Pilot
ado2gh phase run -p pilot -c migration_phase.yaml

# Execute waves
ado2gh phase run -p wave1 -c migration_phase.yaml
ado2gh phase run -p wave2 -c migration_phase.yaml
ado2gh phase run -p wave3 -c migration_phase.yaml

# Skip gate check from previous phase
ado2gh phase run -p pilot -c migration_phase.yaml --force

# Custom DB
ado2gh phase run -p poc -c migration_phase.yaml --db custom.db
```

**What it does:** Migrates repos in sub-batches with checkpointing. Auto-resumes if interrupted.
**Side effects:** Creates GitHub repos, pushes code, transforms pipelines, creates issues.

---

### `run` — Wave-Level Execution (v3 compat)

```bash
# Run specific wave
ado2gh run -c migration_phase.yaml -w 1

# Run all waves
ado2gh run -c migration_phase.yaml

# Dry run
ado2gh run -c migration_phase.yaml -w 1 --dry-run
```

---

### `phase gate-check` — Validate Phase Success

```bash
# Check gate
ado2gh phase gate-check -p poc -c migration_phase.yaml

# Override with reason (stored in DB for audit)
ado2gh phase gate-check -p poc --override --reason "2 repos excluded by design"
```

**What it does:** Checks repo + pipeline success percentages against phase thresholds.
**Output:** PASS / FAIL / OVERRIDE with details

---

## Phase 4: Monitoring

### `phase dashboard` — Live Dashboard

```bash
ado2gh phase dashboard -c migration_phase.yaml
```

**What it does:** Shows all phases, gates, batch checkpoints, progress percentage.

---

### `status` — Migration Status

```bash
# All waves
ado2gh status -c migration_phase.yaml

# Specific wave
ado2gh status -c migration_phase.yaml -w 1
```

---

### `pipelines status` — Pipeline Status

```bash
ado2gh pipelines status -c migration_phase.yaml -w 1
```

**What it does:** Per-pipeline migration status with complexity, warnings, unsupported tasks.

---

## Phase 5: Validation

### `validate` — Post-Migration Verification

```bash
# Validate all repos
ado2gh validate -c migration_phase.yaml

# Custom output
ado2gh validate -c migration_phase.yaml -o output/validation.csv
```

**What it does:** Per-repo checks:
1. GitHub repo exists
2. Default branch matches
3. **HEAD commit SHA matches** (proves code transferred)
4. Branch count comparison
5. Workflows present (if pipelines migrated)
6. Branch protection applied (if policies migrated)

**Output:** CSV + JSON report

---

## Phase 6: Post-Migration

### `ado-cleanup` — ADO Side Cleanup

```bash
# Dry run first
ado2gh ado-cleanup -c migration_phase.yaml --dry-run

# Default: disable pipelines + add redirect notice
ado2gh ado-cleanup -c migration_phase.yaml

# Full cleanup: disable + redirect + archive ADO repo
ado2gh ado-cleanup -c migration_phase.yaml --archive

# Only disable pipelines (no redirect, no archive)
ado2gh ado-cleanup -c migration_phase.yaml --no-redirect --no-archive

# Only add redirect notice
ado2gh ado-cleanup -c migration_phase.yaml --no-disable-pipelines --no-archive

# Cleanup specific phase only
ado2gh ado-cleanup -c migration_phase.yaml -p poc
ado2gh ado-cleanup -c migration_phase.yaml -p wave1
```

**What it does:**
- Disables all ADO build pipelines for migrated repos
- Pushes `MIGRATION_NOTICE.md` to ADO repo with link to GitHub
- Optionally archives (disables) the ADO repo

---

### `report` — Generate Reports

```bash
# HTML report (interactive, tabbed)
ado2gh report -c migration_phase.yaml --format html --output report.html

# CSV report (for stakeholders/Excel)
ado2gh report -c migration_phase.yaml --format csv --output report.csv

# JSON report (for programmatic consumption)
ado2gh report -c migration_phase.yaml --format json --output report.json
```

---

### `export-failed` — Failed Repo List

```bash
# All failed repos
ado2gh export-failed --output failed.txt

# Failed repos for specific phase
ado2gh export-failed -p wave1 --output failed_wave1.txt
```

**Output:** Text file with one `project/repo` per line — can be used for targeted retries.

---

## Recovery & Rollback

### `rollback` — Undo Migration

```bash
# Full rollback (deletes GitHub repos)
ado2gh rollback -c migration_phase.yaml -w 1

# Dry run
ado2gh rollback -c migration_phase.yaml -w 1 --dry-run

# Rollback only branch policies (keep repos)
ado2gh rollback -c migration_phase.yaml -w 1 --scopes branch_policies

# Rollback only pipelines
ado2gh rollback -c migration_phase.yaml -w 1 --scopes pipelines

# Rollback multiple scopes
ado2gh rollback -c migration_phase.yaml -w 1 --scopes "branch_policies,pipelines"
```

---

### `pipelines retry-failed` — Retry Failed Pipelines

```bash
# Retry failed pipelines in wave 1
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1

# Dry run
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1 --dry-run
```

---

## Complete End-to-End Example

```bash
# ── Setup ────────────────────────────────────────────
export ADO_PAT="your-pat"
export ADO_ORG_URL="https://dev.azure.com/CONTOSO"
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"

# ── Discovery ───────────────────────────────────────
ado2gh discover -c migration.yaml
ado2gh pipelines inventory -c migration.yaml --parallel 16
ado2gh pipeline-readiness -c migration.yaml -o output/readiness.csv
ado2gh service-connections -c migration.yaml

# ── Planning ────────────────────────────────────────
ado2gh phase assign -c migration.yaml --output migration_phase.yaml
ado2gh phase plan -c migration_phase.yaml

# ── POC (10 repos) ──────────────────────────────────
ado2gh phase run -p poc -c migration_phase.yaml --dry-run
ado2gh phase run -p poc -c migration_phase.yaml
ado2gh validate -c migration_phase.yaml -o output/poc_validation.csv
ado2gh phase gate-check -p poc -c migration_phase.yaml

# ── Pilot (100 repos) ───────────────────────────────
ado2gh phase run -p pilot -c migration_phase.yaml
ado2gh validate -c migration_phase.yaml
ado2gh phase gate-check -p pilot -c migration_phase.yaml

# ── Waves ────────────────────────────────────────────
ado2gh phase run -p wave1 -c migration_phase.yaml
ado2gh phase gate-check -p wave1 -c migration_phase.yaml

ado2gh phase run -p wave2 -c migration_phase.yaml
ado2gh phase gate-check -p wave2 -c migration_phase.yaml

ado2gh phase run -p wave3 -c migration_phase.yaml

# ── Monitor throughout ───────────────────────────────
ado2gh phase dashboard -c migration_phase.yaml
ado2gh token-status -c migration.yaml

# ── Final validation + cleanup ───────────────────────
ado2gh validate -c migration_phase.yaml -o output/final_validation.csv
ado2gh report -c migration_phase.yaml --format html -o output/final_report.html
ado2gh report -c migration_phase.yaml --format csv -o output/final_report.csv
ado2gh ado-cleanup -c migration_phase.yaml --dry-run
ado2gh ado-cleanup -c migration_phase.yaml --archive
```
