# Migration runbook — approved PEV execution

This runbook is the production procedure for an ADO organization migration.
Use separate identities and change tickets for assessment, migration, and ADO
cleanup where your organization requires separation of duties.

## 1. Establish governance and prerequisites

Before connecting the tool:

- Name the migration owner, plan approver, pipeline reviewers, security reviewer,
  and cutover approver.
- Define the GitHub organization/project naming policy and target ownership.
- Decide whether repository history requires `mirror` or GEI.
- Inventory self-hosted runners, environments, approvals, variable groups,
  service connections, secret owners, and compliance restrictions.
- Confirm the LLM provider/data-processing policy. Deterministic-only operation
  is supported, but LLM-eligible pipeline ambiguity will stop for review.
- Choose durable local paths for the SQLite database and consistent DB/WAL
  backups, plus an independently controlled WORM/signing destination.

Install and verify:

```bash
python -m pip install -e .
ado2gh --version
git --version
git lfs version
```

Set credentials in the execution environment, not in YAML:

```bash
export ADO_PAT="..."
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN_1="..."
export GH_TOKEN_2="..."

# Optional proposal-provider credential after its non-secret identity is
# approved in migration.yaml
export OPENAI_API_KEY="..."
```

Use the least permissions needed for the planned scopes. Do not grant ADO write
permissions until an approved cleanup window.
All configured ADO, GitHub API/web, and optional LLM base endpoints must be
absolute HTTPS URLs. ADO and GitHub URL fields also reject embedded
credentials, queries, and fragments.
For GHES, set both `global.gh_api_url: https://HOST/api/v3` and
`global.gh_web_url: https://HOST`; their authorities must agree.

## 2. Configure deterministic mapping

Copy and review [`../migration.yaml`](../migration.yaml). Keep these defaults
unless an approved exception exists:

```yaml
global:
  # If enabled, assign each unlinked project work item once to the
  # lexicographically first planned source repository in its project.
  include_unlinked_work_items: false
  mapping:
    strategy: project-prefix
    existing_target_policy: fail
    allow_nonempty_target: false
    preflight_targets: true
    allowed_target_visibilities: [private, internal]
  pipeline_conversion:
    llm_provider: disabled
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_api_key_env: OPENAI_API_KEY
  pipeline_delivery:
    mode: pull_request
  execution_lease_seconds: 300
  validation:
    max_repair_attempts: 1
  cleanup:
    allow_disable_pipelines: true
    allow_redirect: false
    allow_archive: false
```

Add explicit overrides for intentional renames. Never resolve a target collision
by allowing the later source to overwrite the earlier target. The planner
compares names case-insensitively and must produce one source per target.

ADO ACL equivalence is never inferred. Each explicit repository policy uses:

```yaml
team_mapping:
  "ADO Contributors":
    github_team: service-contributors
    permission: push
access_policy_approved: true
```

Roles are limited to `pull`, `triage`, `push`, `maintain`, or `admin`.
`access_policy_approved` defaults to false; false is a review gate and blocks
cleanup. Full-organization discovery therefore needs an explicit access-owner
attestation rather than a guessed default role.

## 3. Perform optional assessment

Assessment commands do not authorize migration:

```bash
ado2gh discover --config migration.yaml
ado2gh pipelines inventory --config migration.yaml --parallel 16
ado2gh pipeline-readiness \
  --config migration.yaml \
  --output output/pipeline_readiness.csv
ado2gh service-connections \
  --config migration.yaml \
  --output output/service_connection_manifest.json
ado2gh token-status --config migration.yaml
```

Review classic/release pipelines, unknown tasks, agent pools, environments,
approvals, variable groups, service connections, and repositories with LFS or
large histories. Complete runner and OIDC design before the cutover wave.

## 4. Run a canary with the same controls

Create a small manifest using representative repositories:

```text
# canary-repos.txt
ProjectA/simple-service
ProjectA/service-with-yaml-pipeline
ProjectB/service-with-classic-release
```

Create its immutable plan:

```bash
ado2gh agent plan \
  --config migration.yaml \
  --input canary-repos.txt \
  --output output/canary_plan.json
```

