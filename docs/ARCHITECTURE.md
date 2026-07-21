# Architecture — enterprise Planner–Executor–Validator migration agent

This document describes the production control plane for `ado2gh`. The system
has two nested Planner–Executor–Validator (PEV) loops: an organization-level
loop controls source-to-target migration, while a pipeline-level loop converts
an individual ADO pipeline into a validated GitHub Actions artifact.

The older phase/wave subsystem remains useful for risk segmentation and
reporting. It is not the authorization boundary. Production writes enter
through `ado2gh agent run` with an exact approved plan ID.

The contracts in this document are implemented by immutable plan schema v7,
SQLite state schema v13, and manual pipeline approval manifest schema v2. The
loader rejects an unknown plan/approval version or a state
database newer than the running binary.

The content-addressed `policy.runtime_context` contains `source_api_url`,
`source_org_identity {kind,id}`, `target_api_url`, and
`target_org_identities {org:{kind,id}}`. Production clients resolve immutable
service IDs. Custom/test clients without identity lookups are explicitly marked
`kind: logical`, making their lower-assurance boundary visible rather than
silently treating a display name as immutable identity.

## System context

```text
                                      approval / change ticket
                                                 │
                                                 ▼
ADO organization ──► Organization Planner ──► immutable plan.json
       │                       │                 │ plan_id + DAG
       │                       └── collision and target preflight
       │                                         │
       │                                         ▼
       ├──────────────────────────────► Approved-plan Executor
       │                                         │
       │                 ┌───────────────────────┼─────────────────────┐
       │                 ▼                       ▼                     ▼
       │            Git migration        Pipeline nested PEV      metadata scopes
       │                                   │           │          issues / policies
       │                                   ▼           ▼          manifests / exports
       │                           validated files   review PR
       │                                               │ human merge
       │                                               ▼
       └──────────────────────────────► Organization Validator ◄── GitHub
                                                 │
                                                 ▼
                                      evidence + durable run status
                                                 │
                                      completed / needs_review / failed
```

## Organization-level PEV

### Planner

`ado2gh.pev.planner.MigrationPlanner` is deterministic and read-only apart
from registering planned mappings in the state database. It:

1. Resolves and records the canonical ADO and GitHub API origins plus the
   immutable organization identity returned by each service, then discovers
   all projects and repositories unless the operator supplies a reviewed
   subset manifest.
   Full-organization selection records the complete eligible repository count
   and digest; an explicit cohort is labeled as a subset rather than presented
   as organization completeness.
2. Excludes disabled repositories unless policy opts in.
3. Resolves each ADO `project/repository` to one GitHub
   `organization/repository` using explicit overrides, project policy, and a
   global strategy.
4. Rejects invalid names, case-insensitive duplicates, many-to-one target
   collisions, and unapproved existing targets.
5. Reads repository refs twice to obtain a stable snapshot of the immutable ADO
   repository ID, default branch, every branch/tag name and SHA, the
   default-branch HEAD, and a canonical full-ref digest.
6. For an explicitly reusable existing GitHub target, records its immutable
   repository ID, size, default branch, complete heads/tags maps, and canonical
   full-ref digest. It also records the observed (or intended, when absent)
   target visibility and binds the allowed visibility policy. An absent target
   is represented by an empty canonical baseline.
7. For each planned `pipelines` scope, records normalized inventory receipts
   and source/YAML digests plus one content digest. Partial, failed, or
   unassociated inventory cannot enter a valid plan.
8. For `work_items`, `wiki`, `secrets`, and `branch_policies`, fetches one
   complete source payload and records only a schema version, content digest,
   item count, and scope-specific aggregate counts. Sensitive source content
   does not enter plan JSON.
   Work items are fetched once per ADO project and indexed by immutable
   repository link. When explicitly enabled, unlinked items are assigned once
   to the case-insensitive lexicographically first planned source key in that
   project, never multiplied across all repositories.
9. Preserves only explicit access policy: each source-team mapping declares an
   exact GitHub team and role. `access_policy_approved` defaults to false
   because organization discovery cannot infer ADO ACL equivalence; false is a
   review gate and later blocks source cleanup.
10. Builds an acyclic task graph containing preflight, inventory, per-scope
   execution, and validation tasks.
11. Removes credential-shaped configuration from the plan and computes a
   canonical SHA-256 identity, `plan_id`.

Mapping is never delegated to an LLM. Choosing the wrong destination is a
governance failure, not a language ambiguity.

