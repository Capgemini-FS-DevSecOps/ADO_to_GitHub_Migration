# Architecture Gap Register

**Feature**: 013-clean-code-arch-remediation · **Assessment date**: 2026-09-08
**Baseline commit**: `9c83a29` (post-stabilisation tree, FR-015) plus the uncommitted
working-tree changes listed in `plan.md`; every `path:line` below refers to that tree.

> **Identifiers.** Sequential `GAP-NNN` ids were assigned at T035 in the order the entries
> appear below. Each heading and each cross-reference carries the sequential id followed by
> the per-component placeholder (`GAP-<COMPONENT>-NN`) it replaced, so references written
> before T035 stay resolvable. Ids are never reused. `GAP-TOOL-05` is a tombstone: it was
> merged into GAP-021 (GAP-ARCH-01), holds no sequential id, and is not counted anywhere.

## Summary

54 gaps recorded across the thirteen components of FR-016 / FR-016a. Sequential ids were
assigned at T035 in file order and are never reused (GAP-051 and GAP-052 were appended on 2026-09-08, GAP-053 on 2026-09-09, and GAP-054 on 2026-09-12, each with the next free id); the per-component placeholder each id
replaced is kept in parentheses so earlier cross-references stay resolvable.
Severities are as rated by the assessment passes (T022-T034); the review pass (T036) may
contest a critical or high rating, and any change it produces is recorded in the Disputes
table below rather than by re-rating an entry here.

| Severity | Count |
|----------|-------|
| critical | 16 |
| high | 22 |
| medium | 11 |
| low | 5 |
| **total** | **54** |

All 16 critical entries name a critical_test letter (a)-(e) per FR-019 / FR-020, and every
one of the 54 entries carries at least one path:line citation or a reproduction command
(FR-020). Critical and high entries, in sequential id order:

| Id | Title | Status |
|----|-------|--------|
| GAP-001 (GAP-STAB-01) | Red test suite plus a coverage gate that could not fail | remediated |
| GAP-002 (GAP-AUTH-01) | Live-execution approval is inert in the default configuration | remediated |
| GAP-003 (GAP-AUTH-02) | Agent internal resume-live/deny-live routes have no authorization in any shipped configuration | remediated |
| GAP-004 (GAP-AUTH-04) | Pipeline-run routes let the client self-certify `agent_live_approved` to skip the approval queue | remediated |
| GAP-005 (GAP-AUTH-05) | `operator_requires_live_approval` gates only the OPERATOR role; COORDINATOR bypasses approval entirely | remediated |
| GAP-006 (GAP-AGT-01) | `confirm-live` self-escalates an operator to live execution, and plan-level `dry_run` silently overrides the session gate | remediated |
| GAP-007 (GAP-ACC-01) | Nine `/v1/migrate/*` feature routes perform live mutations with no RBAC, approval, or audit | remediated |
| GAP-008 (GAP-ACC-02) | GitHub write proxy documented as read-only, guarded only by `require_operate`, unaudited | remediated |
| GAP-009 (GAP-CLI-02) | `phase run --force` skips the gate with no reason and no audit record, while the audited override path is never called | remediated |
| GAP-010 (GAP-TOKEN-01) | `redact_payload` misses ADO PAT, Bearer, and prefixed key-name shapes before persisting audit events | remediated |
| GAP-011 (GAP-AGT-02) | `mask_secrets` is wired only into the audit bridge; chat, SSE, and persisted session messages are unmasked | remediated |
| GAP-012 (GAP-UI-01) | LLM provider API key is transmitted in a URL query string | remediated |
| GAP-013 (GAP-ENG-01) | Workflow-integrity check is structurally incapable of reporting FAIL | remediated |
| GAP-014 (GAP-ENG-07) | FR-036 concurrency guard fails open when both of its own checks throw | remediated |
| GAP-015 (GAP-SEAM-01) | Work-item producer emits `scope`/`blocker`; all three consumers read `scopes`/`blocked_reasons` | remediated (residual recorded) |
| GAP-016 (GAP-PIPE-04) | Live workflow-push approval gate is hardcoded satisfied by its only production caller and unsatisfiable from the CLI | remediated |
| GAP-017 (GAP-CLI-01) | `phase gate-check` always raises TypeError; no gate row can be written through the CLI | remediated |
| GAP-018 (GAP-CLI-03) | Migration commands execute live by default with no confirmation, and a declared approval token is discarded | deferred (pending operator decision) |
| GAP-019 (GAP-AUTH-03) | `/sessions/{id}/provision` and `/remediate` take no `Request` and trust a client-supplied `actor` | open |
| GAP-020 (GAP-AUTH-07) | Session cookie omits `Secure`; `max_age` hardcoded instead of reading `SESSION_HOURS` | open |
| GAP-021 (GAP-ARCH-01) | Package layering is inverted in at least nine places, masked by deferred imports (G-seed 3) | remediated (residual recorded) |
| GAP-022 (GAP-TOOL-01) | Coverage ratchet stands at 56% against a constitutional floor of 85% | deferred |
| GAP-023 (GAP-TOOL-02) | mypy is configured so that it cannot fail, and never reaches most of the package | remediated |
| GAP-024 (GAP-UI-02) | Boolean `recommended_value` is stringified server-side and re-read as truthy in the browser, inverting the `confirm_execute` safe default | open |
| GAP-025 (GAP-UI-03) | All 46 component and page files are untested and no linter is configured | remediated |
| GAP-026 (GAP-PHASE-03) | `execute_phase` has no test coverage | remediated |
| GAP-027 (GAP-ENG-04) | "Idempotent — skips completed scopes" is false; per-handler idempotency is ad hoc | remediated |
| GAP-028 (GAP-STATE-01) | `DynamoDBJobStore` can silently double-claim or duplicate a job under concurrency | remediated |
| GAP-029 (GAP-STATE-02) | `create_state_db()` silently discards `--db` under a non-default backend and crashes on a backend its own config validates | remediated |
| GAP-030 (GAP-TOKEN-02) | GitHub token is passed in subprocess argv for the mirror strategy | remediated |
| GAP-031 (GAP-PIPE-01) | ADO variable-group variables are captured as metadata and never reach the generated workflow or its notes | open |
| GAP-032 (GAP-PIPE-02) | The branch that keeps secret values out of generated YAML has no test | remediated |
| GAP-033 (GAP-DEPLOY-02) | Scheduled migration workflow pushes to GitHub on a cron with no environment approval gate | remediated |
| GAP-034 (GAP-DEPLOY-04) | Production compose ships default credentials, an exposed database port, and a `change-me` secret fallback | remediated |
| GAP-051 (GAP-TOOL-07) | The test suite opens the developer's real agent checkpoint DB and root `migration_state.db` | remediated |
| GAP-052 (GAP-CLI-05) | `ado2gh phase assign` crashes on every invocation; no repo can be risk-scored from the CLI | remediated |
| GAP-053 (GAP-ENG-08) | The queue worker cannot execute any job type: every one dies at `Accelerator` construction | remediated |
| GAP-054 (GAP-STATE-05) | `JobStore.complete()`/`.fail()` assign `updated_at` on a `JobRecord` that has no such field, raising at runtime | open |

Zero critical or high entries remain in `open` or `disputed` except four: GAP-019
(GAP-AUTH-03), GAP-024 (GAP-UI-02) and GAP-031 (GAP-PIPE-01) stay `open`, each awaiting an
operator decision on its FR-024 contract change recorded in `plan.md` § Approved contract
changes; GAP-054 (GAP-STATE-05), opened during this same pass, is also `open` pending the
same kind of FR-024 review before its fix — adding fields to `JobRecord` — can be applied.

## Disputes

The T036 review pass ran on 2026-09-08 over all 34 gaps rated critical or high
(GAP-001 through GAP-034). The reviewer was `caveman:cavecrew-reviewer`, run as four
parallel batches over contiguous id ranges; each batch received the constitution, this
register, and the verbatim instruction from `contracts/artifact-schemas.md`, and judged
each gap only from that gap's own cited evidence. Batching changes which reviewer
instance saw a gap, not how it was judged.

All 34 replies were `CONFIRM`. Per the handoff contract, CONFIRM lines are not recorded,
so the table below is empty *because nothing was contested* — not because the review was
skipped. No gap carries `status: disputed`, and the FR-021 deferral rule (record at the
higher severity, hold remediation until the operator decides) does not apply to any entry
in this register. T037 therefore had no dispute to put to the operator.

**The table below has no rows.**

| Gap id | Original rating | Reviewer rating | Reviewer reasoning | Operator decision |
|--------|-----------------|-----------------|--------------------|-------------------|
| — | — | — | — | — |

## Gaps

### GAP-001 (GAP-STAB-01) Red test suite plus a coverage gate that could not fail

- components: tooling & guards, deployment & CI artefacts
- violates: Principle VI (Comprehensive Testing & Coverage, NON-NEGOTIABLE)
- evidence:
  - `specs/013-clean-code-arch-remediation/baseline-failures.md` — 50 failed / 16 errors on the feature branch at the pre-stabilisation head; the suite could not be used as a safety net for any subsequent change
  - `pyproject.toml:88-90` — `[tool.coverage.run]` now carries no `omit` list; the pre-stabilisation tree omitted `cli/`, `state/`, `core/`, which is what let a 60 % tree satisfy a gate asserting 85 %
  - `.github/workflows/ci.yml:36` — `pytest --cov=ado2gh --cov-fail-under=56`, an honest whole-package ratchet
  - reproduction: `.venv\Scripts\python.exe -m pytest` → `821 passed, 30 skipped, 0 failed, 0 errors`
- severity: high (critical_test: —)
- blast_radius: with a red suite and an omit list covering the migration engine and state layer, any regression in `cli/`, `core/`, or `state/` could ship undetected while CI reported green — the primary safety net of Principle VI was inoperative.
- status: remediated
- resolution: baseline stabilisation (T005–T010), commits `bcd9934, d72c117, 60b5b1f, 44564f2, 1345bde, b42bd07, d54ef1f, 57dce51, e85370e, 9c83a29` — all test-side, no production file changed — plus deletion of `[tool.coverage.run] omit` and replacement of the dishonest 85 % assertion with a never-lowered ratchet at the measured whole-package figure. The residual 56 % → 85 % shortfall is **not** closed here and is carried as a separate deferred gap (see GAP-022 (GAP-TOOL-01)).
- regression_check: `.github/workflows/ci.yml:36` (`--cov-fail-under=56`, ratchet never lowered) and the full suite at `.venv\Scripts\python.exe -m pytest`
- revert_proof: restoring the `[tool.coverage.run] omit` block and re-running `pytest --cov=ado2gh --cov-fail-under=56` measures a different (inflated) percentage over a smaller denominator; reverting any one stabilisation commit returns the suite to red. **No stash-based revert proof exists for this entry, and none is owed.** FR-023's revert-proof discipline applies to the critical set (GAP-002 through GAP-016), each of which has a single regression test that must fail with its fix stashed; GAP-001 is rated `high`, its remediation is spread across ten test-side commits and a coverage-configuration change, and it has no single regression test to fail. The earlier note promising a proof "at T035" was wrong about which task would produce it — T035 filled the Summary and verified citations, and recorded no proof. The verification that does exist is the one named under `regression_check`: the whole-package ratchet in CI and the full suite, both of which are exercised on every run.
- contract_change: false
- closed_on: 2026-09-07

### GAP-002 (GAP-AUTH-01) Live-execution approval is inert in the default configuration

- components: auth & RBAC, accelerator service, agent service
- violates: Principle V (Enterprise Migration Safeguards, NON-NEGOTIABLE); property 6 (human approval before irreversible action)
- evidence:
  - `ado2gh/auth/service.py:21-22` — `auth_enabled()` reads `ADO2GH_AUTH_ENABLED` and returns `False` when unset; this is the code-level default, not a deployment choice
  - `ado2gh/api/platform_rbac.py:16-17,25-26` — `_require_authenticated` / `require_capability` short-circuit `if not auth_enabled(): return user` before any capability check, making `require_approve_live_execution` (`:41-42`) a no-op
  - `ado2gh/agents/migration_agent/route_helpers.py:199,209` — `_require_operate` / `_require_approve_live` bodies are wrapped in `if auth_enabled():`
  - `ado2gh/agents/migration_agent/policies.py:127-129,141-143` — `can_execute_live_without_approval` returns `True` and `session_requires_live_approval` returns `False` when auth is off, both in the permissive direction
  - `services/agent/main.py:94-95`, `services/accelerator_api/main.py:106-107` — both auth middlewares `return await call_next(request)` unconditionally when auth is disabled
  - `docker-compose.yml:53` — `ADO2GH_AUTH_ENABLED: "false"` in the compose file CLAUDE.md lists first
  - `ado2gh/api/audit_access.py:12-13` — `can_view_all_audit_history` is disabled by the same short-circuit, exposing the audit trail
- severity: critical (critical_test: a)
- blast_radius: in the default configuration every RBAC capability check on both services is inert. Any caller who can reach either API — no login, no cookie, no role — can flip a session to live and write real changes to GitHub via `/approve`, `/execution-mode`, or `/confirm-live`, with no approval queue entry and no role-derived audit actor. Every other approval finding in this register is additionally masked by this one.
- status: remediated
- resolution: Live-execution authority no longer depends on `ADO2GH_AUTH_ENABLED`. `ado2gh/agents/migration_agent/policies.py:can_execute_live_without_approval` no longer consults `auth_enabled()`, and `session_requires_live_approval` is now its exact complement, so a session without an approved live-approval row always requires approval. A new `enforce_live_mode_request` in the same module is the single check every agent route that can flip a session to live now calls: it raises 401 `Not authenticated` when the request carries no identity and 403 `live_execution_requires_approval` when the caller holds no approve-live capability. `ado2gh/api/platform_rbac.py:require_approve_live_execution` no longer routes through `require_capability`'s auth-disabled short circuit; it raises 401 without an identity and 403 without the capability in every configuration. Dry-run paths are untouched — the identity gate applies only to a request that asks for live execution. **Scoped exclusion:** `ado2gh/api/audit_access.py:12-13` was deliberately not changed. `can_view_all_audit_history` guards an ordinary read, not an irreversible action; tightening it would change who can read migration history without closing any path to live execution, so it stays permissive under the auth-disabled default. The exclusion covers audit reads only and is recorded here so it is not mistaken for an oversight. Operator decision, 2026-09-08: the gate was approved as it stands — live execution now requires `ADO2GH_AUTH_ENABLED=true` plus an ADMIN or APPROVER identity, identity-less live requests receive 401, and dry-run behaviour is unchanged.
- regression_check: `tests/auth/test_gap_002_auth_disabled_bypasses_live_approval.py`
- revert_proof: `git stash push -- ado2gh/api/platform_rbac.py ado2gh/agents/migration_agent/policies.py services/agent/routes/session_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/auth/test_gap_002_auth_disabled_bypasses_live_approval.py`, then `git stash pop`. With the fix reverted: `1 failed, 16 warnings in 4.17s` — `test_anonymous_caller_cannot_switch_agent_session_to_live`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5). `services/agent/routes/session_routes.py` had to be stashed alongside the other two files because it imports `enforce_live_mode_request` from `policies.py`; stashing `policies.py` on its own leaves that route module unimportable, which would have produced a collection error instead of a behavioural failure.
- contract_change: true — recorded as approved change 1 in `contracts/public-contract-freeze.md` § Approved contract changes. No route, CLI command, table, or environment variable was added or removed, so `tests/contract/public_surface_snapshot.json` is unchanged by this gap.
- closed_on: 2026-09-08

### GAP-003 (GAP-AUTH-02) Agent internal resume-live/deny-live routes have no authorization in any shipped configuration

- components: auth & RBAC, agent service, deployment & CI artefacts
- violates: Principle V (fail-safe defaults, auditability); property 6
- evidence:
  - `services/agent/routes/session_routes.py:475-476` — `resume_live_internal(session_id: str)` takes no `Request` parameter; sets `dry_run=False`, `live_approval_status="approved"` with no in-handler authorization check and no audit record (contrast `approve_session`, which does call `_audit.record`)
  - `services/agent/routes/session_routes.py:497-498` — `deny_live_internal` has the same shape
  - `services/agent/main.py:83-104` — the only guard is `_INTERNAL_TOKEN = os.environ.get("ADO2GH_INTERNAL_TOKEN", "")`; `if not _INTERNAL_TOKEN: return await call_next(request)` at `:100-101` opens the route unconditionally when unset
  - `ADO2GH_INTERNAL_TOKEN` appears in none of `docker-compose.yml`, `docker-compose.prod.yml`, or `deploy/kubernetes/configmap.yaml` — including the two that set `ADO2GH_AUTH_ENABLED: "true"` (`docker-compose.prod.yml:26,45`, `deploy/kubernetes/configmap.yaml:7`); `.env.example:70` ships it commented out
  - `docker-compose.yml` publishes agent port `8090:8090` to the host, so the route is not container-internal
  - `ado2gh/api/live_approval_store.py:239-248` — the intended caller is the correctly gated accelerator route (`pipeline_routes.py:205-209`), but nothing prevents any other caller POSTing the same URL
- severity: critical (critical_test: a)
- blast_radius: unlike GAP-002 (GAP-AUTH-01) this is **not** closed by setting `ADO2GH_AUTH_ENABLED=true` — it survives in the hardened prod and k8s configs as shipped, because neither sets the token. Anyone reaching agent port 8090 can resume a session into live execution or silently deny a legitimate approver's decision, entirely outside the accelerator's approval queue, with no audit record on the resume path.
- status: remediated
- resolution: The agent's `/v1/internal/` range now fails closed. `services/agent/main.py` rejects a request to that range with 401 when `ADO2GH_INTERNAL_TOKEN` is unset or does not match, instead of waving it through; there is no configuration in which the resume-live and deny-live routes are reachable without the shared secret. The token is now supplied everywhere the platform ships a configuration that could reach those routes: `docker-compose.prod.yml` and `deploy/kubernetes/secret.yaml.example` make it mandatory with no default value (a shipped default would be a publicly known shared secret), `docker-compose.yml` plumbs it from `.env` so both services see the same value if auth is ever turned on, and `.env.example`, `README.md`, `docs/LOCAL_DEVELOPMENT.md` and `docs/SETUP_GUIDE.md` state that it is required whenever `ADO2GH_AUTH_ENABLED=true`. Because the range now fails closed, a missing or mismatched token turns a silent no-op into a visible failure, so `ado2gh/api/live_approval_store.py` no longer swallows the notify: the approval row is still committed first (rolling the approver back would lose a recorded decision), but a failed notify is written as a `platform.live_execution.notify_failed` audit event under the approver's own name and logged at ERROR with the status code and exception type only — never the request or its headers, which carry the token (CA-003).
- regression_check: `tests/agent/test_gap_003_internal_live_routes_unauthenticated.py`, plus `tests/core/test_live_approval_queue.py::test_failed_agent_notify_is_recorded_not_swallowed` for the notify path
- revert_proof: `git stash push -- services/agent/main.py`, then `.venv\Scripts\python.exe -m pytest tests/agent/test_gap_003_internal_live_routes_unauthenticated.py`, then `git stash pop`. With the fix reverted: `1 failed, 15 warnings in 4.09s` — `test_unauthenticated_caller_cannot_resume_a_session_into_live_execution`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — `ADO2GH_INTERNAL_TOKEN` changes from optional to required under `ADO2GH_AUTH_ENABLED=true`, and the deployment manifests now set it. The variable was already in the frozen environment surface, so `tests/contract/public_surface_snapshot.json` is unchanged by this gap.
- closed_on: 2026-09-08

### GAP-004 (GAP-AUTH-04) Pipeline-run routes let the client self-certify `agent_live_approved` to skip the approval queue

- components: auth & RBAC, accelerator service
- violates: Principle V; property 6
- evidence:
  - `ado2gh/api/contracts.py:402-405` — `PipelineRunStartRequest.agent_live_approved: bool = Field(default=False, ...)`, a plain client-suppliable body field with no signature or cross-check against `LiveApprovalStore`
  - `ado2gh/api/contracts.py:409-412` — `PipelineRunStartApprovedRequest.agent_live_approved`, same pattern on the second route
  - `services/accelerator_api/routes/pipeline_routes.py:117-118` — `skip_live_gate = req.agent_live_approved and not dry` then `if operator_requires_live_approval(user, dry) and not skip_live_gate:` — the client's own boolean disables the approval branch
  - `services/accelerator_api/routes/pipeline_routes.py:135` — the run is stamped `live_approval_status = "approved"` from nothing but the caller's assertion, with no `LiveApprovalStore` row
  - `services/accelerator_api/routes/pipeline_routes.py:138` — the path reaches `_runner.start_async(run.id, step_ids)`, i.e. real execution
  - `services/accelerator_api/routes/pipeline_routes.py:158-162` — identical pattern on `start_existing_pipeline_run`
  - `ado2gh/auth/service.py:71-72` — `can_approve_live_execution` is denied to OPERATOR, yet an authenticated OPERATOR can set this field themselves
- severity: critical (critical_test: a)
- blast_radius: the most directly exploitable finding. Real pipeline execution proceeds and is recorded as approved on the caller's own say-so. Not contingent on GAP-002 (GAP-AUTH-01)'s default — it defeats the OPERATOR/APPROVER separation even with `ADO2GH_AUTH_ENABLED=true`, and leaves an audit record that falsely asserts an approval occurred.
- status: remediated
- resolution: Live authority is now derived only from server-side state — the authenticated user's role on `POST /v1/pipeline/runs`, or an approved `LiveApprovalStore` row on `POST /v1/pipeline/runs/{run_id}/start`. The `agent_live_approved` field is deleted from `ado2gh/api/contracts.py` and is no longer read anywhere; `services/accelerator_api/routes/pipeline_routes.py` no longer computes `skip_live_gate`, and a parked run is released only when `_live_store().has_approved(...)` says an approver decided or when the caller can approve live execution in their own right. `ado2gh/agents/migration_agent/nodes/executor/pipeline.py` loses the `agent_live_approved` helper and stops sending any live-approval claim, so the agent no longer certifies itself. Per approved change 4 in `contracts/public-contract-freeze.md`, two further decisions were taken and are recorded there in the form that landed: **Decision C** — `PipelineRunStartRequest` now carries `model_config = ConfigDict(extra="forbid")`, so a client still posting `agent_live_approved` to `POST /v1/pipeline/runs` receives 422 with `detail[].loc == ["body", "agent_live_approved"]` and `type: "extra_forbidden"` (measured, not inferred) rather than having the field silently ignored; the freeze doc notes the breadth of this, that `extra="forbid"` rejects **any** undeclared field, so every future field addition on this endpoint is a coordinated server/caller change. **Decision D** — `PipelineRunStartApprovedRequest` held nothing but the removed field, so it is deleted outright with no stand-in, and `POST /v1/pipeline/runs/{run_id}/start` now declares no body parameter at all; Decision C does not apply there because no model is left to forbid extras on, so a caller still sending the old field to that route keeps working and it simply has no effect. One field was added in the same change: `override_reason: str = ""` on `PipelineRunStartRequest`, required because the console had already begun sending it for GAP-009 and would otherwise have hit the new `extra="forbid"`. It is deliberately excluded from `PipelineRun.to_dict()`: the value is free operator text redacted only where it is persisted (`redact_payload` in `ado2gh/api/pipeline_steps.py`), so echoing the in-memory value back in a run response would return text that never passed through redaction. The freeze doc records that coupling as permanent — redaction lives on the persist path, not the response path, and nothing in the type system enforces it.
- regression_check: `tests/pipeline/test_gap_004_client_self_certified_live_approval.py`, plus `tests/unit/test_pipeline_executor.py::test_agent_never_self_certifies_live_approval_in_run_body`
- revert_proof: `git stash push -- ado2gh/api/contracts.py services/accelerator_api/routes/pipeline_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/pipeline/test_gap_004_client_self_certified_live_approval.py`, then `git stash pop`. With the fix reverted: `1 failed, 5 warnings in 4.46s` — `test_client_supplied_agent_live_approved_cannot_start_a_live_run`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — approved change 4 in `contracts/public-contract-freeze.md`. Snapshot impact none: request-model fields, model deletions and validation strictness are not among the four frozen keys, and no route path, method, CLI command, environment variable or table name changed. Confirmed against the snapshot test.
- closed_on: 2026-09-08

