# Contract: Public-Surface Freeze

**Feature**: 013-clean-code-arch-remediation · **Date**: 2026-09-07

This feature exposes **no new external interface**. Its contract is the opposite: the
externally observable surface at the starting commit is frozen (spec FR-006) and enforced
by a snapshot test (research R8). Everything not listed here is internal and may change.

## Frozen surfaces (sizes re-measured 2026-09-08)

| Surface | Size | Enumerated by | Guard |
|---------|------|---------------|-------|
| CLI command tree (names, flags, option types, defaults) | 96 snapshot entries: 21 commands, 2 sub-groups plus the root group, and 72 parameter lines | walking the Click group `ado2gh.cli.main:cli` | `tests/contract/test_public_surface_snapshot.py` + existing CLI tests |
| HTTP routes (method + path) on both FastAPI apps | 148 snapshot entries — one per method — across 121 distinct app-and-path pairs; 115 accelerator, 33 agent | `app.routes` of `services.accelerator_api.main` and `services.agent.main` | snapshot test |
| HTTP payload and response shapes | as covered today | existing `tests/contract/*` (14 modules) | unchanged expectations (SC-002) |
| Environment variable names | 68 | `grep` over `ado2gh/ services/ apps/migration-ui/src` | snapshot test |
| Persisted schema: table names and columns | 25 tables | `CREATE TABLE` statements in `ado2gh/state/` | snapshot test (names); existing state tests (columns) |
| Inter-service message shapes (accelerator ↔ agent, SSE event types) | as covered today | `tests/contract/test_agent_interface_contracts.py`, `test_langgraph_contracts.py` | unchanged expectations |
| Docker/compose service names, ports 3000/8080/8090 | 3 services | compose files | unchanged files |

*Note on the figures.* The sizes first recorded here on 2026-09-07 — 131 HTTP routes and 52
environment variables — were written before the snapshot generator existed and do not match
the committed snapshot. The table above now states what the snapshot actually holds,
measured on 2026-09-08 by loading `tests/contract/public_surface_snapshot.json` and counting
each key, then splitting `http_routes` by the leading app name, by HTTP method, and by
distinct app-and-path pair. The committed snapshot is authoritative; the earlier figures are
not reproducible from it by any counting and should be disregarded. Two things explain most
of the route gap: the snapshot records **one line per method**, so a path answering several
verbs contributes several lines (148 lines over 121 distinct paths), and it includes the 8
HEAD routes Starlette adds automatically alongside GET. The `db_tables` figure of 25 was
correct and is unchanged. The CLI row was correct in substance — 21 commands, 2 sub-groups
and the root group — but omitted the 72 parameter lines that make up the rest of the 96
entries.

**Explicitly not frozen** (clarified 2026-09-07; the frozen list above is exhaustive):

- Agent tool names and parameter schemas exposed to the LLM (`StructuredTool`
  definitions in `migration_agent/tools/`). Changing one requires updating, in the same
  change, every reference in `ado2gh/agents/migration_agent/prompts/*.md`, `tests/`, and
  `apps/migration-ui/src` (20 files reference tool names today) and passing `tests/agent/`.
- The package's importable Python API — module paths and public function/class names.
  Renames and moves leave **no** compatibility aliases, re-export shims, or
  `DeprecationWarning` wrappers (Principle III "removed entirely" branch); the structural
  changelog is the record.

## Snapshot test contract

`tests/contract/test_public_surface_snapshot.py`

- Builds the four lists at test time (same enumeration as the table above), sorts and
  de-duplicates them, and compares to `tests/contract/public_surface_snapshot.json`.
- Fails with a unified diff naming the added/removed entries.
- The snapshot is created **before** cleanup increment 1 and committed on its own.
- The snapshot may change only in a commit that also (a) references an approved
  `GAP-NNN` whose `contract_change` is true, (b) is listed in `plan.md` § Approved
  contract changes, and (c) adds a migration note to `docs/MIGRATION_RUNBOOK.md` or the
  affected doc.

## Boundary conversion contract for `ExecutionMode`

External `dry_run` booleans stay booleans:

