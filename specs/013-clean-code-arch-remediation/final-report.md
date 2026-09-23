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

**Phase 9 — the owed second-reviewer pass, and a fourth and final scribe
pass.** Terra (read-only, `codex exec`, artefact
`run-codex-terra-abc.txt`) reviewed the Finding A/B/C diff and found two of
the three fixes incomplete: GAP-123's escape-aware masking still left a lone
trailing backslash before a closing quote unmasked, and GAP-124's denial
forwarding had no test coverage for the platform deny call itself failing
(a failed forward skipped the audit and left `resume-live` free to flip the
session live anyway). Both closed same-day: commit `5663b11` masks the
trailing backslash in both quoted branches of `_SECRET_KEY_VALUE_RE`;
commit `5938388` audits the local denial before the platform-deny forward is
attempted, audits a failed forward as its own
`session.approve.denied.forward_failed` event, and makes
`resume_live_internal` refuse with 409 when the session was already locally
denied. Terra also raised one minor finding on GAP-125 (context-derived
identifiers embedded unminimized in its new audit event) — recorded on the
entry, not fixed, since it is not a live-execution bypass. The fourth scribe
pass folded this into the register: `reopened_and_closed` fields on GAP-123
and GAP-124, a `second_review` field on all three of GAP-123/124/125.
Register totals unchanged: 127 gaps, 24/49/35/19. CI run `35733597922`
(covering `5663b11`) completed `success` on all three jobs.

**Final pass (2026-09-22).** Two source artefacts: `run-astra-final-2026-09-22.txt`
(the Astra final-branch review, eleven numbered findings) and
`run-terra-rules-2026-09-22.txt` (the Terra rule sweep, 73 hardcoded-value,
duplicate-registry and comment-jargon findings). The fixing work for the
eleven Astra findings — plus three more found while fixing them — landed
across commits `28a530b`, `9d8d130`, `030503c`, `16bdf05`, `f284e0f`,
`9c70332`, `afd6e45`, `3069d14` and `b20a6eb`; the Terra sweep's 73 findings
landed across `85adc2a`, `c1f7ee3`, `d4a23c8`, `2bbf5f4`, `cc5bf55`,
`35c95a4`, `1864114`, `a8e0459`, `641fb39`, `c866a56`, `253fea9` and `4a8b05c`.
The register scribe (fifth pass) recorded sixteen new entries, GAP-128
through GAP-143 (fifteen individual findings plus one grouped entry for the
Terra sweep), and two new `plan.md` § Approved contract changes entries:
entry 18 corrects entry 17's now-outdated note that
`PipelineRunStartRequest.dry_run` had never shipped (it has, commit
`8bc89dd`, already recorded as a closed GAP-079 residual in the register
itself), and entry 19 records the form-submission binding fix (GAP-136),
both console (`3069d14`) and service (`b20a6eb`) halves now shipped. New
register totals: 24 critical / 57 high / 41 medium / 21 low, 143 total (was
24/49/35/19/127); open 27 / deferred 3 / remediated 113. The open
critical-or-high set is unchanged: one entry, GAP-069, `deferred`. Detail:
`gap-register.md` § Summary and `plan.md` § Approved contract changes
entries 18-19, both dated 2026-09-22 (fifth/final scribe pass).

**Sixth pass (2026-09-23).** Two pieces of work, both on this branch. First, GAP-069's
DynamoDB claim-conflict finding — open since 2026-09-13 pending a configuration decision —
closed as remediated: commit `41a0e99` adds `ADO2GH_AUDIT_DESTINATION`, letting a DynamoDB
job-store deployment point its audit trail at a database of its own instead of only logging a
refused claim. Second, a new migration knowledge base: five commits (`d4d07bf`, `7eaebf9`,
`671802f`, `9e03f87`, `feac2c7`) add a store of what depends on what, derived from pipeline
inventory already collected, with four honestly-disclosed coverage limits recorded on its own
register entry, GAP-144. `gap-register.md` now totals 143 gaps, open 27 / deferred 2 /
remediated 114, with GAP-144 recorded separately as new work rather than a defect. `plan.md`
§ Approved contract changes gained entries 21 (the three new tables) and 22 (the five new
routes), both approved by operator instruction on 2026-09-23. None of this pass's work has been
test-run as a full suite, pushed, or built by CI yet — that remains the same three steps the
fifth pass already named under Next Steps, still outstanding. PR #7 remains open.

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

