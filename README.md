# ado2gh — Azure DevOps to GitHub Migration Accelerator

`ado2gh` is an enterprise migration agent for moving an Azure DevOps (ADO)
organization to GitHub. Its primary control plane is a
Planner–Executor–Validator (PEV) loop: it discovers the source, emits an
immutable source-to-target plan for approval, executes only that plan, and
records validation evidence before a run can complete.

Repository mapping and routine transformations are deterministic. An LLM is
used only for pipeline constructs that the deterministic planner classifies as
ambiguous. Its schema-constrained output is retained only as a proposal and is
never inserted into an executable workflow. An authorized reviewer must approve
the exact proposed fragment in a content-addressed manifest and approve a new
organization plan before that fragment can execute. Workflows are staged in a
pull request and are never auto-merged.

The current immutable artifact is plan schema **v8**. Durable execution and
audit state use SQLite schema **v13**; manual pipeline approvals use manifest
schema **v2**. Newer plans/databases and unknown approval schemas are rejected
by older binaries instead of being interpreted approximately.

## Quick start: enterprise PEV path

```bash
# 1. Install the CLI.
python -m pip install -e .

# 2. Supply credentials through the environment, never migration.yaml.
export ADO_PAT="..."
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="..."

# Optional: after approving non-secret llm_provider/model/base settings in
# migration.yaml, inject only the configured key value.
export OPENAI_API_KEY="..."

# 3. Discover the full ADO organization and write an immutable plan.
ado2gh agent plan \
  --config migration.yaml \
  --output output/pev_plan.json

# 4. Review output/pev_plan.json and copy the emitted plan_id.
# This performs preflights without authorizing live writes.
ado2gh agent run \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --dry-run

# 5. Execute exactly the reviewed plan. The approval value must match.
ado2gh agent run \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --output output/pev_validation.csv

# 6. Inspect the durable run and task receipts.
ado2gh agent status --run-id run_REPLACE_WITH_EMITTED_ID

# 7. Review and merge each generated workflow PR, provision external secrets,
# environments, runners, and approvals, then resume the same run. The resumed
# publisher verifies workflows on the default branch before completion.
ado2gh agent run \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --approve-plan plan_REPLACE_WITH_REVIEWED_ID \
  --resume run_REPLACE_WITH_EMITTED_ID \
  --output output/pev_validation.csv

# 8. Optional remote-read-only source/target validation recheck (records local evidence).
ado2gh agent validate \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_EMITTED_ID \
  --output output/pev_validation.csv
```

Omit `--input` from `agent plan` to migrate every eligible repository in the
organization. To plan a reviewed subset, pass `--input repos.txt` or a CSV
manifest. Creating a new plan after a source or configuration change is
intentional: a live executor refuses a changed plan or configuration digest.

## What PEV guarantees

### Planner

- Enumerates every paginated source and target collection used as evidence.
  ADO continuation-token loops reject repeated tokens; work items use ascending
  ID keyset pagination beyond WIQL's 20,000-row limit. GitHub workflows,
  secrets, runners, collaborators, teams, members, refs, issues, pull requests,
  and releases are traversed to completion; unstable totals, duplicates, or
  malformed pages fail closed instead of becoming partial evidence.
- Binds the canonical ADO and GitHub API origins plus each immutable
  organization identity into `policy.runtime_context`. Matching display names
  or repository mappings cannot redirect an approved run to another tenant or
  server.
- Captures the immutable ADO repository ID, default branch, every branch/tag
  name and SHA, and a digest of that full ref snapshot.
- Binds either the complete eligible organization inventory or an explicitly
  labeled subset, including the source repository count and content digest.
- Captures the immutable GitHub repository ID, size, default branch, every
  branch/tag name and SHA, and a full-ref digest whenever an existing target is
  explicitly approved for reuse. It also binds the intended/observed target
  visibility and the allowed visibility policy. A target is not approved by
  name alone.