### GAP-005 (GAP-AUTH-05) `operator_requires_live_approval` gates only the OPERATOR role; COORDINATOR bypasses approval entirely

- components: auth & RBAC, accelerator service
- violates: Principle V; property 6
- evidence:
  - `ado2gh/api/platform_rbac.py:49-52` — `operator_requires_live_approval`: `if dry_run or not auth_enabled() or not user: return False` then `return user.role == PlatformRole.OPERATOR`
  - `ado2gh/auth/service.py:65-76` — `permissions_for()` grants `can_operate` to ADMIN, COORDINATOR, OPERATOR (`:68-70`) but `can_approve_live_execution` only to ADMIN and APPROVER (`:72`); COORDINATOR is therefore a role that can operate and cannot approve, and is not the role the gate checks for
  - `services/accelerator_api/main.py:262-274` — `POST /v1/migrate` routes its approval decision through this one function
- severity: critical (critical_test: a)
- blast_radius: an authenticated COORDINATOR, even under the hardened `ADO2GH_AUTH_ENABLED=true` config, can call `POST /v1/migrate` or `POST /v1/pipeline/runs` with `dry_run=false` and is never routed through `LiveApprovalStore` — no approval step, no queue entry, no confirmation. Independent of GAP-002 (GAP-AUTH-01) and GAP-004 (GAP-AUTH-04); turning auth on does not close it.
- status: remediated
- resolution: `ado2gh/api/platform_rbac.py:operator_requires_live_approval` no longer tests for a single role. It now asks the capability question directly — `return not permissions_for(user.role).get("can_approve_live_execution", False)` — so every role that lacks approve-live rights is routed through the approval queue, COORDINATOR included, and a new role added to `permissions_for()` inherits the correct behaviour without touching this function. The same function now raises 401 rather than returning `False` when a live request arrives with no identity at all, so an unauthenticated live call cannot pass by having no role to check. The fix is a single function in the file GAP-002 also changes, and both were committed together for that reason.
- regression_check: `tests/auth/test_gap_005_coordinator_bypasses_live_approval.py`
- revert_proof: `git stash push -- ado2gh/api/platform_rbac.py`, then `.venv\Scripts\python.exe -m pytest tests/auth/test_gap_005_coordinator_bypasses_live_approval.py`, then `git stash pop`. With the fix reverted: `1 failed, 5 warnings in 4.34s` — `test_coordinator_live_pipeline_run_is_routed_through_approval`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5). Unlike GAP-002's proof this one needed only the single file.
- contract_change: true — covered by approved change 1 in `contracts/public-contract-freeze.md` § Approved contract changes, the same live-execution gate entry as GAP-002. No frozen surface key changes.
- closed_on: 2026-09-08

### GAP-006 (GAP-AGT-01) `confirm-live` self-escalates an operator to live execution, and plan-level `dry_run` silently overrides the session gate

- components: agent service
- violates: Principle V; Principle IV (two disagreeing sources of truth for `dry_run`); property 6
- evidence:
  - `services/agent/routes/session_routes.py:1329-1339` — `confirm_live_execution` is gated only by `_require_operate(request)`; it sets `plan["dry_run"] = False` unconditionally, never calls `_require_approve_live`, `session_requires_live_approval`, or `LiveApprovalStore`, and records no audit event
  - `ado2gh/auth/service.py:68-72` — `can_operate` covers ADMIN/COORDINATOR/OPERATOR while `can_approve_live_execution` covers only ADMIN/APPROVER; OPERATOR and COORDINATOR pass the gate this route uses while explicitly lacking approve-live rights
  - `ado2gh/agents/migration_agent/nodes/executor/node.py:160-161` — `dry_run = migration_plan.get("dry_run", session.get("dry_run", True))` then `session["dry_run"] = dry_run` — the plan-level flag wins and is written back onto the shared session dict
  - `ado2gh/agents/migration_agent/guardrails.py:142,164` and `ado2gh/agents/migration_agent/policies.py:125,139` — the per-tool-call BLOCK check and the entire approval gate key off `session["dry_run"]` only, never `migration_plan["dry_run"]`; once `node.py:161` clobbers it, both are disabled for the rest of the session
  - `ado2gh/agents/migration_agent/route_helpers.py:544` — `_try_start_pev_run` checks `session_requires_live_approval(session)`, reading the pre-clobber value, so `run-pev` proceeds even though the plan already says live
  - test coverage: `confirm-live` / `confirm_live_execution` appear in `tests/` only in `tests/contract/public_surface_snapshot.json`, a route-registration snapshot — zero behavioural tests
- severity: critical (critical_test: a, e)
- blast_radius: any holder of the routine operate permission — OPERATOR or COORDINATOR, precisely the roles the approval system exists to restrict — converts a session to live GitHub-writing mode with one unguarded POST, no approver review, and no audit record of how live mode was reached. The plan/session `dry_run` split then disables the per-tool guardrail for the remainder of the session.
- status: remediated
- resolution: Two defects, both closed. **Self-escalation:** `confirm_live_execution` in `services/agent/routes/session_routes.py` no longer relies on the routine operate check. It goes through `policies.enforce_live_mode_request`, the single check introduced for GAP-002, so an OPERATOR or COORDINATOR — the roles the approval system exists to restrict — receives 403 `live_execution_requires_approval` instead of converting the session to live, and an identity-less caller receives 401. **Two sources of truth for `dry_run`:** `ado2gh/agents/migration_agent/policies.py` gains `resolve_execution_dry_run(session, migration_plan)`, and `ado2gh/agents/migration_agent/nodes/executor/node.py` calls it instead of letting the plan-level flag win and writing itself back onto the shared session dict. The safe flag now wins: if either the session or the plan says dry-run, the execution is dry-run, so a plan that says live can no longer disable the per-tool-call guardrail in `guardrails.py` and the approval gate in `policies.py` for the rest of the session.
- regression_check: `tests/agent/test_gap_006_confirm_live_self_escalation.py`, plus `tests/unit/test_executor_node.py::test_executor_tracks_rollback_live` which now has to give the session an approved status to reach live execution
- revert_proof: `git stash push -- ado2gh/agents/migration_agent/policies.py ado2gh/agents/migration_agent/nodes/executor/node.py services/agent/routes/session_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/agent/test_gap_006_confirm_live_self_escalation.py`, then `git stash pop`. With the fix reverted: `1 failed, 11 warnings in 4.27s` — `test_operate_only_role_cannot_self_confirm_live[operator-gap6_operator]`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — covered by approved change 1 in `contracts/public-contract-freeze.md` § Approved contract changes. `POST /v1/sessions/{id}/confirm-live` keeps its path and method and now answers 401 or 403 to callers it previously accepted; no frozen surface key changes.
- closed_on: 2026-09-08

### GAP-007 (GAP-ACC-01) Nine `/v1/migrate/*` feature routes perform live mutations with no RBAC, approval, or audit

- components: accelerator service
- violates: Principle V; property 6
- evidence:
  - `services/accelerator_api/routes/migrate_routes.py:1-44` — module docstring lists nine POST routes (git-mirror, pipeline-convert, secret-provision, service-connection, boards, test-plans, artifacts, wiki, branch-policies); the router is declared at `:44` with no `dependencies=`
  - `services/accelerator_api/routes/migrate_routes.py` — repo-wide grep of this file for `require_operate`, `platform_rbac`, `live_approval`, `AuditWriter`, `audit` returns **zero matches**; handler bodies at `:187-239`, `:244-290`, `:295-335`, `:356-426`, `:431-515`, `:520-588`, `:723-748` call scope handlers directly
  - `services/accelerator_api/main.py:262-294` — the sibling `POST /v1/migrate` route *does* enforce `operator_requires_live_approval` and `store.create_or_get_pending(...)`; these nine routes bypass the gate their own sibling enforces
  - `services/accelerator_api/main.py:490-504` → `ado2gh/core/orchestration/worker.py:38-40` — `/v1/jobs` enqueues to a worker on the same unguarded footing
  - reproduction: existing passing test `tests/unit/test_migrate_routes_core.py:14-18,52-74` POSTs `"dry_run": false` and asserts HTTP 200
- severity: critical (critical_test: a)
- blast_radius: every resource type the platform migrates — repos, pipelines, secrets, service connections, boards, test plans, artifacts, wiki, branch policies — has a live-write route reachable with no role check, no approval queue entry, and no audit event. Under GAP-002 (GAP-AUTH-01)'s default these are reachable unauthenticated; under the hardened config any logged-in user of any role reaches them.
- status: remediated
- resolution: The nine `/v1/migrate/*` routes now hang a single dependency off their router — `guard_live_migration` in the new `services/accelerator_api/routes/migrate_guard.py` — rather than repeating a check per handler, so a route added to that router later is covered by construction. The guard reads `dry_run` from the request body and lets dry runs through unchanged, keeping local development permissive for everything reversible. A live run needs an identity and the `can_approve_live_execution` capability; a caller who can only operate is parked in the same `LiveApprovalStore` queue that the sibling `POST /v1/migrate` already used, which supplies both the audit record (CA-004) and the approve/deny preview path (CA-001), and receives 403 `awaiting_approval` with the approval id. Every live run that does proceed is recorded first, before any irreversible work starts, as an `accelerator.migrate.live_execution` audit event. The scope id and audit payload are built from an allowlist of identity fields (`project`, `repo`, `repo_name`, `github_org`, `github_repo`, `connection_name`, `feed_name`, `wiki_name`, `secret_name`) rather than from the whole body: an allowlist, not a blocklist, so `secret_value` on `/v1/migrate/secret-provision` cannot reach a permanent audit row (CA-003).
- regression_check: `tests/unit/test_gap_007_migrate_routes_unguarded.py`
- revert_proof: `git stash push -- services/accelerator_api/routes/migrate_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_007_migrate_routes_unguarded.py`, then `git stash pop`. With the fix reverted: `1 failed, 3 warnings in 4.00s` — `test_live_migrate_route_rejects_unauthenticated_caller[/v1/migrate/git-mirror]`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false — no route path or method changed and no new module is a public entry point; the nine routes answer 401 or 403 to live callers they previously accepted. `services/accelerator_api/routes/migrate_guard.py` is a new file and is recorded in `docs/STRUCTURAL_CHANGELOG.md` (FR-029).
- closed_on: 2026-09-08

### GAP-008 (GAP-ACC-02) GitHub write proxy documented as read-only, guarded only by `require_operate`, unaudited

- components: accelerator service
- violates: Principle V; Principle II (docstring asserts a property the code does not have)
- evidence:
  - `services/accelerator_api/routes/proxy_routes.py:1` — module docstring: `"""Read-only ADO and GitHub API proxies for the migration agent."""`
  - `services/accelerator_api/routes/proxy_routes.py` — `@router.post`, `@router.patch`, `@router.put`, `@router.delete` on `/v1/github/{path:path}`, each forwarding an arbitrary path and body
  - `services/accelerator_api/routes/proxy_routes.py` — `_proxy_github_request` calls `require_operate(request)` as its only guard; no approval check, no `AuditWriter` call, no path allowlist for write verbs (the only path inspection is a `GET`-only branch on `clean.startswith("repos/")`)
- severity: critical (critical_test: a)
- blast_radius: an arbitrary authenticated operate-capable caller can issue any GitHub REST write the platform token can perform — including `DELETE /repos/{org}/{repo}` — through a route whose own documentation says it is read-only, with no audit record. Inert-by-default under GAP-002 (GAP-AUTH-01) means unauthenticated in the default configuration.
- status: remediated
- resolution: `services/accelerator_api/routes/proxy_routes.py` now separates reads from writes. `_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})` names the verbs that mutate; a request using one of them goes through `require_approve_live_execution(request)` rather than `require_operate`, so the ability to issue an arbitrary GitHub write — up to `DELETE /repos/{org}/{repo}` — now requires the same capability as approving a live migration, and an identity-less caller receives 401. Each write is then recorded by `_audit_github_write(user, method, endpoint)`, which stores the verb, the endpoint path and the acting user and nothing else: not the request body and not the `Authorization` header, either of which can carry a credential (CA-003). GET behaviour is unchanged, so the read proxy the agent depends on is unaffected, and the module docstring no longer claims a read-only property the code does not have.
- regression_check: `tests/unit/test_gap_008_github_write_proxy_unguarded.py`
- revert_proof: `git stash push -- services/accelerator_api/routes/proxy_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_008_github_write_proxy_unguarded.py`, then `git stash pop`. With the fix reverted: `1 failed, 3 warnings in 3.89s` — `test_github_write_not_reachable_on_operate_permission_alone[DELETE-repos/acme/production-service]`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false — no route path or method changed; the write verbs answer 401 or 403 to callers they previously accepted.
- closed_on: 2026-09-08

### GAP-009 (GAP-CLI-02) `phase run --force` skips the gate with no reason and no audit record, while the audited override path is never called

- components: CLI, phase orchestration, accelerator service
- violates: Principle V; property 6
- evidence:
  - `ado2gh/cli/phase.py:22` — `--force` is a bare flag with no companion `--reason`
  - `ado2gh/api/accelerator.py:106-113` — `if not request.force:` wraps the entire `PhaseGateChecker` block, so `--force` skips gate evaluation completely; the check also only consults `PHASE_ORDER[prev_idx]`, so the first phase (`poc`) has no gate at all
  - `ado2gh/api/contracts.py:41` — `force` is a plain request field with no reason or approval companion
  - `ado2gh/phase/gate_checker.py:95` — `override(self, phase, reason: str)` sets `GateStatus.OVERRIDE`, persists `override_reason` via `upsert_phase_gate`, and emits `log.warning` — this is the designed audited escalation path
  - repo-wide grep: `GateChecker.override` has no caller in `ado2gh/cli/`, `ado2gh/api/`, or `services/` — the audited path is dead code
- severity: critical (critical_test: a)
- blast_radius: the documented escalation contract (`--override --reason`, CLAUDE.md "Key Patterns") is unreachable; the reachable escalation writes nothing. Every gate bypass on a real wave — repo creation, git push, pipeline push, ADO cleanup — proceeds with no persisted record of who bypassed it or why, defeating the phase-gate audit trail entirely.
- status: remediated
- resolution: The gate is now always evaluated. `ado2gh/api/accelerator.py` no longer wraps the whole `PhaseGateChecker` block in `if not request.force:`; `force` escalates a gate rather than skipping it, and escalating past a *blocking* gate goes through `checker.override(prior, reason)` — the designed audited path, which was previously dead code — so the override is persisted with its reason. With no reason supplied the call refuses: `Gate blocked for prior phase {prior}: forcing past it requires override_reason`, and nothing is migrated. `ado2gh/cli/phase.py` passes `override_reason=""` from `phase run`: **no `--reason` flag was added to `phase run`**, deliberately, both because the CLI surface is frozen and because an override should be a separate attributable act. Operator decision, 2026-09-08: the sanctioned path is `phase gate-check --override --reason "..."` followed by an ordinary `phase run`, and the same command now rejects `--override` without a non-empty `--reason` (`click.UsageError`) instead of raising the TypeError recorded as GAP-017. `docs/COMMAND_REFERENCE.md` and `docs/EXECUTION_MANUAL.md` document the two-step form and warn runbooks and CI jobs that currently use `phase run --force` against a red gate that they must change. The console keeps a usable path for the same escalation: `apps/migration-ui/src/app/settings/migrate/page.tsx` shows an inline justification field on live runs only, normalised by `gateOverrideReason` in `apps/migration-ui/src/lib/pipelineRunStatus.ts`, carried as `override_reason` through `PipelineRunStartRequest` and `PipelineRunStore`, and redacted by `redact_payload` in `ado2gh/api/pipeline_steps.py` at the point it is persisted. It is deliberately absent from `PipelineRun.to_dict()` so free operator text never rides back out in a run response un-redacted (CA-003).
- regression_check: `tests/core/test_gap_009_phase_force_unaudited.py`
- revert_proof: `git stash push -- ado2gh/api/accelerator.py`, then `.venv\Scripts\python.exe -m pytest tests/core/test_gap_009_phase_force_unaudited.py`, then `git stash pop`. With the fix reverted: `1 failed, 2 warnings in 3.80s` — `test_forced_phase_run_persists_audited_override`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false in the frozen-surface sense — no CLI command, option, route, table or environment variable was added or removed, and `tests/contract/public_surface_snapshot.json` is unchanged by this gap. The *behaviour* of `phase run --force` does change: it no longer skips a blocking gate. That is recorded as approved change 3 in `contracts/public-contract-freeze.md` § Approved contract changes, with the migration note for existing runbooks.
- closed_on: 2026-09-08

### GAP-010 (GAP-TOKEN-01) `redact_payload` misses ADO PAT, Bearer, and prefixed key-name shapes before persisting audit events

- components: token management & audit writing, migration engine
- violates: Principle V (CA-003: secret values masked in all messages, logs, and audit records); property 5
- evidence:
  - `ado2gh/assignments/audit.py:10-18` — all seven `_SECRET_PATTERNS` match GitHub-token shapes only (`ghp_`, `gho_`, `ghu_`, `ghs_`, `github_pat_`, `pat-`, and a `"token|password|secret|pat":"..."` JSON-key pattern); none match a bare Azure DevOps PAT (opaque, no prefix) or a generic `Bearer <token>` value
  - `ado2gh/assignments/audit.py:21,36` — `_SECRET_KEY_NAMES` is matched exactly (`k.lower() in _SECRET_KEY_NAMES`), so realistic keys such as `ado_pat`, `access_token`, or `github_token` — the platform's own env-var naming per CLAUDE.md — fall through to the regex path, which also misses them
  - `ado2gh/assignments/audit.py:52-70` — `AuditWriter.write()` calls `redact_payload(payload or {})` at `:61` as the sole redaction step before `json.dumps(safe)` is persisted by `insert_audit_event` at `:62`; the module docstring at `:1` claims "secret redaction (CA-003)"
  - `ado2gh/api/profile_governance.py:135-148` — `write_profile_audit()` passes the payload straight to `writer.write(...)` with no pre-masking pass
  - persistence chain: `ado2gh/api/contracts.py:471` → `services/accelerator_api/routes/pipeline_routes.py:211,221` → `ado2gh/api/live_approval_store.py:158-164` → `ado2gh/assignments/audit.py:61` → `ado2gh/state/sqlite_agentic_mixin.py:26-37`
  - reproduction: passing a 52-character ADO-PAT-shaped string through `redact_payload` returns it unchanged (measured)
  - three independently maintained masking implementations with non-overlapping coverage and no reuse: `ado2gh/assignments/audit.py:10-18`, `ado2gh/core/scopes/git_scope.py:18-25`, `ado2gh/agents/migration_agent/utils.py:335`
- severity: critical (critical_test: b)
- blast_radius: any audit event whose free-text or payload carries an ADO PAT, a Bearer value, or a prefixed key name is persisted verbatim into `audit_events` — the highest-retention, most broadly exported artefact in the system (`GET /v1/history/sessions/export`). The single function designated as the containment choke point does not contain the platform's own primary credential type.
- status: remediated
- resolution: `ado2gh/assignments/audit.py:redact_payload` is made the platform's single masking choke point (FR-025) and taught the shapes it was missing. It now covers bare Azure DevOps PATs — the platform's own primary credential, which has no prefix to match on — `Bearer <token>` values, and realistic key names such as `ado_pat`, `access_token` and `github_token`, which previously fell through because `_SECRET_KEY_NAMES` was matched by exact equality. The other two masking implementations stop being independent: `ado2gh/core/scopes/git_scope.py:_redact` keeps stripping the exact credential values it holds and then passes the result through `redact_payload`, because a PAT echoed by git for some *other* remote is still a leak, and `ado2gh/logging_config.py` gains `SecretRedactingFilter`, attached to the root handler so records propagated from any module's logger are covered. The filter never raises and never logs — it replaces the record with `<log record suppressed: redaction failed>` if masking fails, since an exception there would take logging down for the whole process.
- regression_check: `tests/auth/test_gap_010_redact_payload_token_shapes.py`
- revert_proof: `git stash push -- ado2gh/assignments/audit.py`, then `.venv\Scripts\python.exe -m pytest tests/auth/test_gap_010_redact_payload_token_shapes.py`, then `git stash pop`. With the fix reverted: `1 failed in 3.60s` — `test_bare_ado_pat_under_realistic_key_is_not_persisted`, AssertionError at test line 58. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5). A first attempt stashed `audit.py` together with its five delegating call sites, which is broader than this gap's own fix; the proof was re-taken with `audit.py` alone and still failed, so the minimal proof is the one recorded here.
- contract_change: false — `redact_payload` keeps its signature and return type; only the set of shapes it masks widens.
- closed_on: 2026-09-08

### GAP-011 (GAP-AGT-02) `mask_secrets` is wired only into the audit bridge; chat, SSE, and persisted session messages are unmasked

