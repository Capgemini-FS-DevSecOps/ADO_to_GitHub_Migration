# Command Reference — All ado2gh Commands

Complete list of every command with flags, examples, and expected output.

**Prerequisites for all commands:**
```bash
export ADO_PAT="<ado-personal-access-token>"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="<github-token>"
```

---

## Global Options

```bash
ado2gh --version          # Show version (5.1.0)
ado2gh --help             # List all commands
ado2gh <command> --help   # Help for a specific command
```

### The `--db` option

Most commands that read or write migration state accept `--db`, defaulting to
`migration_state.db`. Two environment variables outrank it:

- `ADO2GH_SQLITE_PATH` overrides whatever `--db` is set to.
- `ADO2GH_STORAGE_BACKEND=postgres` ignores `--db` entirely and uses the configured
  PostgreSQL connection instead.

### Output paths

Commands that write files default under `$ADO2GH_OUTPUT_DIR` when it is set. Read each
command's `--help` for the exact default: some `--output` options name a **file**
(`report`, `validate`, `export-failed`, `pipeline-readiness`) and others name a
**directory** (`discover`, `service-connections`).

---

## Phase 1: Discovery & Assessment

### `discover` — Scan ADO Org

```bash
# Basic scan
ado2gh discover -c migration.yaml

# Custom output directory
ado2gh discover -c migration.yaml --output output/discovery
```

**Options:** `-c/--config` (required), `-o/--output` (directory, default `$ADO2GH_OUTPUT_DIR/discovery`)

**What it does:** Enumerates all ADO projects, repos, and pipeline counts.
**Output:** four files in the output directory — `repos.csv` (one row per repo, for selecting
the repos you want), `pipelines.csv`, `discovery.json` (full detail), and `repos_template.txt`
(a ready-made input file for the `-i/--input` option other commands take).

---

### `pipelines inventory` — Deep Pipeline Scan

```bash
# Scan all projects from config
ado2gh pipelines inventory -c migration.yaml

# Scan specific projects only
ado2gh pipelines inventory -c migration.yaml -p ProjectA -p ProjectB

# Higher parallelism for faster scanning (default 12)
ado2gh pipelines inventory -c migration.yaml --parallel 16

# Custom DB path
ado2gh pipelines inventory -c migration.yaml --db custom_state.db
```

**Options:** `-c/--config` (required), `-p/--project` (repeatable), `--parallel` (default 12), `--db`

**What it does:** Fetches full pipeline definitions (YAML content, variables, environments, run history). Stores them in the state database, which is what `pipeline-readiness` and the pipeline migration steps read.
**Duration:** ~20 min per 1000 pipelines at `--parallel 12`

---

### `pipeline-readiness` — Conversion Assessment

```bash
# Assess all pipelines
ado2gh pipeline-readiness -c migration.yaml

# Custom output
ado2gh pipeline-readiness -c migration.yaml -o output/readiness.csv

# Assess only the repos listed in an input file
ado2gh pipeline-readiness -c migration.yaml -i in/repos.txt
```

**Options:** `-c/--config` (required), `-i/--input`, `-o/--output` (CSV file, default `$ADO2GH_OUTPUT_DIR/pipeline_readiness.csv`), `--db`

**What it does:** Classifies each inventoried pipeline as auto/assisted/manual with an effort estimate. Without `-i/--input`, the repos come from the config waves.
**Output:** CSV report + JSON detail beside it + console summary table

---

### `service-connections` — Ops Manifest

```bash
# Generate manifest
ado2gh service-connections -c migration.yaml

# Custom output directory
ado2gh service-connections -c migration.yaml -o output/service_connections
```

**Options:** `-c/--config` (required), `-i/--input`, `-o/--output` (directory, default `$ADO2GH_OUTPUT_DIR/service_connections`)

**What it does:** Maps every ADO service connection to GitHub secrets/OIDC with setup instructions. Only connection *names* are readable through the ADO API — never their credentials — so the manifest is a to-do list for an ops team, not a migration.
**Output:** JSON manifest + CSV for ops teams

---

### `token-status` — Token Health Check

```bash
ado2gh token-status -c migration.yaml
```

**Options:** `-c/--config` (required)

**What it does:** Checks rate limit remaining for all configured GitHub tokens.
**Output:** Table showing each token's remaining quota + status

---

## Phase 2: Planning

### `phase assign` — Risk Score & Auto-Assign