Review at minimum:

- immutable plan schema v7, SQLite state schema v13, and manual-approval
  manifest schema v2 compatibility;
- canonical ADO/GitHub API origins and immutable organization identities;
- source organization and immutable repository IDs;
- the complete eligible organization count/digest, or the explicit-subset label;
- default branches, every branch/tag name and SHA, full-ref digests, and HEADs;
- pipeline inventory/source receipts and digests for every planned pipeline
  scope;
- every source/target pair and every scope;
- target-existence preflight results and, for approved reuse, the immutable
  target ID, size, visibility, default branch, complete refs, and ref digest;
- digest/count snapshots for each planned `work_items`, `wiki`, `secrets`, or
  `branch_policies` source;
- the stable single-repository assignment for unlinked project work items if
  that policy is enabled;
- task dependencies and pipeline inventory tasks;
- mapping, conversion, delivery, and validation policy;
- every explicit team role and `access_policy_approved` attestation;
- absence of credentials and secret values.

Record the exact printed `plan_id` in the change record. Then run preflight:

```bash
ado2gh agent run \
  --config migration.yaml \
  --plan output/canary_plan.json \
  --dry-run
```

Record the preview result precisely: `dry_run_passed`,
`dry_run_needs_review`, or `dry_run_failed`. Preview state is isolated from the
live database and cannot be resumed or treated as production completion. The
preview runs nested pipeline planning, conversion, and local validation while
suppressing remote writes, so conversion failures and review gates must be
resolved rather than dismissed as preview limitations.

## 5. Execute the approved canary plan

```bash
ado2gh agent run \
  --config migration.yaml \
  --plan output/canary_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --db migration_state.db \
  --output output/canary_validation.csv
```

Record the emitted `run_id`. A nonzero exit may represent either `failed` or an
expected `needs_review` gate. Inspect receipts before deciding:

```bash
ado2gh agent status --run-id run_REPLACE_WITH_EMITTED_ID --db migration_state.db
```

Do not generate a new plan merely to clear a failure. Source/configuration drift
requires a new review; an external requirement should be satisfied and the same
run resumed.

Only one process may execute a plan at a time. The executor renews a
cross-process plan lease and atomically leases each task. If another executor
reports that the plan/task is leased, do not start it against a copied
database. Confirm the first process is stopped, wait for the configured lease
to expire, and resume the same run against the original durable database.

Ordinary lease expiry does not clear an uncertain target mutation. Every target
write is limited by the immutable plan capability manifest and a fencing token;
Git/LFS/GEI dispatch first commits a durable remote-operation barrier. If it is
still `in_flight` after a crash, reconcile the target under a ticket and use
`agent release-quarantine`. Exact plan/run/source/target/scope/input receipts,
not legacy wave rows, govern resume.

## 6. Review pipeline pull requests

For every generated workflow PR:

- Compare triggers, stages/jobs, dependencies, conditions, failure behavior,
  artifacts, caches, deployments, and schedules with the ADO definition.
- Verify least-privilege `permissions`, runner labels, environment protections,
  concurrency, timeouts, and third-party action provenance.
- Provision secrets through approved tooling. Prefer OIDC/federation over static
  cloud credentials. Never paste values into the workflow or evidence file.
- Inspect `.ado2gh/pipeline-evidence/` and resolve every manual-review finding.
- Test in a non-production environment where feasible.
- Merge through normal branch protection; the agent never merges for you.

For an ambiguity classified as manual-only, use the evidence artifact's exact
`ambiguity_id` and `source_fingerprint` to build a typed record with
`ManualApprovalRecord.create(...)`, serialize it through
`ManualApprovalManifest`, and reference that JSON/YAML file from
`global.pipeline_conversion.manual_approval_manifest`. The record must include
the pipeline identity, approver, ticket, timestamp, target mapping, and SHA-256
evidence. Because the normalized manifest is part of `plan_id`, create and
approve a new plan after adding it. Never hand-write a digest, reuse an old
fingerprint, or use a boolean approval.

