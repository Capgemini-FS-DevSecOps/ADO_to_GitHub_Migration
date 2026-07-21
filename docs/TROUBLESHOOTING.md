# Troubleshooting ado2gh v6

Diagnose from the immutable plan, `agent status`, task errors, validation CSV,
and pipeline evidence together. Do not edit SQLite status rows or create a new
plan merely to clear a failed control.

## Connectivity and authentication

### `ADO_ORG_URL + ADO_PAT required`

```bash
export ADO_PAT="..."
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
```

Verify PAT expiry, organization access, IP allow-listing, and only the ADO
scopes used by the plan.

### GitHub 401/403

Set `GH_TOKEN`, a pool named `GH_TOKEN_1` through `GH_TOKEN_19`, or the complete
GitHub App variables. Check repository/organization permissions for the planned
scopes and SSO authorization. Use `ado2gh token-status -c migration.yaml` to
inspect the active pool.

### Endpoint rejected as unsafe

`ado_org_url`, `gh_api_url`, `gh_web_url`, and `OPENAI_BASE_URL` must be
absolute HTTPS URLs. ADO and GitHub URL fields also reject embedded
user/password data, query strings, and fragments. Plain HTTP is rejected even
for internal endpoints; configure TLS and a trusted certificate rather than
weakening validation.

On GHES, configure both `global.gh_api_url: https://HOST/api/v3` and
`global.gh_web_url: https://HOST`. A missing web URL or mismatched authority
blocks clone/redirect operations instead of deriving an unsafe URL.

## Plan and preview failures

### Plan integrity or configuration digest mismatch

The JSON or non-secret runtime configuration differs from the reviewed plan.
Restore the exact reviewed files. If the change was intentional—including a
mapping, action pin, manual approval manifest, cleanup permission, or delivery
change—create a new plan and obtain a new approval.

### Runtime authority mismatch

Plan schema v7 binds the canonical ADO and GitHub API origins and each service's
immutable organization identity. A different hostname, enterprise endpoint,
or tenant identity is not interchangeable even when organization and repository
names match. Use the reviewed endpoints and credentials, or create and approve
a new plan for an intentional authority change.

### Source repository/ref drift

Execution compares the immutable ADO repository ID, default branch, every
branch/tag name and SHA, default-branch HEAD, and full-ref digest with the plan.
Freeze ADO writes and investigate the change. Intentional drift requires a new
plan; do not retry against the old snapshot.

### Pipeline inventory/source drift

The plan contains normalized pipeline receipts and source/YAML digests.
Execution rescans them before conversion. A changed/deleted definition,
association, source file, or incomplete inventory blocks the pipeline scope.
Stabilize ADO and generate a new plan. Do not copy stale inventory rows.

### Non-Git source snapshot drift

`work_items`, `wiki`, `secrets`, and `branch_policies` are bound by a plan-time
content digest and aggregate counts. Execution fetches one payload, verifies
it, and transforms that same in-memory object. Freeze the relevant ADO source,
investigate the changed item set, and create a new plan for intentional drift;
there is no count-only or second-read bypass.

### Unlinked work items appear under only one repository

This is intentional when `include_unlinked_work_items: true`. ADO work items
without repository links are project-scoped; the planner assigns each once to
the case-insensitive lexicographically first planned source repository in that
project. Copying them into every target would multiply the project backlog.

### Target appeared after planning

The default `existing_target_policy: fail` prevents accidental adoption. Do not
delete an unknown repository just to continue. Establish ownership, then either
select a new target or explicitly approve reuse/nonempty-target policy in a new
plan.

For an approved existing target, a changed immutable repository ID, size,
visibility, default branch, branch/tag SHA, or full-ref digest is also target drift. Restore
the approved state or create a new plan; matching owner/repository text is not
ownership evidence.

### Dry-run status requires attention

`dry_run_passed` is the only successful preview. `dry_run_needs_review` means a
human/external gate remains; `dry_run_failed` means a required check failed.
Dry runs use isolated in-memory state, so none can be resumed as a live run.
They still run nested pipeline planning, conversion, and local validation;
inspect those receipts instead of assuming conversion was skipped.

## Execution and resume

### `Plan ... is already executing in another process`

One renewable cross-process lease protects a plan. Confirm the other process is
healthy and let it finish. If it crashed, stop it, wait for
`execution_lease_seconds` to expire, and resume the same `run_id` using the same
plan, approval, and database. Never work around the lease with a copied DB.

### Task is leased or exhausted attempts

An active worker owns the task, its lease has not expired, or the retry limit
was reached. Inspect `agent status`, process health, and the task error. Resume
after an expired crash lease; diagnose exhausted attempts instead of resetting
state manually.