- For repositories with the `pipelines` scope, records a normalized pipeline
  inventory, exact pipeline receipts, source fingerprints/YAML digests, and an
  inventory digest in the plan. Definition or YAML drift requires a new plan
  and approval.
- For `work_items`, `wiki`, `secrets`, and `branch_policies`, stores a
  content-addressed source snapshot containing only its digest and aggregate
  counts. The potentially sensitive payload is not persisted in the plan.
- Reads work items once per ADO project and indexes links by immutable
  repository ID. If unlinked items are enabled, each is assigned once to the
  lexicographically first planned source repository in its project; it is not
  copied into every repository.
- Resolves every ADO project/repository to one GitHub organization/repository.
- Carries only explicit team access mappings: each ADO team names one GitHub
  team and one exact role (`pull`, `triage`, `push`, `maintain`, or `admin`).
  There is no implicit default-to-write conversion. The separate per-repository
  `access_policy_approved` flag defaults to false because discovery cannot
  infer ADO ACL equivalence.
- Rejects case-insensitive source duplicates, target collisions, unsafe names,
  existing targets under the default `fail` policy, and malformed scope DAGs.
- Produces a content-addressed `plan_id`; timestamps and credentials do not
  affect or enter the plan identity.

### Executor

- Requires the exact `--approve-plan` value for live writes.
- Rechecks both API origins and immutable organization identities, source
  identity and every approved source ref, the complete approved
  state of a reused target, pipeline inventory, non-Git source snapshots,
  configuration digest, and task dependencies before execution.
- Fetches each non-Git payload once just in time, verifies that in-memory
  payload against the approved digest/counts, and transforms that same object.
  A second mutable source read cannot replace the verified payload.
- Records durable run/task receipts in SQLite and resumes by `run_id`.
- Registers an immutable plan capability for every exact
  source/target/scope/input-digest tuple. Scope completion and resume use
  plan/run-bound expectations and receipts; legacy wave rows are reporting
  data, not PEV authority.
- Uses a renewable cross-process plan lease plus atomic per-task leases. A
  second executor cannot run the same plan concurrently; expired task leases
  can be reclaimed safely after a crashed process.
- Acquires per-target fencing tokens before writes and records a durable remote
  operation barrier before dispatching Git, LFS, GEI, or another enabled
  non-idempotent remote write.
  An uncertain operation blocks all later target leases until externally
  reconciled; a timer alone cannot clear it.
- Executes deterministic scopes directly. Pipeline conversion invokes its own
  nested PEV loop only after strict pipeline inventory succeeds.
- Carries each freshly validated workflow/evidence artifact forward as the
  exact in-memory bytes whose SHA-256 matched the validator. The publisher does
  not reopen a mutable path. On resume, a local artifact is admitted only when
  both its persisted SHA-256 and Git blob SHA-1 match the completed conversion
  receipt; a changed or conflicting byte sequence stops delivery.
- Treats exported wikis, secret manifests, external approvals, and open
  workflow PRs as `needs_review`, never as completed migration work.

### Validator

- Verifies the approved source/target mapping, immutable target identity,
  default branch, branch SHAs, tags, workflows, and requested policy scope.
- Re-enumerates a full-organization source inventory before and after
  validation, and re-reads exact source refs, pipeline identities/revisions,
  and non-Git source digests. A repository or pipeline added after approval is
  drift, not silently out of scope.
- Reads back every explicit GitHub team role, Actions enablement and selected
  actions policy, active workflow state, required repository-secret name,
  approved environment protection digest, required online runner label, and
  exact external checkout repository ID/ref/SHA. Approval evidence alone is not
  proof that an external resource exists.
- Resolves every non-local workflow `uses` dependency: repository actions and
  reusable workflows must use a full 40-character commit SHA that resolves to
  that same commit, expose exactly one runnable entrypoint at the pinned commit,
  and have an immutable repository ID. Private actions must be in the target
  organization and shared at organization/enterprise scope; selected-actions
  patterns are checked against every reference. Container actions must use an
  immutable `sha256` image digest.