```bash
# Score and assign
ado2gh phase assign -c migration.yaml

# Custom DB path
ado2gh phase assign -c migration.yaml --db custom_state.db
```

**Options:** `-c/--config` (required), `--db`

`-c/--config` is the *settings* config carrying the ADO organisation and the target
`gh_org`; the target org is read from there, not from a flag. There is no `--output` and no
dry-run mode: the phase config is always written next to the settings config, one wave per
non-empty phase.

**What it does:** Scores every repo in the organisation from nine weighted signals (0-100) and assigns it to POC/Pilot/Wave1-3. Each score is also stored in the state database.
**Output:** a `migration_phase.yaml` beside the settings config, with risk scores and phase assignments. Run this before `phase plan` and `phase run`.

---

### `phase plan` — Review Assignments

```bash
ado2gh phase plan -c migration_phase.yaml
```

**Options:** `-c/--config` (required), `--db`

**What it does:** Shows how many repos and waves each phase holds, without running anything. There is no per-phase filter — every phase is listed.

---

### `plan` — Wave-Level Plan

```bash
ado2gh plan -c migration_phase.yaml
```

**Options:** `-c/--config` (required)

**What it does:** Shows every wave with repos, scopes, and pipeline counts.

---

## Phase 3: Execution

### `phase run` — Execute a Phase

```bash
# Dry run first (always recommended)
ado2gh phase run -p poc -c migration_phase.yaml --dry-run

# Execute POC
ado2gh phase run -p poc -c migration_phase.yaml --live

# Execute Pilot
ado2gh phase run -p pilot -c migration_phase.yaml --live

# Execute waves
ado2gh phase run -p wave1 -c migration_phase.yaml --live
ado2gh phase run -p wave2 -c migration_phase.yaml --live
ado2gh phase run -p wave3 -c migration_phase.yaml --live

# Get past a BLOCKED gate on the previous phase — two steps, in this order.
# Record the override first (this is the audited act), then run the phase.
ado2gh phase gate-check -p poc -c migration_phase.yaml --override --reason "2 repos excluded by design"
ado2gh phase run -p pilot -c migration_phase.yaml --live

# Custom DB
ado2gh phase run -p poc -c migration_phase.yaml --db custom.db --live
```

**Options:** `-c/--config` (required), `-p/--phase` (required, one of `poc|pilot|wave1|wave2|wave3`), `--dry-run/--live` (default: `--dry-run`), `--force`, `--db`

**`--force` does not skip a blocked gate.** Every phase after `poc` checks the previous
phase's gate before it starts, and forcing past a *blocking* one requires a reason, which
`phase run` has no flag to supply — so `phase run --force` against a blocked gate fails with
`Gate blocked for prior phase <name>: forcing past it requires override_reason` and nothing
is migrated. Record the override with `phase gate-check --override --reason "..."` first, as
shown above. `--force` still has an effect where the previous gate is not blocking.

**What it does:** Migrates repos in sub-batches with checkpointing. Auto-resumes if interrupted.
**Side effects:** Creates GitHub repos, pushes code, transforms pipelines, creates issues.

---

### `run` — Wave-Level Execution

```bash
# Run specific wave
ado2gh run -c migration_phase.yaml -w 1 --live

# Run all waves
ado2gh run -c migration_phase.yaml --live

# Dry run
ado2gh run -c migration_phase.yaml -w 1 --dry-run
```

**Options:** `-c/--config` (required), `-w/--wave` (omit to run every wave), `--dry-run/--live` (default: `--dry-run`), `--db`

**A re-run is not free.** Nothing is skipped because an earlier run finished it — every scope
a repo asks for is executed again, and what that costs depends on the scope:

| Scope | On a re-run |
|-------|-------------|
| `repo` | The mirror strategy force-pushes over the GitHub repo again, discarding anything pushed there since. GEI instead skips when the target's HEAD already matches ADO, and stops the repo when it exists with a different HEAD. |
| `pipelines` | Skips the pipelines this wave already recorded as completed; re-transforms if the workflows are gone from the branch, or if you re-run under a new `--wave`. |
| `work_items` | Creates the issues again — one duplicate GitHub issue per ADO work item, every time. |

Only the `repo` scope runs by default, and so is `--dry-run`: without `--live` this
reports what it would do and changes nothing. Read that report before adding `--live`
to a wave
that partly succeeded.

---

### `phase gate-check` — Validate Phase Success