`MigrationPlan.validate()` recomputes its identity when a JSON plan is loaded.
Changing a repository, mapping, scope, dependency, policy, or non-secret
configuration value invalidates the reviewed ID.

### Executor

`ado2gh.pev.executor.PEVExecutor` accepts only a valid plan. Live execution
also requires `approved_plan_id == plan.plan_id`. Before mutation it verifies:

- the runtime ADO/GitHub API origins and immutable organization identities
  match the plan;
- the non-secret runtime configuration digest matches the plan;
- the ADO repository ID, default branch, complete heads/tags snapshot, and
  pipeline source snapshot have not drifted;
- an absent target did not appear after planning, or an approved existing
  target still has the exact immutable ID, size, default branch, complete
  heads/tags snapshot, and digest captured by the planner;
- each non-Git scope payload still matches its approved digest and counts;
- inventory and task dependencies succeeded.

For a bound non-Git scope, the executor fetches the payload once just in time,
verifies that in-memory object, and passes that same object to the deterministic
scope handler. It does not verify one read and transform a later mutable read.

The executor persists every task before work begins. Repository execution is
parallel, while each repository’s scopes follow their declared dependencies.
A stable plan-derived legacy wave key connects PEV receipts to scope and
pipeline reports without weakening plan identity. It is not a resume or write
authority. Before creating a live run, the executor registers an immutable
capability manifest for each exact `plan_id`, source, target, scope, and task
input digest. Each execution stores a matching plan/run-bound scope expectation
and receipt; a completed row with another plan, run, target, or digest cannot be
reused.

A renewable SQLite plan lease allows only one cross-process executor for a
plan. Atomic per-task leases use compare-and-update claims and attempt limits;
expired claims can be recovered after a crashed worker. A heartbeat renews the
plan lease, and losing that lease blocks validation rather than risking two
writers reporting completion. Lease duration is configured with
`global.execution_lease_seconds` (30–86,400 seconds; default 300).

Every mutating repository task also holds a per-target fencing token derived
from the plan capability manifest. Immediately before Git, LFS, GEI, or another
long-running remote mutation is dispatched, the executor commits an
`in_flight` remote-operation barrier with the plan/run, target, operation
digest, lease owner, and fencing token. A successful call closes that barrier.
If the process dies after dispatch, the durable barrier blocks every future
target lease—even after a finite worker lease expires—until ticketed external
reconciliation releases quarantine. Target-use history also prevents an older
run from deleting a repository after a later approved run has reused it.

Resume requires the original `plan.json`, plan approval, state database, and
`run_id`. Completed task receipts are reused. Failed or review-required work is
not silently promoted. Repair mode may reuse only the target owned by the same
approved mapping.

### Validator and repair loop

`ado2gh.pev.validator.PEVValidator` validates the plan’s exact mappings rather
than accepting destinations discovered at runtime. Checks include:

- target existence, immutable repository identity, and default branch;
- exact source branch names and per-branch commit SHAs;
- tag parity;
- exact plan/run/wave-bound workflow and evidence blobs on the target default
  branch;
- exact live ADO pipeline identity/name/revision inventory and exact non-Git
  source snapshot digests;
- the full eligible source-organization inventory before and after validation
  when the plan claims organization coverage;
- explicit GitHub team repository roles and `access_policy_approved`;
- Actions enablement, selected/allowed-actions policy versus every workflow
  `uses`, active workflow state, required repository-secret names, approved
  environment-protection configuration digests, required online runner labels,
  and external checkout repository IDs/refs/resolved SHAs;
- branch protection when requested;
- durable execution-task state.

Repository validation uses two baselines so pipeline delivery does not weaken
Git fidelity:

1. The source baseline requires exact branch/tag identity and SHA parity.
2. The pipeline overlay may advance only the default branch and the configured
   staging branch. The target commit must be a strict descendant of the
   approved source commit, and a complete recursive source/target tree diff
   must contain exactly the approved `.github/workflows/` and
   `.ado2gh/pipeline-evidence/` blobs with their recorded Git object SHAs.

All unaffected branches and all tags remain exact. A truncated or otherwise
incomplete Git-tree response fails validation rather than becoming evidence.

Every check creates a `validation_evidence` record associated with the run and,
when applicable, its validation task. A missing API response is a failure, not
an empty-success result.

`MigrationOrchestrator` may feed a failed validation back to the executor only
when the validator identifies a deterministic scope repair. Attempts are
bounded by `global.validation.max_repair_attempts`. The loop never repairs or
approves an LLM ambiguity, secret, external environment, policy exception,
open pull request, or manual review decision.

