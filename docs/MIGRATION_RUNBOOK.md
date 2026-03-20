# Migration Runbook — Step-by-Step Execution Guide

This runbook walks through a complete ADO-to-GitHub migration from discovery to post-migration cleanup. Follow each step in order.

---

## Pre-Migration (Day 1–2)

### Step 1: Discovery

Scan the entire ADO organization to understand scope.

```bash
ado2gh discover --config migration.yaml
```

**Output:** `discovered_repos.yaml` — all projects, repos, pipeline counts.

**Review:** Check total repo count, identify large repos (>1GB), note projects with many pipelines.

### Step 2: Pipeline Inventory

Deep-scan every pipeline definition (YAML structure, variables, environments, run history).

```bash
ado2gh pipelines inventory --config migration.yaml --parallel 16
```

**Duration:** ~20 min per 1000 pipelines. Data stored in `migration_state.db`.

### Step 3: Pipeline Readiness Assessment

Classify pipelines before committing to migration.

```bash
ado2gh pipeline-readiness --config migration.yaml --output output/pipeline_readiness.csv
```

**Review the output carefully:**
- **Auto** pipelines — will convert without manual work
- **Assisted** — need review after conversion (variable groups, environments)
- **Manual** — have blockers (unsupported tasks, self-hosted pools)
- **Total effort hours** — use for project planning

### Step 4: Service Connection Manifest

Generate the ops-team handoff document for manual secret setup.

```bash
ado2gh service-connections --config migration.yaml --output output/service_connection_manifest.json
```

**Action:** Send the CSV to your ops/platform team. They need to:
1. Create GitHub secrets matching the suggested names
2. Set up OIDC for Azure/AWS connections (preferred over static secrets)
3. Complete this before pipeline testing

### Step 5: Phase Assignment

Score repos and auto-assign to phases.

```bash
ado2gh phase assign --config migration.yaml --output migration_phase.yaml
```

**Review:** `ado2gh phase plan --config migration_phase.yaml`

This generates `migration_phase.yaml` with risk scores and phase assignments.

---

## POC Phase (Day 3)

### Step 6: Dry Run

```bash
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
```

Verifies connectivity, permissions, and config without making changes.

### Step 7: Execute POC

```bash
ado2gh phase run --phase poc --config migration_phase.yaml
```

Migrates 10 lowest-risk repos with their pipelines.

### Step 8: Validate POC

```bash
ado2gh validate --config migration_phase.yaml --output output/poc_validation.csv
```

**Check:** Every repo should show `PASS` for HEAD commit SHA match.

### Step 9: Manual Verification

For each POC repo:
- [ ] Open the GitHub repo — does code look correct?
- [ ] Check branches — all present?
- [ ] Review generated GitHub Actions workflows — do they match ADO pipeline intent?
- [ ] Check issues — work items migrated as expected?
- [ ] Verify branch protection rules applied

### Step 10: Gate Check

```bash
ado2gh phase gate-check --phase poc --config migration_phase.yaml
```

Must show `PASS`. If it shows `FAIL`:
- Fix the issue and re-run the failed repos (auto-resumes)
- Or override: `--override --reason "POC failures are acceptable: <reason>"`

---

## Pilot Phase (Day 4–5)

### Step 11: Execute Pilot

```bash
ado2gh phase run --phase pilot --config migration_phase.yaml
```

100 repos. Monitor with:

```bash
ado2gh phase dashboard --config migration_phase.yaml
```

### Step 12: Validate + Gate

```bash
ado2gh validate --config migration_phase.yaml
ado2gh phase gate-check --phase pilot --config migration_phase.yaml
```

---

## Wave Execution (Day 6+)

### Steps 13–15: Wave 1 → Wave 2 → Wave 3

```bash
# Wave 1: 500 repos
ado2gh phase run --phase wave1 --config migration_phase.yaml
ado2gh validate --config migration_phase.yaml
ado2gh phase gate-check --phase wave1

# Wave 2: 1000 repos
ado2gh phase run --phase wave2 --config migration_phase.yaml
ado2gh phase gate-check --phase wave2

# Wave 3: remaining repos
ado2gh phase run --phase wave3 --config migration_phase.yaml
ado2gh phase gate-check --phase wave3
```

**Interruptions:** If a run is interrupted (network, machine restart), just re-run the same command. It resumes from the last completed batch automatically.

**Failed repos:** After each phase, check `failed_repos_{phase}.txt`. Fix issues and re-run — the tool skips already-completed repos.

---

## Post-Migration (Day N+1)

### Step 16: Final Validation

```bash
ado2gh validate --config migration_phase.yaml --output output/final_validation.csv
```

### Step 17: Generate Reports

```bash
# Interactive HTML report
ado2gh report --config migration_phase.yaml --format html --output output/migration_report.html

# CSV for stakeholders
ado2gh report --config migration_phase.yaml --format csv --output output/migration_report.csv
```

### Step 18: ADO Cleanup

**Only after validation passes for all repos.**

```bash
# Dry run first
ado2gh ado-cleanup --config migration_phase.yaml --dry-run

# Disable pipelines + add redirect notice
ado2gh ado-cleanup --config migration_phase.yaml

# Full cleanup including repo archival (makes ADO repo read-only)
ado2gh ado-cleanup --config migration_phase.yaml --archive
```

### Step 19: Notify Teams

Developers will see `MIGRATION_NOTICE.md` in ADO repos with:
- Link to the new GitHub repo
- `git remote set-url` command to update their local clones
- Migration date

---

## Handling Failures

### Re-run failed repos

```bash
# See what failed
ado2gh export-failed --phase wave1 --output failed.txt

# Re-run the phase (only pending/failed repos are processed)
ado2gh phase run --phase wave1 --config migration_phase.yaml
```

### Rollback specific scopes

```bash
# Rollback only branch policies (keep the repo)
ado2gh rollback --wave 3 --scopes branch_policies --config migration_phase.yaml

# Rollback pipelines only
ado2gh rollback --wave 3 --scopes pipelines --config migration_phase.yaml

# Full rollback (deletes GitHub repos)
ado2gh rollback --wave 3 --config migration_phase.yaml
```

### Override a failed gate

```bash
ado2gh phase gate-check --phase pilot --override --reason "2 repos intentionally excluded"
```

The override reason is stored in the SQLite database for audit.

---

## Monitoring During Migration

```bash
# Live dashboard
ado2gh phase dashboard --config migration_phase.yaml

# Per-wave status
ado2gh status --config migration_phase.yaml --wave 3

# Pipeline migration status
ado2gh pipelines status --config migration_phase.yaml --wave 3

# Token health
ado2gh token-status --config migration.yaml
```
