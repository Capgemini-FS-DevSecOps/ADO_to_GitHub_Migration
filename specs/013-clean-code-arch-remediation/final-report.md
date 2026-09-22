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

### 📄 Generated Files Summary

- `tests/auth/test_gap_123_escaped_quote_secret_value_leak.py` — new, 3 tests
- `tests/agent/test_gap_124_session_deny_closes_platform_approval.py` — new, 2 tests
- `tests/auth/test_gap_125_creation_time_scope_mismatch_audited.py` — new, 2 tests
- `specs/013-clean-code-arch-remediation/register-pending/finisher-notes.md` —
  appended (scribe-owned; closure notes for findings A/B/C plus the honest
  record of the failed ChatGPT consult attempts)
- This file (`final-report.md`)

Modified: `ado2gh/audit/redaction.py`, `services/agent/routes/execution_routes.py`,
`ado2gh/api/live_approval_scopes.py`, `ado2gh/api/live_approval_store.py`.

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
  read.
- **CI is hung, not merely slow — needs operator attention.** Commits
  `a23f216`, `eaed276`, `4dc1c21` (plus the earlier `0f22911` mypy fix) were
  pushed to `feature/ado-agentic-ai`; a new run was triggered (run id
  `35702328507`, head `4dc1c21`). `lint` and `ui-permissions` completed
  successfully in under a minute each, but the `pytest --cov=ado2gh
  --cov=services --cov-fail-under=69` step started at `07:59:19Z` and was
  still `in_progress` at `11:10Z` — nearly 3 hours, against the documented
  baseline of ~100s for the full suite. This is not isolated to this run:
  every CI run on this branch since the PR-creation commit (`a18be4d`,
  predating this addendum's three fixes) shows the same pattern — `lint` and
  `ui-permissions` complete quickly, `test` never finishes. Checked
  githubstatus.com history for a same-day Actions incident and found none, so
  this reads as a real hang in the run, not a platform outage. It matches
  this project's own documented flake in the Testing section of
  `CLAUDE.md`: a leaked non-daemon aiosqlite checkpointer thread can hang
  pytest after the last test completes, diagnosable with `py-spy dump`, with
  `data/agent_checkpoints.db-wal` appearing mid-run as the tell for a test
  reaching the real checkpoint database instead of its per-test temp file.
  Locally-run subsets (this addendum's targeted tests, the related
  `tests/core`/`tests/agent`/`tests/auth`/`tests/pipeline` surface) completed
  normally in seconds, so the trigger is specific to a full-suite run with
  coverage instrumentation on the GitHub-hosted runner, not something this
  addendum's three commits introduce on their own — the identical hang
  predates them. Recommend the operator cancel the stuck runs and re-run, and
  if it recurs, follow the `py-spy dump` playbook `CLAUDE.md` already
  documents for this exact symptom before merging. Runs to review:
  `https://github.com/Capgemini-FS-DevSecOps/ADO_to_GitHub_Migration/actions/runs/35702328507`
  and, since it is the earliest run showing the same hang,
  `https://github.com/Capgemini-FS-DevSecOps/ADO_to_GitHub_Migration/actions/runs/35699227689`.
- The dirty working tree at session start (`.gitignore`, `.specify/**`,
  `AGENTS.md`, `CLAUDE.md`, and a handful of untracked files such as
  `coverage-run-phase4.txt`, `guard-tests-phase4.txt`) was left untouched —
  none of it was created or modified by this addendum, and it falls outside
  the path-limited commits this session made.
- Two stash entries (`stash@{0}: temp-check-baseline`, `stash@{1}`, a
  throwaway mutation-testing scratch) remain in the repository from an
  earlier session segment; an attempt to drop `stash@{1}` was blocked by the
  auto-mode destructive-action classifier. An operator with stash-drop
  permission can clear it once confirmed disposable.

### ⏭️ Next Steps

1. Confirm CI run `35702328507`'s `test` job conclusion (in progress as of
   this report).
2. Obtain the missing second-reviewer pass on findings A/B/C — either retry
   the ChatGPT/Codex MCP bridge once it is diagnosed, or route through a
   human/alternate reviewer — before merging, per the project's standing
   review requirement.
3. Merge PR #7 into `main` once CI is green and the review gap above is
   closed or explicitly waived by the operator.
4. Decide whether the open DynamoDB claim-conflict finding
   (GAP-069/GAP-STATE-06) needs action before or after this merge; it is
   independent of this addendum's scope.