- components: agent service
- violates: Principle V (CA-003); property 5
- evidence:
  - `ado2gh/agents/migration_agent/utils.py:335` — `mask_secrets` definition; repo-wide grep across `ado2gh/` and `services/` returns exactly two call sites, `utils.py:384` and `:388`, both inside `IdeAuditBridge.record` (verified)
  - `ado2gh/agents/migration_agent/utils.py` — `_append_event`, `publish_orchestrator_chat`, `_append_and_stream`, `_emit_tool_call`, `_emit_tool_result` append raw content to `session["messages"]` and the SSE stream with no masking; `_emit_tool_call` forwards raw tool `arguments` verbatim
  - `ado2gh/agents/migration_agent/nodes/streaming.py` — `_stream_llm_response` has no masking call
  - `ado2gh/agents/migration_agent/session/store.py:228,246,256` — `register_http_session` / `update_session` write `json.dumps(session.get("messages") or [])` into the `agent_sessions.messages_json` SQLite column, so unmasked content reaches a durable queryable artefact, not just a transient stream
  - `ado2gh/agents/migration_agent/hitl/intake.py:267-284` — `format_form_submission_summary` renders every submitted field as `f"{key}: {value}"` into a chat message; `ado2gh/agents/migration_agent/hitl/form_fields.py` defines no secret or masked field type
- severity: critical (critical_test: b)
- blast_radius: an operator pasting a token into free-text chat or a HITL form field — the realistic path, since automated flows use name-only secret mappings with empty-value placeholders (`nodes/executor/scope.py:507`) — has that value persisted verbatim to SQLite and broadcast unmasked over SSE to every viewer of the session, unconditionally, in the default configuration.
- status: remediated
- resolution: Masking moved from the audit bridge, where it was one of two call sites, to the single entry point every message passes through. `ado2gh/agents/migration_agent/utils.py:_append_event` now masks `content` and `meta` as it builds the entry and returns that entry, so everything downstream — the SSE frame, the persisted `messages_json` column and the chat feed — reads the masked copy rather than re-deriving its own. `_append_and_stream` and `_emit_tool_call` stream the masked entry instead of the raw content or raw tool arguments. `ado2gh/agents/migration_agent/nodes/orchestrator.py` masks the three `thinking` broadcasts so the SSE frame matches the message-list copy. `ado2gh/agents/migration_agent/session/store.py` routes both `messages_json` writes through a `_messages_json` helper that applies `redact_payload` before `json.dumps`, so the durable SQLite artefact is masked even if an unmasked entry ever reaches the list by another path. `ado2gh/agents/migration_agent/nodes/streaming.py` masks the non-streaming fallback, which emits one complete string. **Deliberate limit, recorded rather than left to be discovered:** individual streamed tokens are not masked. A secret split across two chunks cannot be matched by any regex, and scanning per token would cost more than it buys; the accumulated text is masked where it lands, which is the durable and exported copy. If transient SSE fragments must also be clean, the upgrade path is to buffer to a whole line before emitting and mask the line.
- regression_check: `tests/agent/test_gap_011_agent_message_masking.py`
- revert_proof: `git stash push -- ado2gh/agents/migration_agent/utils.py ado2gh/agents/migration_agent/session/store.py ado2gh/agents/migration_agent/nodes/streaming.py ado2gh/agents/migration_agent/nodes/orchestrator.py ado2gh/logging_config.py`, then `.venv\Scripts\python.exe -m pytest tests/agent/test_gap_011_agent_message_masking.py`, then `git stash pop`. With the fix reverted: `1 failed in 3.54s` — `test_pasted_pat_does_not_reach_session_messages`, AssertionError at test line 71. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false — no message or SSE field was added, removed or renamed; the values carried in them are masked.
- closed_on: 2026-09-08

### GAP-012 (GAP-UI-01) LLM provider API key is transmitted in a URL query string

- components: web console, accelerator service
- violates: Principle V ("never log, commit, or echo tokens or PATs"); property 5
- evidence:
  - `apps/migration-ui/src/lib/llmSettings.ts:102-106` — `fetchCatalog` builds `new URLSearchParams(...)`, `query.set('api_key', params.apiKey)`, then requests `/v1/settings/llm-models/catalog?${query.toString()}` (verified)
  - `services/accelerator_api/routes/settings_routes.py:85-91` — `@router.get("/v1/settings/llm-models/catalog")` declares `api_key: str = ""` as a query parameter, so the unsafe channel is the contracted server API, not a lone client mistake (verified)
  - `apps/migration-ui/src/app/settings/models/page.tsx:346-354` — the value originates from a `type="password"` input, confirming a genuine user secret
  - `apps/migration-ui/src/app/settings/models/page.tsx:159-163` — call site passes the raw key
  - `apps/migration-ui/src/lib/llmSettings.ts:125-131,139-145` — `validateModel` and `saveModel` in the same file POST the identical `api_key` field in a JSON body, proving the safe pattern was already available
- severity: critical (critical_test: b)
- blast_radius: CWE-598. By default the key lands in browser history, any intermediary proxy or server access log that records the request line, and any HAR/devtools capture — none of which requires a non-default topology (the "deployment topology never qualifies" caveat is scoped to test (d) only). Single call site, admin-gated by `require_manage_models`, but the exposure is to infrastructure rather than to other users.
- status: remediated
- resolution: The unsafe channel was the contracted server API, not a lone client mistake, so both ends changed. `services/accelerator_api/routes/settings_routes.py` replaces `@router.get("/v1/settings/llm-models/catalog")` and its `api_key: str = ""` query parameter with `@router.post(...)` reading a JSON body, and `apps/migration-ui/src/lib/llmSettings.ts:fetchCatalog` sends that body instead of building `URLSearchParams`. The safe pattern already existed in the same file — `validateModel` and `saveModel` POST the identical `api_key` field in a JSON body — so this brings the one outlier into line rather than inventing a mechanism. The key no longer reaches browser history, intermediary proxy or server access logs that record the request line, or a HAR capture (CWE-598).
- regression_check: `tests/contract/test_gap_012_api_key_in_query_string.py::test_no_accelerator_route_accepts_a_secret_as_a_query_parameter`, which asserts the property across every accelerator route rather than only this one
- revert_proof: `git stash push -- services/accelerator_api/routes/settings_routes.py`, then `.venv\Scripts\python.exe -m pytest tests/contract/test_gap_012_api_key_in_query_string.py`, then `git stash pop`. With the fix reverted: `1 failed, 5 warnings in 4.23s` — `test_no_accelerator_route_accepts_a_secret_as_a_query_parameter`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — approved change 2 in `contracts/public-contract-freeze.md`, approved by the operator on 2026-09-08, and the only change in this remediation pass authorised to touch the frozen surface. `tests/contract/public_surface_snapshot.json` was edited in the same commit as the fix, exactly as § "Snapshot edit authorised for T040" specifies: `accelerator GET /v1/settings/llm-models/catalog` removed, `accelerator POST /v1/settings/llm-models/catalog` inserted in sort position, `http_routes` count unchanged at 148. Verified afterwards — `{'cli_commands': 96, 'db_tables': 25, 'env_vars': 68, 'http_routes': 148}`, a one-line insertion against a one-line deletion, and `tests/contract/test_public_surface_snapshot.py` green. No other snapshot drift was found.
- closed_on: 2026-09-08

### GAP-013 (GAP-ENG-01) Workflow-integrity check is structurally incapable of reporting FAIL

- components: migration engine, reporting
- violates: Principle V (property 4, validation after transfer); Principle VI
- evidence:
  - `ado2gh/reporting/post_migration_validator.py:254-255` — `parts = wf_path.replace(".github/workflows/", "").split("/")` then `if len(parts) >= 2:`; for the only shape the producer emits, stripping the prefix leaves exactly one element, so the guard is never true and the Contents-API existence check at `:257` never executes (verified)
  - `ado2gh/reporting/post_migration_validator.py:259-260` — `missing_files` can therefore only be appended to from an `except` block that the unreachable call can never raise, so it is always empty
  - `ado2gh/reporting/post_migration_validator.py:266-269` — the enclosing `if gh_count >= ado_count:` branch returns a literal `"verdict": PASS` regardless of `integrity_result`, which is merely attached under `"workflow_integrity"`
  - `tests/contract/test_step_contracts.py:128` — pins the real producer contract, `"output_path": ".github/workflows/build-ci.yml"` — flat, one segment after the prefix
  - `tests/unit/test_workflow_integrity.py:54,71,90,102` — all four tests `@patch` `_check_workflows`, mocking out the method the file exists to test; none exercise the guard or the hardcoded PASS
- severity: critical (critical_test: c)
- blast_radius: once `gh_count >= ado_count`, operators reading post-migration validation (CLI `report`, `ado2gh/api/run_reporting.py`) can never see a FAIL for missing or corrupted workflow files. A migration with silently missing workflows reads as fully validated — the outcome of the transfer cannot be verified, which is the property this check exists to provide.
- status: remediated
- resolution: `ado2gh/reporting/post_migration_validator.py` no longer requires a workflow path to have two or more segments after the `.github/workflows/` prefix before it will check whether the file exists at the target. The producer emits a flat path — one segment — so the guard was never true and the Contents-API existence check could never run, which in turn meant `missing_files` could never be appended to. The existence check now runs for the shape the producer actually emits, and a missing or unreadable workflow file is reported as a FAIL rather than being absorbed by the enclosing `if gh_count >= ado_count` branch that returned a literal PASS regardless of the integrity result.
- regression_check: `tests/unit/test_gap_013_workflow_integrity_can_fail.py`
- revert_proof: `git stash push -- ado2gh/reporting/post_migration_validator.py`, then `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_013_workflow_integrity_can_fail.py`, then `git stash pop`. With the fix reverted: `1 failed, 1 warning in 3.68s` — `test_workflow_check_reports_fail_when_generated_workflow_absent_at_target`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false — the verdict field keeps its shape; a case that could previously only report PASS can now report FAIL.
- closed_on: 2026-09-08

### GAP-014 (GAP-ENG-07) FR-036 concurrency guard fails open when both of its own checks throw

- components: migration engine
- violates: Principle V (fail-safe defaults); property 2 (resumability after interruption)
- evidence:
  - `ado2gh/core/migration_fr036.py:14-21` — the `REPO_LOCK_MANAGER.holder(...)` check is wrapped in `try: ... except Exception: pass`, with no record that the check was inconclusive (verified)
  - `ado2gh/core/migration_fr036.py:22-33` — the `PipelineRunStore.list_active_runs()` check is wrapped identically
  - `ado2gh/core/migration_fr036.py:34` — after both handlers swallow, execution reaches `return False`, i.e. "no other run holds this repo" — the permissive direction for a function whose sole purpose is detecting a concurrency conflict
  - `ado2gh/core/migration_fr036.py:46-47` — `clear_stale_in_progress_migrations` only refuses to clear when `other_run_holds_repo` returns True, so the double-exception path proceeds to mark the in-progress row failed and clear it
  - `ado2gh/core/migration_engine.py:72-78` — `migrate_repo()`'s only guard against two concurrent live migrations of the same repo is `has_repo_in_progress(...)` followed by `_try_clear_orphaned_in_progress(repo)`; a cleared row is what permits the migration to proceed
- severity: critical (critical_test: d)
- blast_radius: a transient error in either the lock manager or the pipeline-run store silently downgrades "conflict detection failed" to "no conflict, proceed". Two live migrations can then race the same repo — corrupting git state, double-applying branch-policy changes, or racing two GEI/mirror pushes at the same target. Reachable on any live migration of a repo carrying a stale in-progress row, which is the normal post-interruption scenario this code exists to handle.
- status: remediated
- resolution: `ado2gh/core/migration_fr036.py` is rebuilt around a new `repo_conflict_reason(repo_key, current_run_id) -> str | None`, which returns a reason string when a check cannot complete instead of swallowing the exception. `other_run_holds_repo` is now a thin wrapper over it, so a transient error in the lock manager or the pipeline-run store is reported as a conflict — the fail-closed direction — rather than being downgraded to "no other run holds this repo". `clear_stale_in_progress_migrations` therefore refuses to clear the in-progress row when conflict detection was inconclusive, and `ado2gh/core/migration_engine.py` no longer proceeds into a live migration on the strength of a check that failed. The reason string is carried rather than discarded so an operator can see *why* the guard held.
- regression_check: `tests/core/test_gap_014_concurrency_guard_fails_closed.py`
- revert_proof: `git stash push -- ado2gh/core/migration_fr036.py ado2gh/core/migration_engine.py`, then `.venv\Scripts\python.exe -m pytest tests/core/test_gap_014_concurrency_guard_fails_closed.py`, then `git stash pop`. With the fix reverted: `1 failed` — `test_guard_does_not_report_no_conflict_when_both_checks_fail`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: false — `other_run_holds_repo` keeps its name and boolean return; `repo_conflict_reason` is a new internal helper with no public surface.
- closed_on: 2026-09-08

### GAP-015 (GAP-SEAM-01) Work-item producer emits `scope`/`blocker`; all three consumers read `scopes`/`blocked_reasons`

- components: integration seams, accelerator service, agent service, web console
- violates: Principle V (CA-002 destructive-operation confirmation never fires; blocked-scope skips lose their reason); Principle IV; Principle VI
- evidence:
  - `ado2gh/api/migration_work_plan.py:265` — the sole work-item producer writes `"scope": scope` (singular string) (verified)
  - `ado2gh/api/migration_work_plan.py:271` — and `"blocker": blocker` (singular string) (verified)
  - `ado2gh/agents/migration_agent/nodes/executor/scope.py:290-332` — confirms this is the only builder feeding a live session's `plan["work_items"]`
  - `services/agent/routes/session_routes.py:1301` — `destructive_operations` is built by `for scope in wi.get("scopes", [])`, a key never set, so the loop body never executes and the list is always empty regardless of `repo_delete` / `workflow_delete` / `secret_delete` / `pipeline_disable` (verified)
  - `services/agent/routes/session_routes.py:1315,1317` — the same handler serialises `"scopes": wi.get("scopes", [])` and `"blocked_reasons": wi.get("blocked_reasons", [])` onto the wire, both always empty (verified)
  - `ado2gh/agents/migration_agent/nodes/executor/node.py:239-244` and `:366-371` — the executor reads `wi.get("blocked_reasons", [])` for every blocked item's skip/audit record, so `details` is always `[]` and the real blocker text is discarded on every skip (verified)
  - `apps/migration-ui/src/lib/agent.ts:414-419` vs `:75-84` — the same file declares plural `scopes`/`blocked_reasons` for `getPlanSummary()` and singular `scope?`/`blocker?` for `MigrationPlan.work_items[]`
  - `ado2gh/agents/migration_agent/prompts/planner.md:100` vs `:105` — the prompt documents singular `scope` yet instructs the LLM to emit `blocked_reasons`
  - `tests/contract/test_agent_pev_flow_contracts.py:472-493` — the one contract test asserting `"scopes" in work_item` is `@pytest.mark.skip(reason="Legacy planner module deleted in spec 012 — rewrite for migration_agent")`
- severity: critical (critical_test: e)
- blast_radius: CA-002's individual confirmation of destructive operations can never trigger, because the list feeding it is unconditionally empty. Every blocked work item loses its human-readable reason from the executor's audit record. `plan-summary` reports zero destructive operations and zero blocker text on the wire. No `.tsx` currently calls `getPlanSummary()`, so no user sees wrong data through that specific path today, but the contract and its own type declaration are wired to fail silently the moment it is used.
- status: remediated (see the residual finding below — the transport is fixed, CA-002 is still unreachable for a second reason)
- resolution: The singular `scope`/`blocker` fields stay canonical, and `ado2gh/api/migration_work_plan.py` gains `sync_work_item_wire_keys(work_item)`, which derives the plural `scopes` and `blocked_reasons` the consumers read from them. It is called wherever a work item is created or mutated — `build_work_items_for_repos`, `apply_operator_secret_mappings`, `apply_scope_results_to_work_items`, and `ado2gh/agents/migration_agent/hitl/blockers.py` — so the two shapes cannot drift apart again from one side. `services/agent/routes/session_routes.py` therefore serialises real values on the wire, and `ado2gh/agents/migration_agent/nodes/executor/node.py` records the actual blocker text on every skip instead of an empty list. `ado2gh/agents/migration_agent/prompts/planner.md` is corrected so the prompt no longer documents singular `scope` while instructing the model to emit `blocked_reasons`.
- **Residual finding — CA-002 is still unreachable in production, for a different reason.** This gap fixed the transport: `destructive_operations` is now built from a key that is actually populated. The producer still cannot emit a destructive scope. `MigrationScope` (`ado2gh/models.py:17-23`) has exactly six members — `repo`, `work_items`, `pipelines`, `wiki`, `secrets`, `branch_policies` — and none of them is destructive. The four values the confirmation logic tests for exist only as string literals, in `ado2gh/agents/migration_agent/guardrails.py:69-70,178` and `services/agent/routes/session_routes.py:1311`; no producer path can put any of them on a work item (measured 2026-09-08). Per-item destructive confirmation therefore still cannot fire in production, and closing this gap must not be read as closing CA-002. Recorded here against GAP-015 rather than opened as a new sequential id, because the register's ids were assigned at T035 and this is a residual of an existing entry, not a newly assessed component. It needs either a destructive member on `MigrationScope` with a producer that emits it, or an explicit decision that destructive scopes are out of scope for the platform and the confirmation logic should be removed rather than left as unreachable code.
- regression_check: `tests/contract/test_gap_015_work_item_field_contract.py`
- revert_proof: `git stash push -- ado2gh/api/migration_work_plan.py ado2gh/agents/migration_agent/hitl/blockers.py ado2gh/agents/migration_agent/nodes/executor/node.py ado2gh/agents/migration_agent/prompts/planner.md`, then `.venv\Scripts\python.exe -m pytest tests/contract/test_gap_015_work_item_field_contract.py`, then `git stash pop`. With the fix reverted: `1 failed, 9 warnings in 3.99s` — `test_plan_summary_flags_destructive_scope_on_producer_work_item`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — the `plan-summary` payload now carries populated `scopes` and `blocked_reasons` where it previously always sent empty lists. No field was added, removed or renamed and no frozen surface key changes; the console's existing plural declarations become correct rather than needing an edit.
- closed_on: 2026-09-08

### GAP-016 (GAP-PIPE-04) Live workflow-push approval gate is hardcoded satisfied by its only production caller and unsatisfiable from the CLI

- components: pipeline transformation, migration engine, CLI
- violates: Principle IV; property 6
- evidence:
  - `ado2gh/pipelines/push_workflows.py:43-45` — `push_repo_workflows(..., dry_run=False, readiness_ok=True, approver_ok=False)` (verified)
  - `ado2gh/pipelines/push_workflows.py:58-63` — `if not dry_run and not readiness_ok: return error("workflow readiness check failed")` and `if not dry_run and not approver_ok: return error("live workflow push requires approval")` (verified)
  - `ado2gh/pipelines/push_workflows.py:47` — the one-line docstring documents neither parameter's contract, so nothing states how a caller derives a genuine approval signal
  - `ado2gh/core/scopes/pipelines_scope.py:37` and `:196` — both live-push call sites reached from `PipelinesScopeHandler.migrate` (the default `phase run` path) pass `readiness_ok=True, approver_ok=True` as literals; repo-wide grep confirms these are the only non-test callers passing True (verified)
  - `ado2gh/reporting/pipeline_readiness.py` — the real auto/assisted/manual classification is never consulted at push time; a "manual" pipeline is pushed identically to an "auto" one
  - `ado2gh/pipelines/push_workflows.py:118-122` — the PR body is one generic string for every push, so the reviewer is not told the pipeline needed manual attention
  - `ado2gh/cli/misc.py:51-76` — the `push-workflows` command exposes no flag for either parameter and calls `push_workflows_for_repos` without them, so `push_workflows.py:154-155`'s `approver_ok: bool = False` applies and every live invocation returns `"live workflow push requires approval"`; `push_workflows_for_repos` (`:158-167`) never inspects `outcome["error"]`, so `misc.py:76` prints `"Pushed workflows for 0 repo(s)"` — a success-shaped message hiding the real cause
- severity: critical (critical_test: e)
- blast_radius: the two components disagree on the shared contract in both directions. In the default engine path the approval/readiness gate provides zero protection — a pipeline classified "manual" gets a branch and a PR identically to a supported one, with no readiness caveat in the PR. Through the documented CLI command the same gate can never be satisfied, and the failure is reported as a zero-count success.
- status: remediated
- resolution: The two boolean parameters a caller could assert for itself are gone. `push_repo_workflows` and `push_workflows_for_repos` no longer take `readiness_ok` and `approver_ok`; they take `db`, and a live push is gated on `workflow_push_readiness(db, repo)` — the same auto/assisted/manual grading the `pipeline-readiness` command reports, now actually consulted at push time. A pipeline that grades `manual` blocks the push and the reason is returned on `result["error"]`; a pipeline that grades `assisted` is pushed with its caveats appended to the PR body, so the reviewer is told the pipeline needs manual attention instead of receiving the same generic text as a fully supported one. `ado2gh/core/scopes/pipelines_scope.py` passes the run's state store at both live-push call sites instead of the two `True` literals, and `ado2gh/cli/misc.py` builds a state store from the configured backend for a live run, so `push-workflows` is satisfiable from the CLI for the first time. `push_workflows_for_repos` now inspects `outcome["error"]`, so a blocked push is reported as a blocked push rather than counted as `"Pushed workflows for 0 repo(s)"` — a success-shaped message that hid the real cause. `dry_run=True` remains the ungated preview path (CA-001).
- regression_check: `tests/pipeline/test_gap_016_workflow_push_approval_gate.py`
- revert_proof: `git stash push -- ado2gh/pipelines/push_workflows.py ado2gh/core/scopes/pipelines_scope.py ado2gh/cli/misc.py ado2gh/reporting/pipeline_readiness.py`, then `.venv\Scripts\python.exe -m pytest tests/pipeline/test_gap_016_workflow_push_approval_gate.py`, then `git stash pop`. With the fix reverted: `1 failed, 2 warnings in 3.73s` — `test_cli_push_workflows_does_not_report_blocked_push_as_zero_count_success`. Taken 2026-09-08 by the T040 implementation agent (Claude Opus 5).
- contract_change: true — `push_repo_workflows` and `push_workflows_for_repos` lose the `readiness_ok` and `approver_ok` parameters and gain `db`, and the result dict gains `readiness_blockers` and `readiness_notes`. Both are internal Python functions with no non-test callers outside `ado2gh/`; no CLI command, route, table or environment variable changes, so `tests/contract/public_surface_snapshot.json` is unchanged by this gap.
- closed_on: 2026-09-08

### GAP-017 (GAP-CLI-01) `phase gate-check` always raises TypeError; no gate row can be written through the CLI

- components: CLI, phase orchestration
- violates: Principle V (gate enforcement); Principle VI (no test exercises the command)
- evidence:
  - `ado2gh/cli/phase.py:46` — `result = checker.check(PhaseType(phase_name), override=override, reason=reason)` (verified)
  - `ado2gh/phase/gate_checker.py:21` — `def check(self, phase: PhaseType) -> PhaseGateResult:` accepts neither keyword; every invocation raises `TypeError` (verified)
  - `ado2gh/phase/gate_checker.py:103-108` — `can_advance` returns False when no gate row exists, and `check()` is the only CLI path that would write one