| Boundary | Shape stays | Conversion point |
|----------|-------------|------------------|
| CLI `--dry-run` flags | `is_flag=True` | inside the Click handler, first line |
| HTTP `dry_run` request fields | `bool` in the Pydantic model | in the route handler before calling the SDK |
| Persisted `dry_run` columns / JSON | `0/1` or `true/false` | in the state-layer read/write methods |
| Agent execution-mode endpoint (`PATCH /v1/sessions/{id}/execution-mode`) | existing string values | unchanged |

No default flips: wherever `dry_run=True` was the default, `ExecutionMode.DRY_RUN` is.

## Approved contract changes

Any gap fix needing a public-contract change is appended here **and** to `plan.md`
§ Approved contract changes before it is applied (FR-024). A change is listed in this
section only once the operator has approved it explicitly; the changes the operator signed off
by instruction on 2026-09-13 are recorded in § Contract changes approved by operator
instruction, 2026-09-13 below.

### 1. Live execution requires an authenticated approver — GAP-002, GAP-005

**Approved by the operator on 2026-09-08** ("Approve as-is"), on the change as it stands
in `ado2gh/api/platform_rbac.py` (`require_approve_live_execution`,
`operator_requires_live_approval`) and `ado2gh/agents/migration_agent/policies.py`
(`can_execute_live_without_approval`, `session_requires_live_approval`).

**What changes for an operator.** Until now the live-execution approval gate asked
whether authentication was switched on before it asked anything else, and stood down
whenever `ADO2GH_AUTH_ENABLED` was unset — which is the shipped default. A request
carrying no signed-in user could therefore start a live migration, an irreversible write
to GitHub, with no human ever approving it. From this build onwards live execution is
treated as a safety gate rather than an identity gate: it stays armed regardless of what
`ADO2GH_AUTH_ENABLED` says. Running anything live now requires **both**
`ADO2GH_AUTH_ENABLED=true` **and** a signed-in user holding the
`can_approve_live_execution` capability — ADMIN or APPROVER in today's role table. A live
request that carries no identity at all is refused with **401 Not authenticated** where it
previously went ahead. A live request from a signed-in user who can operate but cannot
approve is routed into the live-approval queue instead of running immediately; because the
check is now derived from the capability rather than from a role name, that correctly
includes COORDINATOR, which used to slip past the OPERATOR-only comparison.

**Dry-run is unaffected.** Both guards return before they reach the identity check when
the request or session is a dry run. An auth-disabled local, CI or demo environment keeps
working exactly as before for everything reversible: discovery, planning, dry-run
execution, validation, reporting and every read path. Only a genuine live run is refused.

**Every other guard is unchanged.** `require_read`, `require_operate`,
`require_manage_models` and the rest keep their `auth_enabled()` short-circuit and remain
permissive with authentication off. Only the two live-execution guards became
identity-independent.

**Migration note — deployments running with `ADO2GH_AUTH_ENABLED=false`.** Before
upgrading, work out which of these two describes your deployment.

- *You never run live from it* (local development, CI, demos, dry-run evaluation). There
  is nothing to do. Every dry-run path behaves exactly as it did.
- *You do run live from it.* You must set `ADO2GH_AUTH_ENABLED=true` and have at least one
  ADMIN or APPROVER account created **before** you upgrade, and sign in as that user to
  start or approve live runs. If you upgrade without doing this, a live migration that ran
  successfully yesterday will not run today: the accelerator answers **401 Not
  authenticated**, the agent refuses to leave dry-run, and no repository is touched. The
  failure mode is safe — nothing migrates halfway — but the run simply does not happen.
  Unattended or scripted live runs against an auth-disabled deployment stop working
  altogether and have to be re-pointed at an authenticated session.

While you are turning authentication on, set `ADO2GH_INTERNAL_TOKEN` in the same pass: with
auth enabled, the agent's `/v1/internal/` approval-resume routes now fail closed without it
(see GAP-003 in the section below).

**Snapshot impact: none.** No route path, CLI command, environment variable name or table
name changes — only the authorisation outcome of routes that already existed. The four
frozen keys are untouched by this change; the snapshot test confirms it.

### 2. `/v1/settings/llm-models/catalog` becomes a POST — GAP-012

