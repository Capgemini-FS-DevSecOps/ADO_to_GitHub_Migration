# Feature 013 — Clean Code Architecture Remediation: Final Report

Pull request: https://github.com/Capgemini-FS-DevSecOps/ADO_to_GitHub_Migration/pull/7
Branch: `feature/ado-agentic-ai` → `main`

### 🏁 Phase Execution Summary

**Phase 1-3** (source gap remediation, register bookkeeping) completed in an
earlier session segment; see `specs/013-clean-code-arch-remediation/gap-register.md`
for the full per-gap history.

**Phase 4 — coverage ratchet.** The CI gate had only ever measured `ado2gh`,
so several accelerator and agent route modules (`services/agent/routes/form_routes.py`
at 12% covered, 416 statements, among others) were never counted against it at
all. Widened `.github/workflows/ci.yml` to `--cov=ado2gh --cov=services`,
raised the floor from 62 to 69 (measured combined figure: 69.28%, 21,777
statements, 6,693 missed, full suite 2,158 passed / 26 skipped). Also added a
manual `workflow_dispatch` trigger and a `feature/**` push trigger, since this
branch had no push trigger to fire CI on before. Commit `0ca7a99`.

**Phase 5 — pull request and CI stabilization.** Opened PR #7 (commit
`a18be4d`). A subsequent CI run failed mypy on `ado2gh/clients/gh_client.py`'s
404-check path (`httpx.HTTPStatusError.response` accessed without a guard —
unlike `requests.HTTPError.response`, which is `Optional`, the `httpx`
attribute is non-optional, so the existing code was correct but mypy's
inference needed an explicit narrowing). Fixed with commit `0f22911`.

**Phase 5 addendum — three second-review findings.** The read-only review at
`specs/013-clean-code-arch-remediation/final-review-findings-2.md` (tip
`a18be4d`, source `gpt-6-astra` via `codex exec`, cross-referenced against
`gap-register.md`) raised three new major findings and closed five prior
ones. Each of the three was independently re-verified against live source
before any change:

- **Finding A** — `ado2gh/audit/redaction.py`'s quoted-secret-value regex
  matched up to the first literal quote character regardless of a preceding
  backslash, so `password="abc\"def"` masked to `password="***"def"` — the
  escaped quote ended the match early and the real secret's tail (`def`)
  reached output unmasked. Distinct from the already-fixed GAP-121 (an
  unescaped opposite-style quote legitimately inside a value). Fixed by
  changing the `dqval`/`sqval` capture groups to honor backslash escapes
  (`(?:\\.|[^"\\])*`). Commit `a23f216`, test
  `tests/auth/test_gap_123_escaped_quote_secret_value_leak.py`.
- **Finding B** — `services/agent/routes/execution_routes.py`'s
  `approve_session`, on a local denial (`approved: false`), set local session
  state but never closed the matching platform-side `LiveApprovalStore` row
  opened earlier in the same request lifecycle (GAP-122). The row stayed
  pending, so a separately-privileged approver could later approve that
  stale row and resume the session live despite the local denial — a CA-002
  gap. Fixed by forwarding the denial to the same
  `POST /v1/platform/approvals/{id}/deny` path the internal deny-live route
  already uses. Commit `eaed276`, test
  `tests/agent/test_gap_124_session_deny_closes_platform_approval.py`.
- **Finding C** — `ado2gh/api/live_approval_scopes.py`'s two context-match
  guards refused a scope-mismatched approval request with a 422 but wrote no
  audit record, unlike the equivalent execution-time refusal in
  `LiveApprovalStore`, which already writes a
  `platform.live_execution.scope_mismatch` event — a CA-004 gap on the
  default storage path. Fixed by writing the same event (tagged
  `"stage": "creation"` to distinguish it from the execution-time record)
  immediately before raising. Commit `4dc1c21`, test
  `tests/auth/test_gap_125_creation_time_scope_mismatch_audited.py`.

Each fix was proven against a worktree revert: reverting the commit in a
detached, disposable worktree and re-running the regression test (or, when
the test file was removed along with the fix in the same commit, a direct
source probe) reproduced the original defect every time — the escaped quote
leaked again, the deny call disappeared, and both audit assertions failed.
All three worktrees were removed afterward; nothing from them reached the
tracked branch. The related test surface (`tests/core/test_live_approval_queue.py`,
`tests/agent`, `tests/auth`, one `tests/pipeline` file) was re-run afterward:
384 passed, 5 skipped, matching the pre-existing skip count, no new failures.