- severity: high (critical_test: —)
- blast_radius: the documented step 8 of the execution workflow (`phase gate-check --phase poc`) cannot run at all. Because no gate row is ever persisted through the CLI, `can_advance()` is permanently False for every phase, leaving `--force` (GAP-009 (GAP-CLI-02)) as the only way to advance a wave — which is where the unaudited bypass becomes routine rather than exceptional. Rated high rather than critical because the crash itself is fail-safe: nothing advances and nothing is destroyed; the destructive consequence is carried by GAP-009 (GAP-CLI-02).
- status: remediated
- resolution: The signature mismatch itself was already fixed under GAP-009 (GAP-CLI-02)'s commit `ceb6b0b`, which split the CLI's `gate-check` command into two distinct calls — `checker.override(phase, reason)` when `--override` is given, `checker.check(phase)` otherwise — instead of the single call with unsupported `override=`/`reason=` keywords the evidence above describes; those bullets are marked "(verified)" against that landed state, not the current one. `aefea23` (test(GAP-017)) closes the regression-coverage gap this entry was actually tracking: `tests/unit/test_gap_017_gate_check_signature.py` runs the command through Click's `CliRunner` against a real SQLite state DB and asserts the plain call exits 0 and persists a `phase_gates` row, that `--override --reason "..."` persists the override, and that `--override` with an empty reason is refused before any row is written.
- regression_check: `tests/unit/test_gap_017_gate_check_signature.py`
- revert_proof: No proof was recorded when `aefea23` landed. Self-performed 2026-09-12 by Claude (sonnet subagent, T082 close-out): reintroduced the original defect by changing `ado2gh/cli/phase.py`'s non-override branch back to `result = checker.check(phase, override=override, reason=reason)` — the exact call shape the evidence above cites. `.venv/Scripts/python.exe -m pytest tests/unit/test_gap_017_gate_check_signature.py -v` then failed `test_gate_check_runs_and_persists_the_gate` with `TypeError("PhaseGateChecker.check() got an unexpected keyword argument 'override'")`, while `test_gate_check_override_persists_the_reason` and `test_gate_check_override_without_reason_is_refused` still passed, since the override branch calls `checker.override()` and was untouched by the revert. Restored via a combined `git stash push -- ado2gh/cli/phase.py ado2gh/api/accelerator.py ado2gh/core/scopes/git_scope.py ado2gh/pipelines/transform/transformer.py ado2gh/pipelines/transform/job_graph.py`, taken together with the GAP-018, GAP-030 and GAP-032 proofs recorded in this same pass since all five files were reverted together; the same tests re-ran green afterward (17 passed across all four gaps' suites). `git stash drop` was then blocked by this session's own permission classifier — the working tree is confirmed clean on all five files (`git status --short` shows no diff, matching HEAD), but stash entry `5b591bf` was still sitting undropped as of this writing and needs a differently-permissioned session to clear it.
- contract_change: false
- closed_on: 2026-09-12

### GAP-018 (GAP-CLI-03) Migration commands execute live by default with no confirmation, and a declared approval token is discarded

- components: CLI, migration engine, accelerator service
- violates: Principle V; property 6
- evidence:
  - `ado2gh/cli/run_cmd.py:18-19` — `run --dry-run` is `is_flag=True, default=False`, so the command mutates unless the operator opts out
  - `ado2gh/cli/phase.py:20` — `phase run --dry-run` same default
  - `ado2gh/cli/misc.py:41` — `ado-cleanup --dry-run` same default, on a command that disables ADO pipelines and archives repos
  - `ado2gh/cli/run_cmd.py:21-39` — `run()` goes from parsed args to `accel.run_wave(...)` with no `click.confirm`
  - `ado2gh/cli/run_cmd.py:119` — the `rollback` command in the same file *does* call `click.confirm`; repo-wide grep confirms this is the only `click.confirm` in all of `ado2gh/`, so the codebase already decided destructive commands warrant a prompt and applied it to exactly one
  - `ado2gh/api/contracts.py:25` — `RunWaveRequest.live_approval_id: Optional[str] = None` is declared, and grep of `ado2gh/api/accelerator.py` finds zero reads of it: `run_wave` accepts an approval token and discards it (verified)
