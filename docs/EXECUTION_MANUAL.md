# ADO2GH v6 execution manual

This manual covers the production Planner–Executor–Validator (PEV) control
plane. Live migration writes must use `ado2gh agent plan` followed by
`ado2gh agent run --approve-plan`. The old wave, phase, retry, and workflow-push
paths are retained only as `--dry-run` compatibility previews.

## 1. Prerequisites and credentials

Install Python 3.9+, Git 2.30+, Git LFS where needed, and `gh` plus `gh-gei`
only when using GEI:

```bash
python -m pip install -e .
ado2gh --version
git --version
git lfs version
```

Inject credentials from a vault or protected environment:

```bash
export ADO_PAT="..."
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="..."

# Optional rate-limit-aware pool
export GH_TOKEN_1="..."
export GH_TOKEN_2="..."

# Optional LLM proposal provider credential, after non-secret routing is
# approved in migration.yaml
export OPENAI_API_KEY="..."
```

Do not place credentials in configuration, manifests, URLs, command arguments,
logs, or evidence. `ado_org_url`, `gh_api_url`, `gh_web_url`, and
`OPENAI_BASE_URL` accept only absolute HTTPS URLs. ADO and GitHub URL fields
also reject embedded credentials, queries, and fragments.
For GHES, configure both `global.gh_api_url: https://HOST/api/v3` and
`global.gh_web_url: https://HOST`; Git/redirect links use the latter and the
two authorities must agree.

Grant the least ADO and GitHub permissions required by the approved scopes.
Keep ADO write permissions out of the migration identity until the separately
approved cleanup window. Grant GitHub repository deletion only to the identity
used by an approved rollback.

## 2. Configure the control policy

Start from [`../migration.yaml`](../migration.yaml). The essential production
controls are:

```yaml
global:
  # Unlinked project work items, if enabled, are assigned once to the
  # lexicographically first planned source repo in that project.
  include_unlinked_work_items: false
  mapping:
    strategy: project-prefix
    existing_target_policy: fail
    allow_nonempty_target: false
    preflight_targets: true
    allowed_target_visibilities: [private, internal]

  execution_lease_seconds: 300

  pipeline_conversion:
    llm_provider: disabled
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_organization: ""
    llm_project: ""
    llm_api_key_env: OPENAI_API_KEY
    require_permissions: true
    require_pinned_action_sha: true
    forbid_remote_script_execution: true
    # manual_approval_manifest: pipeline-manual-approvals.yaml
    # action_pins: {}

  pipeline_delivery:
    mode: pull_request
    branch: ado2gh/migrated-workflows

  validation:
    max_repair_attempts: 1

  cleanup:
    allow_disable_pipelines: true
    allow_redirect: false
    allow_archive: false
```

Mapping, pipeline approvals, action pins, cleanup permissions, and all other
non-secret policy become part of the immutable plan identity. A policy change
requires a new plan and approval; a CLI flag cannot widen an existing plan.

Non-secret LLM routing is config-owned and plan-bound. Environment routing
variables may repeat it but cannot opt in or change it after approval; only the
key named by `llm_api_key_env` is read from the environment.

ADO ACL equivalence is never inferred. Each explicit repository access policy
uses this shape:

```yaml
team_mapping:
  "ADO Contributors":
    github_team: service-contributors
    permission: push  # pull | triage | push | maintain | admin
access_policy_approved: true
```

`access_policy_approved` defaults to false. Set it true only after an authorized
access owner reviews the complete mapping; false makes validation
`needs_review` and prohibits ADO cleanup. Full-organization discovery therefore
requires an explicit access-policy review rather than guessing from ADO ACLs.