## Pipeline-level PEV

Pipeline conversion starts only after strict inventory associates a definition
with a source repository. Build definition IDs and release artifacts are
resolved to stable source identities; missing or conflicting associations fail
closed rather than sending a workflow to a guessed repository.

### Pipeline planner

`PipelineConversionPlanner` normalizes YAML, classic-build, and classic-release
metadata and fingerprints the source. It separates constructs into:

- deterministic mappings with known semantics;
- LLM-eligible ambiguities, such as a bounded unknown task or condition;
- external requirements, such as credentials, environment protection, runner
  selection, or constructs that cannot be inferred safely.

A pipeline with no ambiguity never calls an LLM.

### Pipeline executor and LLM boundary

`PipelineConversionExecutor` applies deterministic plan operations first. For
each eligible ambiguity, `PipelinePEVConverter` can ask a provider-neutral
client for a proposal. Non-secret provider/model/base URL/organization/project
and key-environment-name settings are immutable plan policy; only the key value
is environment-supplied. The OpenAI Responses adapter enforces:

- redaction of credential-like keys, bearer values, assignments, and known
  secret values before prompting;
- bounded context, output size, retries, and connection/read timeouts;
- strict JSON-schema output plus a second local schema/allow-list check;
- exactly one permitted `run` or explicitly versioned `uses` operation;
- confidence thresholds and an upper bound on resolutions per pipeline;
- prompt, response, source, plan, workflow, and evidence digests;
- `store: false` for provider requests.

Model output is untrusted proposal evidence. It is never passed to the
transformer and therefore cannot enter executable workflow YAML in the run that
requested it. It cannot create an approval, bypass a finding, write a literal
credential, choose an arbitrary shell, or directly call GitHub. A missing
provider, refusal, low-confidence answer, invalid response, or any proposal
becomes review/failure evidence.

Manual-only ambiguity and promotion of an LLM proposal use a separate
schema-v2 content-addressed approval manifest. A
record is bound to the pipeline identity, exact source fingerprint, and
planner-generated ambiguity ID, and includes approver, ticket, typed target
mapping, and SHA-256 evidence. The normalized manifest is part of the immutable
organization policy/config digest. Duplicate, stale, mismatched, tampered, or
boolean approvals are rejected; approved workflow mappings are still subject
to the independent validator. Because adding the exact fragment changes plan
policy, it requires a new organization plan and approval; it cannot be injected
while resuming the run that produced the proposal.

For external-configuration approvals, evidence is not treated as current
state. Completion reads repository secret *names* back from GitHub (never
values) and hashes the policy-bearing environment response to compare it with
the approved `configuration_digest`. Missing secrets, drifted protection,
disabled Actions, or inactive workflows remain non-production.

A `repository_checkout` approval is valid only with an explicit ref,
token-secret name, and `checkout-access-canary` evidence. Execution records the
checkout repository's immutable ID and resolved SHA. Validation re-resolves the
same ref and ID, while cleanup fences that dependency together with online
runner labels and the selected Actions policy before source mutation.

The built-in action catalog maps reviewed major versions to full commit SHAs.
The transformer emits those immutable SHAs when
`require_pinned_action_sha` is enabled. Operator `action_pins` additions or
overrides must be 40-character SHAs, are validated at configuration load, and
become part of the plan identity; changing a pin requires dependency review and
a new plan approval.

### Pipeline validator

`PipelineWorkflowValidator` reparses the generated workflow independently. It
rejects duplicate YAML keys and validates root structure, triggers, jobs,
dependency cycles, step limits, permissions, ADO syntax residue, source-plan
coverage, literal secrets, unsafe event contexts, remote-script execution,
destructive root commands, secret exfiltration patterns, content-approved
ambiguous fragments, and LLM proposal evidence. For LLM-eligible ambiguity it
validates proposal evidence as non-executable;
only separately content-approved fragments can appear in the candidate YAML.

`production_ready` means there are no error or manual-review findings. Warning
and finding details are written to an evidence artifact; raw pipeline source
and credentials are not copied into evidence.

Every successful conversion receipt is bound to the exact organization
`plan_id`, `run_id`, stable wave key, pipeline ID/type, source fingerprint, and
approved inventory digest. It also records the approved individual pipeline
receipt digest plus workflow and evidence Git blob SHAs. Validation applies the
plan's `pipeline_filter` and never redefines the approved set from current
mutable inventory rows.

### Delivery lifecycle

