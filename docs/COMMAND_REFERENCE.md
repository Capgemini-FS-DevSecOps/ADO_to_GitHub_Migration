# Command Reference — All ado2gh Commands

Complete list of every command with flags, examples, and expected output.

**Prerequisites for all commands:**
```bash
export ADO_PAT="your-ado-pat"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="your-github-token"
```

All configured service endpoints (`ado_org_url`, `gh_api_url`, `gh_web_url`,
and `OPENAI_BASE_URL`) must be absolute HTTPS URLs. ADO and GitHub URL fields
also reject embedded credentials, queries, and fragments.
For GitHub Enterprise Server, configure both `global.gh_api_url` (normally
`https://HOST/api/v3`) and `global.gh_web_url` (`https://HOST`); the web URL is
used for Git/redirect links and its authority must match the approved API URL.

---

## Global Options

```bash
ado2gh --version          # Show version (6.0.0)
ado2gh --help             # List all commands
ado2gh <command> --help   # Help for a specific command
```

---

## Production control plane: `agent`

The `agent` group is the enterprise authorization boundary. Phase/wave commands
remain useful for segmentation and reports, but every legacy mutation command
is disabled for live use and accepts only `--dry-run` compatibility previews.

### `agent plan` — create an immutable organization plan

```bash
# Full organization
ado2gh agent plan -c migration.yaml -o output/pev_plan.json

# Reviewed subset manifest
ado2gh agent plan -c migration.yaml -i repos.txt -o output/pev_plan.json

# Explicit scope override
ado2gh agent plan -c migration.yaml --scopes repo,pipelines,branch_policies
```

The command prints a content-addressed plan-schema-v7 `plan_id`. Review the
canonical ADO/GitHub API origins, immutable organization identities, mappings,
scopes, full source refs, approved existing-target refs, non-Git digest/count
snapshots, exact pipeline receipts/revisions, target visibility, explicit team
roles and `access_policy_approved`, policies, and task graph before approval.
For a full-organization plan, also review the eligible source repository count
and inventory digest; an input cohort is explicitly labeled as a subset. If
unlinked work items are enabled, review their one-time assignment to the
lexicographically first planned source repository in each project.

### `agent run` — execute and validate an approved plan

```bash
# Isolated preview: local conversion/validation, no remote writes
ado2gh agent run -c migration.yaml --plan output/pev_plan.json --dry-run

# Live execution: approval must equal the plan's exact plan_id
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  -o output/pev_validation.csv

# Resume the same run after fixing a failure or merging workflow review PRs
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --resume run_REPLACE_WITH_EMITTED_ID \
  -o output/pev_validation.csv
```

The command exits nonzero for live `failed`/`needs_review` and preview
`dry_run_failed`/`dry_run_needs_review`. `dry_run_passed` is the only successful
preview result. Preview state is isolated and cannot be resumed as a live run.
Nested pipeline planning, conversion, and local validation still execute, so a
pipeline finding affects the preview result.

Before live work, the command registers plan-v7 capabilities for every exact
source/target/scope/input digest. It acquires target fencing tokens and writes a
durable remote-operation barrier before dispatching Git, LFS, or GEI. Exact
plan/run scope expectations and receipts—not legacy wave status—govern resume.
An uncertain remote call quarantines the target until ticketed reconciliation.

### `agent validate` — re-run plan-bound validation

```bash
ado2gh agent validate \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_EMITTED_ID \
  -o output/pev_validation.csv
```

Validation re-reads source organization membership, refs, pipeline
identities/revisions, and non-Git digests; it also verifies immutable target
identity/visibility and stable refs, explicit team roles, Actions enablement,
active workflows, selected Actions policy, required secret names, approved
environment-protection digests, required online runner labels, and external
checkout repository IDs/refs/resolved SHAs. `access_policy_approved: false`
yields `needs_review` and blocks later source cleanup.

### `agent status` — inspect durable receipts

```bash
ado2gh agent status
ado2gh agent status --run-id run_REPLACE_WITH_EMITTED_ID
```

State schema v13 includes exact scope expectations/receipts, plan write
capabilities, target-use history, crash-stop remote operations, and one-shot
destructive capability/action receipts.

### `agent release-quarantine` — release a reconciled target fence

