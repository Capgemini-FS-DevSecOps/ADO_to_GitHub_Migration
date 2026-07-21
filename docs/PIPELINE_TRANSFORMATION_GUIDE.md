# Pipeline transformation guide — nested PEV

`ado2gh` converts ADO YAML, classic-build, and classic-release definitions with
a Planner–Executor–Validator loop. The enterprise path does not equate “a YAML
file was written” with success: conversion must be production-ready, pass local
security/semantic validation, receive human review in a pull request, and be
observed on the target default branch.

## Conversion lifecycle

```text
strict inventory → normalized metadata → deterministic plan
                                           │
                       ┌───────────────────┴──────────────────┐
                       ▼                                      ▼
              known mapping                          classified ambiguity
                       │                                      │
                       │                           redacted/schema-bound LLM
                       │                                      │
                       │                              proposal evidence
                       │                                      │
                       │                         exact schema-v2 approval
                       │                         + newly approved plan
                       │                                      │
                       └───────────────────┬──────────────────┘
                                           ▼
                         deterministic/approved candidate workflow
                                           ▼
                           independent deterministic validator
                                           ▼
                           evidence + review PR (never auto-merge)
                                           ▼
                             default-branch remote verification
```

If no ambiguity exists, no LLM call occurs. If an eligible ambiguity exists but
no provider is configured—or the provider refuses, returns low confidence, or
fails local checks—the pipeline is not production-ready. A successful proposal
also remains non-production until the exact fragment is manually approved in a
new plan.

## Source types

| ADO type | Deterministic input | Expected outcome |
|---|---|---|
| YAML pipeline | Source YAML plus definition metadata | Known triggers/tasks/conditions map directly; ambiguity is classified |
| Classic build | Phase/task metadata from the Build API | Known tasks map; missing semantics become review/failure evidence |
| Classic release | Release environments and resolved build artifacts | Deployment structure is planned; approvals, credentials, and unresolved artifact associations remain external gates |

Inventory associates each definition with a source repository using stable IDs.
A missing or conflicting association fails closed rather than using a fuzzy
repository guess. Planning records normalized inventory receipts, source/YAML
digests, and one inventory digest; execution rescans and refuses pipeline
source drift.

The approved set is immutable. Conversion/validation uses the plan's exact
pipeline IDs/types and `pipeline_filter`; it never substitutes whatever happens
to be in the latest mutable inventory table. Each accepted receipt is bound to
the organization plan/run/wave, conversion source fingerprint, approved
inventory digest, and individual pipeline-receipt digest.

## Deterministic mappings

Representative direct mappings include:

| ADO task | GitHub Actions form |
|---|---|
| `NodeTool@0` | `actions/setup-node@v4` |
| `UsePythonVersion@0` | `actions/setup-python@v5` |
| `UseDotNet@2` | `actions/setup-dotnet@v4` |
| `JavaToolInstaller@0` | `actions/setup-java@v4` |
| `GoTool@0` | `actions/setup-go@v5` |
| `Docker@2` | `docker/build-push-action@v5` |
| `AzureCLI@2` | `azure/CLI@v2` |
| `PublishBuildArtifacts@1` | `actions/upload-artifact@v4` |
| `DownloadBuildArtifacts@0` | `actions/download-artifact@v4` |
| `PublishTestResults@2` | `dorny/test-reporter@v1` |
| `CmdLine@2`, `Bash@3`, `PowerShell@2`, `Npm@1` | Validated `run` step with an explicit shell/command |
| `DotNetCoreCLI@2`, `Maven@4`, `Gradle@3`, `Terraform@0`, `HelmDeploy@0`, `Kubernetes@1` | Deterministically constructed and validated command step |

This table is not a guarantee that every use of a named task is equivalent.
Inputs, service connections, conditions, environment behavior, and task version
still affect the plan and validation result. Review the generated evidence.

Common deterministic condition mappings include:

| ADO condition | GitHub Actions expression |
|---|---|
| `succeeded()` | `success()` |
| `failed()` | `failure()` |
| `always()` | `always()` |
| `succeededOrFailed()` | `success() || failure()` |
| source branch equals `main` | `github.ref == 'refs/heads/main'` |
| build reason is pull request | `github.event_name == 'pull_request'` |

Only a recognized semantic pattern is deterministic. Unknown or nested
expressions are classified rather than copied blindly.