**Approved by the operator on 2026-09-08.** `services/accelerator_api/routes/settings_routes.py`
changes `get_llm_catalog` from `@router.get`, taking `provider`, `api_key` and `base_url` as
query parameters, to `@router.post`, reading the same three fields from a JSON body. The
shape now matches the sibling `POST /v1/settings/llm-models/validate`.

**Why.** The provider API key was travelling in a URL query string. A URL is the least
private part of an HTTP request: it is written to browser history, captured in HAR exports,
and recorded in the access log of every proxy on the path, in each case in clear text and
usually with a far longer retention than anyone would choose for a credential. That is
GAP-012 (CWE-598). Moving the credential into the request body removes it from all three.

**What changes for a caller.** A client still issuing
`GET /v1/settings/llm-models/catalog?provider=...&api_key=...` no longer reaches the handler
and gets **404** back. It must switch to `POST /v1/settings/llm-models/catalog` with a JSON
body of `{"provider": ..., "api_key": ..., "base_url": ...}`. The response shape is
unchanged, and the `manage_models` capability requirement is unchanged. The migration
console was updated in the same change — `apps/migration-ui/src/lib/llmSettings.ts` now
issues the POST — so the in-repo caller needs no further action. Any external script or
saved request collection that drives the catalog endpoint does.

**Snapshot impact: yes, and this is the only change in the working tree that drifts it.**
The drift is confined to `http_routes`: `accelerator GET /v1/settings/llm-models/catalog` is
removed, `accelerator POST /v1/settings/llm-models/catalog` is added. With this approval the
snapshot edit is **authorised**, and T040 applies it in the same commit as the fix. The exact
edit is recorded under § Snapshot edit authorised for T040 at the end of this document.

### 3. Forcing past a blocked phase gate becomes a two-step CLI workflow — GAP-009

**Approved by the operator on 2026-09-08**, including the explicit decision **not** to add a
`--reason` flag to `phase run`.

**Why there is no new flag.** `cli_commands` is a frozen surface, and adding an option to
`phase run` would drift it. The operator chose to keep the CLI surface clean and accept a
two-step workflow instead. This is a deliberate decision, not an oversight: it makes
overriding a gate its own separately audited act rather than a side effect of running a
phase. Someone reading the audit log afterwards sees a named person overriding a specific
gate for a stated reason, at a recorded time, rather than a `--force` buried in the flags of
a migration run.

**What changed underneath.** `ado2gh/api/accelerator.py` now always evaluates the
prior-phase gate; `force` escalates it through the audited `PhaseGateChecker.override` path
rather than skipping the check. `ado2gh/api/contracts.py` adds
`override_reason: str = ""` to `PhaseRunRequest` to carry the justification, and
`ado2gh/cli/phase.py` passes an empty string because the CLI has no flag to fill it.

**The operator workflow.** To get past a blocking gate from the command line:

```bash
ado2gh phase gate-check --phase <prior-phase> --override --reason "why this is acceptable"
ado2gh phase run --phase <next-phase> --config migration_phase.yaml
```

The first command records the override against the prior phase with its reason; the second
then finds the gate satisfied and proceeds normally.

*Migration note.* Anyone whose runbook currently uses `ado2gh phase run --force` to push past
a red gate must change it to the two-step form above. `--force` on its own now **fails
closed**: the run is refused with `Gate blocked for prior phase <name>: forcing past it
requires override_reason`, and no migration starts. The refusal is safe — nothing runs
partially — but an unattended pipeline that relied on `--force` will stop at that point until
the override is recorded. `--force` remains meaningful where the prior gate is not blocking.

**Snapshot impact: none.** No CLI option was added, removed or retyped, so `cli_commands`
does not drift, and `override_reason` is a request-model field, which is not one of the four
frozen keys. Confirmed by the snapshot test.

### 4. Pipeline-run request bodies: `agent_live_approved` removed, unknown fields rejected — GAP-004

**Approved by the operator on 2026-09-08**, as two decisions taken after the implementing
agent finished and measured the result. This entry describes the change in its final,
landed form and supersedes the earlier provisional description of it.