LLM responses use the same boundary. Even a high-confidence, locally valid
response is proposal evidence only and is never inserted into workflow YAML.
To promote it, approve that exact fragment in a schema-v2 manifest, create a
new organization plan, and approve the new `plan_id`.

Review every third-party action against the built-in approved, commit-SHA-pinned
catalog. Treat changes under `pipeline_conversion.action_pins` as dependency
review events; the override becomes part of the new plan identity.

After merging and satisfying external requirements, resume the same run. This
step verifies the exact plan/run/wave-bound workflow and evidence blob SHAs on
the default branch, then proves that any branch advance is a strict descendant
whose complete tree diff contains only those approved artifacts:

Validation also reads back Actions enablement, active workflow state, required
repository-secret names, selected Actions policy, required online runner
labels, approved environment-protection digests, exact external checkout
IDs/refs/SHAs, and every explicit GitHub team role. A repository-checkout
approval requires explicit ref, token-secret name, and
`checkout-access-canary` evidence. Secret values are never read. Approval
evidence without matching live external state cannot complete a pipeline.

```bash
ado2gh agent run \
  --config migration.yaml \
  --plan output/canary_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --resume run_REPLACE_WITH_EMITTED_ID \
  --db migration_state.db \
  --output output/canary_validation.csv
```

The canary is successful only when the run is `completed` and application owners
confirm repository access, CI behavior, deployments, protections, and developer
workflow.

## 7. Plan the full organization or reviewed cohort

Omit `--input` to discover the entire eligible ADO organization:

```bash
ado2gh agent plan \
  --config migration.yaml \
  --output output/organization_plan.json
```

For staged cohorts, use separate reviewed input manifests and plans. Risk-based
phase assignment can help select cohorts, but each live cohort still receives
an immutable PEV plan and exact approval.

Compare the plan repository count with independent ADO inventory. Investigate
disabled repositories, empty repositories, deleted projects, fork policy, and
any source that did not map. Approve and execute exactly as for the canary.

## 8. Monitor, resume, and validate

```bash
ado2gh agent status --db migration_state.db

ado2gh agent validate \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --run-id run_REPLACE_WITH_ID \
  --db migration_state.db \
  --output output/organization_validation.csv
```

On interruption, use the original plan and run ID:

```bash
ado2gh agent run \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --resume run_REPLACE_WITH_ID \
  --db migration_state.db \
  --output output/organization_validation.csv
```

Response by state:

| State | Operator response |
|---|---|
| `completed` | Preserve evidence and proceed to approved cutover |
| `needs_review` | Complete the named human/external gate, then resume |
| `failed` | Correct the source, policy, target, or transient failure; resume only when the approved plan is still valid |
| source/config drift | Stop, generate a new plan, and obtain a new approval |

The preview-only states are `dry_run_passed`, `dry_run_needs_review`, and
`dry_run_failed`; none is a live completion receipt.

The orchestrator performs only bounded deterministic repairs. Repeated failures
require diagnosis; do not edit SQLite status rows or suppress evidence.

## 9. Cut over and clean up ADO

Before cleanup:

- the plan-bound run is `completed`;
- the active ADO/GitHub API origins and immutable organization identities still
  match `policy.runtime_context` in the approved plan;
- workflow PRs are merged and verified on default branches;
- branch/tag/HEAD validation passes;
- secret/OIDC, environment, runner, access, and support ownership are complete;
- every selected repository has `access_policy_approved: true` and live team
  roles match the plan;
- repository owners accepted the cutover;
- the ADO write freeze and rollback window are active;
- the change ticket names the exact plan and run IDs.
- each requested mutation is enabled under `global.cleanup` in that immutable
  plan; disabling pipelines is within scope, and archival is also approved per
  repository with `archive_source: true`.

Preview:

```bash
ado2gh ado-cleanup \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --db migration_state.db \
  --dry-run
```

Live default (disable matched ADO YAML/classic-build/release definitions):

```bash
ado2gh ado-cleanup \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --db migration_state.db
```