Validated local YAML is not remote completion. `WorkflowPublisher`:

1. Checks whether every expected workflow and evidence artifact is already on
   the target default branch at its exact recorded blob SHA.
2. Otherwise creates or reuses `ado2gh/migrated-workflows` from the observed
   base SHA.
3. Uploads workflow files and sanitized evidence.
4. Creates or reuses one review pull request.
5. Never merges the pull request.

An open PR yields `needs_review`. On resume after human review and merge, the
publisher observes the exact workflow and evidence blobs on the default branch
and reports `remote_verified`; the repository validator then proves the
two-baseline tree overlay described above. Only then can the pipeline scope
complete. A configured local-only delivery mode remains non-production and
cannot produce a completed run.

## Scope semantics

| Scope | Completion condition |
|---|---|
| `repo` | Git transfer succeeded and source/target refs validate |
| `pipelines` | Every expected conversion is production-ready and workflows are verified on the default branch |
| `work_items` | Repository-linked items were created or matched idempotently; failures are reported |
| `branch_policies` | Requested supported protection was applied and observed |
| `wiki` | Export exists, but the task remains `needs_review` until externally published |
| `secrets` | Names/instructions manifest exists, but credential/OIDC provisioning remains `needs_review` |

Project-wide ADO resources are not duplicated per repository. Resources whose
values cannot be read through the ADO API are represented as explicit external
requirements, never fabricated or marked migrated.

When `include_unlinked_work_items` is false (the default), project work items
without a repository link are excluded. When true, they are assigned once to
the stable lexicographically first planned source repository for that project;
they are not emitted once per target repository.

## State and audit model

`StateDB` schema v13 is a versioned SQLite database using WAL mode, `FULL`
synchronous durability, busy
timeouts, foreign keys, explicit transactions, and conflict-safe upserts.
Schema migrations are applied in order and refuse a database newer than the
running code.

| Table | Purpose |
|---|---|
| `schema_meta` | Current schema version |
| `repository_mappings` | Canonical source/target ownership and fingerprints |
| `pev_runs` | Plan-bound run status and summary |
| `pev_tasks` | Durable DAG task state, attempts, leases, results, and errors |
| `pev_plan_leases` | Renewable one-executor-per-plan cross-process lease |
| `pev_target_leases` | Per-target fencing tokens shared by execution, validation, and rollback; uncertain long-running writes are quarantined |
| `repository_target_uses` | Immutable history of every plan/run that writes a target; protects against superseded rollback |
| `pev_remote_operations` | Pre-dispatch crash-stop barriers for uncertain long-running target mutations |
| `pev_plan_capabilities` | Immutable plan-derived source/target/scope/input-digest write manifest |
| `pev_scope_expectations`, `pev_scope_receipts` | Exact plan/run/source/target/scope/input-digest execution authority and outcome |
| `pev_destructive_capabilities` | One-shot content-addressed cleanup/rollback authorizations bound to exact plan/run requests |
| `pev_destructive_action_receipts` | Durable per-action before/after receipts written around destructive dispatch |
| `validation_evidence` | Per-run deterministic check evidence |
| `llm_decisions` | Redacted provider/model/digest/confidence audit records |
| `pipeline_conversion_attempts` | Source fingerprint, ruleset, outcome, and evidence per conversion attempt |
| `migrations` | Compatibility/reporting scope status; not PEV resume authority |
| `pipeline_inventory` | Normalized definition metadata keyed by project, ID, and type |
| `pipeline_inventory_runs` | Completed/partial inventory scan receipts and digests |
| `pipeline_migrations` | Append-preserving, exact plan/run/wave/source-bound conversion status plus workflow/evidence content receipts |
| `migration_expectations` | Legacy engine expectations retained for compatibility; exact PEV authority is in `pev_scope_*` |
| `wave_runs`, `batch_checkpoints` | Compatibility phase/wave execution receipts |
| `repo_risk_scores`, `phase_gates` | Risk segmentation and composite gate evidence |

State is a transactional audit log and resume aid, not proof by itself.
Completion requires fresh validator evidence against the approved source and
target. Local SHA-256 content addressing detects ordinary mismatches but does
not make the database tamper-evident against a host/database administrator.

An uncertain Git/LFS/GEI push or repository deletion changes the lease owner to
an indefinite quarantine, so a later plan cannot take over merely because a
timer elapsed. Only the ticketed `agent release-quarantine` reconciliation
command can release that fence, and its target identity is written to audit
evidence.

## Security boundaries

### Credentials