**Why.** `agent_live_approved` was a plain boolean in the request body by which a caller
asserted its own live authority, and it disabled the approval gate before the caller's
actual capability was ever consulted. Live authority is now derived only from server-side
state: the authenticated user's role, or an approved `LiveApprovalStore` row. The field is
gone from `ado2gh/api/contracts.py` and is no longer read anywhere.

**Decision C — unknown fields are rejected with 422.** `PipelineRunStartRequest` now carries
`model_config = ConfigDict(extra="forbid")`. Dropping the field silently would have left
every existing caller believing it was still skipping the queue while the server quietly
ignored it, which is a worse failure than a loud one. A client that still posts
`agent_live_approved` to `POST /v1/pipeline/runs` now receives **422 Unprocessable Entity**,
with `detail[].loc == ["body", "agent_live_approved"]` and `type: "extra_forbidden"` —
measured, not inferred.

Note the breadth, because it is an ongoing consequence rather than a one-off: `extra="forbid"`
rejects **any** field this model does not declare, not only `agent_live_approved`. Callers
that were padding the body with extra keys the server used to discard — a client-side
correlation id, a leftover field from an older release — will start receiving 422 as well.
This is the intended posture for a route that governs live execution, but it means every
future field addition to this endpoint is a coordinated change between server and callers.

**Decision D — the empty model is deleted.** `PipelineRunStartApprovedRequest` contained
nothing but the removed field, so it has been deleted outright, with no alias, shim or
deprecated stand-in. `POST /v1/pipeline/runs/{run_id}/start` now declares **no body
parameter at all**: whether a parked run may go live is decided entirely from server-side
state, so there is nothing for a caller to send. Consequently `{}`, a body still carrying
`agent_live_approved`, and no body whatsoever are all treated identically, and **none of them
is rejected**. Decision C does not apply to this endpoint — there is no model left to forbid
extras on. A caller that still sends the old field here keeps working and simply has no
effect, which is the correct outcome for a route that has nothing to validate.

**One field was added in the same change.** `override_reason: str = ""` on
`PipelineRunStartRequest`. This was not cosmetic and it was not optional: the console had
already begun sending `override_reason` as the GAP-009 gate justification, so under
`extra="forbid"` every console-initiated pipeline run would have returned 422. The field is
optional, defaulted and backward-compatible; a caller that omits it is unaffected.

`override_reason` is deliberately **excluded from `PipelineRun.to_dict()`**, so it does not
appear in the run response. This is a mitigation, and it is worth stating why rather than
leaving it to be discovered: the value is free operator text, and it is redacted only at the
point it is persisted — `redact_payload` in `ado2gh/api/pipeline_steps.py`. Echoing the
in-memory value back in the run response would return text that has not been through
redaction, which is exactly what CA-003 forbids.

That coupling is real and permanent, and a future maintainer needs to know about it *before*
adding the next field: any field added to `PipelineRunStartRequest` that can carry free text
or a secret must be checked against `to_dict()` and against the redaction point, and a
decision recorded on whether it is safe to echo in the run response. Redaction lives on the
persist path, not on the response path; nothing in the type system enforces it.

**Snapshot impact: none.** Request-model fields, model deletions and validation strictness
are not among the four frozen keys, and no route path, method, CLI command, environment
variable or table name changed. Confirmed by the snapshot test.

## Contract changes approved by operator instruction, 2026-09-13

Everything in this section is present in the working tree and was **approved by operator
instruction on 2026-09-13** — the operator's "Remediate all findings" answer, recorded in
`operator-decisions.md` § 13 (entries 5–8), § 1 (entry 9) and § 6 (entry 12). These entries keep
a section of their own only so the sign-off date stays visible; they carry exactly the same
weight as § Approved contract changes above, and each carries the number of the matching
`plan.md` § Approved contract changes entry — except entry 9, which the GAP-018 batch has still
to append to `plan.md` under that number.