The second review round's other closure verdicts (from
`final-review-findings-2.md`): the `policies.py` per-run opt-in finding was
already registered with no new information; the `cli/misc.py
push-workflows --dry-run` finding is fixed (explicit
`--dry-run/--live` default); the `_helpers.py` agent-session live-request
finding is fixed (now enters the approval queue and audits either outcome).
One prior finding remains open and out of scope for this addendum: a
DynamoDB claim-conflict issue in `ado2gh/state/job_store.py`
(GAP-069/GAP-STATE-06, critical/high) — it sits on the non-default DynamoDB
job-store backend, not the default SQLite path this addendum touched, and was
already tracked before this session.

**Phase 6 — CI hang, root cause and fix.** The `test` job hang flagged in the
prior section as an open action item is fixed. Root cause:
`services/agent/routes/session_routes.py::create_session` called
`new_isolated_agent_session()`, which unconditionally cleared any prior
LangGraph checkpoint thread via a fire-and-forget `loop.create_task(...)` —
safe on a long-lived server loop, but not under Starlette's bare
`TestClient(app).post()`, which opens and tears down a fresh anyio
blocking-portal event loop per request. The detached task opens an
`AsyncSqliteSaver` checkpointer over `aiosqlite`, whose worker thread is
non-daemon, and could still be pending when the portal's loop shutdown tried
to cancel-and-gather it — cancellation does not reliably unstick a pending
aiosqlite thread-bridged `Future`, so the portal's `thread.join()` waited
forever. Reproduced locally against a throwaway Python-3.11 venv pinned to
CI's exact dependency versions, with full thread stacks confirming the exact
call chain. Fixed in three parts: removed the unconditional clear from the
create path (relocated to the one caller that legitimately needs it,
session rehydration); marked the checkpointer's aiosqlite worker thread
`daemon=True` as defense in depth; and bounded the fire-and-forget clear
with a five-second timeout as a second layer (confirmed by direct experiment
that the timeout alone does not close the hang — the first change is what
actually stops it). Also added a 25-minute job timeout and
`pytest --timeout=600 --timeout-method=thread` so any future hang fails
loudly instead of running indefinitely. Commits `9dec5d4`, `b9cbc26`. Verified
locally: 2165 passed, 26 skipped, 0 failed, 0 errors, 69.26% coverage,
177.80s, no hang. Pushed at `b9cbc26`; run `35727384031` completed the `test`
job in 1m59s versus 3+ hours of no result before — the hang itself is fixed.

**Phase 7 — three Python-3.11 fixes the hang had been hiding.** With the hang
gone, run `35727384031` completed and reported `4 failed, 33 errors`, all
three pre-existing and unrelated to the hang fix: (1) `test_no_dynamodb.py`
called `Path.walk()`, a Python-3.12-only method, against CI's Python 3.11 —
swapped to `os.walk()` (commit `b219176`); (2) the `test` job's install line
never included the `postgres` extra `pyproject.toml` already declares,
producing 33 `ModuleNotFoundError: No module named 'psycopg2'` errors —
added `postgres` to that one line (commit `29142c9`); (3) the `--version`
option's recorded default in the public-surface contract snapshot flipped
between `"False"` and `"<unset>"` depending on the installed click patch
version's eager-resolution behavior for boolean flags — narrowed the
snapshot collector's normalization to genuine boolean flags only (commit
`76eb59f`; regenerating the snapshot under local click 8.4.2 produced a
byte-identical file, so `public_surface_snapshot.json` itself did not need
to change). Verified: targeted run 60/60 under a fresh Python-3.11 venv with
click 8.5.0 and the `postgres` extra; full suite same venv, 2165 passed, 26
skipped, coverage 69.24% (threshold 69%); `ruff`/`mypy` both clean. Pushed;
run `35729268841` completed `success` on all three jobs (`lint`,
`ui-permissions`, `test`) — the first fully green CI run on this branch.

**Phase 8 — third register scribe pass.** Merged
`register-pending/finisher-notes.md` into `gap-register.md`: five new
entries, GAP-123 through GAP-127 (Findings A/B/C as GAP-123/124/125, the CI
hang as GAP-126, the three Python-3.11 fixes as one combined GAP-127); the
GAP-079 residual closed (commit `8bc89dd`); worktree-revert `revert_proof`
evidence added to GAP-120/121/122. New register totals: 24 critical / 49
high / 35 medium / 19 low, 127 total (was 23/47/33/19/122). The open
critical-or-high set is unchanged: one entry, GAP-069, `deferred`. Detail:
`gap-register.md` § Summary and `plan.md` § Completion summary, addendum
dated 2026-09-22 (third scribe pass).