- ADO, GitHub, App, and LLM credentials come from environment variables or
  referenced key files. Inline secrets are disabled by default.
- Git authentication uses an ephemeral askpass helper and environment; tokens
  are not placed in Git remote URLs or process arguments.
- HTTP retries are automatic only for safe/idempotent reads. Mutating requests
  are handled explicitly to avoid duplicate side effects.
- Configured ADO, GitHub API/web, and LLM endpoints must be absolute HTTPS
  URLs. ADO and GitHub fields also reject userinfo, queries, and fragments.
- API clients use thread-local sessions; rate-limit waiting and App-token
  refresh do not hold the token-manager lock.

### Source and target ownership

- Mapping and target collision checks are case-insensitive.
- Canonical source/target API origins and immutable organization identities are
  plan-bound and rechecked; a matching organization name is not tenant proof.
- `agent run`, `agent validate`, live rollback, and live cleanup all reconcile
  `policy.runtime_context` before accepting the plan as authority.
- Existing targets fail by default; nonempty reuse is a separate explicit
  policy decision whose immutable ID, visibility, and complete ref baseline
  are plan-bound.
- ADO ACLs are not inferred. Team mappings must name an exact GitHub role, and
  the separate `access_policy_approved` attestation must be true before access
  validation can pass or cleanup can destroy the source.
- Rollback and repair operate on plan-owned scope records. They must not infer
  ownership merely because a repository has a matching name.
- Live rollback derives targets from the plan and requires the plan, a terminal
  bound run, and an external ticket. It captures a fresh target snapshot
  (identity, visibility, default branch, complete refs, issue/PR and release
  identities, and activity timestamps) into a one-shot content-addressed
  capability. Repository deletion additionally requires a created-by-that-run
  receipt, no later target-use receipt, and an unchanged live snapshot. Adopted
  targets are never deletable.
- Exact ownership and a stable snapshot are necessary but not sufficient proof
  that deletion is safe: GitHub cannot enumerate every external clone,
  integration, deployment, or dependency. Enterprise policy may disable
  automatic whole-repository deletion and require a scope inverse, forward
  repair, or separately reviewed manual deletion instead.
- Git source drift, pipeline inventory drift, non-Git source drift, and reused
  target drift block execution rather than silently changing either baseline.

### Human gates

- `--approve-plan` proves the operator reviewed the exact content identity.
- Workflow PRs, credential/OIDC setup, environments, runners, and unsupported
  semantics remain human gates.
- Live ADO cleanup requires a completed plan-bound run, an external approval
  ticket, interactive confirmation, an approved access policy, and an exact fresh check of the ADO
  repository ID/name, default branch, all branch/tag SHAs, and full-ref digest
  before every mutation. Pipeline disabling additionally requires the exact
  approved YAML/classic/release identity, name, type, and source revision; an
  update completes only after disabled-state and advanced-revision readback.
  Missing, unexpected, unreadable, or stale receipts block the operation.
- Redirect commits require an additional source-mutation flag and invalidate
  the previous parity state; the approved HEAD is used as ADO's optimistic
  concurrency fence.
- Cleanup and rollback convert the exact request shown at confirmation into an
  atomically claimed one-shot capability. Every remote mutation receives a
  durable before receipt prior to dispatch and an after receipt only for a
  known response. A crashed/in-flight receipt blocks automatic reauthorization
  or retry and requires reconciliation.
- Cleanup holds the target fence while re-reading selected Actions policy,
  workflow action references, required online runner labels, exact external
  checkout ID/ref/SHA, repository-secret names, and environment configuration.
  Source destruction is blocked if any approved runtime dependency drifts.

## Failure and status model

`completed` is deliberately narrow. A repository or run with open workflow
review, exported-only content, external credentials, unsupported semantics, or
warnings requiring a decision is `needs_review`. Required API, execution, or
validation failures are `failed`. Dry-run receipts are never reused as live
completion.

CLI commands return nonzero for both `failed` and `needs_review`, allowing CI or
an enterprise orchestrator to stop at governance boundaries. Operators resolve
the underlying condition and resume the same run; they do not overwrite state
or create a new plan to bypass evidence.

Dry runs execute against isolated in-memory state and return
`dry_run_passed`, `dry_run_needs_review`, or `dry_run_failed`. No dry-run result
is reusable as a live receipt, and no remote write occurs. The organization
dry run still executes the nested pipeline planner, converter, and local
validator; its failures and review findings determine the result. The legacy
`run`, `phase run`, `pipelines retry-failed`, and `push-workflows` commands are
hard-disabled for live writes and exist only with `--dry-run` for compatibility
assessment.