| # | Change | GAP | Frozen key affected | Snapshot drifts? | Operator sign-off |
|---|--------|-----|---------------------|------------------|-------------------|
| 5 | Nine `/v1/migrate/*` routes gain a live-execution guard | GAP-007 | none (paths unchanged) | No | **Yes — 2026-09-13** |
| 6 | GitHub write proxy requires `can_approve_live_execution` | GAP-008 | none (paths unchanged) | No | **Yes — 2026-09-13** |
| 7 | Agent `/v1/internal/` fails closed without `ADO2GH_INTERNAL_TOKEN` | GAP-003 | `env_vars` — already frozen, no drift | No | **Yes — 2026-09-13** |
| 8 | `phase gate-check --override` rejects an empty `--reason` | GAP-017 | `cli_commands` — flags unchanged, no drift | No | **Yes — 2026-09-13** |
| 9 | `run`, `phase run` and `ado-cleanup` default to dry-run; live needs `--live` | GAP-018 | `cli_commands` — three option lines retyped | Yes | **Yes — 2026-09-13** |
| 12 | `GET /v1/settings/cloud-credentials` no longer accepts `scan` | CA-004 | none (path and method unchanged) | No | **Yes — 2026-09-13** |

Items 5, 6 and 7 are security fixes at the heart of this feature, so reverting them was never
on the table. They are as operator-visible as the approved live-gate change, and each needs a
migration note as good as the approved ones — which is what the entries below give them.

### 5. Nine `/v1/migrate/*` routes gain a live-execution guard — GAP-007

`services/accelerator_api/routes/migrate_guard.py` (new) hangs a single
`guard_live_migration` dependency off the `/v1/migrate` router, so every route on it is
covered, including any added later. Route paths, methods and payloads are unchanged, so
there is no snapshot drift; what changes is who gets an answer.

Affected endpoints: all nine `/v1/migrate/*` feature routes on the accelerator (:8080) —
the boards, wiki, artifacts, test-plan, secret-provision, service-connection and sibling
migration features. What each caller now sees on a **live** request, meaning a body whose
`dry_run` is explicitly `false`:

| Caller | Result | Status | Body |
|--------|--------|--------|------|
| Any caller, dry-run request (or `dry_run` omitted) | Unchanged, passes straight through | as before | as before |
| No identity on the request | Refused | **401** | `{"detail": "Not authenticated"}` |
| Signed in, can operate but not approve live execution (OPERATOR, COORDINATOR) | Parked in the shared live-approval queue; the work does **not** start | **403** | `{"detail": {"code": "awaiting_approval", "approval_id": "<id>"}}` — the `approval_id` is new and identifies the queued request |
| Signed in and holds `can_approve_live_execution` (ADMIN, APPROVER) | Proceeds, audited before any irreversible work | as before | as before |
| Any caller whose request already has an approved queue entry | Proceeds, audited | as before | as before |

*Migration note.* A dry-run client needs no change. A client that drove these routes live
without authenticating stops working entirely and must be moved onto an authenticated
session, exactly as described in approved change 1. A client authenticated as OPERATOR or
COORDINATOR keeps working but becomes asynchronous: it must now expect the 403
`awaiting_approval` response, surface the `approval_id` to a human approver, and retry once
the approval is granted. Treating that 403 as a hard failure is the most likely way this
change breaks an existing integration.

**Status: approved by operator instruction, 2026-09-13.**

### 6. GitHub write proxy requires `can_approve_live_execution` — GAP-008

`services/accelerator_api/routes/proxy_routes.py` splits the GitHub proxy by verb. The
docstring described it as read-only, but it forwards the executor's write verbs too, up to
`DELETE /repos/{org}/{repo}`, and it was guarded only by `require_operate` and left no audit
trail. Paths are unchanged, so no snapshot drift.

Affected endpoints: the accelerator's GitHub proxy routes (:8080). The ADO proxy is
genuinely read-only and is untouched.

| Caller | Verb | Result | Status |
|--------|------|--------|--------|
| Anyone with the operate permission | GET | Unchanged | as before |
| No identity on the request | POST, PATCH, PUT, DELETE | Refused | **401** `{"detail": "Not authenticated"}` |
| Signed in, can operate but not approve (OPERATOR, COORDINATOR) | POST, PATCH, PUT, DELETE | Refused | **403** `{"detail": "Missing capability: can_approve_live_execution"}` |
| Signed in and holds `can_approve_live_execution` (ADMIN, APPROVER) | POST, PATCH, PUT, DELETE | Proceeds, and an audit event recording verb, endpoint and actor is written **before** the request is forwarded | as before |