- Treats `access_policy_approved: false` as `needs_review`; source cleanup is
  prohibited until an authorized access owner attests the explicit policy in a
  newly approved plan.
- Uses two explicit Git baselines: code refs must match the approved source
  exactly, while a pipeline-delivery commit may be a strict descendant only
  when its complete recursive tree diff contains exactly the approved workflow
  and evidence blobs. Tags and unaffected branches remain exact.
- Accepts pipeline evidence only from the exact plan/run/wave receipts bound to
  the approved pipeline identity and source fingerprint; it does not reinterpret
  the latest mutable inventory.
- Persists machine-readable evidence for repository and pipeline checks.
- Replays only bounded, deterministic repairs (configured by
  `validation.max_repair_attempts`). It does not auto-approve ambiguity,
  credentials, policy exceptions, or review gates.
- Returns a nonzero exit status for `failed` and `needs_review` outcomes.

## Pipeline conversion: nested PEV

Each YAML, classic-build, or classic-release definition follows a second PEV
cycle:

1. **Plan** — normalize source metadata, fingerprint it, apply the exact
   `pipeline-pev-3` grammar, and classify every construct outside that grammar.
2. **Propose** — apply deterministic mappings first. For an eligible ambiguity,
   provider egress contains only its control metadata (ID, kind, source
   location) and bounded container/type shape for source/context. Source keys,
   strings, numeric values, identifiers, hashes, scripts, and credentials are
   not transmitted. The response is proposal evidence only and is not passed
   to the workflow executor.
3. **Approve** — an authorized reviewer may bind the exact fragment to the
   pipeline fingerprint and ambiguity in a schema-v2 manual approval manifest.
   That manifest requires a newly reviewed organization plan.
4. **Validate** — parse YAML with duplicate-key rejection and check workflow
   structure, source coverage, dependencies, ADO residue, literal secrets,
   dangerous commands, permissions, action references, exact approved
   ambiguous fragments, and proposal-only LLM evidence.

The provider uses strict structured output, bounded retries/timeouts, local
allow-lists, confidence thresholds, prompt/response digests, and `store: false`.
No model output is executable in the producing run, even when it is valid,
pinned, and high-confidence; only a subsequent exact schema-v2 human approval
and newly approved organization plan can promote that fragment.
If an ambiguity cannot be resolved safely, conversion stops at `needs_review`
or `failed`; placeholder workflows are not counted as production ready.

`pipeline-pev-3` treats only the following task/input shapes as deterministic:
the five setup tasks (`NodeTool@0`, `UsePythonVersion@0`, `UseDotNet@2`,
`JavaToolInstaller@0`, `GoTool@0`) with their small allow-listed version/
architecture keys, and `CmdLine@2`, `Bash@3`, or `PowerShell@2` with a script or
file path plus only arguments/working-directory keys. Native script, checkout,
publish, and download step shapes are also preserved and validated. A task name
appearing in a renderer is not sufficient: Docker publish/authentication,
Azure/service-connection work, package authentication, and every unsupported
input combination require an exact content-approved workflow step. Conditions
are deterministic only when their entire recursively parsed expression uses
the allow-listed function/arity grammar and literal or `variables`/`parameters`
atoms; a partial or unknown expression is classified, never copied through.

Validated artifacts are staged on `ado2gh/migrated-workflows` (configurable)
with evidence under `.ado2gh/pipeline-evidence/`. The agent opens or updates one
review pull request and never merges it. A pipeline scope completes only after
the reviewed workflow and its sanitized evidence are observable with their
recorded Git blob SHAs on the target default branch. The repository validator
then proves the delivery commit is a strict descendant of the approved source
commit and that the complete tree diff contains no path other than those exact
approved artifacts.

### LLM configuration and credential environment

Provider, model, base URL, organization, project, and API-key environment name
are non-secret `global.pipeline_conversion` policy and are bound into
`plan_id`. Only the key value is read from the environment. Legacy routing
environment variables may repeat plan-bound values but cannot select or change
them at execution time.

