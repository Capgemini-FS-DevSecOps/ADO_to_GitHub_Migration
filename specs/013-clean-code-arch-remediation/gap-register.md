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

50 gaps recorded across the thirteen components of FR-016 / FR-016a. Sequential ids were
assigned at T035 in file order and are never reused; the per-component placeholder each id
replaced is kept in parentheses so earlier cross-references stay resolvable.
Severities are as rated by the assessment passes (T022-T034); the review pass (T036) may
contest a critical or high rating, and any change it produces is recorded in the Disputes
table below rather than by re-rating an entry here.

| Severity | Count |
|----------|-------|
| critical | 15 |
| high | 19 |
| medium | 11 |
| low | 5 |
| **total** | **50** |

All 15 critical entries name a critical_test letter (a)-(e) per FR-019 / FR-020, and every
one of the 50 entries carries at least one path:line citation or a reproduction command
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
| GAP-009 (GAP-CLI-02) | `phase run --force` skips the gate with no reason and no audit record, while the audited override path is never called | open |
| GAP-010 (GAP-TOKEN-01) | `redact_payload` misses ADO PAT, Bearer, and prefixed key-name shapes before persisting audit events | remediated |
| GAP-011 (GAP-AGT-02) | `mask_secrets` is wired only into the audit bridge; chat, SSE, and persisted session messages are unmasked | remediated |
| GAP-012 (GAP-UI-01) | LLM provider API key is transmitted in a URL query string | open |
| GAP-013 (GAP-ENG-01) | Workflow-integrity check is structurally incapable of reporting FAIL | remediated |
| GAP-014 (GAP-ENG-07) | FR-036 concurrency guard fails open when both of its own checks throw | remediated |
| GAP-015 (GAP-SEAM-01) | Work-item producer emits `scope`/`blocker`; all three consumers read `scopes`/`blocked_reasons` | remediated (residual recorded) |
| GAP-016 (GAP-PIPE-04) | Live workflow-push approval gate is hardcoded satisfied by its only production caller and unsatisfiable from the CLI | open |
| GAP-017 (GAP-CLI-01) | `phase gate-check` always raises TypeError; no gate row can be written through the CLI | open |
| GAP-018 (GAP-CLI-03) | Migration commands execute live by default with no confirmation, and a declared approval token is discarded | open |
| GAP-019 (GAP-AUTH-03) | `/sessions/{id}/provision` and `/remediate` take no `Request` and trust a client-supplied `actor` | open |
| GAP-020 (GAP-AUTH-07) | Session cookie omits `Secure`; `max_age` hardcoded instead of reading `SESSION_HOURS` | open |
| GAP-021 (GAP-ARCH-01) | Package layering is inverted in at least nine places, masked by deferred imports (G-seed 3) | open |
| GAP-022 (GAP-TOOL-01) | Coverage ratchet stands at 56% against a constitutional floor of 85% | deferred |
| GAP-023 (GAP-TOOL-02) | mypy is configured so that it cannot fail, and never reaches most of the package | open |
| GAP-024 (GAP-UI-02) | Boolean `recommended_value` is stringified server-side and re-read as truthy in the browser, inverting the `confirm_execute` safe default | open |
| GAP-025 (GAP-UI-03) | All 46 component and page files are untested and no linter is configured | open |
| GAP-026 (GAP-PHASE-03) | `execute_phase` has no test coverage | open |
| GAP-027 (GAP-ENG-04) | "Idempotent — skips completed scopes" is false; per-handler idempotency is ad hoc | open |
| GAP-028 (GAP-STATE-01) | `DynamoDBJobStore` can silently double-claim or duplicate a job under concurrency | open |
| GAP-029 (GAP-STATE-02) | `create_state_db()` silently discards `--db` under a non-default backend and crashes on a backend its own config validates | open |
| GAP-030 (GAP-TOKEN-02) | GitHub token is passed in subprocess argv for the mirror strategy | open |
| GAP-031 (GAP-PIPE-01) | ADO variable-group variables are captured as metadata and never reach the generated workflow or its notes | open |
| GAP-032 (GAP-PIPE-02) | The branch that keeps secret values out of generated YAML has no test | open |
| GAP-033 (GAP-DEPLOY-02) | Scheduled migration workflow pushes to GitHub on a cron with no environment approval gate | open |
| GAP-034 (GAP-DEPLOY-04) | Production compose ships default credentials, an exposed database port, and a `change-me` secret fallback | open |

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
- revert_proof: restoring the `[tool.coverage.run] omit` block and re-running `pytest --cov=ado2gh --cov-fail-under=56` measures a different (inflated) percentage over a smaller denominator; reverting any one stabilisation commit returns the suite to red. Proof to be recorded at T035.
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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: true
- closed_on: —

### GAP-017 (GAP-CLI-01) `phase gate-check` always raises TypeError; no gate row can be written through the CLI

- components: CLI, phase orchestration
- violates: Principle V (gate enforcement); Principle VI (no test exercises the command)
- evidence:
  - `ado2gh/cli/phase.py:46` — `result = checker.check(PhaseType(phase_name), override=override, reason=reason)` (verified)
  - `ado2gh/phase/gate_checker.py:21` — `def check(self, phase: PhaseType) -> PhaseGateResult:` accepts neither keyword; every invocation raises `TypeError` (verified)
  - `ado2gh/phase/gate_checker.py:103-108` — `can_advance` returns False when no gate row exists, and `check()` is the only CLI path that would write one