```bash
# Check gate
ado2gh phase gate-check -p poc -c migration_phase.yaml

# Override with reason (stored in DB for audit)
ado2gh phase gate-check -p poc -c migration_phase.yaml --override --reason "2 repos excluded by design"
```

**Options:** `-c/--config` (required), `-p/--phase` (required, one of `poc|pilot|wave1|wave2|wave3`), `--override`, `--reason`, `--db`

**What it does:** Checks repo + pipeline success percentages against phase thresholds. `--override` without a non-empty `--reason` is rejected; the reason is stored on the gate record and shown in audit history.
**Output:** PASS / FAIL / OVERRIDE with details

---

## Phase 4: Monitoring

### `phase dashboard` — Live Dashboard

```bash
ado2gh phase dashboard -c migration_phase.yaml
```

**Options:** `-c/--config` (required), `--db`

**What it does:** Shows all phases, gates, batch checkpoints, progress percentage.

---

### `status` — Migration Status

```bash
# All waves
ado2gh status -c migration_phase.yaml

# Specific wave
ado2gh status -c migration_phase.yaml -w 1
```

**Options:** `-c/--config` (required), `-w/--wave` (omit for a summary of every wave), `--db`

---

### `pipelines status` — Pipeline Status

```bash
ado2gh pipelines status -c migration_phase.yaml -w 1
```

**Options:** `-c/--config` (required), `-w/--wave` (required), `--db`

**What it does:** Per-pipeline migration status with complexity, warnings, unsupported tasks.

---

## Phase 5: Validation

### `validate` — Post-Migration Verification

```bash
# Validate all repos
ado2gh validate -c migration_phase.yaml

# Custom output
ado2gh validate -c migration_phase.yaml -o output/validation.csv

# Validate only the repos listed in an input file
ado2gh validate -c migration_phase.yaml -i in/repos.txt
```

**Options:** `-c/--config` (required), `-i/--input`, `-o/--output` (CSV file, default `$ADO2GH_OUTPUT_DIR/validation_report.csv`), `--db`

**What it does:** Per-repo checks:
1. GitHub repo exists
2. Default branch matches
3. **HEAD commit SHA matches** (proves code transferred)
4. Branch count comparison
5. Workflows present (if pipelines migrated)
6. Branch protection applied (if policies migrated)

**Output:** CSV at `-o`, with the JSON detail written beside it

---

## Phase 6: Post-Migration

### `ado-cleanup` — ADO Side Cleanup

```bash
# Dry run first
ado2gh ado-cleanup -c migration_phase.yaml --dry-run

# Default: disable pipelines + add redirect notice
ado2gh ado-cleanup -c migration_phase.yaml --live

# Full cleanup: disable + redirect + archive ADO repo
ado2gh ado-cleanup -c migration_phase.yaml --archive --live

# Clean up only the repos listed in an input file
ado2gh ado-cleanup -c migration_phase.yaml -i in/poc_repos.txt --live
```

**Options:** `-c/--config` (required), `-i/--input`, `--archive`, `--dry-run/--live` (default: `--dry-run`)

**What it does:**
- Disables all ADO build pipelines for the selected repos
- Pushes `MIGRATION_NOTICE.md` to the ADO repo with a link to GitHub
- With `--archive`, archives (disables) the ADO repo afterwards

Disabling the pipelines and pushing the notice are not individually switchable; to limit the
blast radius, narrow the repo set with `-i/--input` instead.

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

**Options:** `-c/--config` (required), `--output` (file, default `$ADO2GH_OUTPUT_DIR/migration_report.html`), `--format` (`html|json|csv`, default `html`), `--db`

`--output` has no `-o` short form here, unlike the other commands. The report is built
entirely from the state database, so `--config` is accepted for consistency but not read.

---

### `export-failed` — Failed Repo List

```bash
# All failed repos
ado2gh export-failed --output failed.txt

# Failed repos for specific phase
ado2gh export-failed -p wave1 --output failed_wave1.txt
```

**Options:** `-o/--output` (file, default `$ADO2GH_OUTPUT_DIR/failed_repos.txt`), `-p/--phase` (omit to export every failure), `--db`. No `--config` — the failures come from the state database.

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

**Options:** `-c/--config` (required), `-w/--wave` (required), `-s/--scopes`, `--dry-run`, `--db`

Omitting `--scopes` rolls back the whole wave, which deletes the GitHub repositories it created.

---

### `pipelines retry-failed` — Retry Failed Pipelines