Unlike the migrate routes, there is no approval queue on this path: a write is either
permitted outright or refused. There is no `approval_id` to wait on.

*Migration note.* Any tool or script that performed GitHub writes through the proxy under an
OPERATOR identity must be re-run under an ADMIN or APPROVER identity, or moved onto the
`/v1/migrate/*` routes, which do offer the approval queue. Reads are unaffected. The audit
event records the verb, the endpoint and the actor only — never the forwarded body or the
platform's own `Authorization` header (CA-003).

**Status: approved by operator instruction, 2026-09-13.**

### 7. Agent `/v1/internal/` fails closed without `ADO2GH_INTERNAL_TOKEN` — GAP-003

`services/agent/main.py` previously let the whole `/v1/internal/` range through
unauthenticated whenever `ADO2GH_INTERNAL_TOKEN` was unset — which, being the shipped
default, left the published agent port one request away from an unauthenticated
live-execution resume. The token must now be both set and matching.
`ADO2GH_INTERNAL_TOKEN` is **already in the frozen `env_vars` list**, so there is no
snapshot drift — verified against `tests/contract/public_surface_snapshot.json`. What
changed is the variable's meaning, not its existence: it is now mandatory wherever
`ADO2GH_AUTH_ENABLED=true`.

Affected endpoints: `POST /v1/internal/sessions/{id}/resume-live` and
`POST /v1/internal/sessions/{id}/deny-live` on the agent (:8090). The only legitimate caller
is the accelerator, service-to-service; these routes carry no operator session cookie, which
is why they are guarded by a shared secret rather than by RBAC.

| Caller | Condition | Result | Status |
|--------|-----------|--------|--------|
| Any caller | `ADO2GH_AUTH_ENABLED=false` | Unchanged — the agent skips `/v1/` authentication entirely, so the token is never consulted | as before |
| Accelerator sending a matching `x-ado2gh-internal-token` header | auth on, token set on both sides | Proceeds | as before |
| Accelerator, token unset on either side, or the two values differ | auth on | Refused | **401** |
| Any other caller reaching the port directly with no token | auth on | Refused | **401** — previously this was **allowed through** |

*Migration note.* Any deployment running with authentication on must set the *same*
`ADO2GH_INTERNAL_TOKEN` on both the accelerator and the agent before upgrading. Unset or
mismatched, the agent answers 401 to the accelerator's `resume-live` and `deny-live` calls
and live-migration approvals never resume: an operator approves a migration in the console,
sees it accepted, and the run silently never starts. Generate the value with
`python -c "import secrets; print(secrets.token_hex(32))"`. `docker-compose.prod.yml` now
refuses to start without the variable and `deploy/kubernetes/secret.yaml.example` carries
it; the development `docker-compose.yml` leaves it empty, which is harmless there because
that stack also turns authentication off, so the middleware never reaches the check.

**Status: approved by operator instruction, 2026-09-13.**

### 8. `phase gate-check --override` rejects an empty `--reason` — GAP-017

`ado2gh/cli/phase.py` now routes `--override` through `PhaseGateChecker.override` and raises
a `click.UsageError` when `--reason` is empty. No option was added, removed or retyped, so
`cli_commands` does not drift — confirmed by the snapshot test. The behaviour change is that
`--override` with no reason, which previously raised a `TypeError` and wrote no gate row at
all, is now a clean usage error.

**Status: approved by operator instruction, 2026-09-13.**

### 9. `run`, `phase run` and `ado-cleanup` default to dry-run — GAP-018

`ado2gh/cli/migration.py`, `ado2gh/cli/phase.py` and `ado2gh/cli/misc.py` declared `--dry-run` as
`is_flag=True, default=False`, so each of the three commands migrated for real unless the
operator opted out, and none of them prompted for confirmation. On the CLI path that default was
the only guard there was: the approval-token check in `ado2gh/api/accelerator.py` fires only when
a request quotes a `live_approval_id`, and the CLI never sends one. Each option is now the
boolean pair `--dry-run/--live` with `default=True`. `--dry-run` keeps its name and its meaning
and `ExecutionMode.from_dry_run(dry_run=...)` still converts at the boundary, so no handler body
changed. Applied in `b9eb89c`.