Modified (second-reviewer follow-up and fourth scribe pass, Phase 9):
`ado2gh/audit/redaction.py`, `services/agent/routes/execution_routes.py`,
`tests/auth/test_gap_123_escaped_quote_secret_value_leak.py`,
`tests/agent/test_gap_124_session_deny_closes_platform_approval.py`,
`specs/013-clean-code-arch-remediation/gap-register.md`,
`specs/013-clean-code-arch-remediation/plan.md`.

Modified (fifth/final register scribe pass): `specs/013-clean-code-arch-remediation/gap-register.md`,
`specs/013-clean-code-arch-remediation/plan.md`, this file
(`final-report.md`); staged alongside the two source artefacts,
`specs/013-clean-code-arch-remediation/run-astra-final-2026-09-22.txt` and
`specs/013-clean-code-arch-remediation/run-terra-rules-2026-09-22.txt`
(both untracked before this pass). The register scribe did not touch code
or tests this pass — every fix commit named above was already on disk and
independently verified via `git show <hash>` before being recorded.

### ⚠️ Action Required (Incomplete Parts)

- **The second-reviewer pass on findings A/B/C is complete.** Terra
  (read-only, `codex exec`, artefact `run-codex-terra-abc.txt`) reviewed the
  diff and found two of the three fixes incomplete — a trailing-backslash
  masking gap on GAP-123 and a denial-forward-failure gap on GAP-124 — both
  closed same-day (commits `5663b11`, `5938388`; see Phase 9 above). A third,
  minor finding on GAP-125 (context-derived identifiers embedded
  unminimized in its new audit event) was recorded on the entry rather than
  fixed, since it is not a live-execution bypass.
- **CI is green.** Run `35733597922` (covering `5663b11`, the last commit of
  this pass) completed `success` on all three jobs (`lint`,
  `ui-permissions`, `test`).
- The operator's stash entries (`stash@{0}: temp-check-baseline`,
  `stash@{1}`, a throwaway mutation-testing scratch) still remain in the
  repository; an earlier attempt to drop `stash@{1}` was blocked by the
  auto-mode destructive-action classifier. An operator with stash-drop
  permission can clear both once confirmed disposable.
- The DynamoDB claim-conflict finding (GAP-069/GAP-STATE-06) is now
  `remediated` (commit `41a0e99`, 2026-09-23): the audit destination is a
  setting of its own, `ADO2GH_AUDIT_DESTINATION`, so a DynamoDB job-store
  deployment can point its audit trail at a database that survives, instead
  of only logging a refused claim.
- 27 open medium/low follow-ups remain in `gap-register.md` (readability,
  naming and non-safety-impact items, e.g. GAP-035 through GAP-050,
  GAP-062, GAP-080, GAP-097, GAP-112 through GAP-119) — none blocks merge;
  each carries its own entry and rationale in the register.
- GAP-143's Terra-sweep grouping carries four residual items, recorded on
  the entry itself: `services/agent/routes/_helpers.py`'s `_accel_get_impl`
  timeout, `ado2gh/api/llm/http_llm.py`'s language-model request timeout
  (deferred pending a connectivity-settings field), the bare-literal
  truncation length in `hitl/operator_input.py`'s `_slug(...)[:32]`, and
  five LangGraph builder constants (checkpointer timeouts, retry policy)
  that remain candidates for settings fields. The audit-event-name registry
  item that was open when this pass began is now closed (commit `4a8b05c`).
- The full test suite has not been re-run since this pass's fix commits
  landed; run it before merge (`.venv\Scripts\python.exe -m pytest`,
  redirected to a file per the repo's own testing guidance) to confirm the
  coverage ratchet still clears 69% with all sixteen new gaps' regression
  tests included.
- This branch has not been pushed since the commits covering GAP-128
  through GAP-143 landed; push is still needed, and a CI run against the
  pushed head has not yet been triggered or observed for this pass.
- PR #7's ready-for-review status: verified in an earlier pass via
  `gh pr view 7` — `isDraft: false`, `state: OPEN`, `mergeable: MERGEABLE`,
  `mergeStateStatus: CLEAN`. Not re-verified against the commits this pass
  adds; the merge itself remains pending on the push, CI run and full-suite
  confirmation above.

### ⏭️ Next Steps

1. Run the full test suite locally and confirm the coverage ratchet still
   clears 69% with the sixteen new gaps' regression tests included.
2. Push the branch (including this pass's five documentation-only files)
   and confirm a green CI run against the pushed head.
3. Merge PR #7 into `main` once the above are confirmed; nothing else is
   known to block it.