```bash
# Retry failed pipelines in wave 1
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1

# Dry run
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1 --dry-run
```

**Options:** `-c/--config` (required), `-w/--wave` (required), `--dry-run`, `--db`

**What it does:** Clears the previous failure for that wave's pipelines and runs the wave again. Under `--dry-run` nothing is reset and nothing is pushed.

---

### `push-workflows` — Push Generated Workflows

```bash
# Push the generated workflows and open a PR per repo
ado2gh push-workflows -c migration_phase.yaml --live

# Push from a custom workflows directory onto a named branch
ado2gh push-workflows -c migration_phase.yaml -d output/workflows --branch ado2gh/migrated-workflows --live

# Target a base branch other than the repo default
ado2gh push-workflows -c migration_phase.yaml --base develop --live

# Dry run (the default; omit --live to preview only)
ado2gh push-workflows -c migration_phase.yaml
```

**Options:** `-c/--config` (required), `-i/--input`, `-d/--workflows-dir` (default `$ADO2GH_OUTPUT_DIR/workflows`), `--branch` (default `ado2gh/migrated-workflows`), `--base` (default: the repo's default branch), `--dry-run/--live` (default `--dry-run`; pass `--live` to actually push and open the pull request)

**What it does:** Commits locally generated workflow YAML to a branch on each GitHub target repo and opens a pull request for it.

---

## Complete End-to-End Example

```bash
# ── Setup ────────────────────────────────────────────
export ADO_PAT="<ado-personal-access-token>"
export ADO_ORG_URL="https://dev.azure.com/CONTOSO"
export GH_TOKEN_1="<github-token-1>"
export GH_TOKEN_2="<github-token-2>"

# ── Discovery ───────────────────────────────────────
ado2gh discover -c migration.yaml
ado2gh pipelines inventory -c migration.yaml --parallel 16
ado2gh pipeline-readiness -c migration.yaml -o output/readiness.csv
ado2gh service-connections -c migration.yaml

# ── Planning ────────────────────────────────────────
# Writes migration_phase.yaml next to migration.yaml
ado2gh phase assign -c migration.yaml
ado2gh phase plan -c migration_phase.yaml

# ── POC (10 repos) ──────────────────────────────────
ado2gh phase run -p poc -c migration_phase.yaml --dry-run
ado2gh phase run -p poc -c migration_phase.yaml --live
ado2gh validate -c migration_phase.yaml -o output/poc_validation.csv
ado2gh phase gate-check -p poc -c migration_phase.yaml

# ── Pilot (100 repos) ───────────────────────────────
ado2gh phase run -p pilot -c migration_phase.yaml --live
ado2gh validate -c migration_phase.yaml
ado2gh phase gate-check -p pilot -c migration_phase.yaml

# ── Waves ────────────────────────────────────────────
ado2gh phase run -p wave1 -c migration_phase.yaml --live
ado2gh phase gate-check -p wave1 -c migration_phase.yaml

ado2gh phase run -p wave2 -c migration_phase.yaml --live
ado2gh phase gate-check -p wave2 -c migration_phase.yaml

ado2gh phase run -p wave3 -c migration_phase.yaml --live

# ── Monitor throughout ───────────────────────────────
ado2gh phase dashboard -c migration_phase.yaml
ado2gh token-status -c migration.yaml

# ── Final validation + cleanup ───────────────────────
ado2gh validate -c migration_phase.yaml -o output/final_validation.csv
ado2gh report -c migration_phase.yaml --format html --output output/final_report.html
ado2gh report -c migration_phase.yaml --format csv --output output/final_report.csv
ado2gh ado-cleanup -c migration_phase.yaml --dry-run
ado2gh ado-cleanup -c migration_phase.yaml --archive --live
```

---

## Removed Scripts

The following shell scripts were removed as they purely wrapped existing CLI commands (FR-033). Use the equivalent CLI commands directly:

| Removed Script | Replacement |
|----------------|-------------|
| `scripts/discover.sh` | `ado2gh discover -c migration.yaml -o output/discovery` |
| `scripts/migrate.sh` | `ado2gh phase run` + `ado2gh validate` + `ado2gh push-workflows` + `ado2gh report` |
| `scripts/migrate-full.sh` | Same as above plus `ado2gh pipeline-readiness` + `ado2gh service-connections` + `ado2gh phase plan` |

Local development helpers are now in `scripts/dev/`.