- severity: high (critical_test: —)
- blast_radius: any operator or script invoking `ado2gh run --wave N` without `--dry-run` immediately performs live repo creation, git mirror/GEI transfer, and scoped pipeline and work-item writes with no interactive confirmation and no server-side approval check on this path. Not rated critical because a documented `--dry-run` option exists on every command named and invoking the command is itself the operator's explicit act; the dangling `live_approval_id` is the sharper defect and the reason this is high rather than medium.
- status: deferred
- resolution: `5f799e7` (fix(GAP-018)) closes the `live_approval_id` half of this gap only: `Accelerator.run_wave` — the single function shared by the CLI, the queue worker, the pipeline runner and both accelerator routes — now looks up a quoted `request.live_approval_id` against `live_execution_approvals` before it loads the migration config or resolves any credential, and raises `ConfigurationError` (surfaced as an HTTP 400 on the route) when the row is missing or not `status == "approved"`; a request that quotes no token is unaffected. Previously the only reader of that field lived in the `POST /v1/migrate` route, whose own RBAC check already ran for every caller that reached it, so a pending, denied, or fabricated approval id reaching `run_wave` through any other caller (CLI, queue worker) was never checked at all. The other three evidence bullets — `run --dry-run`, `phase run --dry-run` and `ado-cleanup --dry-run` all defaulting to `False` with no `click.confirm` anywhere in `ado2gh/cli/run_cmd.py` — are unchanged: flipping those defaults is a contract change against the frozen CLI surface in `tests/contract/public_surface_snapshot.json`, and per FR-024 is held for an explicit operator decision rather than landed unilaterally. Blocker: operator decision pending on the FR-024 contract change (`plan.md` § Approved contract changes, awaiting item: `--dry-run` default). Compensating control: the landed approval-token verification in `Accelerator.run_wave`, plus CA-001's dry-run default on the accelerator path.
- regression_check: `tests/unit/test_gap_018_live_approval_id.py`
- revert_proof: No proof was recorded when `5f799e7` landed. Self-performed 2026-09-12 by Claude (sonnet subagent, T082 close-out): neutralized the check in `Accelerator.run_wave` (`ado2gh/api/accelerator.py`) by changing `if request.live_approval_id:` to `if False and request.live_approval_id:`. `.venv/Scripts/python.exe -m pytest tests/unit/test_gap_018_live_approval_id.py -v` then failed three of five tests — both parametrised cases of `test_undecided_or_denied_approval_migrates_nothing` (`pending`, `denied`) and `test_unknown_approval_id_migrates_nothing`, each with `Failed: DID NOT RAISE ConfigurationError` — while `test_approved_token_lets_the_wave_run` and `test_request_without_a_token_is_unaffected` still passed, since neither depends on the check firing. Restored via the same combined `git stash push -- ado2gh/cli/phase.py ado2gh/api/accelerator.py ado2gh/core/scopes/git_scope.py ado2gh/pipelines/transform/transformer.py ado2gh/pipelines/transform/job_graph.py` used for the GAP-017/GAP-030/GAP-032 proofs; the same tests re-ran green afterward. `git stash drop` was then blocked by this session's own permission classifier — the working tree is confirmed clean (`git status --short` shows no diff on any of the five files), but stash entry `5b591bf` was still sitting undropped as of this writing.
- contract_change: false
- closed_on: — (deferred; the `--dry-run` default flip awaits the operator's FR-024 decision, with the landed approval-token check as its interim control)

### GAP-019 (GAP-AUTH-03) `/sessions/{id}/provision` and `/remediate` take no `Request` and trust a client-supplied `actor`

- components: auth & RBAC, agent service
- violates: Principle V (fail-safe defaults)
- evidence:
  - `services/agent/routes/session_routes.py:1224-1233` — `provision_session(session_id: str, req: ProvisionRequest)` has no `Request` parameter at all and grants `tier="write"` based solely on `req.actor != "approver"`
  - `ado2gh/agents/migration_agent/route_helpers.py:86-89` — `ProvisionRequest.actor: str = "operator"`, free text with no binding to the authenticated identity
  - `services/agent/routes/session_routes.py:1236-1237` — `remediate_session` has the identical structural defect
  - repo-wide search: `session["provision_tier"]`, the field this route writes, has no reader anywhere; neither route has a UI caller or a test
- severity: high (critical_test: —)
- blast_radius: unconditional — no environment variable changes it, since the handler never receives the information needed to check identity. Rated high rather than critical because no destructive or irreversible action follows: the field written has no consumer today, so the practical impact is an unauthenticated write to inert session state. It becomes critical the moment a consumer is added.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- awaiting: operator decision (FR-024, plan.md § Approved contract changes)
- closed_on: —

### GAP-020 (GAP-AUTH-07) Session cookie omits `Secure`; `max_age` hardcoded instead of reading `SESSION_HOURS`

- components: auth & RBAC
- violates: Principle V (fail-safe defaults); property 5
- evidence:
  - `services/accelerator_api/auth_routes.py:59-67` — `_set_session_cookie` calls `response.set_cookie(key=SESSION_COOKIE, value=token, httponly=True, samesite="lax", path="/", max_age=8*3600)`; `secure=` is never passed and Starlette defaults it to False
  - same block — `max_age=8*3600` is a literal rather than a read of `SESSION_HOURS` (`ado2gh/auth/service.py:16`, configurable via `ADO2GH_SESSION_HOURS`), so cookie and server-side lifetimes can drift
  - no compose or k8s manifest in this repo terminates TLS or otherwise prevents cleartext transport
- severity: high (critical_test: —)
- blast_radius: on any deployment without an external HTTPS-enforcing proxy the session cookie — the sole bearer credential for both services — is transmitted in cleartext and can be intercepted, enabling session hijack up to APPROVER/ADMIN. Capped at high rather than critical (b) because the exposure requires a network position rather than a code path that writes the secret to an artefact.
- status: remediated
- resolution: `a5e085a` (fix(GAP-020)) makes `_set_session_cookie` (`services/accelerator_api/auth_routes.py`) pass `secure=` computed from the request's own scheme, instead of never passing it and taking Starlette's `False` default: plain-HTTP local development still gets a cookie the browser will keep, and any HTTPS-terminated deployment gets `Secure` so the platform's one bearer credential never rides in cleartext past a TLS-terminating proxy. `max_age` is now computed from `SESSION_HOURS` (`ado2gh/auth/service.py`) at the moment the cookie is issued, rather than the literal `8*3600`, so the browser-held cookie cannot outlive the server-side session regardless of how `ADO2GH_SESSION_HOURS` is configured — one source of truth, by construction. `_clear_session_cookie` now repeats the same `HttpOnly`/`SameSite`/`Secure` flags on the deleting response, since a browser will refuse to let a non-Secure response overwrite a Secure cookie, which would otherwise leave a stale session cookie behind after logout over HTTPS.
- regression_check: `tests/auth/test_gap_020_session_cookie_flags.py`
- revert_proof: Recorded in the fix commit: `git stash push -- services/accelerator_api/auth_routes.py` → 5 of 8 tests failed → `git stash pop`. Taken 2026-09-12 by the T080-series implementation agent (Claude Opus 5).
- contract_change: false — the public surface snapshot's routes, methods, tables and environment variables are unchanged; only response cookie header values differ.
- closed_on: 2026-09-12

### GAP-021 (GAP-ARCH-01) Package layering is inverted in at least nine places, masked by deferred imports (G-seed 3)

- components: state persistence, auth & RBAC, migration engine, token management, agent service
- violates: Principle IV (Intuitive Architecture & Naming)
- evidence:
  - `ado2gh/state/job_store.py:11-12` — `from ado2gh.api.contracts import JobRecord, JobStatus` / `JobTypeEnum` — the state layer takes its core record types from the API layer
  - `ado2gh/clients/gh_client.py:22` — `GHClient._session` defers `from ado2gh.core.sessions import get_thread_session` into a property body; `ado2gh/core/__init__.py:5` eagerly imports `RollbackHandler`, which transitively imports `ado2gh.clients`, which is the collision a top-level import would hit
  - `ado2gh/core/migration_fr036.py:15,23` — `ado2gh.core` reaches up into `ado2gh.api` with function-local `from ado2gh.api.repo_lock import REPO_LOCK_MANAGER` and `from ado2gh.api.pipeline_store import PipelineRunStore`
  - `ado2gh/auth/service.py` — lazily imports `ado2gh.api.profile_governance` inside `_audit_auth` and `register_operator` specifically to dodge a load-time cycle, while `ado2gh/api/platform_rbac.py:6-7`, `ado2gh/api/audit_access.py:6-7`, `ado2gh/api/profile_governance.py:8`, and `ado2gh/api/live_approval_store.py:14` all import `ado2gh.auth.*` at module top level
  - `ado2gh/api/pipeline_steps.py:943`, `ado2gh/api/validation_run.py:102,113` — API-layer modules reaching across into engine internals
  - `ado2gh/agents/migration_agent/route_helpers.py:26` vs `ado2gh/api/pipeline_runner.py:168` — agent and API layers mutually referencing
  - `ado2gh/auth/models.py` — 34 lines, zero non-stdlib imports, confirming the cycle runs through `auth.service` rather than the foundational models module
  - pre-registered as `specs/013-clean-code-arch-remediation/research.md:344` (G-seed 3, Principle IV, high) and independently reproduced by four separate assessments this pass
- severity: high (critical_test: —)
- blast_radius: no runtime failure today — every deferred import resolves. The cost is that the package graph is the opposite of what the directory names imply, `ado2gh/state/job_store.py` cannot be imported or unit-tested without `ado2gh/api` on the path, and any contributor "cleaning up" a deferred import back to top level is likely to reintroduce a genuine circular-import failure. This is the single largest obstacle to reasoning about which layer owns a given contract.
- status: remediated (residual recorded)
- resolution: `0e1da0a` (fix(GAP-021), T078) and `623152f` (refactor(013), T079) close the three worst-measured edges by relocating five symbols down a layer, plus removing the agents↔api mutual reference this entry's evidence also named. `state → api`: `JobRecord`/`JobStatus`/`JobTypeEnum` move from `ado2gh/api/contracts.py` to `ado2gh/models.py`; `manual_phase_overrides` (from `ado2gh/api/profile_discovery.py`) and `pack_scan_summary_json`/`extract_discovery_fields` (from `ado2gh/api/migration_scan.py`) move to the new `ado2gh/state/scan_payload.py`, closing the function-local imports in `ado2gh/state/postgres_risk_gates_scan_mixin.py` and `sqlite_profile_scan_mixin.py`. `clients → core`: `get_thread_session` moves from the deleted `ado2gh/core/sessions.py` to `ado2gh/http_utils.py`, so `ado2gh/clients/gh_client.py`'s `GHClient._session` property no longer needs a deferred import to dodge the load-time cycle through `ado2gh/core/__init__.py`'s eager `RollbackHandler` import. `api → cli`: `load_repos` moves from `ado2gh/cli/helpers.py` to the new `ado2gh/api/repo_input.py`, closing the function-local imports in `ado2gh/api/pipeline_steps.py` and `ado2gh/api/validation_run.py`. Separately, T079 moves `ado2gh/api/agentic_routes.py` to `services/accelerator_api/routes/history_routes.py` and `ado2gh/agents/migration_agent/route_helpers.py` to `services/agent/routes/_helpers.py`, removing the agents↔api mutual reference this entry's evidence cited (`route_helpers.py:26` vs `api/pipeline_runner.py:168`) by relocating one side out of `ado2gh` entirely. All seven relocations are byte-identical cut/paste diffs — verified via `git show --stat 0e1da0a` and `623152f`; no production function was deleted, so no FR-029 inventory pointer applies. The two lazy-import evidence bullets against `ado2gh/core/migration_fr036.py` (since renamed to `conflict_detection.py`) and `ado2gh/auth/service.py` were not touched by either commit and remain open as the residual below.
- regression_check: `tests/unit/test_gap_021_layering.py` — reuses the AST import-graph walker from `test_no_orphaned_modules.py` (function-local imports included, not just top-level) and asserts zero edges for `ado2gh.state → ado2gh.api`, `ado2gh.clients → ado2gh.core`, `ado2gh.api → ado2gh.cli`.
- revert_proof: `git stash push --` against the 21 tracked paths T078 touched (production: `ado2gh/models.py`, `ado2gh/api/contracts.py`, `ado2gh/http_utils.py`, `ado2gh/core/sessions.py`, `ado2gh/core/orchestration/worker.py`, `ado2gh/clients/gh_client.py`, `ado2gh/state/job_store.py`, `ado2gh/api/migration_scan.py`, `ado2gh/api/profile_discovery.py`, `ado2gh/state/postgres_risk_gates_scan_mixin.py`, `ado2gh/state/sqlite_profile_scan_mixin.py`, `ado2gh/cli/helpers.py`, `ado2gh/api/pipeline_steps.py`, `ado2gh/api/validation_run.py`, `ado2gh/cli/migration.py`, `ado2gh/cli/misc.py`; plus 5 test files whose imports followed the moved symbols — full list in `docs/STRUCTURAL_CHANGELOG.md`'s "013 Phase 8 T078: GAP-021 layering" section) — `python -m pytest tests/unit/test_gap_021_layering.py -q` then fails on all 8 forbidden edges the relocations removed (`ado2gh.api.pipeline_steps -> ado2gh.cli.helpers`, `ado2gh.api.validation_run -> ado2gh.cli.helpers`, `ado2gh.clients.gh_client -> ado2gh.core.sessions`, `ado2gh.state.job_store -> ado2gh.api.contracts`, plus two each from `postgres_risk_gates_scan_mixin` and `sqlite_profile_scan_mixin` against `api.migration_scan`/`api.profile_discovery`) — `git stash pop` restores the tree exactly (`git status` after matches before). The two new modules (`scan_payload.py`, `repo_input.py`) and the new test stay in place throughout since they did not exist pre-fix. Taken 2026-09-12 by Claude (sonnet subagent, T078 finisher).
- contract_change: false — all seven relocations are internal Python module moves; no CLI command, HTTP route, database table or environment variable changes, and `tests/contract/test_public_surface_snapshot.py` passes unchanged for T079's route move too — the HTTP paths never moved, only the module serving them.
- follow_up: two edges from this entry's original evidence are deliberately unclosed and stay open here rather than under a new id, following GAP-015's precedent of recording a residual against the existing entry rather than opening one. `core → api`: `ado2gh/core/orchestration/worker.py:12-13` imports `ado2gh.api.accelerator`/`ado2gh.api.contracts` at module level (driving the Accelerator SDK is that module's job), and `ado2gh/core/conflict_detection.py:33,41` (the file this entry's evidence names under its pre-rename `migration_fr036.py`) does two function-local imports of `api.repo_lock`/`api.pipeline_store`. `auth → api`: `ado2gh/auth/service.py:149,255` still does a function-local import of `ado2gh.api.profile_governance.write_profile_audit`. Neither is one of T078's three named edges, so neither is asserted by `test_gap_021_layering.py`. Closing them means moving the worker out of `ado2gh/core/` and moving the profile-audit writer below `auth`, both out of scope for T078; re-verified against disk at 2026-09-12 (all four line numbers current, not assumed from the commit message).
- closed_on: 2026-09-12

### GAP-022 (GAP-TOOL-01) Coverage ratchet stands at 56% against a constitutional floor of 85%

- components: tooling & guards, deployment & CI artefacts
- violates: Principle VI (Comprehensive Testing & Coverage, NON-NEGOTIABLE)
- evidence:
  - `.github/workflows/ci.yml:36` — `pytest --cov=ado2gh --cov-fail-under=56` (the value as found on 2026-09-07; the ratchet has been raised since — see resolution)
  - `.specify/memory/constitution.md` Quality Gates §1 — "coverage >= 85% on `ado2gh`"
  - `pyproject.toml:88-90` — `[tool.coverage.run]` carries no `omit` key, so 56% is measured over the whole package and is the honest figure
- severity: high (critical_test: —)
- blast_radius: 29 percentage points of the package have no regression net, which is the enabling condition for several findings in this register that shipped undetected (GAP-017 (GAP-CLI-01)'s always-raising command, GAP-013 (GAP-ENG-01)'s unreachable guard, GAP-015 (GAP-SEAM-01)'s key mismatch). Closing the gap to 85% is explicitly out of scope for this feature per FR-027a.
- status: deferred
- resolution: exclusions removed and honest ratchet active since T011 (starting value 56 %, current value 60 %); 85 % outstanding, follow-up owner `operator`. FR-027a and the operator decision of 2026-09-07 (`plan.md` § Coverage measurement) scope the climb to 85 % out of feature 013, so this entry stays deferred rather than remediated. The compensating control is the ratchet itself: `--cov-fail-under` is measured over the whole package with no `omit` list and has never been lowered — 56 at T011 (`9c83a29`) → 58 at increment 1 (`0ab1ac9`) → 59 at increment 3 (`7eba3d7`) → 59 at HEAD `7396829`, with increment 8 measuring 58 and correctly left at 59. The full trail is copied into `plan.md` § Coverage measurement. Measured coverage on 2026-09-09 is 60.27 %, one point above the gate, because increments 10–12 were still in flight; T094/T095 re-measure and raise the gate at completion. No individual is named as follow-up owner anywhere in `plan.md` (it says only "a time-boxed follow-up owner"), so the owner is recorded here as `operator`.
- regression_check: `.github/workflows/ci.yml:36` — the `--cov-fail-under` value on that line, monotonically non-decreasing and measured over the whole package, since `pyproject.toml:88-90` `[tool.coverage.run]` carries no `omit` key. It read 59 at HEAD `7396829` when this entry was written and was already being raised to 60 by an uncommitted increment-11 edit, which is the control working as intended rather than a stale citation
- revert_proof: raise the gate above the measured figure, on the command line only, and the CI check fails: `.venv\Scripts\python.exe -m pytest -q --cov=ado2gh --cov-fail-under=99 -p no:cacheprovider` ends `FAIL Required test coverage of 99% not reached. Total coverage: 60.27%` with `ERROR: Coverage failure: total of 60 is less than fail-under=99`; the real gate `.venv\Scripts\python.exe -m pytest -q --cov=ado2gh --cov-fail-under=59 -p no:cacheprovider` passes on the same tree with `Required test coverage of 59% reached. Total coverage: 60.27%`. Both runs were 937 passed / 30 skipped and both carried the same one unrelated failure, `tests/auth/test_gap_002_auth_disabled_bypasses_live_approval.py::test_anonymous_caller_cannot_switch_agent_session_to_live`, which is GAP-002's own revert proof being taken concurrently in the same tree; the coverage verdict is therefore the only difference between the two runs. Taken 2026-09-09 by the T075/T076 implementation agent (Claude Opus 5). Neither `.github/workflows/ci.yml` nor `pyproject.toml` was edited for the proof — the command-line form is the alternative T076 allows, and restoring an `omit` block would have contradicted FR-027a.
- contract_change: false
- closed_on: — (deferred; carried to the follow-up owner with the ratchet as its control)

### GAP-023 (GAP-TOOL-02) mypy is configured so that it cannot fail, and never reaches most of the package

- components: tooling & guards, deployment & CI artefacts, pipeline transformation
- violates: Principle I (Clean Code & Readability); Principle VI
- evidence:
  - `.github/workflows/ci.yml:22` — `mypy ado2gh/ --ignore-missing-imports || true` — the exit status is discarded, so no type error can fail CI
  - `pyproject.toml:109-111` — `[[tool.mypy.overrides]] module = ["ado2gh.state.*", "ado2gh.cli.*", "ado2gh.pipelines.*"]` with `ignore_errors = true`, suppressing three of the largest packages including the state layer
  - measured: the CI invocation aborts on a stub error at `.venv/Lib/site-packages/numpy/__init__.pyi:737` before checking a single `ado2gh` line, so the step is not merely permissive — it produces no analysis at all
  - measured, `ado2gh/pipelines` alone with the override bypassed: exactly two real errors — `ado2gh/pipelines/validation/workflow_validator.py:93` (`List item 0 has incompatible type "str | None"`, a lost cross-method narrowing, runtime-safe) and `ado2gh/pipelines/resolve/template_resolver.py:160` (`Function "fetch_template" could always be true in boolean context`, a redundant conditional already narrowed at `:136`)
- severity: high (critical_test: —)
- blast_radius: the repository advertises static type checking in CI and receives none. Combined with the 56% coverage ratchet, two of the three automated quality gates named in the constitution are non-functional, which is why contract mismatches such as GAP-015 (GAP-SEAM-01) and signature mismatches such as GAP-017 (GAP-CLI-01) can reach the default branch green.
- status: remediated
- resolution: Deleted the blanket `[[tool.mypy.overrides]] ignore_errors` block and the `|| true` mask on the CI mypy step (`.github/workflows/ci.yml:22`); added a numpy-only override (`follow_imports = "skip"` plus `follow_imports_for_stubs = true`, both required for `.pyi` files) instead of bumping `python_version`, which stays 3.11; fixed all 389 reported errors across 55 files with precise annotations and explicit Optional narrowing. Landed in two commits: `d1427fd` (T077) did the bulk of the fix, and `7a05ca9` (T077) committed nine files left uncommitted when `d1427fd` landed — eight `StateDB` → `StateDBBase` annotation swaps (`ado2gh/core/conflict_detection.py`, `migration_engine.py`, `rollback.py`, `ado2gh/phase/progress_tracker.py`, `repo_scoring.py`, `ado2gh/pipelines/inventory.py`, `ado2gh/reporting/csv_exporter.py`, `reporter.py`) plus `ado2gh/state/job_store.py`, an original T077 baseline file whose `JobRecord` construction sites and `created_at`/`updated_at` attribute writes needed `# type: ignore[call-arg]`/`[attr-defined]` rather than adding those fields to `JobRecord`, which would change its serialized shape (the runtime consequence of that gap is now tracked separately as GAP-054). The T077 agent's own `# type: ignore` figures were inconsistent between its two commit messages, so this entry measures instead of quoting: `git grep -n "type: ignore" ab4a303 -- ado2gh | wc -l` (the pre-T077 baseline) is 3; the same command at `7a05ca9` (current HEAD makes no further change to these lines) is 19, of which 10 are in `job_store.py` alone. `mypy ado2gh/ --ignore-missing-imports` exits 0 on 195 source files.
- regression_check: the CI mypy step `.github/workflows/ci.yml:22` (`mypy ado2gh/ --ignore-missing-imports`, no `|| true`)
- revert_proof: `git stash push -- .github/workflows/ci.yml pyproject.toml` reproduced both halves of the defect: mypy aborts with `.venv\Lib\site-packages\numpy\__init__.pyi:737: error: Type statement is only supported in Python 3.12 and greater [syntax]` (exit 2) and the restored `|| true` masks that exit as 0; `git stash pop` restored the green state. Taken 2026-09-12 by Claude (sonnet subagent, T077).
- contract_change: false
- closed_on: 2026-09-12

### GAP-TOOL-05 — tombstone, not a gap

This placeholder was merged into GAP-021 (GAP-ARCH-01) during the assessment. No sequential
`GAP-NNN` id was assigned to it, it is excluded from the Summary counts and tables, and the
placeholder identifier `GAP-TOOL-05` is never reused.

### GAP-024 (GAP-UI-02) Boolean `recommended_value` is stringified server-side and re-read as truthy in the browser, inverting the `confirm_execute` safe default

- components: web console, agent service
- violates: Principle V; Principle VI
- evidence:
  - `ado2gh/agents/migration_agent/hitl/form_fields.py:176-183` — `build_field_recommendations` sets `recs["confirm_execute"]["recommended_value"] = False` as a real Python bool
  - `ado2gh/agents/migration_agent/hitl/form_fields.py:74-78` — `field_dict_from_spec` unconditionally does `field["recommended_value"] = str(recommended_value).strip()[:120]`, turning `False` into `"False"`; `ado2gh/agents/migration_agent/hitl/forms.py:39-43` repeats the pattern
  - `apps/migration-ui/src/lib/agentChat.ts:162` — `if (field.type === 'checkbox') { return Boolean(field.recommended_value); }`; `Boolean("False")` is `true`, so the checkbox initialises checked against the backend's intent
  - `apps/migration-ui/src/lib/agentChat.test.ts:110-170` — no case covers a `"False"` / `false` `recommended_value` for a checkbox
  - `apps/migration-ui/src/components/AgentChat.tsx:411` — the checkbox is disabled when live approval is required but its value is not forced back to unchecked, so it can render disabled-and-checked
  - not critical: `ado2gh/agents/migration_agent/route_helpers.py:544` and `ado2gh/agents/migration_agent/policies.py:138-149` — `_try_start_pev_run` re-checks `session_requires_live_approval` from server-held state before any live run, independent of the submitted `confirm_execute`
- severity: high (critical_test: —)
- blast_radius: the confirmation control an operator relies on to see whether they are about to authorise live execution renders in the wrong state by default, in every session that reaches this form. No path to unauthorised live execution was found because the server re-checks independently, which is why this is high rather than critical (a). The root cause is in two backend files, so any other boolean recommended field reproduces it.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- awaiting: operator decision (FR-024, plan.md § Approved contract changes)
- closed_on: —

### GAP-025 (GAP-UI-03) All 46 component and page files are untested and no linter is configured

- components: web console
- violates: Principle VI (NON-NEGOTIABLE); Principle I (no automated enforcement)
- evidence:
  - 20 files under `apps/migration-ui/src/components/*.tsx` and 26 under `apps/migration-ui/src/app/**/page.tsx`; all 8 test files live under `src/lib/*.test.ts` or `src/__tests__/`, none co-located with any component or page
  - `apps/migration-ui/src/components/AgentChat.tsx` — 1877 lines, carries the live-execution approval UI (`:1772-1816`) and the defect in GAP-024 (GAP-UI-02), with zero direct coverage
  - `apps/migration-ui/src/__tests__/exports-documented.test.ts:1-77` — tests `walkExports` against synthetic fixtures in a temp directory, not the real codebase's JSDoc coverage
  - `apps/migration-ui/package.json` — no eslint dependency and no lint script; globs for `.eslintrc*` and `eslint.config.*` return nothing
  - measured: `npx vitest run` in `apps/migration-ui` → 8 files, 42 tests, all passing
- severity: high (critical_test: —)
- blast_radius: no regression net for any UI change, including the settings pages that handle secrets and the live-execution approval flow. Demonstrated rather than hypothetical: GAP-024 (GAP-UI-02) lived undetected in exactly this untested surface.
- status: remediated
- resolution: `617b08f` (test(GAP-025)) adds a co-located `*.test.tsx`/`*.test.ts` file beside every component and page file (19 under `src/components/`, 26 under `src/app/**`), plus `apps/migration-ui/vitest.config.ts` configuring the `jsdom` environment and automatic JSX runtime the new files need, and the `src/__tests__/components-and-pages-tested.test.ts` guard that walks the real source tree — unlike the pre-existing `exports-documented.test.ts`, which only exercises synthetic fixtures — and fails if any component or page file has no sibling test. Coverage prioritised the surface adjacent to GAP-024 (GAP-UI-02): the live-execution approval UI, the gate-override justification field that only renders on a live run, permission-gated loading states, and the credential fields masked per CA-003. Interaction assertions (events, effects) stay out of scope for this entry. No new dependency was added; rendering uses `react-dom/server` and the project's existing `tsc --strict`/`noUnusedParameters` settings. **Residual finding**: no linter is configured for `apps/migration-ui` — `eslint` is neither installed nor declared as a devDependency, and adding one was out of scope for this entry per SC-003's bar on new dependencies; `tsc --strict` plus this new test suite is the compensating control until an operator approves the devDependency addition. Verified in the commit: `npx tsc --noEmit -p tsconfig.json` → no errors; `npx vitest run` → 55 files / 166 tests (baseline before this commit: 9 files / 48 tests).
- regression_check: `apps/migration-ui/src/__tests__/components-and-pages-tested.test.ts`
- revert_proof: No proof was recorded when `617b08f` landed. Self-performed 2026-09-12 by Claude (sonnet subagent, T082 close-out): renamed the tracked `apps/migration-ui/src/components/BrandLogo.test.tsx` to `BrandLogo.test.tsx.disabled`, outside vitest's `*.test.tsx` glob, then ran `npm --prefix apps/migration-ui test -- src/__tests__/components-and-pages-tested.test.ts`, which failed: `expected [ 'src/components/BrandLogo.tsx' ] to deeply equal []`. Renamed the file back to its original name; the same command then passed (2 passed), and `git status --short` showed no residual diff on either `BrandLogo.tsx` or `BrandLogo.test.tsx`.
- contract_change: false
- closed_on: 2026-09-12

### GAP-026 (GAP-PHASE-03) `execute_phase` has no test coverage

- components: phase orchestration
- violates: Principle VI (NON-NEGOTIABLE)
- evidence:
  - `ado2gh/phase/batch_executor.py:35` — `def execute_phase(self, phase: PhaseType, waves: list[WaveConfig], ...)`, the function that drives every batched wave run; the phase test files exercise risk scoring, wave assignment, and gate thresholds but not the executor loop
  - reproduction: `grep -rn 'execute_phase' tests/` → **0 matches** (verified at T035)
- severity: high (critical_test: —)
- blast_radius: the single function that fans a phase out into batches, applies gates between them, and checkpoints progress is unverified, so a regression in batching or checkpoint placement would ship green.
- status: remediated
- resolution: `84618d2` (test(GAP-026)) adds seven tests exercising `BatchExecutor.execute_phase` through real `Accelerator.run_phase` calls rather than summary counters: batch boundaries follow each phase's configured `batch_size`; only the requested phase's waves are batched, not sibling phases'; a phase with no waves returns a zeroed summary and touches no repo; one `batch_checkpoints` row is written per completed batch with the resume cursor advancing with it; a resumed run executes only the batches after the last checkpoint, never re-running a completed one; `DRY_RUN` writes no checkpoint and opens no wave run, so a live run afterward still has every batch to do; and a blocked prior-phase gate stops every batch until an audited override releases them. This entry's own blast_radius line above ("applies gates between them") is corrected by the tests themselves: the gate is evaluated once, ahead of the batch fan-out, by `Accelerator.run_phase`, not between individual batches. No production file was changed by this commit.
- regression_check: `tests/unit/test_gap_026_execute_phase.py`
- revert_proof: Recorded in the fix commit, taken 2026-09-09, against a backup copy of `ado2gh/phase/batch_executor.py` rather than `git stash` since the commit changed no production file to stash: collapsing the batching loop to a single unbatched pass (`batches = [all_repos]`) produced 5 failed / 2 passed (`assert 1 == 3` on batches_run); skipping both `upsert_batch_checkpoint` calls produced 4 failed / 3 passed (`assert [] == [0, 1, 2]` on the checkpoint batch numbers). The original file was restored from the backup; `git diff -- ado2gh/phase/batch_executor.py` was empty afterward.
- contract_change: false
- closed_on: 2026-09-09

### GAP-027 (GAP-ENG-04) "Idempotent — skips completed scopes" is false; per-handler idempotency is ad hoc

- components: migration engine, CLI
- violates: Principle II (false docstring); Principle IV (no idempotency contract on `ScopeHandler`); property 1
- evidence:
  - `ado2gh/cli/run_cmd.py:22` — `"""Execute migration wave(s). Idempotent — skips completed scopes."""`
  - `ado2gh/core/migration_engine.py:92-113` — the scope loop calls `handler.migrate(...)` unconditionally for every requested scope on every invocation; `MigrationStatus.COMPLETED` is written at `:129` and never read back as a skip condition
  - `ado2gh/core/scopes/git_scope.py:163-182` — `_verify_existing_target_repo`, the real HEAD-SHA pre-check, is wired only into the `gei` branch; the mirror branch proceeds after a bare existence check
  - `ado2gh/core/scopes/git_scope.py:301-306` — mirror runs `git push --force origin +refs/heads/*:refs/heads/* +refs/tags/*:refs/tags/*` unconditionally, capable of discarding GitHub-side changes made since the last run
  - `ado2gh/core/scopes/work_items_scope.py:33-54` — `create_issue` in a loop with no dedup check; re-running re-creates one duplicate issue per ADO work item
  - `ado2gh/core/scopes/pipelines_scope.py:108-117` — the contrast: this handler filters `pending` against `completed_ids` from the StateDB, proving per-handler idempotency is achievable and simply not required by `ado2gh/core/scopes/base.py`'s Protocol
  - `ado2gh/models.py:51` — `DEFAULT_MIGRATION_STRATEGY = MigrationStrategy.GEI.value`; `ado2gh/core/config_loader.py:88` — default scope list is `["repo"]`, so both affected paths are non-default selections
- severity: high (critical_test: c)
- blast_radius: any retry or wave re-run — including `pipelines retry-failed`, which re-runs the whole wave — force-clobbers GitHub-side git state for mirror-strategy repos and duplicates GitHub issues for work-item-scoped repos, while the CLI docstring tells the operator re-running is safe. Downgraded from critical (c) because both affected paths require a non-default strategy or an opt-in scope.
- status: remediated
- resolution: `95ddc3f` (docs(GAP-027)) corrects the false "Idempotent — skips completed scopes" claim rather than implementing idempotency: `ado2gh/cli/migration.py`'s docstring (and `ado2gh run --help`, which changes under FR-010a as a result) now says what a re-run actually does per scope, and the `ScopeHandler` protocol docstring (`ado2gh/core/scopes/base.py`) states plainly that it imposes no idempotency requirement, so a new handler's author is told what guarantee, if any, to provide rather than inheriting an assumed one. `ado2gh/core/migration_engine.py`, `ado2gh/core/scopes/git_scope.py`, `ado2gh/core/scopes/pipelines_scope.py` and `ado2gh/core/scopes/work_items_scope.py` each gained a docstring stating their actual, differing re-run behaviour — `PipelinesScopeHandler` guards itself against re-running completed pipeline ids within one wave, `GitScopeHandler` force-pushes again under the mirror strategy but skips (or refuses, on a HEAD mismatch) under GEI, and `WorkItemsScopeHandler` consults nothing and duplicates an issue per ADO work item on every re-run. No behaviour changed anywhere; `tests/unit/test_gap_027_idempotency_docstrings.py` keys on the specific behavioural claims each docstring now makes, so the prose was rewritten but the underlying ad hoc idempotency this entry describes is not itself un-said. `phase run --help` and `phase gate-check --help` are byte-identical to before; the frozen `cli_commands` table in `tests/contract/public_surface_snapshot.json` is untouched.
- regression_check: `tests/unit/test_gap_027_idempotency_docstrings.py`
- revert_proof: No revert proof was recorded when `95ddc3f` landed — the commit's own message describes only the docstring changes, not a stash/failure cycle — and `run-gap-017-018-027.txt` is a plain 21-passed log covering this test file alongside GAP-017's and GAP-018's. Self-performed 2026-09-12 by Claude (sonnet subagent, T083 close-out) as a partial proof: the fix is already an ancestor of HEAD, so `git stash push` against it is a no-op (`No local changes to save`); five of the six fixed files — `ado2gh/cli/migration.py`, `ado2gh/core/scopes/base.py`, `ado2gh/core/scopes/git_scope.py`, `ado2gh/core/scopes/pipelines_scope.py`, `ado2gh/core/scopes/work_items_scope.py` — were instead reverted with `git checkout 95ddc3f^ -- <path>`, each confirmed clean (`git status --short`) immediately beforehand. The sixth, `ado2gh/core/migration_engine.py`, was left untouched per the standing instruction not to touch a file a concurrent agent (T077) has modified; `git show 95ddc3f -- ado2gh/core/migration_engine.py` independently confirms its slice of the fix is a self-contained +9-line docstring insertion with no code change, so omitting it from the revert does not weaken the proof. Reverting the five files also reintroduced a since-superseded import, `from ado2gh.cli.helpers import load_clients, load_repos`, because the unrelated later commit `0e1da0a` (GAP-021) moved `load_repos` to `ado2gh.api.repo_input`; that one import line was corrected to the current module split so the test would exercise the docstring regression rather than an unrelated `ImportError`, with every other line of the reverted files left at their pre-fix content. `.venv/Scripts/python.exe -m pytest tests/unit/test_gap_027_idempotency_docstrings.py -v` then gave `4 failed, 2 passed in 4.00s`: `cli-run`, `git-scope`, `work-items-scope` and `scope-handler-protocol` failed on the missing required phrases, exactly as expected from the five-file revert; `migration-engine` passed because that file was deliberately not reverted. `pipelines-scope` also passed, but not as a valid check: its assertion only requires the substring `"skip"`, and `PipelinesScopeHandler.migrate`'s pre-fix docstring already contained it for an unrelated reason (`ScopeResult`'s stats fields, "counting pipelines transformed, failed and skipped"), confirmed by printing the reverted docstring directly — that one parametrised case does not discriminate pre- and post-fix text, the other five do. Output: `specs/013-clean-code-arch-remediation/run-t083-gap-027-revert-proof.txt`. All five reverted files were restored with `git checkout HEAD -- <path>`, confirmed clean (`git status --short` empty) immediately after.
- contract_change: false
- closed_on: 2026-09-12

### GAP-028 (GAP-STATE-01) `DynamoDBJobStore` can silently double-claim or duplicate a job under concurrency

- components: state persistence
- violates: Principle V (fail-safe defaults); properties 1 and 2
- evidence:
  - `ado2gh/state/job_store.py:51` and `:173` — SQLite and Postgres `jobs` schemas both declare `idempotency_key TEXT UNIQUE`, a DB-enforced constraint
  - `ado2gh/state/job_store.py:294-303` — `DynamoDBJobStore._ensure_table()` declares only `id` as the partition key; `idempotency_key` is an unindexed attribute with no uniqueness enforcement
  - `ado2gh/state/job_store.py:354-360` — `get_by_idempotency()` uses an eventually-consistent `scan()` with a `FilterExpression` and no `ConsistentRead=True`, so two racing `enqueue()` calls with the same key can both miss and both insert
  - `ado2gh/state/job_store.py:220-238` — `PostgresJobStore.claim_next()` uses `SELECT ... FOR UPDATE SKIP LOCKED` (`:225`), atomic under concurrent workers
  - `ado2gh/state/job_store.py:365-379` and `:305-317` — `DynamoDBJobStore.claim_next()` scans for `PENDING` then `put_item()`s with no `ConditionExpression`; two workers can both claim the same job with no error to either
- severity: high (critical_test: e)
- blast_radius: a serverless deployment — the role `ado2gh/state/storage_config.py`'s own docstring advertises for this backend — can execute the same migration job twice concurrently, or create duplicate jobs for one idempotency key, with no error, no log, and no audit entry. Downgraded from critical (e) because it requires `ADO2GH_STORAGE_BACKEND=dynamodb`, a non-default topology.
- status: remediated
- resolution: `d88db23` (fix(GAP-028)) makes `DynamoDBJobStore.claim_next()` atomic: instead of scanning for a `PENDING` job and writing it back with an unconditional `put_item()`, it now moves the row from `pending` to `running` with a conditional `update_item()` guarded by `Attr("status").eq("pending")`, so a worker that loses the race gets `None` back instead of silently duplicating the claim — the same effect `PostgresJobStore.claim_next()` already gets from `SELECT ... FOR UPDATE SKIP LOCKED`. `get_by_idempotency()`'s scan is now strongly consistent (`ConsistentRead=True`), closing the second race where two callers enqueuing under the same idempotency key could both miss an eventually-consistent read and both insert. A claim lost to the race is no longer silent: it is logged at WARNING and audited as a `job.claim_conflict` event through the existing `AuditWriter` (CA-004), with the payload naming only `job_id`, `job_type` and the backend — never the job payload itself (CA-003).
- regression_check: `tests/unit/test_gap_028_dynamo_double_claim.py`
- revert_proof: No revert proof was recorded when `d88db23` landed; `run-gap-028.txt` is a plain 37-passed log with no failure/stash content. Self-performed 2026-09-12 by Claude (sonnet subagent, T083 close-out): the fix is already an ancestor of HEAD, so `git stash push -- ado2gh/state/job_store.py` is a no-op (`No local changes to save`); the file was instead reverted with `git checkout d88db23^ -- ado2gh/state/job_store.py` (parent commit `84618d2a`), confirmed clean (`git status --short`) immediately beforehand. The reverted content's import of `JobStatus` from `ado2gh.api.contracts` (`from ado2gh.api.contracts import JobRecord, JobStatus`, `job_store.py:16` at `84618d2a`) no longer resolves, because the unrelated later commit `0e1da0a` (GAP-021) moved `JobStatus` to `ado2gh.models` without leaving a re-export; `ado2gh/api/contracts.py:8` still does `from ado2gh.models import JobRecord, JobTypeEnum`, so those two names resolve unchanged and only the `JobStatus` half of that one line needed correcting to import from `ado2gh.models` directly — the sibling line, `from ado2gh.api.contracts import JobTypeEnum as JobType`, was left as-is. `.venv/Scripts/python.exe -m pytest tests/unit/test_gap_028_dynamo_double_claim.py -v` then gave `4 failed in 4.23s`: three are `AssertionError`s that directly trip the exact logic the fix added — `test_two_racing_workers_claim_at_most_one_job` (`both workers claimed the same job: ['job-1', 'job-1']`), `test_claim_write_carries_a_condition_expression` (`claim write carries no ConditionExpression`), and `test_get_by_idempotency_reads_consistently` (`idempotency scan is eventually consistent`); only the fourth, `test_claim_conflict_is_logged_and_audited`, fails earlier on a constructor-level `TypeError: DynamoDBJobStore.__init__() got an unexpected keyword argument 'audit_writer'`, since the pre-fix constructor takes no audit-writer parameter. Three of the four failures are a direct trip of the race-condition/consistency logic itself, not a constructor mismatch. Output: `specs/013-clean-code-arch-remediation/run-t083-gap-028-revert-proof.txt`. Restored with `git checkout HEAD -- ado2gh/state/job_store.py`, confirmed clean (`git status --short` empty) immediately after.
- contract_change: false
- closed_on: 2026-09-09

### GAP-029 (GAP-STATE-02) `create_state_db()` silently discards `--db` under a non-default backend and crashes on a backend its own config validates

- components: state persistence, CLI
- violates: Principle IV; Principle V (fail-safe defaults)
- evidence:
  - `ado2gh/state/storage_config.py:9-12` — `StorageBackend` lists `SQLITE`, `POSTGRES`, `DYNAMODB` as three first-class values
  - `ado2gh/state/storage_config.py:36,47-50` — `dynamodb_table` defaults to `"ado2gh"`, so the only DynamoDB validation almost never fires and `from_env()` returns a fully valid `StorageConfig(backend=DYNAMODB, ...)`
  - `ado2gh/state/factory.py:21-30` — `create_state_db()` branches only on SQLITE (`:23`) and POSTGRES (`:26`), then falls through to `raise ValueError(f"Unsupported storage backend: {cfg.backend}")` (`:30`) for the value the config layer just validated, with no reference to the env var that caused it
  - `ado2gh/state/factory.py:15,21,26-28` — `db_path` (the `--db` value) is passed as `sqlite_default` and used only as the SQLite fallback; the Postgres branch uses `cfg.database_url` exclusively and never references it
  - `ado2gh/cli/phase.py:23,39,51,65,83`, `ado2gh/cli/pipelines.py:20,34,44`, `ado2gh/cli/run_cmd.py:20`, `ado2gh/cli/misc.py:16` — every `--db` option is `default="migration_state.db", show_default=True` with no mention that `ADO2GH_STORAGE_BACKEND` overrides it
- severity: high (critical_test: —)
- blast_radius: an operator who sets `ADO2GH_STORAGE_BACKEND=postgres` in a shell or CI job and runs `ado2gh phase run --db test.db` believing they are isolated to a scratch file is silently redirected to the shared production database named by `ADO2GH_DATABASE_URL`, with no warning. Separately, any command run with the DynamoDB backend crashes with an unhandled `ValueError` for a configuration the platform accepts as legitimate.
- status: remediated
- resolution: `4999e19` (fix(GAP-029), T081) makes `create_state_db()`'s `backend` a keyword-only argument that defaults from the environment, so all 88 existing call sites are unchanged and a caller that does care can say which backend it wants explicitly. A non-default `--db` under a non-SQLite backend can no longer be silently discarded without a trace: it is now logged as a WARNING naming `ADO2GH_STORAGE_BACKEND` as the reason `db_path` was not honoured — raising outright was rejected because every CLI command passes a `--db` default and would then fail under a perfectly valid Postgres deployment, so this entry's fault was the silence, not which value wins. A DynamoDB backend is now refused with a message naming the actual configuration variable and pointing at the job store, the only thing this codebase backs with DynamoDB (`docs/ARCHITECTURE.md:188-190`), rather than the previous bare `Unsupported storage backend: {cfg.backend}`. `ADO2GH_SQLITE_PATH` still outranks an explicit `db_path` — deployments use it to point every command at one mounted volume, and `tests/conftest.py` relies on that precedence for per-test database isolation (GAP-051 (GAP-TOOL-07)) — so that precedence is documented by this fix, not changed. `tests/unit/test_state_factory.py::test_factory_no_dynamodb_option`, which had asserted only that the substring `"dynamodb"` never appears in the factory's source — a check that would have passed the exact bare-error-message defect this gap reports — was rewritten to assert FR-009's real invariant via an AST walk: no DynamoDB import and no DynamoDB construction anywhere in `ado2gh/state/factory.py`.
- regression_check: `tests/unit/test_gap_029_backend_parity.py`
- revert_proof: Recorded in the fix commit, taken 2026-09-12 by the T081 implementation agent (Claude Opus 5): `git stash push -- ado2gh/state/factory.py` → 4 failed, 11 passed → `git stash pop`.
- contract_change: false
- closed_on: 2026-09-12

### GAP-030 (GAP-TOKEN-02) GitHub token is passed in subprocess argv for the mirror strategy

- components: token management & audit writing, migration engine
- violates: Principle V; property 5
- evidence:
  - `ado2gh/core/scopes/git_scope.py:267-271`, `:290-293`, `:348-352` — the token is embedded in the remote URL passed as a subprocess argument, making it visible in the process table to any local user and in any tooling that captures argv
  - `ado2gh/core/scopes/git_scope.py:361-373` — the GEI path correctly passes the token via the environment instead, showing the safe pattern is already in use in the same file
  - mitigating: `ado2gh/core/scopes/git_scope.py:18-25` `_redact()` scrubs the value from captured subprocess output, so the leak is to argv rather than to logs
- severity: high (critical_test: —)
- blast_radius: on any multi-user host the platform token is readable from the process table for the duration of a clone or push. Capped at high rather than critical (b) because the default strategy is `gei`, which uses the environment; the argv path requires selecting `migration_strategy: mirror`.
- status: remediated
- resolution: `80a9301` (fix(GAP-030)) replaces the mirror strategy's token-bearing remote URL (`https://x-access-token:<token>@github.com/...`, passed as a literal subprocess argument to `git remote set-url`, `git push` and `git lfs push`) with the same environment-based mechanism the GEI path and the Azure DevOps clone already used: `_build_ado_git_env`'s `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` triple is generalised into `_build_git_auth_env`, optionally scoped to a single remote via `http.<url>.extraHeader`. The mirror clone, the mirror push and the LFS push now all pass the tokenless `https://github.com/<org>/<repo>.git` in argv, with the credential carried only in the subprocess environment (CA-003). The Azure DevOps clone side of the same file is unchanged, and a dry run still starts no subprocess at all (CA-001).
- regression_check: `tests/core/test_gap_030_token_not_in_argv.py`
- revert_proof: No proof was recorded when `80a9301` landed. Self-performed 2026-09-12 by Claude (sonnet subagent, T082 close-out): reintroduced the original defect by rebuilding `_run_mirror`'s `target_url` in `ado2gh/core/scopes/git_scope.py` with the token embedded (`https://x-access-token:{gh_token}@github.com/...`) instead of the plain URL. `.venv/Scripts/python.exe -m pytest tests/core/test_gap_030_token_not_in_argv.py -v` then failed four of six tests — `test_no_argv_element_carries_a_credential` (the placeholder credential turned up inside an argv element), `test_push_remote_is_tokenless`, `test_push_env_carries_the_url_scoped_auth_header` (the `http.<url>.extraHeader` config key the env-based path writes was simply absent) and `test_lfs_push_is_tokenless_and_authenticated_through_env` — while `test_ado_clone_auth_is_unchanged` and `test_dry_run_starts_no_subprocess` still passed, since the revert touched only the GitHub mirror-push URL construction. Restored via the same combined `git stash push -- ado2gh/cli/phase.py ado2gh/api/accelerator.py ado2gh/core/scopes/git_scope.py ado2gh/pipelines/transform/transformer.py ado2gh/pipelines/transform/job_graph.py` used for the GAP-017/GAP-018/GAP-032 proofs; the same tests re-ran green afterward (17 passed across all four gaps' suites together). `git stash drop` was then blocked by this session's own permission classifier — the working tree is confirmed clean on all five files (`git status --short` shows no diff, matching HEAD), but stash entry `5b591bf` was still sitting undropped as of this writing and needs a differently-permissioned session to clear it.
- contract_change: false
- closed_on: 2026-09-09

### GAP-031 (GAP-PIPE-01) ADO variable-group variables are captured as metadata and never reach the generated workflow or its notes

- components: pipeline transformation
- violates: Principle IV; property 4 (validation after transfer)
- evidence:
  - `ado2gh/pipelines/extractor.py:220-228` and `:258-267` — variable-group references are captured into `meta.variable_groups` but never appended to `meta.variables`, the only list the transformer consumes
  - `ado2gh/pipelines/extractor.py:383` — `_score_complexity` weights `len(meta.variable_groups) * 2`, so the data is treated as meaningful elsewhere; its absence downstream is an omission, not a design decision
  - `ado2gh/pipelines/transform/transformer.py:66-67` — `env_keys` derives solely from the env block built from `meta.variables`, so group-sourced names never enter it
  - `ado2gh/pipelines/transform/expressions.py:45-59` — `rewrite_expressions_inplace(obj, env_keys)` has no warnings channel; an unresolved `$(name)` macro is left as literal text with no warning
  - `ado2gh/pipelines/transform/transformer.py:371-411` — `_write_migration_notes` has a `## Service Connections` section (`:394-399`) and no equivalent for `variable_groups`; grep confirms zero `variable_group` references in `transformer.py`, `job_graph.py`, `triggers.py`
  - mitigating: `ado2gh/reporting/pipeline_readiness.py:156-160,184-191` does warn "Variable groups need manual setup" and forces classification away from `auto`, so the omission is not silent at the aggregate report level
- severity: high (critical_test: —)
- blast_radius: a pipeline using group-sourced variables produces a workflow with dead literal `$(groupVar)` text and per-pipeline notes that never mention the group; an operator reading only the generated YAML and its notes has no signal anything is missing, and must separately consult the readiness report to learn it.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- awaiting: operator decision (FR-024, plan.md § Approved contract changes)
- closed_on: —

### GAP-032 (GAP-PIPE-02) The branch that keeps secret values out of generated YAML has no test

- components: pipeline transformation
- violates: Principle VI (NON-NEGOTIABLE); property 5
- evidence:
  - `ado2gh/pipelines/transform/transformer.py:361-369` (`_build_env_block`) and `ado2gh/pipelines/transform/job_graph.py:144-151` — the only two sites that write `PipelineVariable.value` into generated output, both gated behind `if v.is_secret:` substituting `${{ secrets.NAME }}`
  - reproduction: `Grep pattern="PipelineVariable\(|is_secret\s*=\s*True|isSecret" path="tests"` → **0 matches**; no test anywhere constructs a secret `PipelineVariable` or asserts the output contains `${{ secrets.` rather than a literal value
- severity: high (critical_test: —)
- blast_radius: the one code path guaranteeing a secret value never reaches generated GitHub Actions YAML has no regression net. An accidental `if not v.is_secret` inversion or a merge dropping either branch would ship with the suite green. Present behaviour is correct — verified that no path in `ado2gh/pipelines/` writes a variable value into output outside these two guarded sites — so this is a missing guard, not a live leak.
- status: remediated
- resolution: `52e94bf` (test(GAP-032)) adds the missing regression guard for the only two sites that ever write a `PipelineVariable.value` into generated output — `transformer.py::_build_env_block` (workflow-level) and `job_graph.py`'s job-level env builder — both already gated behind `if v.is_secret:` substituting `${{ secrets.NAME }}`. The new tests pin the guard twice: once through the real render path (`PipelineTransformer.transform` → the workflow file written to disk, asserted on its raw text) and once directly against the writer function, so an inverted condition or a dropped branch on either site fails the suite instead of shipping green. A plain (non-secret) variable is asserted to still render its value, so a blanket mask could not pass either. The values used in the tests are fabricated placeholders, and the assertion helper reports only the leaking variable *names*, never a value, so a failure message can never echo anything credential-shaped (CA-003). No production file was changed by this commit.
- regression_check: `tests/pipeline/test_gap_032_secret_values_never_inlined.py`
- revert_proof: No proof was recorded when `52e94bf` landed. Self-performed 2026-09-12 by Claude (sonnet subagent, T082 close-out): inverted both pre-existing (unmodified-by-this-commit) guards — `if v.is_secret:` to `if not v.is_secret:` in `ado2gh/pipelines/transform/transformer.py::_build_env_block` and the equivalent stage-env loop in `ado2gh/pipelines/transform/job_graph.py`. `.venv/Scripts/python.exe -m pytest tests/pipeline/test_gap_032_secret_values_never_inlined.py -v` then failed all three tests — `test_transform_writes_secret_references_not_values` (`API_KEY`, `DB_PASSWORD` and `DEPLOY_TOKEN` all found written into the generated workflow), `test_build_env_block_substitutes_a_secrets_reference_for_every_secret` and `test_stage_variables_become_job_env_with_secrets_referenced` — each reporting the leaking variables by name only, never by value. Restored via the same combined `git stash push` used for the GAP-017/GAP-018/GAP-030 proofs above; the same three tests re-ran green afterward. The same stash-cleanup caveat noted under GAP-030 applies here: the working tree is confirmed clean, but the stash entry (`5b591bf`) was not yet dropped as of this writing because `git stash drop` was blocked by this session's own permission classifier.
- contract_change: false
- closed_on: 2026-09-09

### GAP-033 (GAP-DEPLOY-02) Scheduled migration workflow pushes to GitHub on a cron with no environment approval gate

- components: deployment & CI artefacts
- violates: Principle V; property 6
- evidence:
  - `.github/workflows/migrate-repo.yml:3-15` — `schedule: cron: "0 0 1,16 * *"` alongside `workflow_dispatch`, with no `environment:` key on the job
  - `.github/workflows/migrate-repo.yml:58-65` — the job performs pushes
- severity: high (critical_test: —)
- blast_radius: a migration push runs unattended twice a month with no GitHub Environment approval gate, so no reviewer is interposed between the schedule and a write to the target org. Capped at high per FR-016a.
- status: remediated
- resolution: the `transfer_main_branch` job in `.github/workflows/migrate-repo.yml` now declares `environment: migration-target`. The gate sits on the job rather than on individual steps because every path into the job ends in a push to the destination organisation, and both triggers reach it — the cron and `workflow_dispatch` alike. With a required-reviewer protection rule configured on the `migration-target` environment, the scheduled run waits for a named approver before its first step instead of writing to another org unattended. The schedule, the dispatch inputs and the job body are unchanged. Operator action outside this tree: create the `migration-target` environment in repository settings and add required reviewers to it — the environment name is the hook the protection rule attaches to, and no file in the repository can carry the rule itself.
- regression_check: `tests/unit/test_gap_033_ci_artifacts.py` — `test_every_migration_job_runs_under_a_protected_environment` (every job in the workflow names an environment) and `test_migration_workflow_still_carries_the_unattended_trigger` (the gate stays meaningful only while the cron path exists), both loading the workflow with `yaml.safe_load`
- revert_proof: `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_033_ci_artifacts.py` run against the unmodified artefacts, before the first edit: `5 failed, 4 passed in 4.81s` — `test_every_migration_job_runs_under_a_protected_environment` among the failures, reporting `jobs ['transfer_main_branch'] push to the destination repository with no environment: key, so no required-reviewer rule can gate them`. No `git stash` proof was taken: a concurrent Phase 6 cleanup increment held this working tree, and stashing would have reverted its files alongside the artefacts under test. After the fix the same command reports `9 passed in 4.32s`. Taken 2026-09-08 by the T080 implementation agent (Claude Opus 5).
- contract_change: false — a workflow-file change only; no CLI command, HTTP route, database table or environment variable was added or removed, so `tests/contract/public_surface_snapshot.json` is unchanged (verified: `tests/contract/test_public_surface_snapshot.py`, 3 passed).
- closed_on: 2026-09-08

### GAP-034 (GAP-DEPLOY-04) Production compose ships default credentials, an exposed database port, and a `change-me` secret fallback

- components: deployment & CI artefacts
- violates: Principle V (fail-safe defaults)
- evidence:
  - `docker-compose.prod.yml:8-9` — Postgres user and password both `ado2gh`
  - `docker-compose.prod.yml:11-12` — port `5432` published to the host
  - `docker-compose.prod.yml:27` — a `change-me-in-production` fallback value that takes effect when the corresponding variable is unset
  - no committed secret *value* was found anywhere in the repository (searched across compose files, workflows, `deploy/`, and `.env.example`), so the FR-016a critical exception does not apply
- severity: high (critical_test: —)
- blast_radius: the file named as the production topology starts with a guessable database credential reachable from the host network, and silently substitutes a placeholder secret rather than refusing to start. Capped at high per FR-016a since no secret value is committed.
- status: remediated
- resolution: four changes to `docker-compose.prod.yml`, all of them fail-closed. (1) `POSTGRES_PASSWORD` is now `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD to a strong value}` — the required interpolation form T039 established for `ADO2GH_INTERNAL_TOKEN` — so an unset value stops the stack instead of bringing up a database whose password is printed in the repository. (2) The `5432:5432` host publication is gone: accelerator, agent and worker reach the database as `postgres:5432` over the compose network, which is the only access any service in the file needs, so the database is no longer exposed to whatever else can reach the host. (3) All three `ADO2GH_DATABASE_URL` values interpolate that same required variable rather than embedding `ado2gh:ado2gh`, leaving the credential a single source that cannot drift from the one Postgres actually starts with. (4) The `SESSION_SECRET: ${SESSION_SECRET:-change-me-in-production}` line was removed rather than converted to the required form: nothing under `ado2gh/`, `services/` or `apps/migration-ui/src` reads a session secret (sessions are random tokens persisted in the state DB), so requiring the operator to supply one would be ceremony around a value no code consumes, while the `:-` fallback it replaces was the shipped placeholder the evidence names. `POSTGRES_USER` deliberately stays `ado2gh`: a username is not the credential, and `deploy/kubernetes/postgres.yaml` already keys its `secretKeyRef` on the same `POSTGRES_PASSWORD` name, so the two topologies now agree. `.env.example` documents the variable as required for this compose file and no longer shows a runnable `ado2gh:ado2gh` connection string.
- regression_check: `tests/unit/test_gap_033_ci_artifacts.py` — one module covers both open deployment gaps and is named for the lower id. Four of its tests carry this gap: `test_prod_postgres_password_has_no_shipped_default`, `test_prod_compose_does_not_publish_the_database_port`, `test_prod_database_urls_carry_no_literal_password`, and `test_no_compose_secret_falls_back_to_a_placeholder`, the last parametrised over both compose files so the `${SECRET:-default}` shape cannot return to either. `test_no_secret_is_passed_as_a_docker_build_arg` is a standing shape guard rather than a reproduction — no service passes a secret through `build.args` today (the only build args are `NEXT_PUBLIC_*`), and the guard keeps it that way, since a build arg is baked into image history.
- revert_proof: `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_033_ci_artifacts.py` run against the unmodified artefacts, before the first edit: `5 failed, 4 passed in 4.81s` — the four failures named above, among them `POSTGRES_PASSWORD is 'ado2gh'; expected the required form ${POSTGRES_PASSWORD:?...}` and `secret-shaped variables with a shipped default: ["accelerator.SESSION_SECRET -> 'change-me-in-production'"]`. No `git stash` proof was taken: a concurrent Phase 6 cleanup increment held this working tree, and stashing would have reverted its files alongside the artefacts under test. After the fix the same command reports `9 passed in 4.32s`. Taken 2026-09-08 by the T080 implementation agent (Claude Opus 5).
- contract_change: false — `POSTGRES_PASSWORD` is a deployment variable consumed by the Postgres image and already named in `deploy/kubernetes/secret.yaml.example`; it is not read by `ado2gh/`, `services/` or `apps/migration-ui/src`, which is the surface `env_vars` in `tests/contract/public_surface_snapshot.json` enumerates, and the removed `SESSION_SECRET` is likewise absent from that snapshot. No CLI command, HTTP route or database table changed. Verified unchanged: `tests/contract/test_public_surface_snapshot.py`, 3 passed. Migration note for operators of the prod compose file: set `POSTGRES_PASSWORD` in `.env` before `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`; an existing `pgdata` volume still holds the old `ado2gh` password, so rotate it (`ALTER ROLE ado2gh WITH PASSWORD ...`) or recreate the volume.
- closed_on: 2026-09-08

### GAP-035 (GAP-CLI-04) Public command handlers are missing docstrings

- components: CLI, phase orchestration
- violates: Principle II (Documented Functions & Classes)
- evidence:
  - `ado2gh/cli/phase.py:24-25` — `phase_run` has no docstring, so `--help` shows no description for the primary execution command
  - `ado2gh/cli/misc.py:43-44` — `ado_cleanup` has no docstring, on a command that disables pipelines and archives repos
  - `ado2gh/phase/` — several public methods on the gate and batch types likewise carry none
- severity: medium
- blast_radius: documentation drift only; Click renders an empty help body for two commands whose behaviour is destructive, which is a discoverability problem rather than a safety one.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Add docstrings to `phase_run`, `ado_cleanup`, and the undocumented public methods under `ado2gh/phase/`; registered-only per spec.md's critical+high remediation-scope clarification (spec.md:35) — no successor task filed.
- closed_on: —

### GAP-036 (GAP-ACC-04) Module naming and placement do not match responsibility (G-seed 11)

- components: accelerator service, token management & audit writing
- violates: Principle IV (Intuitive Architecture & Naming)
- evidence:
  - `ado2gh/assignments/__init__.py:1` — the package docstring describes it as the audit event writer, while the directory name `assignments` describes something else entirely; `ado2gh/assignments/audit.py` is its only substantive module
  - `ado2gh/api/agentic_routes.py` — a routes module living under `ado2gh/api/` rather than with the other route modules in `services/accelerator_api/routes/`, and named for an adjective rather than a resource
  - `services/accelerator_api/routes/_shared.py:99-105` — module-level singletons constructed through `__import__` rather than a normal import, obscuring the dependency from any static reader or tool
  - `docs/STRUCTURAL_CHANGELOG.md:137` (contract example) already anticipates `ado2gh/assignments/` → `ado2gh/audit/` — citation unverified at T035: that line is an unrelated `ado2gh/api/credentials/__init__.py` row, and no `assignments/` → `audit/` row exists anywhere in `docs/STRUCTURAL_CHANGELOG.md`
- severity: medium
- blast_radius: a reader looking for audit-writing code has no reason to open `ado2gh/assignments/`, and the `__import__` singletons are invisible to the orphan-module guard and to import graphing. No safety impact.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Rename `ado2gh/assignments/` to match its actual responsibility (audit-event writing) and replace `services/accelerator_api/routes/_shared.py`'s `__import__`-based singletons with ordinary imports so the orphan guard and import graphing can see them; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-037 (GAP-ACC-05) Deprecated `/v1/plan` points operators at a route that does not exist

- components: accelerator service
- violates: Principle III (Deprecation Policy)
- evidence:
  - `services/accelerator_api/main.py:242,244` — the deprecation notice for `/v1/plan` directs callers to `/v1/migration/wave`, which is not among the registered routes
  - no removal version or date accompanies the notice
- severity: medium
- blast_radius: a caller following the deprecation notice reaches a 404; the deprecation lacks the removal timeline Principle III requires. No safety impact.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Point the `/v1/plan` deprecation notice at a route that actually exists and add the removal version/date Principle III requires; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-038 (GAP-AGT-04) Deprecated in-memory `_sessions` store has no removal timeline, and `_runs` is write-only

- components: agent service, auth & RBAC
- violates: Principle III (Deprecation Policy)
- evidence:
  - `ado2gh/agents/migration_agent/route_helpers.py:45-47` — "DEPRECATED: this in-memory session store is being replaced by the persistent MigrationSessionStore ... in Spec 011. Do not add new consumers; existing routes will be migrated incrementally." — no date, version, or milestone; CLAUDE.md lists spec 011 as already completed
  - `ado2gh/agents/migration_agent/route_helpers.py:237` and `services/agent/routes/session_routes.py:1226` — `_sessions` remains the primary store the audited approval and provision routes read directly
  - `ado2gh/agents/migration_agent/route_helpers.py:48` — `_runs` is populated only by `resume_live_internal` (`session_routes.py:483-493`) and deleted only on session delete (`:314-315`); exhaustive grep across `session_routes.py`, `route_helpers.py`, and `run_routes.py` finds no read of `_runs[run_id]`
  - `ado2gh/agents/migration_agent/route_helpers.py:42-44` — an adjacent comment does honestly document the store's single-replica and restart-loss ceiling, so the limitation itself is disclosed; the deprecation timeline is what is missing
- severity: medium
- blast_radius: process and hygiene risk rather than safety — the durable store exists and is consulted on restart (`services/agent/main.py:57-70`), so resumability is intact. The concern is a store marked "do not extend" that remains load-bearing for the approval routes, with its retirement open-ended past the completion of the spec meant to retire it.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Give the in-memory `_sessions`/`_runs` stores a concrete removal timeline now that spec 011's persistent `MigrationSessionStore` has landed, or drop the stale "being replaced" language if they are staying long-term; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-039 (GAP-PHASE-04) Failed repos are not auto-exported after a phase run, contradicting the documented behaviour

- components: phase orchestration
- violates: Principle II (documentation asserts behaviour the code lacks)
- evidence:
  - `CLAUDE.md:216` — "Failed repos auto-exported to `failed_repos_{phase}.txt` after each phase run"
  - `ado2gh/phase/batch_executor.py:35` — `execute_phase` writes no such file; `ado2gh/cli/run_cmd.py:122-132` — the only producer of the export is the separate `export-failed` command, which takes its filename from its own `--output` option rather than the documented `failed_repos_{phase}.txt` shape
  - reproduction: `grep -c 'failed_repos' ado2gh/phase/batch_executor.py` → **0** (verified at T035)
- severity: medium
- blast_radius: an operator relying on the documented automatic export finds no file and may believe there were no failures. Recoverable by running `export-failed` manually, so no data is lost.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Either wire `execute_phase` to auto-write `failed_repos_{phase}.txt` as CLAUDE.md documents, or correct CLAUDE.md to describe the manual `export-failed` command instead; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-040 (GAP-TOKEN-03) `AuditWriter` redacts the payload but not the actor field

- components: token management & audit writing
- violates: Principle V; property 5
- evidence:
  - `ado2gh/assignments/audit.py:52-70` — `write()` applies `redact_payload` to `payload` only; `actor` is passed through to `insert_audit_event` unmodified
  - `ado2gh/api/profile_governance.py:135-148` — a caller that supplies `actor` from upstream request data
- severity: medium
- blast_radius: narrower than GAP-010 (GAP-TOKEN-01) since `actor` is normally a username or display name, but it is an unredacted free-text field written to the same persisted audit artefact, so the containment guarantee is incomplete on a second axis.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Extend `AuditWriter.write()`'s redaction to the `actor` field alongside `payload`, closing the same class of hole GAP-010 (GAP-TOKEN-01) closed for payloads; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-041 (GAP-TOKEN-05) Rate-limit handling and regex redaction paths are untested

- components: token management & audit writing
- violates: Principle VI
- evidence:
  - `ado2gh/clients/token_manager.py` — the rate-limit update and rotation paths driven from response headers have no direct test
  - `ado2gh/assignments/audit.py:10-18` — the `_SECRET_PATTERNS` regex branch has no test asserting a match or a miss, which is why GAP-010 (GAP-TOKEN-01)'s coverage hole was not visible
  - confirmed clean and separately worth recording: `ado2gh/clients/token_manager.py:92-124` — `get_token()` raises rather than returning an empty or unauthenticated token, so this path is fail-safe
- severity: medium
- blast_radius: the two behaviours that keep the platform inside GitHub's rate limits and keep secrets out of the audit log both lack regression tests. No live defect in the rate-limit path was found.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Add direct tests for `token_manager.py`'s rate-limit update/rotation path and `audit.py`'s `_SECRET_PATTERNS` regex branch, the same kind of coverage sweep GAP-025 (GAP-UI-03) did for the console; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-042 (GAP-ENG-03) `rollback_repos()` is dead code and would corrupt sibling repos if called

- components: migration engine
- violates: Principle II; Principle IV; property 3 (scope-targeted rollback)
- evidence:
  - `ado2gh/core/rollback.py:105` — `rollback_repos()`, docstring "Rollback specific repos (not entire wave)" — the only implementation of repo-level targeted rollback
  - whole-repo grep for `rollback_repos` returns only its own definition; zero call sites in `ado2gh/`, `services/`, or `tests/`
  - `ado2gh/cli/run_cmd.py:120` — the CLI `rollback` command only ever calls `rollback_wave(...)`
  - `ado2gh/core/rollback.py:176-181` — `_rollback_pipelines` calls `self.db.reset_failed_pipeline_migrations(wave_id)` with no repo argument
  - `ado2gh/state/sqlite_db.py:591` — `reset_failed_pipeline_migrations(self, wave_id: int)` takes no repo parameter and deletes every failed row for the whole wave (mirrored in `base.py` and `postgres_db.py`)
- severity: medium
- blast_radius: no impact today — zero callers. If wired up for a plausible future "roll back just this repo" action, it would silently wipe failed-pipeline tracking for every other repo in the same wave, contradicting its own docstring with no error. The reachable `rollback_wave(scopes=...)` path was checked and correctly applies per-repo scope filtering.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Either delete `rollback_repos()` as dead code or give `reset_failed_pipeline_migrations` a repo parameter before wiring up a caller; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-043 (GAP-STATE-04) SQLite and Postgres schemas have drifted with no column-level parity test

- components: state persistence
- violates: Principle IV; Principle VI (NON-NEGOTIABLE)
- evidence:
  - `ado2gh/state/sqlite_db.py:229-233` — `_migrate_agentic_columns` adds `migrations.assignment_id TEXT` on SQLite only
  - `ado2gh/state/postgres_db.py:22-37,212-224` — the Postgres `migrations` table has no such column and its migration path backfills only `platform_users.status`
  - `ado2gh/state/sqlite_db.py:156-164` vs `ado2gh/state/postgres_db.py:145-153` — `audit_events.actor` and `payload_json` are `NOT NULL DEFAULT` on SQLite and nullable with no default on Postgres
  - `tests/core/test_storage_config.py:52-73` — the existing parity tests check table and method *presence* only; neither divergence would fail any test
- severity: medium
- blast_radius: currently inert — nothing reads `assignment_id`, and `ado2gh/assignments/audit.py:52-70` never passes `None` for either audit column. The risk is that the moment a second `insert_audit_event` caller omits a payload, or any code starts reading `assignment_id`, behaviour diverges by backend with no test to catch it. All other shared tables, including `batch_checkpoints` upsert semantics, were verified symmetric.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- follow_up: Add a column-level schema parity test between `sqlite_db.py` and `postgres_db.py` (today's parity tests check table/method presence only); registered-only per spec.md:35, no successor task filed — would need `contract_change: true` if the schemas themselves are reconciled rather than just tested.
- closed_on: —

### GAP-044 (GAP-SEAM-02) `blocked_items` is always empty because the field it filters on does not exist on the wire contract

- components: integration seams, agent service
- violates: Principle I (a field that reads as a live safety signal but is structurally inert); Principle VI
- evidence:
  - `ado2gh/agents/migration_agent/route_helpers.py:493` and `ado2gh/agents/migration_agent/nodes/planner_plan_builders.py:140` — both compute `"blocked_items": [r for r in repos_data if r.get("blocked")]`
  - `ado2gh/api/contracts.py:435-443` — `DiscoveryRepoItem` has no `blocked` field, so the predicate is always falsy
  - `ado2gh/agents/migration_agent/hitl/blockers.py:107` — `sanitize_plan_for_operator_view()` pops `blocked_items` before the plan reaches the operator anyway
  - `apps/migration-ui/src/lib/agent.ts:66-86` — the `MigrationPlan` type has no `blocked_items` field
  - `tests/unit/test_planner_node.py:62-65` — mocks `"blocked": True` on the input repo dict, a shape the real contract never produces
- severity: medium
- blast_radius: no operator-visible effect. The gate operators actually depend on — `work_items[].status == "blocked"` via `outstanding_blockers()` — is independently reachable and was verified working (`ado2gh/api/migration_work_plan.py:191-212` does set `status="blocked"`). Risk is a future maintainer trusting `blocked_items` as a working signal.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Either add a `blocked` field to `DiscoveryRepoItem` and its producers so `blocked_items` computes something real, or delete the always-empty computation as dead code; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-045 (GAP-SEAM-03) The SSE event-kind contract test asserts nothing about event kinds

- components: integration seams
- violates: Principle II (docstring claims a verification the body does not perform); Principle VI
- evidence:
  - `tests/contract/test_langgraph_contracts.py:100-108` — the docstring states "SSE events must support these kinds: token, thinking, tool_call, tool_result, status, heartbeat, message, form_request, done"; the body builds `valid_kinds` at `:102-105`, never references it again, and its sole assertion at `:108` is `SSE_HEARTBEAT_INTERVAL_SECONDS == 15`, duplicating `test_sse_heartbeat_interval` at `:111-113`
- severity: medium
- blast_radius: false confidence only, no runtime effect. A rename of any SSE `kind` on either the producer (`nodes/streaming.py:50,55,60`, `runtime/orchestrator.py:376-439`) or the consumer (`apps/migration-ui/src/components/AgentChat.tsx:60-280`) would pass this suite with no warning — and this is the only test covering that shared vocabulary.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Rewrite the SSE event-kind contract test to actually assert `valid_kinds` against the producer (`nodes/streaming.py`, `runtime/orchestrator.py`) and consumer (`AgentChat.tsx`) instead of duplicating the heartbeat-interval test; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-046 (GAP-DEPLOY-05) Container base images use floating tags

- components: deployment & CI artefacts
- violates: Principle V (reproducibility of the deployed artefact)
- evidence:
  - `services/accelerator_api/Dockerfile:1` and `services/agent/Dockerfile:1` — both `FROM python:3.11-slim`, a moving tag with no `@sha256:` digest (verified at T035)
  - `apps/migration-ui/Dockerfile:1,14` — `FROM node:20-alpine AS builder` and `FROM node:20-alpine AS runner`, same shape on both stages (verified at T035)
  - reproduction: `grep -rn '^FROM ' --include=Dockerfile .` returns four lines, none carrying a digest; two builds of the same commit can therefore produce different runtime images
- severity: low
- blast_radius: build reproducibility only; no observed safety impact. Capped at low per FR-016a.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Pin the four `FROM` lines (`services/accelerator_api/Dockerfile`, `services/agent/Dockerfile`, `apps/migration-ui/Dockerfile` x2) to a `@sha256:` digest; capped at low per FR-016a, registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-047 (GAP-TOOL-03) Orphan-module guard allowlist names a deleted module

- components: tooling & guards
- violates: Principle I
- evidence:
  - `tests/unit/test_no_orphaned_modules.py:76` — the allowlist still names `ado2gh.reporting.boards_gaps`
  - `docs/STRUCTURAL_CHANGELOG.md:201` — that module's deletion is already recorded
- severity: low
- blast_radius: a stale allowlist entry weakens the guard by one slot; if a future module reuses the name it would be silently exempted. No current effect.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Remove the stale `ado2gh.reporting.boards_gaps` entry from `test_no_orphaned_modules.py`'s allowlist now that its deletion is recorded in `docs/STRUCTURAL_CHANGELOG.md:201`; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-048 (GAP-TOOL-04) CLAUDE.md misstates the test count, the coverage threshold, and local tool availability

- components: tooling & guards
- violates: Principle II (documentation drift)
- evidence:
  - `CLAUDE.md:221` — "~970 tests"; measured collection is 842
  - `CLAUDE.md:226-228` — states CI runs `--cov-fail-under=85` and that `pytest-cov`, `ruff`, and `vulture` are not preinstalled locally; `.github/workflows/ci.yml:36` uses `--cov-fail-under=56`, and all three tools are present in the local `.venv` (measured)
  - `pyproject.toml:83-85` — the `addopts` comment likewise still names 85
- severity: low
- blast_radius: a contributor following CLAUDE.md installs tools that are already present and expects a coverage gate that does not exist. No safety impact, but it is the file every agent and new contributor reads first.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Correct CLAUDE.md's test count, coverage-gate value, and local-tool-availability claims — best done once T083's Summary-table refresh and the ratchet's final 013 value are both settled, so the correction is written once; registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-049 (GAP-TOOL-06) The two inventory generators disagree on `--excluded` handling

- components: tooling & guards
- violates: Principle IV
- evidence:
  - `specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs:344-361,366` — the TS walker's exclusion handling does not mirror the Python generator's, so the same `excluded-paths.txt` can yield different exclusion counts per language
- severity: low
- blast_radius: the excluded-definition counts in `inventory-summary.md` can disagree between the Python and TS halves, weakening the FR-003b guarantee that the zero-tag denominator cannot shrink unnoticed. Affects this feature's own tooling only.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Align `function_inventory_ts.mjs`'s `--excluded` handling with the Python generator's; affects only this feature's own tooling, registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-050 (GAP-AGT-05) `clear_langgraph_thread_sync` can build a checkpointer on a second event loop during teardown

- components: agent service
- violates: Principle VI (CI reliability); property 2 (test-infrastructure scope only)
- evidence:
  - `ado2gh/agents/migration_agent/graph/builder.py:378-389` — when no loop is running in the calling thread it falls back to `asyncio.run(clear_langgraph_thread(session_id))`, which can build a checkpointer bound to a new loop while `_get_checkpointer` (`:72-151`) holds one bound to another
  - mitigating: `_close_checkpointer` (called at `:95`) bounds the stale-connection close with a 2s `asyncio.wait_for` and degrades to a logged leaked thread rather than hanging
  - callers are teardown-only — `ado2gh/agents/migration_agent/session/state.py:144-146` and `session/lifecycle.py:28-30`; the request path never calls it
  - CLAUDE.md Testing already documents the symptom and the `py-spy dump` diagnosis, so the behaviour is known rather than silent
- severity: low
- blast_radius: confined to test and teardown paths, bounded by a 2s timeout, already self-documented. Production runs a single stable event loop, where this branch does not trigger.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- follow_up: Bound or serialize the fallback `asyncio.run()` path in `clear_langgraph_thread_sync` against whatever event loop the primary checkpointer holds; confined to test/teardown paths, registered-only per spec.md:35, no successor task filed.
- closed_on: —

### GAP-051 (GAP-TOOL-07) The test suite opens the developer's real agent checkpoint DB and root `migration_state.db`

- components: tooling & guards, agent service, state persistence
- violates: Principle V (Enterprise Migration Safeguards — migration state must not be written by anything but a migration run); Principle VI (CI reliability)
- evidence:
  - `ado2gh/agents/migration_agent/graph/builder.py:129` — `db_path = os.environ.get("ADO2GH_SQLITE_PATH", "data/agent_checkpoints.db")`, resolved at call time inside `_get_checkpointer`, relative to the working directory; `get_compiled_graph()` (`:335`) reaches it from the agent's `/health` route and from every session test
  - `tests/conftest.py` (before this fix) — the session fixture isolated only `ADO2GH_DATA_DIR`; `ADO2GH_SQLITE_PATH` stayed unset, so the checkpointer opened the developer's real `data/agent_checkpoints.db` (2.3 MB, gitignored via `*.db`, the store for real agent sessions) and `ado2gh/auth/service.py:26,87,149`, `ado2gh/api/live_approval_store.py:50`, `ado2gh/api/agentic_routes.py:18`, `ado2gh/api/profile_governance.py:145` opened `migration_state.db` in the repo root
  - `ado2gh/agents/migration_agent/graph/builder.py:364-389` — `clear_langgraph_thread` deletes a checkpoint thread by session id; session-lifecycle tests call it against whatever file the checkpointer holds
  - `ado2gh/state/storage_config.py:51` — `sqlite_path = os.environ.get("ADO2GH_SQLITE_PATH", sqlite_default)`: the environment overrides an explicit `create_state_db(db_path)`, so a single session-wide override would have made every test that passes its own path share one file (measured: `tests/profile/test_profile_discovery.py` and `tests/profile/test_profile_scan_db.py`, 3 failures, `run-isolation-fix.first-attempt.txt`)
  - `docs/STRUCTURAL_CHANGELOG.md:368,435` — increments 1–3 could only be verified in a detached worktree because the shared tree stalled mid-suite with `data/agent_checkpoints.db-wal` / `-shm` present
  - reproduction: with `tests/conftest.py` at its pre-fix state, `.venv\Scripts\python.exe -m pytest` in the shared working tree wedges partway through and leaves `data/agent_checkpoints.db-wal` and `-shm` beside the real database; observed three times on 2026-09-08 (`tests/feature` test 17, `tests/contract/test_agent_pev_flow_contracts.py::test_health_endpoint_contract`, and after 12 tests)
- severity: critical (critical_test: c)
- blast_radius: every run of the suite on a developer machine writes checkpoint rows, and can delete checkpoint threads, in the database that holds real agent sessions, and writes auth, approval and profile rows into the root `migration_state.db`. A real session whose id collides with a test's is unresumable afterwards. The secondary effect is the one that surfaced first: a hot WAL left by a killed run wedges the next run on open, which is why three increments had to be verified in a detached worktree instead of the tree being shipped.
- status: remediated
- resolution: `tests/conftest.py` gains a function-scoped autouse fixture, `_isolated_sqlite_path`, that points `ADO2GH_SQLITE_PATH` at `tmp_path / "migration_state.db"` for every test through `monkeypatch`, so a test's own `monkeypatch.setenv` still wins and teardown restores the caller's environment. The variable already existed and is the one `_get_checkpointer` reads, so no environment variable was added and no production file changed. It is function-scoped rather than an extension of the session-scoped `_isolated_data_dir` because `create_state_db()` lets the variable override an explicit `db_path`; a single session-wide file would have made every test that passes its own path share one database (three tests failed that way on the first attempt). The three tests that build a `StateDB` directly and then call code that routes through `create_state_db()` — `tests/profile/test_profile_discovery.py` (two tests) and the `db` fixture in `tests/profile/test_profile_scan_db.py` — now pin the variable to the same file they construct, which they had silently relied on being unset. The pre-existing WAL/SHM files beside `data/agent_checkpoints.db` and `migration_state.db` were left in place; nothing under `data/` was created, modified or deleted.
- regression_check: `tests/agent/test_gap_051_checkpointer_isolation.py` — builds the real checkpointer, asks its aiosqlite connection `PRAGMA database_list` for the file it holds, and asserts the file is neither under the repository's `data/` directory nor named `agent_checkpoints.db`
- revert_proof: `git stash push -- tests/conftest.py`, then, from a temporary working directory so the reverted default cannot touch the real file, `.venv\Scripts\python.exe -m pytest d:\GitHub\Work\ADO_to_GitHub_Migration\tests\agent\test_gap_051_checkpointer_isolation.py --rootdir d:\GitHub\Work\ADO_to_GitHub_Migration`, then `git stash pop`. With the fix reverted: `1 failed in 4.16s` — `AssertionError: WindowsPath('.../gap051-revert-t86ifzdw/data/agent_checkpoints.db')`, and a fresh `data/agent_checkpoints.db` appeared under the temporary directory. Taken 2026-09-08 by the implementation agent (Claude Opus 5). With the fix in place the full suite in the shared working tree gives `898 passed, 30 skipped, 0 failed in 83.30s` (`run-isolation-fix.txt`; 886 baseline + this test + the 11 tests of the untracked `tests/unit/test_function_inventory_script.py` present in the tree), and the mtimes of `data/agent_checkpoints.db`, its `-wal`/`-shm`, and the root `migration_state.db` are byte-for-byte unchanged before and after (`run-isolation-fix.mtimes-before.txt`).
- contract_change: false — no route, CLI command, table or environment variable changed; `tests/contract/public_surface_snapshot.json` is unchanged.
- closed_on: 2026-09-08

### GAP-052 (GAP-CLI-05) `ado2gh phase assign` crashes on every invocation; no repo can be risk-scored from the CLI

- components: CLI, phase orchestration
- violates: Principle V (Enterprise Migration Safeguards — risk-based phasing: the phase a repo runs in can never be established from the CLI); Principle VI (no test exercises the command)
- evidence (paths at `463cc35`, the tree this entry was written against):
  - `ado2gh/cli/phase.py:113` — `scores = RiskScorer(ado, state).score_all()` (verified)
  - `ado2gh/phase/risk_scorer.py:17-32` — `class RiskScorer` declares no `__init__` and no `score_all`; the call raises `TypeError: RiskScorer() takes no arguments` before any ADO request is made (verified, reproduction below)
  - `ado2gh/cli/phase.py:114` — `WaveAssigner(state).assign_and_write(scores, config)`; `ado2gh/phase/wave_assigner.py:18-26` takes `phase_configs` and exposes only `assign`, so the second call would raise `AttributeError` had the first survived (verified by reading)
  - `ado2gh/api/accelerator.py:125` — `phase_scores = db.get_risk_scores_for_phase(phase_t)`: `phase run` migrates exactly the `repo_risk_scores` rows that `phase assign` was the only CLI path to write
  - `ado2gh/phase/gate_checker.py:35-40` — a phase with no assigned repos fails its gate with a hint to run `phase assign`, the command that cannot run
  - the same scoring already worked on the API path, which is why the breakage was invisible in the UI: `ado2gh/api/migration_scan.py:324-345` (pre-fix line numbers) scored every repo with `RiskScorer.score` and assigned with a wave assigner
  - no test named the command: `phase assign` appeared nowhere under `tests/` before this remediation, which is how a hard crash survived on a documented entry point (CLAUDE.md, Execution Workflow step 5)
  - reproduction: `.venv\Scripts\python.exe -m ado2gh phase assign -c migration.yaml --db migration_state.db` → `TypeError: RiskScorer() takes no arguments`
- severity: high (critical_test: —). Rated under US3 scenario 3, first clause: it violates a constitution principle (V) in the default configuration. It meets no critical test — (a) nothing destructive runs, the command dies before its first ADO call; (b) no secret is handled; (c) no state is written, so none can be corrupted — the process aborts before `upsert_risk_score`; (d) the failure path is a loud uncaught `TypeError`, which is the fail-safe default, not the absence of one; (e) the two sides do not silently disagree, they crash. Medium was rejected: medium is reserved for readability, naming or documentation drift with no safety impact, and a documented workflow step that cannot execute is not drift. Same reasoning, and the same rating, as GAP-017 (GAP-CLI-01).
- blast_radius: step 5 of the documented execution workflow cannot run at all, so no `repo_risk_scores` row and no `migration_phase.yaml` is ever produced through the CLI. `phase run --phase <name>` then finds no repos for any phase and migrates nothing, and `phase gate-check` (itself GAP-017 (GAP-CLI-01)) has nothing to measure. A CLI-only operator has no risk-based phasing at all: the safeguard is not weakened, it is absent, and the only route to it is the accelerator UI. Nothing is destroyed by the crash, which is why this is high rather than critical.
- status: remediated
- resolution: the per-repository half of the scoring that `ado2gh/api/migration_scan.py` already exercised was lifted into a new module, `ado2gh/phase/repo_scoring.py`, so both callers run the same code and neither imports the other (`phase/` still imports nothing from `api/`, so GAP-021 (GAP-ARCH-01) is not deepened). `score_repo` fetches a repository's branch stats and last commit, best-effort, and hands them to `RiskScorer.score`; `MigrationScanner.scan` now calls it in place of its inline block. `score_org_repos` walks the organisation and scores every enabled repository, taking each repo's pipelines from the state database's inventory (`get_pipelines_for_repo`, populated by workflow step 2) rather than a second ADO crawl — which is what the `--db` option on this command was always for. `phase_assign` now calls `score_org_repos`, assigns with the existing `WaveAssigner().assign`, persists every score with `upsert_risk_score`, prunes rows for repositories that no longer exist, and writes `migration_phase.yaml` next to the settings config: one wave per non-empty phase, in phase order, in the shape `ConfigLoader` parses and `BatchExecutor.execute_phase` filters on. The `global` block is copied into that file minus `ado_pat` and `gh_token`, so a planning artefact never carries a credential (CA-003). No option, flag or help text changed.
- regression_check: `tests/unit/test_gap_052_phase_assign_broken.py` — invokes `phase assign` through Click's `CliRunner` against a stub ADO client (one live repo, one disabled) and a temp state DB, and asserts a zero exit, a `migration_phase.yaml` holding the scored repo with a non-zero risk score and a named phase, and a persisted risk score that `get_risk_scores_for_phase` returns.
- revert_proof: with `ado2gh/cli/phase.py` restored to its `463cc35` content (`git show HEAD:ado2gh/cli/phase.py`) and the rest of the fix in place, `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_052_phase_assign_broken.py -p no:cacheprovider -q` gives `2 failed, 2 warnings in 4.78s`, each with `AssertionError: phase assign exited 1: TypeError('RiskScorer() takes no arguments')`. With the fix restored: `2 passed, 2 warnings in 4.50s`. Taken 2026-09-08 by the implementation agent (Claude Opus 5).
- contract_change: false — the command keeps exactly its two options (`-c/--config`, `--db`) with unchanged types, defaults and help text; `ado2gh phase assign --help` is byte-identical to `463cc35` (md5 `9dd05625b031ae9e38b8fde893ce4a6b` before and after) and `tests/contract/public_surface_snapshot.json` is unchanged.
- closed_on: 2026-09-08

### GAP-053 (GAP-ENG-08) The queue worker cannot execute any job type: every one dies at `Accelerator` construction

- components: migration engine (background job worker), accelerator service, deployment artefacts
- violates: Principle V (Enterprise Migration Safeguards — migration-appropriate patterns: the asynchronous batch-execution path of the full stack is inoperative, so no queued discover, inventory, migrate, validate or transform job can run); Principle VI (no test exercised `execute_job`, which is how three separate call-site defects survived in one 40-line function)
- evidence (paths at `d11d25e`, the tree this entry was written against):
  - `ado2gh/core/orchestration/worker.py:35-38` (pre-fix) — `Accelerator(config_path=..., db_path=...)`, but `ado2gh/api/accelerator.py:97` declares `def __init__(self, db_path: str = "migration_state.db") -> None` and the facade has never taken a `config_path`: increment 9 touched that line only to add the return annotation (`git show d11d25e -- ado2gh/api/accelerator.py`), so this is pre-existing, not a regression of the cleanup. Every job type raises before its handler is reached (verified, reproduction below)
  - `ado2gh/core/orchestration/worker.py:48` and `:59` (pre-fix) — `accel.inventory(projects=...)` with no config path, against `ado2gh/api/accelerator.py:281-287`, where `config_path: str` is the first, required, positional parameter. With the constructor defect fixed and nothing else, both call sites raise `TypeError: missing a required argument: 'config_path'` (measured)
  - `ado2gh/core/orchestration/worker.py:51-52` (pre-fix) — `results = accel.run_wave(...)` then `{"waves": [r.model_dump() for r in results]}`, but `run_wave` returns one `RunWaveResult`, not a list (`ado2gh/api/accelerator.py:131`, `:173`). Iterating a pydantic model yields `(field, value)` tuples, so the migrate job raises `AttributeError: 'tuple' object has no attribute 'model_dump'` after the wave has already executed and written its rows (measured)
  - `services/accelerator_api/main.py:495-500` — `POST /v1/jobs` stores `req.payload` verbatim; `ado2gh/api/contracts.py:148-158` declares `payload` as a free-form dict. No code in `ado2gh/`, `services/` or `apps/migration-ui/src` builds an `inventory_project` or `transform_pipeline` payload, so there is no in-repo producer to correct — the config path can only come from the caller's payload, which is the same value the accelerator's own readiness path passes to `inventory` (`services/accelerator_api/main.py:382-386`)
  - `docker-compose.yml:91-115` — the `worker` service runs `python -m ado2gh.core.orchestration.worker` under the `default` profile; `docker-compose.prod.yml:44-51` keeps the same service on the postgres backend. Both shipped stacks run this dispatcher
  - why it survived: `docker-compose.yml:24` sets `ADO2GH_LIGHTWEIGHT_MODE: "true"` for the accelerator, and under that flag `enqueue_job` completes each job inline and never pushes it to Redis (`services/accelerator_api/main.py:501-504`), so in the shipped compose almost nothing reaches the worker; and `execute_job` appeared nowhere under `tests/` before this remediation
  - reproduction: with `git show d11d25e:ado2gh/core/orchestration/worker.py` loaded as a module, `execute_job(JobRecord(id="j", job_type=JobTypeEnum.INVENTORY_PROJECT, status=JobStatus.RUNNING, payload={"config_path": "migration.yaml", "projects": ["Payments"]}))` → `TypeError: Accelerator.__init__() got an unexpected keyword argument 'config_path'`
- severity: high (critical_test: —). Rated under US3 scenario 3, first clause: it violates a constitution principle (V) in the configuration that ships the worker, and FR-016a independently caps a gap reached through a deployment artefact at high. It meets no critical test — (a) nothing destructive runs, the dispatcher dies at construction before any Azure DevOps or GitHub call; (b) no secret is handled, the `TypeError` names the keyword, never a value, so nothing maskable reaches the job record or the log (CA-003); (c) no migration state is written before the raise, and `run_worker` catches the exception and records it with `store.fail`, so the job is left visibly failed rather than lost — the one exception is the migrate job, whose `AttributeError` fires after `BatchExecutor` has already persisted its wave rows, and even there the state is written, not corrupted: the job is marked failed while the wave rows stand, which is a recoverable disagreement, not an unresumable run; (d) the failure path is a loud uncaught exception that the worker loop records — that is the fail-safe default, not the absence of one; (e) the two sides do not silently disagree, they crash. Medium was rejected on the same ground as GAP-052 and GAP-017 (GAP-CLI-01): medium is reserved for readability, naming or documentation drift with no safety impact, and a shipped service that cannot execute a single job type is not drift.
- blast_radius: the entire asynchronous execution path of the full stack is dead. Under `docker compose --profile default` and under `docker-compose.yml` + `docker-compose.prod.yml`, any job that reaches the worker — pushed to Redis, or claimed straight from the store by `claim_next` when the Redis push failed — fails immediately, so an operator relying on queued execution sees every job go to `failed` with a `TypeError` naming an internal keyword argument, and no discovery, inventory, migration, validation or transform work happens at all. The two inventory job types would still have been broken after the constructor was fixed, and the migrate job would have reported a failed job for a wave that had in fact run. Nothing is destroyed and nothing is silently wrong, which is why this is high rather than critical; the capability is simply absent, exactly as in GAP-052.
- status: remediated
- resolution: all three call sites in `ado2gh/core/orchestration/worker.py` were corrected to the signatures `ado2gh/api/accelerator.py` actually declares, and `Accelerator` was not touched — increment 9 owns that module and its signatures are unchanged. The facade is now constructed with `db_path` alone; both inventory job types pass the config path as the first positional argument; and the migrate job dumps the single `RunWaveResult` it receives, keeping the existing `{"waves": [...]}` result shape so a stored job result still deserialises the same way. The config path is read through one new module-level helper, `_require_config_path`, which raises `ValueError("Job payload is missing 'config_path'")` when the payload omits it: no default config file is guessed, because the wrong one would scan the wrong Azure DevOps organisation. `POST /v1/jobs` was left alone — it is a verbatim pass-through of a caller-supplied payload with no in-repo producer, so there was nothing to fix upstream; a payload that omits the key now fails with a message naming it instead of a `TypeError` naming an internal parameter.
- regression_check: `tests/unit/test_gap_053_worker_inventory_job.py` — eight tests that drive `execute_job` with `worker.Accelerator` patched by an `autospec` mock, so any call that does not match the real signature raises instead of passing silently. They assert that both inventory job types pass the config path positionally, that a payload without one raises `ValueError` and never reaches `inventory`, that the facade is constructed with `db_path` only, that the discover branch forwards its request, that a single `RunWaveResult` is dumped into `{"waves": [...]}`, and that the unsupported-job-type guard still raises. No network call and no file under `data/` is touched, and no credential literal appears in the payloads (CA-003).
- revert_proof: with `ado2gh/core/orchestration/worker.py` at its `d11d25e` content, `.venv\Scripts\python.exe -m pytest tests/unit/test_gap_053_worker_inventory_job.py -p no:cacheprovider -q` gives `8 failed, 2 warnings in 5.98s`, every one of the eight reporting `TypeError: got an unexpected keyword argument 'config_path'` from `inspect`'s signature binding — the constructor defect masks the other two. Reverting only the two later defects (constructor fixed, `inventory` and `run_wave` call sites restored) gives `5 failed, 3 passed, 2 warnings in 4.40s`: four failures reporting `TypeError: missing a required argument: 'config_path'` and one reporting `AttributeError: 'tuple' object has no attribute 'model_dump'`, which proves each half of the check bites on its own defect. With the full fix in place: `8 passed, 2 warnings in 4.19s`. Taken 2026-09-09 by the implementation agent (Claude Opus 5).
- contract_change: false — no route, CLI command, table or environment variable changed. `Accelerator`'s signatures are untouched, the `POST /v1/jobs` request body is unchanged, and a completed migrate job keeps its `{"waves": [...]}` result shape; `tests/contract/public_surface_snapshot.json` is unchanged.
- closed_on: 2026-09-09

### GAP-054 (GAP-STATE-05) `JobStore.complete()`/`.fail()` assign `updated_at` on a `JobRecord` that has no such field, raising at runtime

- components: state persistence (DynamoDB job store), migration engine (queue worker)
- violates: Principle V (Enterprise Migration Safeguards — CLAUDE.md documents "DynamoDB is job-store only", and a documented job-store backend cannot complete or fail a single job); Principle VI (no test exercises `DynamoDBJobStore.complete` or `.fail` — confirmed: neither file that references `DynamoDBJobStore`, `tests/unit/test_gap_028_dynamo_double_claim.py` or `tests/unit/test_gap_029_backend_parity.py`, calls either method)
- evidence:
  - `ado2gh/models.py:501-510` — `class JobRecord(BaseModel)` declares `id`, `job_type`, `status`, `payload`, `result`, `error`, `idempotency_key`; no `created_at` or `updated_at`. Pydantic 2.13.4's default `extra="ignore"` (measured: `python -c "import pydantic; print(pydantic.VERSION)"` → `2.13.4`) means passing either name to the constructor is silently dropped rather than rejected — measured live: `JobRecord(id="x", job_type=..., status=..., created_at=now, updated_at=now)` constructs without error, but `hasattr(rec, "created_at")` is `False`
  - `ado2gh/state/job_store.py:432-441` (`DynamoDBJobStore.enqueue`) builds exactly that record, then immediately calls `self._save(record)`; `_save` (`:386-399`) reads `record.created_at.isoformat()` at `:396` — since the attribute was silently dropped at construction, this raises `AttributeError: 'JobRecord' object has no attribute 'created_at'` (measured live) on the very first write. No job can ever be enqueued through `DynamoDBJobStore`, before `complete()`/`fail()` are ever reached
  - `ado2gh/state/job_store.py:537-545` (`complete()`) and `:547-555` (`fail()`) each assign `rec.updated_at = datetime.now(timezone.utc)` (`:544`, `:554`) before calling `self._save(rec)`. Assigning an attribute pydantic does not declare — unlike passing it to the constructor — is a hard error: measured live, `ValueError: "JobRecord" object has no field "updated_at"`. The `# type: ignore[attr-defined]` comments on both lines were added by `7a05ca9` (T077, see GAP-023) to silence mypy on exactly this line, not to fix it
  - compounding failure mode: `ado2gh/core/orchestration/worker.py:116-122` calls `store.complete(job.id, result)` inside a `try`, and on any exception falls back to `store.fail(job.id, str(exc))` in the paired `except`, with nothing wrapping that fallback. For every other job store this fallback is the fail-safe default; for `DynamoDBJobStore` it is not, because `fail()` carries the identical defect, so the fallback call itself raises and propagates out of `run_worker`'s `while True:` loop uncaught — killing the whole worker process rather than leaving one job stuck
  - reachability: only when `ADO2GH_STORAGE_BACKEND=dynamodb` (`ado2gh/state/job_store.py:577-579`, `JobStoreFactory.from_env`), which is not the shipped default (`sqlite` — CLAUDE.md, `docker-compose.yml`) and requires `boto3`, which this project does not declare anywhere in `pyproject.toml` (no match) and which is absent from this venv (measured: `ModuleNotFoundError: No module named 'boto3'`); `DynamoDBJobStore.__init__` (`:359`) imports `boto3` directly, so the class cannot even be constructed here
- severity: high (critical_test: —). (a) not met — no destructive action, a crash; (b) not met — the `ValueError`/`AttributeError` names only a field, never a value (CA-003); (c) not met — `enqueue()` fails before `put_item` ever runs and `complete()`/`fail()` raise before `self._save` runs, so no row is ever written with wrong or corrupted content, there is nothing persisted to lose because nothing after the initial defect ever persists at all; (d) is met — the worker's own fail-safe fallback (catch, then `store.fail()`) is exactly the path that also raises, so a completed or failed `DynamoDBJobStore` job leaves no fail-safe default, it kills the worker instead — but per spec.md US3 Scenario 3, a critical test met only in a non-default configuration is rated high, not critical, and this backend is reachable only under a non-default `ADO2GH_STORAGE_BACKEND` value that also requires an undeclared, uninstalled dependency; (e) not met — no second component reads a disagreeing contract, the one process just fails outright. High rather than medium because Principle V is violated in a configuration the project documents as supported (CLAUDE.md: "DynamoDB is job-store only"), not merely hypothetical — the same reasoning GAP-053 and GAP-052 used for a shipped-but-broken capability.
- blast_radius: identical in kind to GAP-053 before its fix — a documented backend that cannot perform its basic job lifecycle at all — but worse in degree for the one operator who does configure it: GAP-053's worker at least reached `store.fail()` cleanly on every defect it had; here `store.fail()` is itself the second point of failure, so the worker process dies instead of recording one job as failed and continuing. Capped in practice by the gate named above: no compose file, CI workflow, or documented setup in this repository selects the DynamoDB backend, and the dependency it needs is not installed, so no run anywhere in this project's own footprint is actually affected today.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true — adding `created_at`/`updated_at` to `JobRecord` changes every backend's serialized job record (`model_dump()` gains two fields), not only DynamoDB's, so any consumer that asserts an exact key set on a job payload would need to be checked
- follow_up: add `created_at`/`updated_at` fields to `JobRecord` (`ado2gh/models.py:501-510`) with a serialization/migration review, once an operator approves the contract change under FR-024 (`plan.md` § Approved contract changes); doing so would also let the `# type: ignore[call-arg]` (six sites) and `# type: ignore[attr-defined]` (four sites) comments `7a05ca9` added across `ado2gh/state/job_store.py` be dropped instead of permanently suppressed. No successor task filed.
- closed_on: —

## Removal verdicts (US3 scenario 4 — did production lose a feature?)

Three deletions from the pre-stabilisation tree were investigated specifically for
unreplaced capability loss. All three are **NO GAP**; no register entry is created for them.

**`migration_operations` table — NO GAP.** The table was written and read exclusively by
`MigrationExecutor` in `ado2gh/api/migration_executor.py` (`_get_operation`, `_update_status`,
`_fail_operation`), and that entire file was deleted with it in `0ec25d2`. The live path —
`POST /v1/migrate` (`services/accelerator_api/main.py:262`) → `AcceleratorSDK.run_wave`
(`ado2gh/api/accelerator.py:83-99`) → `create_state_db` (`:87`) → `MigrationEngine` /
`BatchExecutor` — persists through the surviving `migrations`, `wave_runs`,
`batch_checkpoints`, and `audit_events` tables, and additionally gained a live-approval
gate (`main.py:262-294`) that `MigrationExecutor` never had. Recorded in the commit message
and in `docs/STRUCTURAL_CHANGELOG.md`, so Principle III's no-silent-deprecation bar is met.
A strictly more capable replacement, not a loss.

**`_gate_payload` — NO GAP.** It was a private response serialiser for the deleted
assignment routes, not a persistence or audit function. The real audit path,
`ado2gh/phase/gate_checker.py:95-101` → `ado2gh/state/sqlite_risk_gates_mixin.py:135-163`,
is intact and untouched. Documented at `docs/STRUCTURAL_CHANGELOG.md:244-258`. (That this
audit path has no live caller is a separate finding — GAP-009 (GAP-CLI-02) — not a consequence of
the removal.)

**Dependency-graph / topological-sort storage — NO GAP.** `repo_dependency_edges` and
`discovery_dependency_edges`, with their `base.py` accessors, were removed from both
backends in `859bb6b`. The removed *ordering* code was gated on `wave.profile_id`, a field
`WaveConfig` (`ado2gh/models.py:77-87`) never had — all four construction sites were
checked — so it was unreachable dead code. The production phase engine never consumed the
tables: `ado2gh/phase/*.py` contains no dependency, topological, or DAG reference, only the
unrelated `PHASE_ORDER` enum. The capability itself is alive and tested today at
`ado2gh/agents/migration_agent/nodes/planner_plan_builders.py:78`
(`tests/unit/test_planner_node.py::test_heuristic_plan_topological_sort`,
`tests/unit/test_resource_mapping.py::test_plan_includes_topological_order`) and persists
via the frozen `migration_plans` table (`ado2gh/agents/migration_agent/session/store.py:50-60`).
Documented at `docs/STRUCTURAL_CHANGELOG.md:260-276`. A relocation, not a loss.


## Appendix: Assessment checklist

Every component in FR-016 / FR-016a was read against this grid. A cell is a question, not
a gap: a gap is recorded only where the reading found evidence.

**Six constitution principles** (`.specify/memory/constitution.md`, v1.0.0 — there are
exactly six; there is no Principle VII or VIII):

| # | Principle |
|---|-----------|
| I | Clean Code & Readability |
| II | Documented Functions & Classes |
| III | Deprecation Policy |
| IV | Intuitive Architecture & Naming |
| V | Enterprise Migration Safeguards (NON-NEGOTIABLE) |
| VI | Comprehensive Testing & Coverage (NON-NEGOTIABLE) |

**Six migration-safety properties** (FR-017):

| # | Property | Question asked of every component |
|---|----------|-----------------------------------|
| 1 | idempotency | does re-running the same action against the same target produce the same end state without duplicating or corrupting it? |
| 2 | resumability after interruption | after a crash or restart, does the component resume from a persisted checkpoint rather than restarting or silently skipping? |
| 3 | scope-targeted rollback | can an operator undo one scope without undoing or destroying the rest? |
| 4 | validation after transfer | is the transfer verified against the source, and does a verification failure surface? |
| 5 | secret containment | can a token, PAT, or secret value reach a log, report, message, persisted artefact, or UI? |
| 6 | human approval before irreversible action | does every path to a non-dry-run or destructive action pass an explicit confirmation or a documented override with reason? |

**Grid** — 6 × 6 per component:

| Component (FR-016 / FR-016a) | I | II | III | IV | V | VI | idem | resume | rollback | validate | secrets | approval |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLI (`ado2gh/cli/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Accelerator service (`services/accelerator_api/`, `ado2gh/api/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Agent service (`services/agent/`, `ado2gh/agents/migration_agent/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Web console (`apps/migration-ui/src/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Migration engine (`ado2gh/core/`, `ado2gh/core/scopes/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Phase orchestration (`ado2gh/phase/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pipeline transformation (`ado2gh/pipelines/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| State persistence (`ado2gh/state/`, all backends + job store) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Auth & RBAC (`ado2gh/auth/`, `ado2gh/api/platform_rbac.py`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Token management & audit writing (`ado2gh/clients/token_manager.py`, `ado2gh/assignments/audit.py`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Integration seams (accelerator ↔ agent, SSE, `tests/contract/`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Deployment & CI artefacts (FR-016a) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tooling & guards (`pyproject.toml`, orphan guard, docs) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Severity rules applied** (FR-019 / FR-020, spec US3 scenarios 2–3):

- **critical** — meets one of: (a) destructive/irreversible action without confirmation,
  dry-run, or audit trail; (b) can expose a secret in a log, report, message, or persisted
  artefact; (c) can lose or corrupt migration state so a run cannot be resumed or its
  outcome verified; (d) an **in-code** failure path leaves no fail-safe default; (e) two
  components disagree on a shared contract in a way that silently produces wrong results.
  Deployment topology never qualifies under (d).
- **high** — violates a principle or property in the default configuration, or meets a
  critical test only in a non-default configuration or deployment topology. Every
  deployment/CI finding (FR-016a) is capped here except a committed secret value.
- **medium** — readability, naming, documentation drift with no safety impact.
- **low** — cosmetic.
