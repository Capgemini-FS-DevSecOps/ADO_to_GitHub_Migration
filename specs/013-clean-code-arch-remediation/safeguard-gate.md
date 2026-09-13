# T093 — Safeguard-preservation gate (CA-001…CA-004)

**Feature**: 013-clean-code-arch-remediation
**Date**: 2026-09-12
**Baseline commit**: `9c83a29` — "post-stabilisation tree, FR-015" (recorded in `gap-register.md` line 4; committed 2026-09-08 01:46:23 -0400). Confirmed via `git merge-base --is-ancestor 9c83a29 HEAD` to be an ancestor of HEAD.
**HEAD commit**: `7a05ca9` (2026-09-12 18:37:23 -0400)
**T011 baseline safeguard counts** (measured 2026-09-08, recorded in `baseline-failures.md` § "Safeguard baseline counts (for T093)"): `can_approve_live_execution` 6; `require_confirmation|confirm_execute|plan_confirmed` 28; `audit_event(` 4; `AuditWriter` 7 (combined 11); `--dry-run` CLI flags 6. All four independently re-measured against commit `9c83a29` with `git grep` below and match exactly.

## Verdict table

| # | Check | Baseline | HEAD | Delta | Verdict |
|---|---|---|---|---|---|
| 1 | CA-001 flags — `--dry-run` in `tests/contract/public_surface_snapshot.json` | 6 (T011 CLI count; snapshot file itself postdates baseline) | 6 | 0 | PASS |
| 2 | CA-001 defaults — removed `dry_run=True` paired with `ExecutionMode.DRY_RUN` | 12 diff lines matched the pattern (10 real removals + 2 signature-only edits) | 10/10 real removals paired or relocated; 2 non-removals confirmed | 0 unpaired | PASS |
| 3 | CA-002 approve — `can_approve_live_execution` | 6 | 11 | +5 | PASS (see note) |
| 4 | CA-002 confirm — `require_confirmation\|confirm_execute\|plan_confirmed` | 28 | 29 | +1 | PASS |
| 5 | CA-003 tests — `pytest -k "mask or redact or secret"` | green at T011 (part of 807-pass baseline) | 44 passed, 9 skipped, 997 deselected, 0 failed/errors | — | PASS |
| 6 | CA-003 secrets grep — added `ghp_/github_pat_/password="` in diff | required 0 | 8 lines matched, all non-secrets (see below) | +8 raw, +0 real | PASS — documented exception, not a clean numeric pass (see note) |
| 7 | CA-004 audit — `AuditWriter\|audit_event(` | 11 (4 `audit_event(` + 7 `AuditWriter`) | 21 | +10 | PASS |

No blocker. Two checks need a note after the Codex/ChatGPT consultation corrected imprecise wording (see "ChatGPT consultation outcome" below): **#3** originally described two HEAD hits as "new call sites" when they are docstring cross-references, not guard invocations (the real enforcement in those two files runs through separately-named functions); **#6** the literal command instruction ("must print nothing") is not met by raw output, but every one of the 8 matches is either the redaction safeguard's own pattern source, documentation describing/quoting the check, or a named fake test fixture — none is a real credential. Detail below.

---

## 1. CA-001 flags

`tests/contract/public_surface_snapshot.json` does not exist at the baseline commit — it was added by `a01fea5` ("test: freeze public surface for 013", 2026-09-08 02:08:49 -0400), ~22 minutes after baseline `9c83a29` (01:46:23 -0400). `git diff 9c83a29..HEAD -- tests/contract/public_surface_snapshot.json` is therefore not meaningful (the file is wholly "added" in that diff). Comparison instead uses the T011-recorded CLI flag count from `baseline-failures.md`, which was measured directly against the CLI (not the snapshot) at the same baseline commit.

Command and output at HEAD:

```
$ grep -n -- "--dry-run" tests/contract/public_surface_snapshot.json
8:    "ado2gh ado-cleanup :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
36:    "ado2gh phase run :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
53:    "ado2gh pipelines retry-failed :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
65:    "ado2gh push-workflows :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
76:    "ado2gh rollback :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
82:    "ado2gh run :: param dry_run :: opts=--dry-run :: type=boolean :: default=False :: required=False :: is_flag=True :: multiple=False",
```

6 flags: `ado-cleanup`, `phase run`, `pipelines retry-failed`, `push-workflows`, `rollback`, `run`. This matches the T011 breakdown exactly (`cli/misc.py` 2, `cli/phase.py` 1, `cli/pipelines.py` 1, `cli/run_cmd.py` 2 = 6). None dropped. PASS.

## 2. CA-001 defaults — removed `dry_run=True` paired with `ExecutionMode.DRY_RUN`

Command: `git diff 9c83a29..HEAD -- ado2gh services | grep -E "^-.*dry_run.*=.*True"` → 12 lines. Re-run with an `awk` pass to associate each removed line with its file unambiguously (plain `grep -n` line numbers on the raw diff text do not reliably map to file boundaries when scanned by eye):

```
ado2gh/agents/migration_agent/nodes/_common.py
  -        if not _failure_is_benign(f, dry_run=bool(executor_result.get("dry_run", True)))
ado2gh/agents/migration_agent/nodes/executor/node.py
  -    dry_run_early = True
  -        dry_run_early = bool(migration_plan_early.get("dry_run", session.get("dry_run", True)))
  -        dry_run_early = bool(session.get("dry_run", True))
  -    dry_run = migration_plan.get("dry_run", session.get("dry_run", True))
ado2gh/agents/migration_agent/nodes/planner_research.py
  -    dry_run: bool = True,
ado2gh/agents/migration_agent/tools/orchestrator_tools.py
  -    def invoke_planner(repository_id: str, dry_run: bool = True, phase: str | None = None) -> dict[str, Any]:
  -    def invoke_bulk_planner(repository_ids: list[str], dry_run: bool = True, phase: str | None = None) -> dict[str, Any]:
services/agent/routes/session_routes.py
  -        session["dry_run"] = outcome.get("dry_run", session.get("dry_run", True))
  -            dry_run=session.get("dry_run", True),
  -        session["dry_run"] = outcome.get("dry_run", session.get("dry_run", True))
  -            dry_run=session.get("dry_run", True),
```

Of the 12 matched diff lines, 10 are real removals of a `dry_run=True` default and 2 (`orchestrator_tools.py`) are signature edits the pattern matched but which removed nothing — see that file's bullet. Disposition, by file:

- **`nodes/_common.py`** (1 line): replaced in the same file by `from ado2gh.models import ExecutionMode` + `executed_mode = ExecutionMode.from_dry_run(dry_run=bool(executor_result.get("dry_run", True)))`. The boolean is still read off the executor result dict (an internal, transient dict — not an external boundary), then converted immediately. PASS.
- **`nodes/executor/node.py`** (4 lines): replaced in the same file by `from ado2gh.models import ExecutionMode`, `mode = ExecutionMode.from_dry_run(dry_run=bool(session.get("dry_run", True)))`, and subsequent `mode is ExecutionMode.DRY_RUN` / `ExecutionMode.LIVE` comparisons plus `mode=ExecutionMode.from_dry_run(dry_run=dry_run)`. PASS.
- **`nodes/planner_research.py`** (1 line, a function parameter default): replaced by `mode: ExecutionMode = ExecutionMode.DRY_RUN,` plus a derived `dry_run = mode is ExecutionMode.DRY_RUN` for any remaining boolean-consuming callee, and the `ExecutionMode` import. This is exactly the `ado2gh/models.py`-driven pattern named in the task's Accumulated Context. PASS.
- **`tools/orchestrator_tools.py`** (2 lines, `invoke_planner`/`invoke_bulk_planner`): **not one of the 10 real removals** — excluded from that count. The only textual change was inserting `*,` before `dry_run` to make it keyword-only (confirmed via full diff context) — `dry_run: bool = True` is still present verbatim in both signatures at HEAD. These are LangChain `StructuredTool` schemas exposed to the LLM, i.e. an external tool-call boundary that the project's convention (CLAUDE.md "Key Patterns") keeps boolean by design, like a CLI flag or HTTP field. Nothing to pair because nothing was removed.
- **`services/agent/routes/session_routes.py`** (4 lines): relocated verbatim, not deleted. `docs/STRUCTURAL_CHANGELOG.md` (row: `services/agent/routes/session_routes.py` → `services/agent/routes/form_routes.py`, "module split — created", "Human-in-the-loop form domain: `_resolve_pending_form` … `_continue_graph_after_form` … `submit_session_form` … All three", 2026-09-09) documents `session_routes.py` being cut from 1439 to ~270 lines across six new route modules. `git diff --stat 9c83a29..HEAD -- services/agent/routes/session_routes.py` confirms 38 insertions / 1125 deletions (1364→277 lines). The exact code is present at HEAD in `services/agent/routes/form_routes.py`: `session["dry_run"] = outcome.get("dry_run", session.get("dry_run", True))` (lines 198, 477) and `dry_run=bool(session.get("dry_run", True))` feeding `ExecutionMode.from_dry_run(...)` (lines 259-260, 575-576). `docs/STRUCTURAL_CHANGELOG.md` row for `ado2gh/api/migration_work_plan.py` (`bool_flag` → `ExecutionMode`, 2026-09-09) additionally documents this same boundary conversion living in `services/agent/routes/form_routes.py`. This is not just a changelog claim: `services/agent/main.py:35` imports `form_router` from `form_routes` and `main.py:48` does `app.include_router(form_router)`, so the relocated code is wired into the live FastAPI app, not dead in an unimported module. PASS via documented, verified relocation.

10/10 real removals accounted for; the 2 `orchestrator_tools.py` lines matched the grep pattern but removed nothing. None is a genuine, unreplaced safeguard loss.

## 3. CA-002 approve — `can_approve_live_execution`

`grep -rn "can_approve_live_execution" ado2gh services --include=*.py | wc -l` → **11** (baseline **6**, T011). Baseline re-measured directly at commit `9c83a29` with `git grep -n "can_approve_live_execution" 9c83a29 -- ado2gh services` (6 hits, exact match, confirming the T011 record independently):

```
BASELINE (9c83a29), 6 hits:
ado2gh/agents/migration_agent/policies.py:133,178,231
ado2gh/agents/migration_agent/route_helpers.py:213
ado2gh/api/platform_rbac.py:42
ado2gh/auth/service.py:72

HEAD, 11 hits:
ado2gh/agents/migration_agent/policies.py:223,371,383
ado2gh/api/platform_rbac.py:129,140,142,194
ado2gh/auth/service.py:133
services/accelerator_api/routes/migrate_guard.py:89
services/accelerator_api/routes/proxy_routes.py:165
services/agent/routes/_helpers.py:243
```

Delta +5. `ado2gh/agents/migration_agent/route_helpers.py:213` → `services/agent/routes/_helpers.py:243` is the file rename named in the task's Accumulated Context (identical guard: `if not perms.get("can_approve_live_execution", False):`) — net zero.

**Correction from the Codex/ChatGPT consultation** (see below): this section originally called the `migrate_guard.py:89` and `proxy_routes.py:165` hits "two new call sites." Re-reading both lines in context shows they are **docstring cross-references**, not guard invocations:
- `migrate_guard.py:84-93` — the docstring for `guard_live_migration()` names the capability in prose; the function's actual enforcement at line 107 calls `operator_requires_live_approval(user, ExecutionMode.from_dry_run(dry_run=dry_run))`, a differently-named function (one of the two signature changes named in the task's Accumulated Context). `git cat-file -e 9c83a29:services/accelerator_api/routes/migrate_guard.py` fails — this file did not exist at baseline at all; it is a wholly new live-migration guard route.
- `proxy_routes.py:162-167` — same pattern: the docstring for `_forward_github()` names the capability; the real enforcement at line 196 calls `require_approve_live_execution(request)`. This file did exist at baseline (0 hits there for this string), so the docstring line is new prose in an existing file.