| Variable | Purpose |
|---|---|
| `ADO2GH_LLM_PROVIDER` | Legacy deployment assertion; if present, must equal plan-bound provider |
| `OPENAI_API_KEY` | Provider credential; environment only |
| `ADO2GH_LLM_MODEL` | Legacy assertion; if present, must equal plan-bound model |
| `OPENAI_BASE_URL` | Legacy assertion; if present, must equal plan-bound HTTPS base |

If the plan sets `llm_provider: disabled`, fully deterministic pipelines still convert.
Pipelines containing LLM-eligible ambiguity fail closed for operator action.
Manual-only ambiguity can instead use a schema-v2 validated approval manifest.
Each record is content-addressed and bound to the exact pipeline identity,
source fingerprint, and `ambiguity_id`; it records the approver, change ticket,
typed target mapping, and SHA-256 evidence. Stale, tampered, duplicate,
mismatched, or boolean approvals fail closed. The normalized manifest is part
of the immutable organization plan policy and digest.

A `repository_checkout` approval must name the exact repository, explicit ref,
token-secret *name*, and `checkout-access-canary` evidence. Execution resolves
the immutable repository ID and ref SHA; validation and cleanup re-resolve both.
Secret values are never stored or read.

A direct-secret or service-connection approval maps only statically enumerable
GitHub secret names and proves that those names exist; it never supplies a
value or authorizes arbitrary use. A `run:` step may not reference a GitHub
secret directly or inherit one through workflow/job/step `env`. Secret-bearing
operations must use a catalog-approved action input and still pass the normal
pin, Actions-policy, and runtime readback checks. Bare/dynamic/serialized
`secrets` contexts fail closed.

## Configuration

Use [`migration.yaml`](migration.yaml) as the annotated baseline. A minimal
production-safe configuration is:

```yaml
global:
  ado_org_url: "https://dev.azure.com/YOUR_ORG"
  gh_org: "your-github-org"
  # GitHub.com defaults are implicit. For GHES, both values are required and
  # their HTTPS authorities must agree.
  # gh_api_url: "https://github.example.com/api/v3"
  # gh_web_url: "https://github.example.com"
  migration_strategy: mirror
  parallel: 4
  pipeline_parallel: 12
  default_scopes:
    - repo
    - pipelines
    - branch_policies

  mapping:
    target_org: "your-github-org"
    strategy: project-prefix
    separator: "-"
    lowercase: true
    existing_target_policy: fail
    allow_nonempty_target: false
    preflight_targets: true
    allowed_target_visibilities: [private, internal]
    projects:
      "Payments Platform":
        prefix: "payments"
    repositories:
      "Legacy Project/Orders API":
        gh_repo: "orders-api"

  pipeline_conversion:
    # Non-secret execution identity. Use openai-responses only after provider
    # governance approval; model output remains proposal-only.
    llm_provider: disabled
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_organization: ""
    llm_project: ""
    llm_api_key_env: OPENAI_API_KEY
    min_llm_confidence: 0.80
    max_llm_resolutions: 32
    # Relative to migration.yaml; normalized content becomes part of plan_id.
    # manual_approval_manifest: "pipeline-manual-approvals.yaml"
    require_permissions: true
    require_pinned_action_sha: true
    forbid_remote_script_execution: true
    # Optional reviewed additions/overrides; keys are owner/repo or
    # owner/repo@major and values are full 40-character commit SHAs.
    # action_pins: {}

  pipeline_delivery:
    mode: pull_request
    branch: "ado2gh/migrated-workflows"
    title: "Review migrated GitHub Actions workflows"

  validation:
    max_repair_attempts: 1

  # Renewable plan/task lease duration; minimum 30 seconds.
  execution_lease_seconds: 300

  # These source mutations must be present before the plan is approved.
  cleanup:
    allow_disable_pipelines: true
    allow_redirect: false
    allow_archive: false
```