Add `--archive` only when both the plan cleanup policy and each selected
repository authorize archival. A redirect commit is not part of the default
because it changes the validated source HEAD. It requires
`cleanup.allow_redirect: true` in the approved plan plus both
`--add-redirect` and `--allow-source-mutation`; then revalidate and retain the
new evidence.

Before each requested mutation, cleanup reconciles the immutable ADO repository
ID/name, default branch, all branch/tag SHAs, and full-ref digest. Pipeline
disable additionally requires each live YAML/classic/release identity, name,
type, and `source_revision` to match its approved receipt. Disabled state and
the advanced revision are read back after update. Missing, unexpected,
unreadable, or stale receipts block the action. Cleanup also asks for
interactive confirmation. Any drift or action failure blocks completion.

The confirmed request becomes a one-shot content-addressed cleanup capability
bound to the exact plan/run, repository/action set, source/target identities,
pipeline receipt digest, and redirect content. Cleanup atomically claims it,
holds target fences, performs a fresh parity/access/external-resource check,
including selected Actions policy, online runners, and external checkout
identity/ref/SHA, and writes a durable before/after receipt around every remote mutation. Treat
an in-flight receipt after a crash as a reconciliation event; do not retry by
assumption.

## 10. Retain the audit set

Store these together according to retention policy:

- approved configuration and input manifest;
- immutable plan JSON and recorded `plan_id` approval;
- SQLite state database and schema version;
- validation CSV/JSON and pipeline evidence artifacts;
- workflow PR URLs, reviews, and merge SHAs;
- LLM decision digests/provider/model metadata (not prompts containing source);
- change ticket, cleanup receipt, exceptions, and owner sign-off.

Export this set and its digest to an independently controlled WORM system. If
non-repudiation is required, sign it with an enterprise-managed key and retain
an independent timestamp/attestation. SQLite is a local transactional store,
not protection against an administrator who can rewrite the DB, WAL, plan, and
local evidence together.

Protect this set as migration evidence. It contains repository names and
operational metadata even though credentials and raw pipeline source are
deliberately excluded.

## Rollback and incident handling

Stop new work and disable the relevant target automation before attempting a
rollback. Prefer a forward repair when history has already diverged or
developers have started work on GitHub.

Live rollback requires the immutable plan, matching source/target API and
organization authority, a terminal run bound to it, an external ticket, and
interactive confirmation. The target set and stable wave key are derived from
the plan:

```bash
# Branch-policy inverse request. This currently fails closed because exact
# per-policy before/after fingerprints are not persisted.
ado2gh rollback \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --scopes branch_policies \
  --db migration_state.db

# No --scopes requests deletion; policy may refuse when downstream use cannot
# be enumerated.
ado2gh rollback \
  --config migration.yaml \
  --plan output/organization_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --db migration_state.db
```

Repository deletion requires a created-by-run ownership receipt and a matching
live immutable GitHub repository ID. Its one-shot capability also binds a fresh
snapshot of visibility, default branch, every ref, issue/pull-request and
release identities, and activity timestamps. The snapshot must remain exact,
and any later plan/run target-use receipt blocks an older rollback.
Adopted/pre-existing targets are blocked.
Because GitHub cannot enumerate every external clone, integration, deployment,
or dependency, enterprise policy may disable automatic whole-repository
deletion even when those checks pass. Prefer a safe scope inverse or forward
repair; use separately reviewed manual deletion when downstream-use assurance
cannot be automated.
Scope rollback requires artifact-level creation provenance and the exact
current fingerprint recorded by the run. Those per-policy fingerprints are not
currently persisted, so automated branch-policy rollback fails closed and
requires reviewed manual remediation.
Pipeline rollback is a reviewed reverse PR; `--scopes pipelines` deliberately
fails because a state reset cannot remove merged workflows. Unsupported scope
inverses also fail closed.

The legacy `run`, `phase run`, `pipelines retry-failed`, and `push-workflows`
commands are available only with `--dry-run`; v6 rejects live use. They are not
an incident bypass around the approved PEV run.

If a credential may have entered source, logs, a workflow, or provider input,
rotate it immediately, remove the artifact through the appropriate incident
process, preserve forensic evidence, and do not rely on redaction after the
fact.