### Target remains quarantined after a crash

Git, LFS, GEI, and deletion dispatch first write a durable remote-operation
barrier. If the process exits while it is `in_flight`, ordinary plan/task lease
expiry does not authorize another writer. Reconcile the exact target and
operation under a ticket, then use `agent release-quarantine`; do not delete the
row, copy the database, or retry by assumption.

### Interrupted run

```bash
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REVIEWED_ID \
  --resume run_EMITTED_ID \
  --db migration_state.db
```

Use the original plan and DB. A resume rechecks source and target state before
reusing completed receipts.

### Legacy live command is disabled

`run`, `phase run`, `pipelines retry-failed`, and `push-workflows` reject live
use in v6. Their `--dry-run` forms are compatibility assessments only. Create a
cohort plan or resume the PEV run; there is no live bypass flag.

## Git and LFS transfer

### `git clone --mirror failed`

Check ADO Code Read permission, disk capacity, TLS/proxy policy, and access to
the exact source. Tokens are passed through an ephemeral askpass helper, so do
not add a PAT to the clone URL while debugging.

### Git heads/tags push failed

Check GitHub Contents/Administration permission, target ownership/preflight,
branch rules, object limits, and network errors. Do not delete/recreate or
reuse a target unless the immutable plan explicitly governs that action.

### LFS failure

```bash
git lfs version
git lfs install
```

LFS detection or push failure is a required migration failure, not a warning.
Fix Git LFS, network, storage, or permissions and resume the same PEV run if the
source snapshot is unchanged. `skip_lfs` must be an explicit reviewed repository
decision in a new plan.

## Pipeline conversion and delivery

### Pipeline remains `needs_review`

Inspect `.ado2gh/pipeline-evidence/` for the exact finding. Common gates are an
open review PR, secrets/OIDC, environment protection, a self-hosted runner,
manual-only semantics, or missing provider configuration. Satisfy the named
gate; do not mark the DB completed.

### LLM ambiguity could not be resolved

Deterministic pipelines do not need an LLM. For eligible ambiguity, verify the
plan-bound `pipeline_conversion.llm_provider`, model, HTTPS base URL,
organization/project, approved API-key environment name, model access, timeout,
schema response, and confidence threshold. Legacy routing environment values
must exactly match the plan. A refusal, low-confidence result, validator
rejection, or successful proposal correctly remains a review gate: model output
is never inserted into workflow YAML.

### Manual approval rejected

Use the current evidence's exact pipeline identity, `ambiguity_id`, and
`source_fingerprint`. Generate canonical IDs with
`ManualApprovalRecord.create(...)` and `ManualApprovalManifest`; do not
hand-write them. Check ticket, approver, timestamp/timezone, compatible typed
target mapping, and SHA-256 evidence. After changing the manifest, create and
approve a new organization plan because the digest is part of `plan_id`.
The root must be approval manifest schema v2. To promote an LLM proposal,
approve the exact fragment through this same process; do not paste it into the
workflow or resume the old plan with changed content.

### Action is not pinned or not in the reviewed catalog

The production policy requires a full commit SHA. Prefer the built-in approved
catalog. For another action, review publisher and commit provenance, add a
40-character `pipeline_conversion.action_pins` entry, then create and approve a
new plan. Do not disable pinning to clear the finding.

### Workflow PR is open or workflow/evidence content is missing

This is `needs_review`, not success. Review and merge the agent-created PR
through normal branch protection, provision external requirements, then resume
the same run. The validator accepts only exact plan/run/wave/source-bound
workflow and evidence blob receipts. If the default branch advanced, it must be
a strict descendant of the approved source commit whose complete tree diff
contains exactly those artifacts. Do not manually push local output or use
legacy `push-workflows` live.

### External pipeline resource is still missing or drifted

Approval evidence is not live-state evidence. The validator reads Actions
enablement and selected-actions policy, expected workflow state,
repository-secret names, approved environment-protection digests, required
online runner labels, and external checkout repository IDs/refs/resolved SHAs
from GitHub. A repository-checkout approval must carry explicit ref,
token-secret name, and `checkout-access-canary` evidence. Provision or restore
the exact resource, then resume; secret values are never supplied to the agent.

## Validation failures

### Branch or tag mismatch

Validation compares exact names and commit SHAs, not counts. Determine whether
the source changed, transfer failed, or the target diverged. Source drift needs
a new plan; a retryable transfer failure may be repaired only through the
bounded PEV repair/resume path.