### Approved action catalog

With `require_pinned_action_sha: true` (the production default), generated
workflows replace the reviewed major reference with the following full commit
SHA. These pins were resolved from the publishers' official repositories on
2026-07-21; updating one is an explicit dependency-review and new-plan event.

| Reviewed reference | Emitted commit SHA |
|---|---|
| `actions/checkout@v4` | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-node@v4` | `49933ea5288caeca8642d1e84afbd3f7d6820020` |
| `actions/setup-python@v5` | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `actions/setup-dotnet@v4` | `67a3573c9a986a3f9c594539f4ab511d57bb3ce9` |
| `actions/setup-java@v4` | `c1e323688fd81a25caa38c78aa6df2d33d3e20d9` |
| `actions/setup-go@v5` | `40f1582b2485089dde7abd97c1529aa768e1baff` |
| `actions/upload-artifact@v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `actions/download-artifact@v4` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `actions/cache@v4` | `0057852bfaa89a56745cba8c7296529d2fc39830` |
| `docker/build-push-action@v5` | `ca052bb54ab0790a636c9b5f226502c73d547a25` |
| `azure/CLI@v2` | `d4515bc8e518874d3c814bf5c307a1cb08ec9b35` |
| `azure/webapps-deploy@v3` | `b686016b4de8820a23519503685d8d03fbfa03a6` |
| `azure/functions-action@v1` | `c9cc8ee93d1285e2a17312cc8357dc1775955272` |
| `dorny/test-reporter@v1` | `3eeb9fc888e82e8be2fb356bbeec2750231672bc` |

An organization can add or override reviewed pins:

```yaml
global:
  pipeline_conversion:
    require_pinned_action_sha: true
    action_pins:
      owner/reviewed-action@v2: "0123456789abcdef0123456789abcdef01234567"
```

Keys must be `owner/repository` or `owner/repository@ref`, and values must be
40 hexadecimal characters. The normalized catalog is embedded in the
organization plan policy; do not use an override to bypass publisher or supply
chain review.

## What the planner preserves

Where source evidence is sufficient, the plan covers:

- push, pull-request, schedule, and manual triggers;
- stages/jobs and dependency order;
- known conditions and failure behavior;
- non-secret variables;
- task inputs, scripts, working directories, and shells;
- environments and deployment jobs;
- artifacts and known tool setup;
- hosted runner images.

Custom ADO pools are not presumed equivalent to a GitHub runner. Runner labels,
installed software, networking, capacity, and trust boundaries require an
approved runner design.

## LLM boundary for ambiguity

Bind the non-secret provider identity in `migration.yaml`. These fields become
part of the immutable organization plan and cannot change between approval and
execution:

```yaml
global:
  pipeline_conversion:
    llm_provider: openai-responses
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_organization: org-example
    llm_project: proj-migrations
    llm_api_key_env: OPENAI_API_KEY
```

Only the credential value comes from the environment:

```bash
export OPENAI_API_KEY="..."
```

Legacy routing environment variables may repeat the approved provider, model,
base URL, organization, and project for deployment compatibility, but any
mismatch fails closed. The presence of `OPENAI_API_KEY` alone never opts a run
into LLM use.

The provider receives one bounded ambiguity and redacted context, not an entire
organization or credential set. Responses use strict structured output and are
validated again locally. A proposed step must contain exactly one allowed `run`
or an action pinned to a full 40-character commit SHA; enterprise action-catalog
policy still applies. Sensitive keys cannot receive literals, shells and object
keys are allow-listed, and destructive/exfiltration patterns are rejected.

LLM output is always a proposal, never production authorization. Generated
shell, action, and condition resolutions receive a `manual_review` finding even
after structural and security validation. A pinned/catalog-approved action
establishes supply-chain policy, not semantic equivalence to an unknown ADO
task. Promote a proposal only by creating a content-addressed approval for the
exact workflow step or condition and then planning a new immutable migration.

For each accepted or rejected decision, evidence records provider/model,
confidence, ambiguity identity, and prompt/response digests. It does not store
the API key or treat model rationale as proof.

## External requirements

These are not safe language-generation tasks and remain explicit human gates:

- secret values and service-connection credentials;
- OIDC/federated identity configuration;
- environment reviewers, wait timers, and deployment protections;
- self-hosted runner provisioning and trust policy;
- an ADO extension with no approved GitHub equivalent;
- a release artifact that cannot be uniquely associated with one repository;
- business decisions about schedules, production access, or destructive steps.

Secret values cannot be read through the supported ADO APIs. Use the generated
manifest to assign owners and provision them through approved tooling. Prefer
OIDC over long-lived cloud credentials.

### Content-addressed manual approvals

Configure `global.pipeline_conversion.manual_approval_manifest` with an inline
mapping or a JSON/YAML path relative to `migration.yaml`. The loader validates
schema **v2** and embeds the normalized manifest (including its digest and records) in the
immutable organization plan. The enterprise converter consumes that same
content; it never accepts a bare boolean callback as authorization.

The root object is exact: `schema_version: 2`, `manifest_digest`, and
`approvals`. Unknown keys or another schema version are rejected rather than
upgraded approximately. Both the manifest digest and each `approval_id` cover
canonical JSON content.

Each record has `approval_id`, `ambiguity_id`, `source_fingerprint`, `project`,
`repository`, `pipeline_id`, `pipeline_type`, `decision: approved`, `approver`,
`ticket`, `approved_at`, a typed `target_mapping`, and one or more evidence
records (`type`, `reference`, and `sha256:<64 hex>` digest). `approval_id` is
`approval-sha256:` plus the SHA-256 of canonical JSON for every other record
field. `ManualApprovalRecord.create(...)` can produce the canonical identifier.

Target mappings are allow-listed: external configuration, parity attestation,
runner label, repository checkout, a validated workflow step, or a validated
workflow condition. The mapping must match the ambiguity kind. Runner,
checkout, workflow-step, and workflow-condition mappings are applied before
deterministic validation; external configuration and parity records are
retained as audit evidence. An old source fingerprint, unknown ambiguity, wrong
pipeline/repository, incompatible mapping, changed record, or duplicate
approval is an error and cannot make a pipeline production-ready.

Schema v2 external-configuration records authorize expected resource identity,
not an assertion that provisioning succeeded. Repository-secret mappings carry
names only. Environment-protection mappings also require a
`configuration_digest` over the policy-bearing GitHub environment response.
Final validation re-reads secret names and environment configuration from
GitHub and fails on absence or drift; secret values are never requested.

A `repository_checkout` record must include the exact `owner/repository`, an
explicit `ref`, a GitHub token-secret name, and evidence with
`type: checkout-access-canary`. The canary proves the reviewed identity can read
that ref without placing a credential in the manifest. Execution records the
external repository's immutable ID and resolved commit SHA; validation and
cleanup re-resolve both and fail on drift.

```yaml
target_mapping:
  type: repository_checkout
  repository: approved-org/shared-build
  ref: refs/tags/v3.2.1
  token_secret: SHARED_BUILD_READ_TOKEN
evidence:
  - type: checkout-access-canary
    reference: CHG-12345/checks/checkout-canary
    digest: sha256:REPLACE_WITH_64_HEX
```

Use this operator workflow:

1. Run the pipeline conversion without a manual approval and inspect its
   redacted evidence for the exact `ambiguity_id` and `source_fingerprint`.
2. Review the source semantics and select one compatible typed target mapping.
3. Generate the record and manifest digest with the library; do not hand-write
   either content address:

```python
import yaml
from ado2gh.pipelines.approvals import (
    ManualApprovalManifest,
    ManualApprovalRecord,
)

record = ManualApprovalRecord.create(
    ambiguity_id="amb-REPLACE_FROM_EVIDENCE",
    source_fingerprint="sha256:REPLACE_WITH_64_HEX",
    project="Payments",
    repository="checkout",
    pipeline_id=42,
    pipeline_type="yaml",
    approver="reviewer@example.com",
    ticket="CHG-12345",
    approved_at="2026-07-21T12:00:00Z",
    target_mapping={"type": "runner", "runner_label": "approved-linux"},
    evidence=[{
        "type": "review-record",
        "reference": "CHG-12345",
        "digest": "sha256:REPLACE_WITH_64_HEX",
    }],
)
manifest = ManualApprovalManifest([record])
print(yaml.safe_dump(manifest.to_dict(), sort_keys=False))
```

4. Save the output and reference it relative to `migration.yaml`:

```yaml
global:
  pipeline_conversion:
    manual_approval_manifest: pipeline-manual-approvals.yaml
```

5. Run `agent plan` again, review the normalized manifest and new `plan_id`,
   and obtain a new approval before live execution. Adding an approval changes
   policy; it cannot be injected into or used to resume the old plan.

An LLM proposal follows exactly this promotion path. Copying model output into
a workflow, hand-authoring an approval ID, or resuming the proposal-producing
run with new content is not supported.

Valid target mapping types are `external_configuration`, `parity_attestation`,
`runner`, `repository_checkout`, `workflow_step`, and `workflow_condition`. The
ambiguity kind limits which type is compatible. A workflow step must contain
exactly one of `run` or `uses` and still passes the normal action, shell, secret,
and command validator. A workflow condition supplies the exact GitHub Actions
condition string for one `unsupported_condition` ambiguity and is checked for
size, residual ADO syntax, and immutable source/ambiguity binding.

## Validation policy

The independent validator checks:

- UTF-8 and size limits;
- duplicate YAML keys and root structure;
- a nonempty name, trigger, jobs, and valid job identifiers;
- dependency references/cycles and job/step limits;
- explicit least-privilege permissions;
- unsafe `pull_request_target` use;
- residual ADO macros, variables, and unsupported syntax;
- source-plan operation coverage;
- literal values assigned to sensitive names;
- remote download-to-shell, untrusted event data in scripts, destructive root
  commands, and secret exfiltration patterns;
- every content-approved ambiguous fragment, every LLM proposal as
  non-executable evidence, and every required external requirement.
- Actions enablement and selected/allowed-actions policy for every `uses`;
- availability of required labels on at least one online runner;
- exact external checkout repository identity, ref, and resolved SHA.

Policy can additionally require third-party actions to use full commit SHAs.
`production_ready` is false when any error or manual-review finding remains.
Warnings and findings are evidence, not comments embedded as executable
placeholders.

## Artifacts and evidence

For each pipeline, the converter writes an identity-safe workflow filename and
a JSON evidence artifact containing:

- source fingerprint and conversion plan ID;
- deterministic ruleset version and conversion mode;
- ambiguity classifications and source digests;
- LLM decision metadata/digests when used;
- accepted/rejected manual approval identifiers, mappings, tickets, and evidence;
- workflow digest and validator findings;
- `production_ready` outcome.

The durable delivery receipt also records the Git blob SHA for both the
workflow and evidence file. A completed conversion row from a different
plan/run/wave or source fingerprint cannot satisfy validation.

Raw source YAML is fetched just in time and is not stored in SQLite. Evidence
summarizes source locations and digests without copying full proprietary scripts
or secret values.

## Pull-request review and completion

The publisher uploads validated workflows to the configured review branch
(default `ado2gh/migrated-workflows`) and evidence under
`.ado2gh/pipeline-evidence/`. It creates or reuses one pull request and never
merges it.

Reviewers should compare triggers, dependencies, scripts, runners, permissions,
actions, artifacts, environments, and deployment behavior against ADO. After
secrets/OIDC and external protections are configured, test and merge through
normal branch protection.

Resume the same organization PEV run after merge. The publisher must observe
every exact workflow and evidence blob on the default branch before the
pipeline scope can complete. Repository validation then applies a two-baseline
proof: the delivery commit must be a strict descendant of the approved source
commit, and a complete recursive tree diff may contain only those exact
`.github/workflows/` and `.ado2gh/pipeline-evidence/` artifacts. All unaffected
branches and tags remain exact. Local-only output and an open PR remain
`needs_review`.

Completion additionally verifies that Actions is enabled, expected workflows
are active, every action reference is permitted by the selected Actions policy,
required repository-secret names exist, required runner labels are online,
external checkout IDs/refs/SHAs remain exact, and each approved environment
protection digest matches live GitHub state. Cleanup captures and rechecks the
same runtime dependency set while holding the target fence. An approval manifest
or merged file without that readback is insufficient.

## Readiness assessment

The separate assessment report helps estimate migration effort:

```bash
ado2gh pipeline-readiness \
  --config migration.yaml \
  --output output/pipeline_readiness.csv
```

`auto`, `assisted`, and `manual` are planning estimates, not execution results.
Only nested PEV validation and remote verification establish completion.