Both guards are real and independently confirmed (`operator_requires_live_approval` / `require_approve_live_execution` are not part of this grep pattern, so neither is double-counted) — there is no enforcement gap — but the +2 from these files is documentation, not code, and the original wording overstated it. The remaining +3 is in `platform_rbac.py` (1→4): line 140 (`if not permissions_for(user.role).get("can_approve_live_execution"):`) and line 194 (`return not permissions_for(user.role).get("can_approve_live_execution", False)`) are real guard bodies — `require_approve_live_execution` already existed at baseline (confirmed: `git show 9c83a29:ado2gh/api/platform_rbac.py` line 41-42, delegating to `require_capability(request, "can_approve_live_execution")`) and was restructured to check the permission map directly; lines 129 and 142 are a docstring and an HTTP error-detail string on the same guard. No drop anywhere, and no fabricated call site now that the wording is corrected. PASS.

## 4. CA-002 confirm — `require_confirmation\|confirm_execute\|plan_confirmed`

`grep -rn "require_confirmation\|confirm_execute\|plan_confirmed" ado2gh services --include=*.py | wc -l` → **29** (baseline **28**, T011, "measured at T011" per `baseline-failures.md`). Baseline re-measured at `9c83a29` restricted to `*.py` (the unrestricted `git grep` also turns up 3 hits in `prompts/orchestrator.md`, which the original `--include=*.py` measurement excludes):

```
Per-file counts, BASELINE (9c83a29, *.py only) = 28:
  1  ado2gh/agents/migration_agent/guardrails.py
  3  ado2gh/agents/migration_agent/hitl/form_fields.py
 13  ado2gh/agents/migration_agent/hitl/intake.py
  1  ado2gh/agents/migration_agent/hitl/intake_llm.py
 10  ado2gh/agents/migration_agent/hitl/schemas.py

Per-file counts, HEAD (*.py only) = 29:
  1  ado2gh/agents/migration_agent/guardrails.py
  3  ado2gh/agents/migration_agent/hitl/form_fields.py
 14  ado2gh/agents/migration_agent/hitl/intake.py
  1  ado2gh/agents/migration_agent/hitl/intake_llm.py
 10  ado2gh/agents/migration_agent/hitl/schemas.py
```

Delta +1, entirely inside `hitl/intake.py` (13→14), no file added or removed. No rename involved; treated as a normal in-file addition. PASS.

## 5. CA-003 tests — masking/redaction/secret suite

Command: `.venv\Scripts\python.exe -m pytest tests/ -k "mask or redact or secret" -q`, output redirected (never piped) to [`run-t093-ca003.txt`](./run-t093-ca003.txt).

Result: **44 passed, 9 skipped, 997 deselected, 11 warnings in 7.03s** — 0 failed, 0 errors, exit code 0. PASS.

## 6. CA-003 secrets grep

Command: `git diff 9c83a29..HEAD | grep -E "^\+.*(ghp_|github_pat_|password\s*=\s*\")"`. Required to print nothing; it printed **8 matching lines**. Each was inspected individually (never reproduced verbatim below, per instruction not to paste matched secret-looking strings):