The configured pipeline staging branch and default branch may differ only by
the exact approved workflow/evidence overlay. Every other branch and every tag
must remain identical to source; unrelated or unverified tree changes fail.

### Branch protection mismatch

Confirm the planned policy is supported, the token has Administration access,
and the default branch exists. A phase gate override does not override PEV
validation.

### Access policy remains `needs_review`

Full-organization discovery cannot infer ADO ACL equivalence. Supply explicit
per-repository `team_mapping` entries of source team to `{github_team,
permission}`, obtain an access-owner review, and set
`access_policy_approved: true` in a newly approved plan. The validator reads
every declared GitHub role back; false approval or role drift also blocks ADO
cleanup.

## Rollback

### Live rollback asks for plan/run/ticket

Supply the exact plan, a terminal `run_id` belonging to it, and the external
incident/change ticket. The CLI derives targets and its stable wave key from
the plan; `--wave` is only required for a legacy `--dry-run` preview.

```bash
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --scopes branch_policies
```

### Repository deletion is blocked

Deletion requires a created-by-the-authorized-run ownership receipt and the
same live immutable GitHub repository ID. Its one-shot capability also binds
visibility, default branch, every ref, issue/pull-request and release
identities, and activity timestamps. Drift, a later plan/run target-use receipt,
adopted/pre-existing repos, renamed or recreated targets, incomplete receipts,
and mismatched provenance are intentionally blocked. Escalate for manual
incident handling; do not edit the receipt.

The request may also fail closed even when ownership and the snapshot match,
because GitHub cannot enumerate every external clone, integration, deployment,
or dependency. Use a safe scope inverse or forward repair, or follow a
separately reviewed manual deletion procedure.

### Scope rollback is blocked

An automated inverse requires an artifact-level creation receipt and a current
target fingerprint matching the exact artifact produced by the approved run.
Missing provenance or drift fails closed so a similarly named pre-existing or
operator-modified artifact is not deleted. Preserve evidence and use a reviewed
manual or forward-repair procedure.

### Pipeline rollback is unsupported

`--scopes pipelines` fails deliberately because resetting state cannot remove
merged workflows. Prepare a reviewed reverse PR and validate target behavior.
Other scopes without a safe inverse also fail closed.

## ADO cleanup

### Cleanup action is outside the approved plan

CLI flags cannot widen approval. The immutable plan must enable the action in
`global.cleanup`; pipeline disabling also requires the `pipelines` scope, and
archival requires `archive_source: true` for every selected repository. Create
and approve a new plan if governance authorizes a changed cleanup policy.

### Redirect requires source-mutation acknowledgment

The plan must contain `cleanup.allow_redirect: true`, and the command requires
both `--add-redirect` and `--allow-source-mutation`. A successful redirect
changes the source HEAD and returns the run to `needs_review`; preserve and
revalidate the new evidence.

### Cleanup detects source drift

Cleanup requires a completed plan-bound run and, before every mutation,
rechecks the immutable ADO repository ID/name, default branch, every branch/tag
SHA, and full-ref digest. Re-establish the write freeze and investigate the
drift. Never suppress the check.

### Cleanup detects pipeline receipt drift

Pipeline disabling requires exact equality of each approved YAML/classic/release
identity, name, type, and `source_revision`. The disabled state and advanced
revision must pass immediate readback. Missing or unexpected definitions,
definition-read failures, stale revisions, or ambiguous update outcomes fail
closed. Re-inventory and obtain a new plan for an intentional change; do not
disable only the subset that happened to match.

### Cleanup/rollback capability is already claimed or has an in-flight action

Destructive requests are one-shot and content-addressed. Each action writes a
before receipt before dispatch and an after receipt only for a known outcome.
Do not issue a changed request or delete state to retry an `in_progress` action;
reconcile the external system and preserve the capability/action evidence.

### Cleanup reports pipeline runtime dependency drift

Cleanup holds the target fence and rechecks the selected Actions policy against
workflow `uses`, required labels on online runners, external checkout immutable
ID/ref/SHA, repository-secret names, and environment configuration. Restore the
exact approved runtime dependency or approve a new plan; do not destroy ADO
while the GitHub runtime cannot be proven usable.

## GEI and rate limits

For GEI, verify `gh extension install github/gh-gei`, destination support, and
the required blob-storage configuration. Server-side GEI state remains subject
to the same plan mapping and validator.

When all GitHub tokens are limited, add approved token identities, use GitHub
App authentication, or reduce `parallel`/`pipeline_parallel`. ADO HTTP 429
responses use bounded backoff; persistent limits require lower concurrency or a
different window. Do not increase parallelism until API, Git, disk, and SQLite
capacity have been measured.