The default `project-prefix` mapping prevents same-named repositories in
different ADO projects from colliding. Use `strategy: preserve` only when
repository names are already unique across the planned target. Explicit
repository mappings have highest precedence. Mapping comparison is
case-insensitive because GitHub repository identity is case-insensitive.

`existing_target_policy: fail` and `allow_nonempty_target: false` are the safe
defaults. Set `reuse` or allow a nonempty target only after independently
proving ownership and approving that exception; the decision becomes part of
the plan identity.

### Optional repository manifest

Text format:

```text
# ADO_PROJECT/ADO_REPOSITORY
Payments/checkout
Identity/login::target-org/identity-login
```

CSV supports `ado_project`, `ado_repo`, optional `gh_org`, `gh_repo`, `scopes`
(`|` separated), and `pipeline_filter`. Explicit destinations remain subject to
collision and name validation.

When an explicit repository definition carries `team_mapping`, every source
team entry must declare both `github_team` and an exact least-privilege
`permission`. Plan schema v7 preserves that mapping, the executor applies only
those declared grants, and validation reads each role back from GitHub. Set the
adjacent `access_policy_approved: true` only after reviewing the complete
source-to-target access design; it defaults to false, leaves the run
`needs_review`, and blocks ADO cleanup.

### Scopes and honest completion

| Scope | Automated result |
|---|---|
| `repo` | Mirror all refs/LFS, or use GEI when configured |
| `pipelines` | Nested PEV conversion plus review-PR delivery |
| `work_items` | Repository-linked work items become idempotent GitHub issues |
| `branch_policies` | Supported policies become GitHub branch protection |
| `wiki` | Local export; requires review/publishing |
| `secrets` | Names/instructions manifest only; values are never read or copied |

`include_unlinked_work_items` defaults to false. If enabled, each unlinked
project-scoped work item is assigned exactly once to the case-insensitive
lexicographically first planned `project/repository` key in that ADO project.
This stable anchor avoids multiplying one project backlog across all targets.

## Authentication and prerequisites

- Python 3.9 or later and Git 2.30 or later.
- Git LFS for repositories that use LFS.
- `gh` plus the `gh-gei` extension only for `migration_strategy: gei`.
- ADO PAT permissions for only the scopes being planned. Add ADO write
  permissions only for an approved cleanup operation.
- GitHub token or App permissions for repository creation/content, issues,
  Actions workflows, environments, and branch policies that are in scope.

Credentials are loaded from the environment:

```bash
export ADO_PAT="..."
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
export GH_TOKEN="..."                 # single-token mode

# Or a rate-limit-aware pool at large scale:
export GH_TOKEN_1="..."
export GH_TOKEN_2="..."

# Or GitHub App authentication:
export GH_APP_ID="..."
export GH_APP_INSTALLATION_ID="..."
export GH_APP_PRIVATE_KEY_PATH="/secure/path/app.pem"
```

GitHub API capacity is reserved atomically under the token-manager lock before
each request and tracked by a monotonically increasing in-flight reservation
ID. Concurrent responses merge conservatively: capacity can only decrease
inside one reset window, an older reset window cannot replace a newer one, and
pending reservations remain charged across a reset. Primary limits and
secondary/abuse limits honor `X-RateLimit-Reset` or `Retry-After`; the client
rotates only to another immediately available credential, and malformed or
duplicate credentials never create fictitious quota. Transport failures always
release in-flight tracking while retaining the conservative one-call charge.

For GitHub Enterprise Server, set both `gh_api_url` and `gh_web_url` in the
config. The web URL is used for clone, redirect, and operator-facing links and
cannot be inferred from an arbitrary enterprise API path.
`ado_org_url`, `gh_api_url`, `gh_web_url`, and `OPENAI_BASE_URL` must be
absolute HTTPS URLs. The ADO and GitHub URL fields additionally reject embedded
credentials, query strings, and fragments.
Do not put PATs, App private keys, LLM keys, or secret values in YAML, input
manifests, command arguments, or checked-in files. Inline config secrets are
disabled by default.