| File | What the line actually is |
|---|---|
| `ado2gh/audit/redaction.py` (2 lines) | The redaction module's own pattern source: a comment naming the GitHub-token prefixes it recognises, and the `_SECRET_PATTERNS` regex literal itself. This *is* the CA-003 masking implementation — it necessarily contains these substrings to recognise and mask them. Not a secret. |
| `specs/013-clean-code-arch-remediation/gap-register.md` (1 line) | Spec prose describing which token shapes the redaction patterns match (documentation, not code). |
| `specs/013-clean-code-arch-remediation/quickstart.md` (1 line) | The stored text of this exact grep command, kept as documentation — a self-referential match on the pattern's own literal text. |
| `specs/013-clean-code-arch-remediation/tasks.md` (1 line) | The T093 task description itself, containing the same grep command text — same self-reference reason. |
| `tests/agent/test_gap_011_agent_message_masking.py` (1 line) | A test constant with a `FAKE_`-prefixed name, its value built by string concatenation of a fake prefix and an obviously synthetic body, used to exercise the masking path in a test. |
| `tests/auth/test_gap_010_redact_payload_token_shapes.py` (1 line) | A docstring listing the token-shape prefixes under test (test documentation, not a live value). |
| `tests/unit/test_gap_028_dynamo_double_claim.py` (1 line) | A test constant with a `FAKE_`-prefixed name whose body is all zeros — synthetic by construction. |

None is a genuine credential. All 8 are either the safeguard's own pattern-matching source, spec/task documentation that quotes the check, or clearly-fake test fixtures. Verdict: **PASS as a security property, but not as a clean mechanical gate** — the task's literal instruction ("must print nothing") is not satisfied by raw output, so this is recorded as a documented exception rather than an unqualified pass. The Codex/ChatGPT consultation (below) reviewed this reasoning independently and reached the same substantive conclusion (no blocker-level secret exposure) while agreeing the mechanical check itself should not be marked as cleanly green.

## 7. CA-004 audit — `AuditWriter\|audit_event(`

`grep -rn "AuditWriter\|audit_event(" ado2gh services --include=*.py | wc -l` → **21** (baseline **11** = `audit_event(` 4 + `AuditWriter` 7, both recorded at T011). Baseline re-measured at `9c83a29`:

```
BASELINE (9c83a29), 11 hits:
ado2gh/agents/migration_agent/utils.py:360,363
ado2gh/api/profile_governance.py:7,147
ado2gh/assignments/__init__.py:3,6
ado2gh/assignments/audit.py:46,62
ado2gh/state/base.py:206
ado2gh/state/postgres_agentic_users_mixin.py:13
ado2gh/state/sqlite_agentic_mixin.py:16

HEAD, 21 hits:
ado2gh/agents/migration_agent/utils.py:15,16,394,396,400,404,407
ado2gh/api/profile_governance.py:7,190
ado2gh/audit/writer.py:18,45
ado2gh/audit/__init__.py:4,7
ado2gh/state/base.py:334
ado2gh/state/job_store.py:24,349,528,531,534
ado2gh/state/postgres_agentic_users_mixin.py:31
ado2gh/state/sqlite_agentic_mixin.py:26
```