## Scale and availability

- Project, repository, branch, workflow, issue, and work-item APIs paginate.
- Repository and pipeline concurrency are configured independently.
- Multiple GitHub PATs can be rotated using observed rate-limit headers;
  GitHub App installation tokens are refreshed without serializing all callers.
- Pipeline source YAML is fetched just in time and is not persisted in SQLite.
- Per-repository isolation lets unrelated repositories continue while the
  aggregate run remains failed or review-required.

SQLite is appropriate for one controlled migration deployment with local
durable storage. Do not place the database on an unreliable network filesystem
or run independent writers that do not share the same state file. Back up the
database and WAL consistently with the plan, reports, and evidence artifacts.

SQLite is an explicit local-host trust boundary: an administrator who can
rewrite the database, WAL, plan, and local artifacts can also rewrite local
evidence. For compliance or non-repudiation, export the audit set and its digest
to an independently controlled WORM system, cryptographically sign it with an
enterprise-managed key, and retain an independent timestamp/attestation. The
application's content digests and one-shot receipts do not replace external
signing, immutable retention, or separation of duties.

## Primary module map

```text
ado2gh/
├── cli.py                         # operator entry points and approval flags
├── pev/
│   ├── contracts.py               # immutable plan/task/result contracts
│   ├── planner.py                 # discovery, mapping, collision checks
│   ├── executor.py                # approved execution and durable resume
│   ├── validator.py               # plan-bound validation/evidence
│   └── orchestrator.py            # bounded Execute→Validate→Repair loop
├── pipelines/
│   ├── inventory.py               # strict source association
│   ├── extractor.py               # normalized ADO metadata
│   ├── planner.py                 # deterministic/ambiguous classification
│   ├── executor.py                # plan application
│   ├── llm.py                     # redacted, schema-bound provider boundary
│   ├── validator.py               # structural/semantic/security checks
│   └── pev.py                     # nested pipeline orchestration/evidence
├── core/
│   ├── migration_engine.py        # scoped migration side effects
│   ├── workflow_publisher.py      # idempotent review-PR delivery
│   └── ado_cleanup.py             # separately approved source cutover
├── clients/                       # paginated ADO/GitHub APIs and token manager
├── state/db.py                    # versioned SQLite state/audit store
├── phase/                         # risk segmentation and compatibility gates
└── reporting/                     # reports and post-migration checks
```

## Production invariants

The following are release-blocking invariants:

1. No live execution without an exact, integrity-checked plan approval.
2. No nondeterministic repository mapping and no unresolved target collision.
3. No LLM call on a deterministically convertible pipeline.
4. No LLM output enters executable YAML. Only an exact schema-v2
   content-addressed human approval in a newly approved plan can authorize an
   ambiguous fragment.
5. No pipeline completion from a local file or an unmerged pull request, and
   no pipeline evidence from a different plan/run/wave/source fingerprint.
6. No success inferred from an empty, partial, stale, or failed API response.
7. No secret value in plans, prompts, logs, evidence, URLs, or process arguments.
8. No concurrent executor for one plan and no task execution without an atomic
   lease claim.
9. No ADO cleanup without action authorization in the immutable plan, a
   completed run, ticket, full source-ref reconciliation, exact approved
   pipeline identities where applicable, and confirmation.
10. No rollback deletion without exact plan/run provenance and a matching
    immutable GitHub repository ID; no scope rollback without artifact-level
    provenance and an unchanged current fingerprint; no fake pipeline rollback
    through state.
11. No non-Git scope mutation from a payload other than the one verified
    against its plan-bound digest and counts.
12. No pipeline-overlay parity unless complete tree traversal proves that only
    the exact approved workflow and evidence blobs differ from source.
13. No target mutation outside the immutable plan capability manifest, without
    a current fencing token, or while an unresolved remote-operation barrier
    exists.
14. No destructive cleanup/rollback mutation without an atomically claimed
    one-shot request capability and a durable pre-dispatch action receipt.
15. No project-level unlinked work item duplicated across repositories; its
    plan-bound stable owner receives it once.
16. No repository checkout approval without explicit ref, token-secret name,
    access-canary evidence, immutable target ID, and resolved-SHA readback.
17. No cleanup while an approved pipeline runtime dependency—Actions policy,
    workflow action, online runner label, external checkout, secret name, or
    environment protection—cannot be read back under the target fence.