Git, LFS, GEI, or repository-deletion calls with an uncertain outcome leave an
indefinite target quarantine. After independently reconciling the remote state,
release it with the exact plan/run and an external approval ticket:

```bash
ado2gh agent release-quarantine \
  --config migration.yaml \
  --plan migration_plan.json \
  --run-id run_REPLACE_WITH_EMITTED_ID \
  --target-org my-github-org \
  --target-repo project-repository \
  --approval-ticket CHG-12345
```

The release is recorded as validation evidence. It never retries or assumes
the uncertain remote operation succeeded.

---

## Phase 1: Discovery & Assessment

### `discover` — Scan ADO Org

```bash
# Basic scan
ado2gh discover -c migration.yaml

# Custom output path
ado2gh discover -c migration.yaml --output output/my_discovery
```

**What it does:** Enumerates ADO projects, repositories, and pipeline counts.
**Output:** An output directory containing `repos.csv`, `pipelines.csv`, and a
`repos_template.txt` cohort template.

---

### `pipelines inventory` — Deep Pipeline Scan

```bash
# Scan repositories selected in a reviewed manifest
ado2gh pipelines inventory -c migration.yaml -i repos.txt

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

**What it does:** Fetches pipeline definitions and stores normalized metadata,
source association/fingerprint, and inventory-run receipts in SQLite. Raw YAML
source is fetched just in time by conversion and is not retained in SQLite.
**Duration:** ~20 min per 1000 pipelines at --parallel 12

When neither `--input` nor `--projects` is supplied, the standalone command
uses repositories in configured waves. `agent plan` performs its own strict
full-organization inventory for every planned `pipelines` scope.

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
ado2gh service-connections -c migration.yaml -i repos.txt

# Custom output
ado2gh service-connections -c migration.yaml -i repos.txt -o output/svc_manifest.json
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
ado2gh phase assign -c migration.yaml -i repos.txt --output migration_phase.yaml

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

## Phase 3: Compatibility previews

### `phase run` — Preview a legacy phase

```bash
ado2gh phase run -p poc -c migration_phase.yaml --dry-run
ado2gh phase run -p pilot -c migration_phase.yaml --dry-run --force
ado2gh phase run -p wave1 -c migration_phase.yaml --dry-run --db custom.db
```

Without `--dry-run`, v6 exits before execution and directs the operator to
`agent plan` / `agent run`. A preview is assessment only and cannot authorize
or record a production migration.

---

### `run` — Preview legacy wave execution

```bash
ado2gh run -c migration_phase.yaml -w 1 --dry-run
ado2gh run -c migration_phase.yaml --dry-run
```

Live use is disabled. Create a PEV plan for the desired cohort instead.

### `push-workflows` — Preview legacy local workflow publishing

```bash
ado2gh push-workflows -c migration_phase.yaml --dry-run
```

Live use is disabled. `agent run` performs validated, idempotent review-PR
delivery and default-branch verification as part of the approved task graph.

---

### `phase gate-check` — Validate Phase Success

```bash
# Check gate
ado2gh phase gate-check -p poc -c migration_phase.yaml

# Override with reason (stored in DB for audit)
ado2gh phase gate-check -p poc --override --reason "2 repos excluded by design"
```

**What it does:** Checks compatibility repo + pipeline receipts against phase thresholds.
**Output:** PASS / FAIL / OVERRIDE with details

This assessment is not a substitute for PEV validation or plan approval.

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

**What it does:** Compatibility per-repo checks:
1. GitHub repo exists
2. Default branch matches
3. **HEAD commit SHA matches** (proves code transferred)
4. Every branch name and tip SHA matches exactly
5. Every tag name and ref SHA matches exactly
6. Expected workflows/content are present (if pipelines migrated)
7. Branch protection is applied (if policies migrated)

**Output:** CSV + JSON report

Use `agent validate` for the production plan-bound validator. This top-level
command compares the currently configured cohort and live source state.

---

## Phase 6: Post-Migration

### `ado-cleanup` — ADO Side Cleanup

```bash
# Dry run first. Plan/run/ticket are shown so the preview matches cutover.
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --dry-run

# Live default: disable matched YAML/classic-build/release definitions after
# confirmation.
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345

# Add archival as a separate explicit cutover action.
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --archive