Delta +10, explained by:
- **Rename**: `ado2gh/assignments/audit.py` and `ado2gh/assignments/__init__.py` (4 baseline hits) → `ado2gh/audit/writer.py` and `ado2gh/audit/__init__.py` (4 HEAD hits), recorded in `docs/STRUCTURAL_CHANGELOG.md` ("`ado2gh/audit/audit.py` → `ado2gh/audit/writer.py`", "moved", "`AuditWriter` half of the same split... nothing remains in `audit.py`", pass (886), 2026-09-08). Net zero from the rename itself.
- **Genuinely new caller**: `ado2gh/state/job_store.py` (5 new hits — a lazy import at line 24, a type-hinted parameter at line 349, and an `_audit` property at lines 528/531/534) did not reference `AuditWriter` at baseline at all. This is new audit-write wiring on the DynamoDB job-store path. The actual write call this plumbing exists to support — `self._audit().write(event_type="job.claim_conflict", ...)` in `_record_claim_conflict()` at line 515 — matches neither half of the grep pattern (it's a `.write(` method call, not `AuditWriter(` or `audit_event(`), so the count if anything *understates* the new coverage here rather than inflating it.
- **More thorough wiring in `utils.py`**: 2 baseline hits → 7 HEAD hits (added a `TYPE_CHECKING` import branch and docstring cross-references alongside the existing lazy-import/property pattern).

No drop anywhere. PASS.

---

## Exact commands run (verbatim, per the Execution Directive)

```
git diff 9c83a29..HEAD -- ado2gh services | grep -E "^-.*dry_run.*=.*True"
grep -rn "can_approve_live_execution" ado2gh services --include=*.py | wc -l
grep -rn "require_confirmation\|confirm_execute\|plan_confirmed" ado2gh services --include=*.py | wc -l
python -m pytest tests/ -k "mask or redact or secret" -q          # via .venv\Scripts\python.exe -m pytest
git diff 9c83a29..HEAD | grep -E "^\+.*(ghp_|github_pat_|password\s*=\s*\")"
grep -rn "AuditWriter\|audit_event(" ado2gh services --include=*.py | wc -l
```

Supplementary (not in the task's verbatim list, run to attribute deltas to specific files/lines so they could be explained rather than merely counted): `git merge-base --is-ancestor`, `git log --diff-filter=A` on the snapshot file, `git grep -n <pattern> 9c83a29 -- ado2gh services` per pattern, an `awk` pass over the `dry_run=True` diff to associate each removed line with its file unambiguously, and per-file `grep`/`git diff` scoped to the five CA-001 files and `services/agent/routes/form_routes.py`.

## ChatGPT consultation outcome

Per repo `CLAUDE.md`, one consultation was run through the Codex CLI channel specified in the task (the `chatgpt` MCP tool was not bound in this session; `codex exec` completed successfully, exit 0). Asked to review the delta explanations skeptically for rationalized drops. It raised three points, all independently re-verified against the actual source (not taken on faith):

1. **CA-003 (§6)**: agreed no blocker-level secret exposure, but said the verdict label should not read as an unqualified `PASS` given the literal "must print nothing" requirement was not met mechanically. Accepted — verdict label and note reworded above to say "documented exception," not a clean pass.
2. **CA-001 (§2)**: said the "12/12 paired or relocated" phrasing overstated the accounting, since the 2 `orchestrator_tools.py` lines are signature-only edits, not removals. Accepted — reworded to "10 real removals" with the 2 signature-only edits called out separately (no verdict change; that file's disposition was already correct, only the count label was imprecise). It also independently confirmed `form_routes.py` is registered in the live app (`main.py:35,48`), which is now cited.
3. **CA-002 (§3)**: said `migrate_guard.py`/`proxy_routes.py` were called "new call sites" when grep-context shows they are docstring mentions, with real enforcement running through `operator_requires_live_approval` / `require_approve_live_execution`. Verified directly (`Read` on both files, plus `git cat-file -e 9c83a29:...migrate_guard.py` confirming that file is new at HEAD): correct. Accepted — §3 rewritten to attribute the delta accurately (2 docstring lines in `migrate_guard.py`/`proxy_routes.py`; the remaining +3 in `platform_rbac.py` breaks down as 2 real guard conditionals, 1 error-detail string from that same guard, and 1 docstring — see §3 for the line-by-line). No verdict change: the underlying approval gate is real and present in both files, just enforced through different function names than the grep pattern.

One consultation claim was checked and **not** accepted: it read the `docs/STRUCTURAL_CHANGELOG.md` citation in §7 as citing the wrong source path for the audit-module move. Re-reading `STRUCTURAL_CHANGELOG.md` lines 356 and 358 shows both a package-level rename (`ado2gh/assignments/` → `ado2gh/audit/`, row 356) and a subsequent file-level split (`ado2gh/audit/audit.py` → `ado2gh/audit/writer.py`, row 358); the original §7 text correctly reflects the resulting baseline→HEAD path chain (`ado2gh/assignments/audit.py` → `ado2gh/audit/writer.py`) across those two rows, so no change was made there.

No blocker was raised or found. All 7 verdicts remain PASS; three sections (§2, §3, §7) were tightened for accuracy and §6's verdict label was made explicitly conditional rather than bare.