*Migration note.* Any unattended script that invokes `ado2gh run`, `ado2gh phase run` or
`ado2gh ado-cleanup` without `--dry-run` and expected a real migration now performs a dry run and
writes nothing. It must be changed to pass `--live`. The failure mode is safe — nothing migrates
halfway — but the run does not happen. A script that already passes `--dry-run` is unaffected: the
flag still parses and still means what it meant.

**Snapshot impact: yes — three retyped `cli_commands` lines.** For each of `run`, `phase run` and
`ado-cleanup`, `param dry_run` moves from `opts=--dry-run :: type=boolean :: default=False` to
`opts=--dry-run/--live :: type=boolean :: default=True`. No entry is added or removed, so
`cli_commands` stays at **96** and the other three frozen keys are untouched. The snapshot edit
landed in `b9eb89c`, the same commit as the fix, per the snapshot-test contract above. It is a
second authorised drift, later than and independent of the GAP-012 one; § Snapshot edit
authorised for T040 below describes the GAP-012 edit only and is not a statement about the tree
after `b9eb89c`.

**Status: approved by operator instruction, 2026-09-13** (`operator-decisions.md` § 1, option A).

### 12. `GET /v1/settings/cloud-credentials` no longer accepts `scan` — CA-004

The listing endpoint accepted `?scan=true` to re-probe the host for cloud credential sources
before answering. It performed exactly the probe that `POST /v1/settings/cloud-credentials/scan`
performs, but wrote no audit record — so the console's page load, `fetchCloudCredentials(true)`
on every render and every refetch, was probing the host outside the audit trail (CA-004). The
parameter and its branch are removed from `services/accelerator_api/routes/settings_routes.py`
with no shim (FR-006a); `apps/migration-ui/src/lib/cloudCredentials.ts` drops the argument, and
the console's existing Rescan button, already wired to the audited POST, becomes the only way to
re-probe.

*Migration note.* A client still sending `?scan=true` gets the listing without a probe — FastAPI
ignores an undeclared query parameter, so nothing errors, but the response reflects the last
recorded detection rather than a fresh one. Any caller that relied on the flag must issue
`POST /v1/settings/cloud-credentials/scan` first, which needs the same `can_manage_models`
capability and additionally writes a `cloud_credentials.scanned` audit event.

**Snapshot impact: none.** The route path and method are unchanged; query parameters are not
recorded in `http_routes` and are not among the four frozen keys. Verified by
`tests/contract/test_public_surface_snapshot.py`; the removal is covered by
`tests/contract/test_cloud_credentials_contracts.py::test_listing_never_probes_the_host`.

**Status: approved by operator instruction, 2026-09-13** (`operator-decisions.md` § 6).

## Snapshot edit authorised for T040

Approved change 2 (GAP-012) is the **only** drift the snapshot test reports against the
current working tree, and the operator approved it on 2026-09-08, so this edit is authorised
and T040 applies it in the same commit as the fix. Nothing else in the working tree touches
the four frozen keys.

In `tests/contract/public_surface_snapshot.json`, inside the `http_routes` list:

- **Delete** `    "accelerator GET /v1/settings/llm-models/catalog",` — line 232 of the
  committed file.
- **Insert** `    "accelerator POST /v1/settings/llm-models/catalog",` immediately after
  `    "accelerator POST /v1/settings/llm-models",` (line 282 before the deletion, 281
  after) and immediately before `    "accelerator POST /v1/settings/llm-models/validate",`.
  That is the position sort order requires.

The `http_routes` entry count stays at **148**; one line out, one line in. No other key
changes. Regenerating with `UPDATE_SURFACE_SNAPSHOT=1` produces exactly this edit and
nothing else — but only while GAP-012 is the sole applied contract change, so re-run the
test first and stop if it reports anything beyond these two lines. Cite GAP-012 in the
commit message, per the snapshot-test contract above.