- severity: high (critical_test: —)
- blast_radius: the documented step 8 of the execution workflow (`phase gate-check --phase poc`) cannot run at all. Because no gate row is ever persisted through the CLI, `can_advance()` is permanently False for every phase, leaving `--force` (GAP-009 (GAP-CLI-02)) as the only way to advance a wave — which is where the unaudited bypass becomes routine rather than exceptional. Rated high rather than critical because the crash itself is fail-safe: nothing advances and nothing is destroyed; the destructive consequence is carried by GAP-009 (GAP-CLI-02).
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

### GAP-022 (GAP-TOOL-01) Coverage ratchet stands at 56% against a constitutional floor of 85%

- components: tooling & guards, deployment & CI artefacts
- violates: Principle VI (Comprehensive Testing & Coverage, NON-NEGOTIABLE)
- evidence:
  - `.github/workflows/ci.yml:36` — `pytest --cov=ado2gh --cov-fail-under=56`
  - `.specify/memory/constitution.md` Quality Gates §1 — "coverage >= 85% on `ado2gh`"
  - `pyproject.toml:88-90` — `[tool.coverage.run]` carries no `omit` key, so 56% is measured over the whole package and is the honest figure
- severity: high (critical_test: —)
- blast_radius: 29 percentage points of the package have no regression net, which is the enabling condition for several findings in this register that shipped undetected (GAP-017 (GAP-CLI-01)'s always-raising command, GAP-013 (GAP-ENG-01)'s unreachable guard, GAP-015 (GAP-SEAM-01)'s key mismatch). Closing the gap to 85% is explicitly out of scope for this feature per FR-027a.
- status: deferred
- resolution: FR-027a scopes the 56% → 85% climb out of feature 013. The ratchet is honest (no omit list) and may never be lowered; each subsequent increment raises it to the measured figure.
- regression_check: `.github/workflows/ci.yml:36` — `--cov-fail-under` is monotonically non-decreasing
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

### GAP-026 (GAP-PHASE-03) `execute_phase` has no test coverage

- components: phase orchestration
- violates: Principle VI (NON-NEGOTIABLE)
- evidence:
  - `ado2gh/phase/batch_executor.py:35` — `def execute_phase(self, phase: PhaseType, waves: list[WaveConfig], ...)`, the function that drives every batched wave run; the phase test files exercise risk scoring, wave assignment, and gate thresholds but not the executor loop
  - reproduction: `grep -rn 'execute_phase' tests/` → **0 matches** (verified at T035)
- severity: high (critical_test: —)
- blast_radius: the single function that fans a phase out into batches, applies gates between them, and checkpoints progress is unverified, so a regression in batching or checkpoint placement would ship green.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

### GAP-030 (GAP-TOKEN-02) GitHub token is passed in subprocess argv for the mirror strategy

- components: token management & audit writing, migration engine
- violates: Principle V; property 5
- evidence:
  - `ado2gh/core/scopes/git_scope.py:267-271`, `:290-293`, `:348-352` — the token is embedded in the remote URL passed as a subprocess argument, making it visible in the process table to any local user and in any tooling that captures argv
  - `ado2gh/core/scopes/git_scope.py:361-373` — the GEI path correctly passes the token via the environment instead, showing the safe pattern is already in use in the same file
  - mitigating: `ado2gh/core/scopes/git_scope.py:18-25` `_redact()` scrubs the value from captured subprocess output, so the leak is to argv rather than to logs
- severity: high (critical_test: —)
- blast_radius: on any multi-user host the platform token is readable from the process table for the duration of a clone or push. Capped at high rather than critical (b) because the default strategy is `gei`, which uses the environment; the argv path requires selecting `migration_strategy: mirror`.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- closed_on: —

### GAP-032 (GAP-PIPE-02) The branch that keeps secret values out of generated YAML has no test

- components: pipeline transformation
- violates: Principle VI (NON-NEGOTIABLE); property 5
- evidence:
  - `ado2gh/pipelines/transform/transformer.py:361-369` (`_build_env_block`) and `ado2gh/pipelines/transform/job_graph.py:144-151` — the only two sites that write `PipelineVariable.value` into generated output, both gated behind `if v.is_secret:` substituting `${{ secrets.NAME }}`
  - reproduction: `Grep pattern="PipelineVariable\(|is_secret\s*=\s*True|isSecret" path="tests"` → **0 matches**; no test anywhere constructs a secret `PipelineVariable` or asserts the output contains `${{ secrets.` rather than a literal value
- severity: high (critical_test: —)
- blast_radius: the one code path guaranteeing a secret value never reaches generated GitHub Actions YAML has no regression net. An accidental `if not v.is_secret` inversion or a merge dropping either branch would ship with the suite green. Present behaviour is correct — verified that no path in `ado2gh/pipelines/` writes a variable value into output outside these two guarded sites — so this is a missing guard, not a live leak.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

### GAP-033 (GAP-DEPLOY-02) Scheduled migration workflow pushes to GitHub on a cron with no environment approval gate

- components: deployment & CI artefacts
- violates: Principle V; property 6
- evidence:
  - `.github/workflows/migrate-repo.yml:3-15` — `schedule: cron: "0 0 1,16 * *"` alongside `workflow_dispatch`, with no `environment:` key on the job
  - `.github/workflows/migrate-repo.yml:58-65` — the job performs pushes
- severity: high (critical_test: —)
- blast_radius: a migration push runs unattended twice a month with no GitHub Environment approval gate, so no reviewer is interposed between the schedule and a write to the target org. Capped at high per FR-016a.
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
- status: open
- resolution: —
- regression_check: —
- revert_proof: —
- contract_change: false
- closed_on: —

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