# A redirect mutates the validated source and needs an additional acknowledgment.
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --add-redirect \
  --allow-source-mutation
```

**What it does:**
- Requires a completed run belonging to the exact approved plan
- Rechecks the plan-bound ADO/GitHub API origins and organization identities
- Rechecks the approved repository ID/name, default branch, all heads/tags, and
  full-ref digest before each source mutation, and records the approval ticket
- Disables ADO YAML/classic-build/release definitions only when each live
  identity, name, type, and `source_revision` equals its approved receipt; the
  disabled state and advanced revision must also pass immediate readback
- Optionally archives the ADO repository
- Adds a redirect only when explicitly requested; this returns the run to review

After confirmation, the command content-addresses the exact plan/run,
repositories, actions, target/source identities, approved pipeline receipt
digest, and redirect content into a one-shot cleanup capability. The cleanup
service atomically claims it, holds plan-authorized target fences, performs a
fresh target parity/access/runtime check—including selected Actions policy,
online runner labels, and external checkout ID/ref/SHA—immediately before
source destruction, and writes a durable before/after receipt around each mutation.
An action left in flight after a crash is not automatically retried.

Every requested action must already be enabled in the immutable plan's
`global.cleanup` policy. Disabling pipelines also requires the `pipelines`
scope. Archival additionally requires `archive_source: true` on each selected
repository. Changing those settings means creating and approving a new plan;
CLI flags cannot widen an existing approval.

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
# Legacy read-only preview. A wave is required only for this mode.
ado2gh rollback -c migration_phase.yaml -w 1 --dry-run

# Branch-policy inverse request. It currently fails closed because exact
# per-policy before/after fingerprints are not persisted.
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --scopes branch_policies

# Live full rollback: requests deletion of targets created by this exact run;
# policy may still refuse whole-repository deletion.
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345
```

For live rollback, the CLI derives the stable wave key and all targets from the
plan. If `--wave` is also supplied it must equal that derived value. The run
must be terminal and bound to the plan, and runtime API origins/organization
identities must match `policy.runtime_context`. Repository deletion additionally
requires a created-by-that-run ownership receipt and the same immutable GitHub
repository ID; adopted/pre-existing targets are blocked. Confirmation creates
a one-shot capability over a fresh snapshot of target identity, visibility,
default branch, complete refs, issue/pull-request and release identities, and
activity timestamps. The snapshot must remain exact when claimed and before
deletion, and a later plan/run target-use receipt blocks an older rollback.

Whole-repository deletion can still be disabled or refused because GitHub
cannot enumerate every external clone, integration, deployment, or dependency.
An ownership receipt is necessary, not sufficient; enterprise policy may
require a scope inverse, forward repair, or separately reviewed manual deletion.

Owned-repository deletion is the only current automated destructive inverse.
Branch-policy rollback fails closed until exact per-policy before/after
fingerprints are persisted; use reviewed manual remediation. `--scopes
pipelines` also fails deliberately; remove merged workflows through a reviewed
reverse pull request. Other unsupported scope inverses fail instead of
resetting local state.

---

### `pipelines retry-failed` — Preview legacy retry selection

```bash
ado2gh pipelines retry-failed -c migration_phase.yaml -w 1 --dry-run
```

Live use is disabled. Fix the underlying pipeline requirement and resume the
same plan-bound run with `agent run --resume RUN_ID`.

---

## Complete End-to-End Example

```bash
# ── Setup ────────────────────────────────────────────
export ADO_PAT="your-pat"
export ADO_ORG_URL="https://dev.azure.com/CONTOSO"
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"

# ── Immutable plan and preflight ─────────────────────
ado2gh agent plan -c migration.yaml -o output/pev_plan.json
ado2gh agent run -c migration.yaml --plan output/pev_plan.json --dry-run

# Review plan JSON and substitute its exact printed ID.
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REVIEWED_ID \
  -o output/pev_validation.csv

# Review/merge workflow PRs and satisfy external requirements, then resume.
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REVIEWED_ID \
  --resume run_EMITTED_ID \
  -o output/pev_validation.csv

# ── Completed-run cutover ────────────────────────────
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_EMITTED_ID \
  --approval-ticket CHG-12345 \
  --dry-run
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_EMITTED_ID \
  --approval-ticket CHG-12345
```