The built-in reviewed action catalog is documented in
[Pipeline transformation guide](PIPELINE_TRANSFORMATION_GUIDE.md#approved-action-catalog).
Only add or override `action_pins` after dependency review. Keys are
`owner/repository` or `owner/repository@major`; values are full 40-character
commit SHAs.

## 3. Select a cohort

Omit `--input` to plan every eligible repository in the organization. A text
manifest can select a reviewed cohort:

```text
# ADO project/repository
Payments/checkout
Identity/login::target-org/identity-login
```

CSV input supports `ado_project`, `ado_repo`, optional `gh_org`, `gh_repo`,
`scopes` separated by `|`, and `pipeline_filter`. Explicit targets still pass
case-insensitive collision and naming checks.

Risk assignment and assessment may help define cohorts, but they do not
authorize writes:

```bash
ado2gh discover -c migration.yaml
ado2gh pipelines inventory -c migration.yaml --parallel 16
ado2gh pipeline-readiness -c migration.yaml -o output/readiness.csv
ado2gh service-connections -c migration.yaml -o output/service-connections.json
ado2gh phase assign -c migration.yaml -i repos.txt --output migration_phase.yaml
```

## 4. Create and approve an immutable plan

```bash
# Full organization
ado2gh agent plan \
  -c migration.yaml \
  -o output/pev_plan.json

# Or one reviewed cohort
ado2gh agent plan \
  -c migration.yaml \
  -i repos.txt \
  -o output/cohort_plan.json
```

Before recording the printed `plan_id`, review:

- plan schema v7, state schema v13, approval-manifest schema v2, and the
  operator/runtime compatibility statement;
- canonical ADO/GitHub API origins and immutable organization identities;
- every immutable ADO repository ID and source/target mapping;
- the full eligible source inventory count/digest, or the explicit-subset label;
- the default branch plus every source branch/tag name and commit SHA;
- the canonical full-ref digest and default-branch HEAD;
- for every approved existing target, its immutable repository ID, size,
  visibility, default branch, complete branch/tag maps, and full-ref digest;
- pipeline inventory receipts, source fingerprints/YAML digests, and inventory
  digest for each repository with the `pipelines` scope;
- digest/count snapshots for `work_items`, `wiki`, `secrets`, and
  `branch_policies`; payload content is deliberately absent from the plan;
- the stable one-repository owner for project-level unlinked work items when
  `include_unlinked_work_items` is enabled;
- existing-target decisions, scopes, task dependencies, and cleanup policy;
- every explicit source-team to GitHub-team role and the separate
  `access_policy_approved` attestation;
- manual approval manifest digest, action catalog overrides, delivery policy,
  and the absence of credentials.

The planner reads repository refs twice and fails on an unstable snapshot.
Execution reads them again and blocks on any branch, tag, default-branch, ID, or
pipeline-source drift. Do not edit plan JSON; its integrity is recomputed when
loaded.

## 5. Run an isolated preview

```bash
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --dry-run
```

The preview performs API preflights, strict inventory reconciliation,
deterministic scope execution, and nested pipeline planning, conversion, and
local validation using an isolated in-memory state database. It suppresses
remote writes and does not write a resumable run to the live state database.

| Preview status | Meaning |
|---|---|
| `dry_run_passed` | No required blocker was found |
| `dry_run_needs_review` | A human or external gate remains |
| `dry_run_failed` | A required preflight, inventory, or scope assessment failed |

Only `dry_run_passed` exits successfully. It is evidence for review, not
authorization and not proof of live completion. Local pipeline conversion runs
in preview, but pull-request delivery, human review, and final remote target
validation remain live gates.

## 6. Execute and resume the approved plan

```bash
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --db migration_state.db \
  -o output/pev_validation.csv
```

Record the emitted `run_id`. Live execution verifies that the plan ID,
configuration digest, both API origins and immutable organization identities,
source snapshots, and target state still match.

One renewable cross-process lease protects the whole plan. Atomic leases also
protect individual task claims and prevent duplicate work. A second process
attempting the same plan is rejected. The executor heartbeats the plan lease;
losing it blocks validation. After a process crash, wait for the configured
lease to expire, then resume the same run:

```bash
ado2gh agent run \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --resume run_REPLACE_WITH_EMITTED_ID \
  --db migration_state.db \
  -o output/pev_validation.csv
```

Do not launch independent executors with different copies of the state
database. Store SQLite on reliable local durable storage and preserve its WAL
files during backup.

The executor also registers an immutable write capability for each exact
plan/source/target/scope/input digest. A target write requires a current fencing
token. Scope resume requires its exact plan/run expectation and receipt; legacy
wave rows never authorize PEV work. Before Git, LFS, or GEI dispatch, a durable
remote-operation barrier is committed. If the process dies after dispatch, that
barrier blocks every later target lease until ticketed reconciliation; waiting
for the ordinary lease timeout is insufficient.

## 7. Resolve pipeline review gates

Known pipeline semantics are transformed deterministically. An LLM is invoked
only for eligible ambiguity, and its checked output remains proposal evidence:
it is never passed to the workflow transformer. To use an exact proposed step
or condition, an authorized reviewer must add it to a schema-v2
content-addressed approval manifest and obtain approval for a new organization
plan. Secrets, OIDC, environment protection, self-hosted runners, and business
decisions remain external gates.

Production-ready workflows and redacted evidence are sent to a review branch.
The agent creates or reuses a pull request and never merges it. An open PR is
`needs_review`; merge through normal protection, provision external resources,
then resume the same run. Completion requires the exact plan/run/wave-bound
workflow and evidence blob SHAs on the target default branch. Repository
validation permits that branch to differ from source only as a strict
descendant whose complete recursive tree diff contains exactly those approved
artifacts; all other branches and tags remain exact.

For manual-only ambiguity:

1. Run conversion and inspect `.ado2gh/pipeline-evidence/` for the exact
   `ambiguity_id` and `source_fingerprint`.
2. Have an authorized reviewer decide a typed target mapping and retain
   independently digestible evidence.
3. Create a canonical record with
   `ManualApprovalRecord.create(...)`, then serialize
   `ManualApprovalManifest([record]).to_dict()` to JSON/YAML. Do not invent the
   `approval_id` or manifest digest.
4. Reference that file with
   `global.pipeline_conversion.manual_approval_manifest`.
5. Create and approve a new organization plan, because the normalized manifest
   is part of `plan_id`.

Each approval is bound to one project, repository, pipeline ID/type, source
fingerprint, and ambiguity. It includes approver, ticket, time, target mapping,
and SHA-256 evidence. Stale, duplicated, mismatched, tampered, or boolean
approvals fail closed. See
[the manifest schema and workflow](PIPELINE_TRANSFORMATION_GUIDE.md#content-addressed-manual-approvals).

For external configuration, a record is only authorization evidence. Final
validation reads repository secret names from GitHub, compares the live
policy-bearing environment response with the approved configuration digest,
verifies Actions is enabled, checks selected/allowed-actions policy against
every workflow action, requires approved runner labels on an online runner, and
requires every expected workflow to be active. Repository-checkout approval
requires an explicit ref, token-secret name, and `checkout-access-canary`
evidence; execution and validation bind the external repository's immutable ID
and resolved SHA. Secret values are never read. Missing or drifted resources
keep the run incomplete.

## 8. Inspect status and validation evidence

```bash
ado2gh agent status --db migration_state.db
ado2gh agent status --run-id run_REPLACE_WITH_ID --db migration_state.db

ado2gh agent validate \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_ID \
  --db migration_state.db \
  -o output/pev_validation.csv
```

| Live status | Operator action |
|---|---|
| `completed` | Preserve evidence and proceed to approved cutover |
| `needs_review` | Complete the named human/external gate and resume |
| `failed` | Correct the cause; resume only if the approved source and policy are unchanged |

The orchestrator may replay only explicitly retryable deterministic checks and
only up to `validation.max_repair_attempts`. Source/config drift requires a new
plan; never edit SQLite status rows or generate a replacement plan merely to
bypass evidence.

Validation also re-enumerates the eligible organization before and after a
full-organization check, re-fetches source refs and exact pipeline revisions,
and rechecks non-Git source digests. It verifies stable target
identity/visibility/refs and reads back every explicit GitHub team role.

## 9. ADO cleanup

Cleanup is a separate source-side cutover. Live cleanup requires a completed
run bound to the plan, an external approval ticket, an interactive
confirmation, matching `policy.runtime_context` API/organization authority,
and immutable-plan authorization for every requested action.

```bash
# Preview
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --db migration_state.db \
  --dry-run

# Live default: disable pipelines when approved in the plan
ado2gh ado-cleanup \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --db migration_state.db
```

Pipeline disabling requires both `cleanup.allow_disable_pipelines: true` and
the `pipelines` scope. `--archive` additionally requires
`cleanup.allow_archive: true` and `archive_source: true` per repository.
`--add-redirect --allow-source-mutation` requires
`cleanup.allow_redirect: true`; it changes the validated source HEAD and moves
the run back to `needs_review`. Before every mutation, cleanup rechecks the ADO
repository ID/name, default branch, every branch/tag SHA, and full-ref digest.
Pipeline disabling also requires exact equality between each approved and live
YAML/classic/release identity, name, type, and `source_revision`. The update is
complete only after disabled-state and advanced-revision readback; missing,
unexpected, unreadable, or stale receipts fail closed.

After confirmation, the CLI authorizes one content-addressed capability over
the exact plan/run, repositories, actions, source/target identities, pipeline
receipt digest, and redirect content. The cleanup service atomically claims the
capability, holds plan-authorized target fences, revalidates target parity,
access, workflows, selected Actions policy, online runner labels, exact external
checkout ID/ref/SHA, secrets, and environments immediately before source
destruction, and records a durable before/after receipt around every mutation.
An in-flight action after a crash is a reconciliation event, not permission to
retry.

## 10. Rollback and incident response

Prefer forward repair once developers have started using GitHub. Before live
rollback, stop target automation, freeze writes, preserve evidence, and obtain
an incident/change ticket.

Live rollback rechecks the plan's canonical source/target API origins and
organization identities before it evaluates target ownership.

```bash
# Request branch-policy rollback. It currently fails closed because exact
# per-policy before/after fingerprints are not persisted.
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --scopes branch_policies \
  --db migration_state.db

# Omitting --scopes requests deletion; policy may fail closed when downstream
# use cannot be enumerated.
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --db migration_state.db
```

The CLI derives the stable wave key and exact target set from the approved
plan. The run must be terminal and belong to that plan. Deletion requires a
created-by-that-run ownership receipt and a live immutable GitHub repository ID
matching the receipt. The one-shot rollback capability binds a fresh snapshot
of visibility, default branch, every ref, issue/pull-request and release
identities, and activity timestamps. It must remain exact at claim/deletion;
any later plan/run target-use receipt blocks the older rollback. Adopted or
pre-existing repositories are never deleted.
Even those checks cannot enumerate every downstream clone, integration,
deployment, or dependency. Enterprise policy may disable automatic
whole-repository deletion; in that posture the request fails closed and the
operator uses a safe scope inverse, forward repair, or separately reviewed
manual deletion. An ownership receipt alone is never sufficient proof of no
downstream use.
Scope rollback additionally requires artifact-level provenance and an exact
current-state fingerprint. The current state schema does not persist those
per-policy fingerprints, so automated branch-policy rollback is deliberately
refused and requires reviewed manual remediation.

Pipeline rollback is intentionally not automated: local state changes cannot
remove merged workflows. Use a reviewed reverse pull request. Unsupported
scope inverses fail instead of reporting false rollback success. A legacy
read-only preview remains available only with an explicit wave:

```bash
ado2gh rollback -c migration_phase.yaml --wave 1 --dry-run
```

## 11. Disabled legacy live commands

| Compatibility command | v6 behavior |
|---|---|
| `ado2gh run ... --dry-run` | Wave preview only |
| `ado2gh phase run ... --dry-run` | Phase preview only |
| `ado2gh pipelines retry-failed ... --dry-run` | Retry-selection preview only |
| `ado2gh push-workflows ... --dry-run` | Local workflow publish preview only |

Omitting `--dry-run` from any command in this table is rejected before the
legacy executor can mutate a target. Use an immutable PEV plan and resume the
same PEV run instead.

## 12. Retain the audit set

Retain the reviewed configuration/input, immutable plan and approval record,
SQLite state database (schema v13), validation outputs, pipeline evidence,
workflow PR reviews and merge SHAs, LLM decision metadata/digests,
cleanup/rollback tickets and
receipts, and owner sign-off. Protect the database, plan, reports, and evidence
as one coordinated audit set.

SQLite schema v13 is a local transactional trust boundary, not a WORM or
cryptographic non-repudiation system. A host/database administrator can rewrite
the database, WAL, plan, and local evidence together. Back up the database and
WAL consistently, export the complete audit set plus its digest to an
independently controlled WORM store, and—for regulated evidence—sign the export
with an enterprise key and retain an independent timestamp/attestation. Local
content hashes and receipts do not replace external signing or separation of
duties.