### 📄 Generated Files Summary

- `tests/auth/test_gap_123_escaped_quote_secret_value_leak.py` — new, 3 tests
- `tests/agent/test_gap_124_session_deny_closes_platform_approval.py` — new, 2 tests
- `tests/auth/test_gap_125_creation_time_scope_mismatch_audited.py` — new, 2 tests
- `specs/013-clean-code-arch-remediation/register-pending/finisher-notes.md` —
  appended (scribe-owned; closure notes for findings A/B/C plus the honest
  record of the failed ChatGPT consult attempts)
- This file (`final-report.md`)

Modified (Findings A/B/C): `ado2gh/audit/redaction.py`,
`services/agent/routes/execution_routes.py`, `ado2gh/api/live_approval_scopes.py`,
`ado2gh/api/live_approval_store.py`.

Modified (CI hang and Python-3.11 fixes, Phases 6-7):
`ado2gh/agents/migration_agent/session/lifecycle.py`,
`ado2gh/agents/migration_agent/graph/builder.py`, `.github/workflows/ci.yml`,
`tests/unit/test_no_dynamodb.py`, `tests/contract/test_public_surface_snapshot.py`.

Modified (register scribe pass, Phase 8):
`specs/013-clean-code-arch-remediation/gap-register.md`,
`specs/013-clean-code-arch-remediation/plan.md`,
`specs/013-clean-code-arch-remediation/spec.md`.

### ⚠️ Action Required (Incomplete Parts)

- **ChatGPT/Codex consult did not complete.** The project rule and the
  coordinator's instruction both call for one read-only review consult before
  reporting these three fixes done. `mcp__chatgpt__ask_chatgpt` was tried four
  times across this addendum, including a minimal connectivity probe, and
  every attempt returned the identical error `"Codex exited with code 1.
  Check Codex login status and CLI settings."` A direct `codex --version` /
  `codex login status` in the same shell succeeded (`codex-cli 0.153.4`,
  `Logged in using ChatGPT`), so the underlying CLI is authenticated and
  working — the failure is in the MCP bridge that invokes it, not the login
  itself. No review was obtained; this is reported as not done rather than
  claimed. The three fixes were instead verified through direct reproduction
  (before/after probes, worktree revert proofs) and the existing test suite,
  which is a real but narrower form of verification than a second reviewer's
  read. **This gap is still open after Phases 6-8** — no further consult
  attempt was made in this pass, so the second-reviewer pass on findings
  A/B/C (commits `a23f216`, `eaed276`, `4dc1c21`) is still owed before
  merging.
- **CI is green.** The hang described in an earlier version of this report is
  fixed (Phase 6, commits `9dec5d4`/`b9cbc26`); the three Python-3.11
  failures it had been hiding are fixed (Phase 7, commits `b219176`,
  `29142c9`, `76eb59f`). Run `35729268841` completed `success` on all three
  jobs (`lint`, `ui-permissions`, `test`) — nothing further needed here.
- **Register totals (Phase 8).** `gap-register.md` now holds 127 gaps: 24
  critical / 49 high / 35 medium / 19 low. The open critical-or-high set is
  unchanged from before this pass: one entry, GAP-069 (DynamoDB claim
  conflict), `deferred`.
- The dirty working tree at session start (`.gitignore`, `.specify/**`,
  `AGENTS.md`, `CLAUDE.md`, and a handful of untracked files such as
  `coverage-run-phase4.txt`, `guard-tests-phase4.txt`) is still untouched —
  none of it was created or modified by this or the prior addendum, and it
  falls outside the path-limited commits this session made.
- Two stash entries (`stash@{0}: temp-check-baseline`, `stash@{1}`, a
  throwaway mutation-testing scratch) still remain in the repository from an
  earlier session segment; an attempt to drop `stash@{1}` was blocked by the
  auto-mode destructive-action classifier. An operator with stash-drop
  permission can clear both once confirmed disposable.

### ⏭️ Next Steps

1. Obtain the missing second-reviewer pass on findings A/B/C — either retry
   the ChatGPT/Codex MCP bridge once it is diagnosed, or route through a
   human/alternate reviewer — before merging, per the project's standing
   review requirement. This is now the only blocker: CI is green.
2. Merge PR #7 into `main` once the review gap above is closed or explicitly
   waived by the operator.
3. Decide whether the open DynamoDB claim-conflict finding
   (GAP-069/GAP-STATE-06) needs action before or after this merge; it is
   independent of this and the prior addendum's scope.
4. Operator: drop the two stale stash entries once confirmed disposable
   (blocked here by the auto-mode destructive-action classifier).