## Migration strategies

`mirror` (default) runs a real `git clone --mirror`, then pushes only the
approved `refs/heads/*` and `refs/tags/*` namespaces in atomic batches guarded
by exact per-ref `--force-with-lease` baselines. It deletes only extra refs that
were present in the approved reusable-target snapshot. Source and target clone
authorities are checked before credentials are exposed. LFS is fetched from ADO
before the remote is changed, pushed under a crash barrier, then fetched into a
fresh target clone and verified by object ID and SHA-256. It does not push
provider-internal refs and does not migrate ADO pull-request history.

`gei` delegates repository migration to GitHub Enterprise Importer through the
`gh gei` extension. Use it when the destination organization and GitHub plan
support the history types you require. The same immutable mapping, approval,
and validation controls still apply.

## Cleanup is a separate cutover

ADO cleanup is intentionally outside migration execution. Run it only against a
completed PEV run and an externally approved change ticket. Each requested
action must also be authorized by `global.cleanup` in the immutable approved
plan; archival additionally requires `archive_source: true` for every selected
repository. Cleanup rechecks the plan's API origins and organization identities
before evaluating any source action.

```bash
# Preview only; does not mutate ADO.
ado2gh ado-cleanup \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --dry-run

# Live default: disable matched ADO YAML/classic-build/release definitions
# after interactive confirmation.
ado2gh ado-cleanup \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345

# Archival is an additional explicit action and works only when the reviewed
# plan contains cleanup.allow_archive=true plus per-repository archive_source.
ado2gh ado-cleanup \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_COMPLETED_ID \
  --approval-ticket CHG-12345 \
  --archive
```

Immediately before every source mutation, cleanup rechecks the immutable ADO
repository ID/name, default branch, every branch/tag SHA, and full-ref digest.
Pipeline disabling additionally re-discovers definitions and requires the exact
approved identity, name, type, and `source_revision` for every selected YAML,
classic-build, and release definition. Each update is followed by a readback
whose disabled state and new revision must match; missing, unexpected,
unreadable, or stale receipts fail closed.
Adding a redirect commit uses the approved source HEAD as an optimistic
concurrency fence and therefore also requires both `--add-redirect` and
`--allow-source-mutation`; the run returns to `needs_review` afterward. Cleanup
never happens implicitly.

Live cleanup additionally builds a canonical request containing the exact
plan/run, selected repositories, approved pipeline receipt digest, target
identity/visibility, actions, and redirect content digest. Confirmation and the
external ticket authorize a one-shot content-addressed capability. Each remote
mutation records a before receipt before dispatch and an after receipt only on
a known result. Cleanup holds target fences and first captures a full target
baseline: immutable repository identity, visibility/default branch, complete
refs/workflows, Actions and selected-action policy, required secrets,
environment digests, online runners, external checkout IDs/refs/SHAs, and the
organization base permission plus collaborator/team/member access inventory.
Immediately before **each** ADO mutation it recaptures that entire target,
runtime, and access guard and requires byte-for-byte equality with the baseline.
It also repeats the relevant source/revision CAS check.

Cutover ordering is redirect, repository freeze, then pipeline disable. A
failed freeze CAS-reverts the redirect. If a pipeline disable fails with a
provable state, already changed definitions are restored in reverse order; the
repository is unfrozen and the redirect is reverted only after that
compensation succeeds. Ambiguous outcomes leave receipts open and the source in
the safest known state for ticketed reconciliation—never an assumed retry or
blind rollback. An in-flight receipt after a crash is likewise not retried by
assumption.

## Rollback is plan-bound

Live rollback derives its targets and stable wave key from the immutable plan;
`--wave` is not needed. It requires the plan, a terminal run belonging to that
plan, matching runtime API/organization authority, an external ticket, and
interactive confirmation:

```bash
# Preview a legacy wave only; this is read-only and is not authorization.
ado2gh rollback -c migration_phase.yaml --wave 1 --dry-run

# Request a scoped branch-policy rollback. The current state model does not
# persist safe per-policy before/after fingerprints, so this fails closed and
# directs the operator to reviewed manual remediation.
ado2gh rollback \
  -c migration.yaml \
  --plan output/pev_plan.json \
  --run-id run_REPLACE_WITH_TERMINAL_ID \
  --approval-ticket INC-12345 \
  --scopes branch_policies

```

Automatic whole-repository deletion is **disabled unconditionally**. Omitting
`--scopes` (or explicitly requesting `repo`) is rejected before a destructive
capability can authorize deletion. GitHub cannot provide one complete,
race-free inventory of packages, projects, discussions, deployments,
integrations, credentials, external clones, or other downstream users, so even
an exact created-by-run ownership receipt is not sufficient deletion proof.
Quarantine/archive the target and use a separately reviewed manual deletion
procedure when it is truly required.

Scope rollback also requires artifact-level creation provenance and the exact
current-state fingerprint. Branch-policy fingerprints are not yet persisted,
so automated branch-policy rollback fails closed instead of deleting
branch-wide protection that may include operator changes. Pipeline rollback
intentionally fails because deleting or resetting local state would not remove
merged workflows; use a reviewed reverse pull request. Prefer a forward repair
whenever no complete, ownership-safe inverse exists.

## Evidence trust boundary

SQLite provides transactional resume, exact content digests, fencing, and
crash-stop receipts for one controlled deployment. It is not an external WORM
store and is not cryptographically trustworthy against an administrator who can
rewrite the database, WAL, plan, and local evidence together. Keep the database
on durable local storage, back up the database and WAL consistently, and export
the approved plan, validation reports, capability/action receipts, workflow
evidence, and database digest to an independently controlled WORM audit system.
For regulated evidence, sign that export with an enterprise key and retain an
independent timestamp/attestation. Local SHA-256 content addresses detect
ordinary mismatch; they do not replace external signing or separation of
duties.

## Commands

The production control plane is:

| Command | Purpose |
|---|---|
| `agent plan` | Discover/map sources and create an immutable plan |
| `agent run` | Execute, validate, and apply bounded deterministic repairs |
| `agent validate` | Re-run evidence-backed validation for one run |
| `agent status` | Show durable run and task status |
| `agent release-quarantine` | Release an uncertain-write target fence after ticketed external reconciliation |

Discovery, readiness, service-connection manifests, reports, risk-based phase
assignment, and dashboards remain available. The older `run`, `phase run`,
`pipelines retry-failed`, and `push-workflows` commands are read-only
compatibility assessments: v6 rejects them unless `--dry-run` is present. They
cannot perform production writes. See
[the command reference](docs/COMMAND_REFERENCE.md).

## Operational states

| State | Meaning |
|---|---|
| `completed` | Execution and deterministic validation passed |
| `needs_review` | Safe progress was made, but a human/external gate remains |
| `failed` | A required execution or validation check failed |
| `dry_run_passed` | Isolated preview completed without a blocker |
| `dry_run_needs_review` | Isolated preview found a human/external gate |
| `dry_run_failed` | Isolated preview found a required failure |

Dry runs use an isolated in-memory state database and never create resumable
live receipts or remote writes. They do execute deterministic scopes and the
nested pipeline planner/converter/local validator, so conversion failures and
review gates determine the dry-run status. Only `completed` returns success
for live execution; `dry_run_passed` is the only successful preview status.

An interrupted live run is resumed with the same plan, approval, database, and
`--resume RUN_ID`. Do not generate a replacement plan merely to bypass drift or
a failed review gate.

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
```

Architecture details and trust boundaries are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Operational procedures are in
[`docs/MIGRATION_RUNBOOK.md`](docs/MIGRATION_RUNBOOK.md).

## License

No distribution license is currently declared in this repository. Add the
organization-approved license before external distribution, and follow the
applicable migration governance and data-retention policy.
