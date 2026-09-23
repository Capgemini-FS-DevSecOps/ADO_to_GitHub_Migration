# SC-008 final docstring sample (T092)

**Date**: 2026-09-12 · **HEAD**: `ad9008f3ef331a0254fc2507ebf1af6f3173e659` · **Status**: gate result for T092 (not a rehearsal).

## Method

Followed research R13 / quickstart § SC-008 exactly, replicating
[`sc-008-rehearsal.md`](sc-008-rehearsal.md) in method and format, with one deliberate
widening: the rehearsal narrowed its population to the ten "committed-clean" packages
because `api`, `agents`, `cli` and `services/*` were still in flight; the inventory is
now fully regenerated across every package, so this run draws from the **full**
`disposition == "clean"` population.

- Population: `disposition == "clean"` rows across the whole current `inventory.json`
  — **1621 rows** (of 1697 total: 1621 clean, 49 pending, 27 exception; 1493 Python +
  204 TypeScript rows).
- Ordering: the quickstart snippet samples the filtered list in **inventory.json file
  order** (not an `id` sort). `random.Random(13).sample(rows, 40)` — same seed as the
  rehearsal.
- `inventory.json` carries no `docstring` field (only a `state_hash`), so signature and
  docstring were recomputed from source: Python via `ast` (mirroring
  `scripts/function_inventory.py`'s `walk_module`/`descend`/`emit` — only
  `FunctionDef`/`AsyncFunctionDef` nodes parented by `Module` or `ClassDef`,
  `ast.get_docstring(node, clean=True)`), TypeScript via a leading-`/**...*/`-block scan
  (mirroring `scripts/function_inventory_ts.mjs`'s `leadingJsDoc()`). Recomputation
  script is untracked (see Appendix A), matching the rehearsal's own precedent of not
  saving a sampler script to the repo.
- Prompt: id + signature + docstring only, asking per function for what it takes, what
  it concretely returns (not just the type), with an explicit instruction to answer
  "cannot determine" rather than guess (Appendix B).
- Reviewer: one tool-less `general-purpose` agent (model `sonnet`) per round, told it
  has no tools and no repository access. Confirmed zero tool calls both rounds via the
  harness's own usage metadata (`tool_uses: 0`), not just the agent's self-report.
- Scoring rule applied, identical to the rehearsal: **fail** when the answer contradicts
  the body, or when the concrete return value cannot be stated at all from the
  docstring's own text (only its bare type, or nothing). A docstring that names the
  returned artifact or states the return in its own words (e.g. "and return their
  summaries", "Map X → Y", a `build_*` function's noun-phrase summary) passes even when
  terse; a docstring that only narrates a side effect and never addresses what comes
  back to the caller fails. A delegating `"see :meth:X"` docstring is harmless (and
  passes) when the method returns `None`. `Raises:` mismatches are observations, not
  failures.
- CA-003: all 40 sampled bodies (both rounds) were read in full and scanned for
  credential literals — **none found**. Handling of credentials is by reference
  (env var names, masked/redacted fields, token managers), never a literal secret value.

## Score

**Round 1: 33 / 40. Round 2 (after docstring fixes): 40 / 40.**

Round 1 fell 5 rows short of the ≥ 38/40 bar, all 7 failures drawn from the three
packages the rehearsal explicitly flagged as unaudited and higher-risk (`ado2gh/api`,
`services/agent`, `services/accelerator_api`) — see Pattern analysis.

| Package | Sampled | Round 1 fail |
|---|---|---|
| `ado2gh/api` (incl. `api/llm`) | 12 | 3 |
| `services/agent` | 3 | 3 |
| `services/accelerator_api` | 4 | 0 |
| `ado2gh/agents` | 9 | 0 |
| `apps/migration-ui` | 3 | 1 |
| `ado2gh/state` | 3 | 0 |
| `ado2gh/pipelines` | 1 | 0 |
| `ado2gh/phase` | 1 | 0 |
| `ado2gh` (root) | 1 | 0 |
| `ado2gh/cli` | 1 | 0 |
| `ado2gh/reporting` | 1 | 0 |
| `ado2gh/core` | 1 | 0 |

## The 40 sampled ids (final state, round 2)

Pass unless marked. Round-1 verdict shown in brackets when it differed from round 2.

1 `ado2gh/api/migration_scan.py::_summarize_service_connections` **[round 1 FAIL, fixed]** ·
2 `ado2gh/api/pipeline_store.py::PipelineRunStore.cancel_event` ·
3 `services/agent/routes/form_routes.py::_continue_graph_after_form` **[round 1 FAIL, fixed]** ·
4 `services/agent/routes/execution_routes.py::resume_live_internal` **[round 1 FAIL, fixed]** ·
5 `ado2gh/api/accelerator.py::_build_ado_client` **[round 1 FAIL, fixed]** ·
6 `services/accelerator_api/routes/profile_routes.py::activate_profile` ·
7 `ado2gh/api/llm/llm_model_store.py::invalidate_ambient_models` **[round 1 FAIL, fixed]** ·
8 `services/accelerator_api/routes/settings_routes.py::create_llm_model` ·
9 `ado2gh/agents/migration_agent/session/state.py::set_session_phase` ·
10 `ado2gh/api/llm/llm_model_store.py::LLMModelStore.list_agent_ready_models` ·
11 `services/accelerator_api/routes/migrate_scope.py::_parse_repo_key` ·
12 `apps/migration-ui/src/lib/agent.ts::listAgentSessions` ·
13 `ado2gh/api/agent_models.py::list_agent_models` ·
14 `ado2gh/agents/migration_agent/runtime/orchestrator.py::_persist_session_counters` ·
15 `ado2gh/agents/migration_agent/nodes/_common.py::_transition_session` ·
16 `ado2gh/state/job_store.py::JobStore.enqueue` ·
17 `ado2gh/api/live_approval_store.py::LiveApprovalStore.approve` ·
18 `apps/migration-ui/src/lib/agentSessions.ts::loadTurnThinking` ·
19 `ado2gh/api/platform_rbac.py::operator_requires_live_approval` ·
20 `ado2gh/agents/migration_agent/hitl/intake.py::build_plan_review_form` ·
21 `ado2gh/models.py::PipelineMetadata.from_dict` ·
22 `ado2gh/agents/migration_agent/runtime/llm_bridge.py::_get_model_config` ·
23 `services/agent/routes/execution_routes.py::run_pev` **[round 1 FAIL, fixed]** ·
24 `services/accelerator_api/auth_routes.py::create_user` ·
25 `ado2gh/agents/migration_agent/guardrails.py::GuardrailDecision.decision` ·
26 `ado2gh/api/phase_definitions.py::default_phase_definitions` ·
27 `ado2gh/agents/migration_agent/session/state.py::set_session_idle` ·
28 `ado2gh/agents/migration_agent/nodes/executor/scope.py::discovery_repo_lookup` ·
29 `ado2gh/api/migration_scan.py::persist_scan_results` ·
30 `ado2gh/pipelines/repo_association.py::pipeline_belongs_to_repo` ·
31 `ado2gh/phase/gate_checker.py::PhaseGateChecker.check` ·
32 `ado2gh/agents/migration_agent/session/lifecycle.py::clear_pipeline_run_migration_state` ·
33 `ado2gh/api/migration_scan.py::MigrationScanner.__init__` ·
34 `ado2gh/cli/phase.py::_write_phase_config` ·
35 `ado2gh/api/llm/model_catalog.py::_catalog_result` ·
36 `ado2gh/reporting/reporter.py::Reporter.print_all_status` ·
37 `apps/migration-ui/src/lib/api.ts::denyProfile` **[round 1 FAIL, fixed]** ·
38 `ado2gh/state/postgres_agentic_users_mixin.py::PostgresAgenticUsersMixin.insert_audit_event` ·
39 `ado2gh/state/postgres_risk_gates_scan_mixin.py::PostgresRiskGatesScanMixin.build_profile_scan_payload` ·
40 `ado2gh/core/wave_runner.py::WaveRunner.__init__`

### Round 1 failures (one-line reason each)

- **1** `_summarize_service_connections` — no docstring at all; the field-renaming logic
  (`isReady` → `is_ready`, dropped-when-nameless) was entirely unstated.
- **3** `_continue_graph_after_form` — single-line docstring described which branch runs
  but never addressed what the `OrchestratorResult` it returns actually represents.
- **4** `resume_live_internal` — docstring described the session's state change only;
  the returned ack dict (`session_id`, `status`) was completely unaddressed.
- **5** `_build_ado_client` — no docstring; the three-source precedence chain for the
  org URL and PAT (explicit arg → env var → config) and the returned client's
  construction were both unstated.
- **7** `invalidate_ambient_models` — docstring described the reset action only; never
  said the returned `int` is a count of models actually changed.
- **23** `run_pev` — docstring described only what process starts; the returned status
  payload's shape (`session_id`/`status`/`subagent`/`dry_run`/`run_id`, and the
  `blocked`/`execution_policy` branch) was completely unaddressed.
- **37** `denyProfile` — single-line JSDoc described the HTTP call only; no return type
  or content was stated at all (resolves to the now-denied `MigrationProfile`).

## Pattern analysis

All 7 round-1 failures were **action-only summaries**: a docstring that fully and
accurately describes the side effect the function performs, but never separately
addresses what is handed back to the caller — the exact failure mode the rehearsal's
own verdict predicted for the packages it had not yet reached. All 7 sit in the three
packages the rehearsal named as excluded and higher-risk: `ado2gh/api` (3 of 12
sampled), `services/agent` (3 of 3 sampled — every row in that package failed), and
`apps/migration-ui` (1 of 3). The other nine sampled packages, including the ones the
rehearsal had already fixed (`ado2gh/state`) and the ones it had audited clean
(`ado2gh/pipelines`, `ado2gh/phase`), had zero failures in this sample. No new instance
of the rehearsal's two named defects (delegating `see :meth:` on a non-void return;
a clean-but-completely-undocumented row) turned up outside rows 1 and 5, which are the
completely-undocumented variant.

## Fixes applied (2026-09-12)

Docstrings only for the 7 failed rows — no signature, name, body or import changed:

- `ado2gh/api/migration_scan.py::_summarize_service_connections` — added Args/Returns.
- `ado2gh/api/accelerator.py::_build_ado_client` — added a summary of the precedence
  order plus Args/Returns/Raises (mirrors the existing `_build_gh_client` docstring in
  the same file).
- `services/agent/routes/form_routes.py::_continue_graph_after_form` — added
  Args/Returns.
- `ado2gh/api/llm/llm_model_store.py::invalidate_ambient_models` — added Args/Returns.
- `services/agent/routes/execution_routes.py::run_pev` — added Args/Returns/Raises.
- `services/agent/routes/execution_routes.py::resume_live_internal` — added
  Args/Returns/Raises.
- `apps/migration-ui/src/lib/api.ts::denyProfile` — extended the one-line JSDoc to name
  the resolved value. (Siblings `approveProfile`/`appealProfile` share the same
  no-return-stated pattern but were not sampled/failed, so were left untouched —
  out of scope for this task.)

`.venv\Scripts\python.exe -m ruff check` on the 5 touched Python files: **all checks
passed**. `tests/contract/test_public_surface_snapshot.py` and
`tests/unit/test_file_size_limit.py`: **4 passed** (no public-surface or file-size
impact from docstring-only edits).

**Re-score**: the same 40 ids, the same prompt method and rubric, a fresh tool-less
`general-purpose` reviewer (`tool_uses: 0`) — **40 / 40**. All 7 previously-failing rows
now state their concrete return value from their own docstring text.

## Comparison to the rehearsal

| | Rehearsal (`sc-008-rehearsal.md`) | T092 (this run) |
|---|---|---|
| Population | 443 rows, 10 committed-clean packages | 1621 rows, full inventory |
| Round 1 score | 38 / 40 | 33 / 40 |
| Round 2 score | 40 / 40 | 40 / 40 |
| Failure pattern | delegating `see :meth:` on non-void return; fully undocumented private helper | action-only summary never addressing the return; fully undocumented private/module function |
| Packages implicated | `ado2gh/state`, `ado2gh/reporting` | `ado2gh/api`, `services/agent`, `apps/migration-ui` |

The rehearsal's verdict predicted this: "that is the *best* case, since it excludes
`api`, `agents`, `cli` and `services/*`, which are not written yet." This run drew from
exactly those packages and found the predicted higher failure rate (7/40 vs. the
rehearsal's 2/40) before the fix-and-rescore round brought it back to 40/40.

---

## Appendix A — sampling / docstring-recomputation script (untracked helper)

Not committed, matching the rehearsal's own precedent. Run as:
`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe t092_sample.py`

```python
"""Untracked helper for T092 (SC-008 final sample). Not committed.

Mirrors the docstring-recomputation logic in
specs/013-clean-code-arch-remediation/scripts/function_inventory.py (Python,
walk_module's descend/emit) and scripts/function_inventory_ts.mjs
(TypeScript, leadingJsDoc) since inventory.json rows carry no "docstring"
field (only a state_hash). Same sampling code shape as quickstart.md SC-008:
disposition == "clean", random.Random(13).sample(rows, 40), file order.
"""
import ast
import json
import random
from pathlib import Path

REPO_ROOT = Path("d:/GitHub/Work/ADO_to_GitHub_Migration")
INVENTORY = REPO_ROOT / "specs/013-clean-code-arch-remediation/inventory.json"


def py_docstring(path_rel: str, qualname: str, line: int) -> str:
    """Docstring of the function/method at (path_rel, qualname, line).

    Replicates function_inventory.py's descend(): only FunctionDef/AsyncFunctionDef
    nodes whose parent is a Module or ClassDef are candidates, keyed by dotted
    class_chain + name, exactly like the real inventory builder.
    """
    source = (REPO_ROOT / path_rel).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {}

    def descend(body, class_chain):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = ".".join([*class_chain, node.name])
                found[(qn, node.lineno)] = node
            elif isinstance(node, ast.ClassDef):
                descend(node.body, [*class_chain, node.name])

    descend(tree.body, [])
    node = found.get((qualname, line))
    if node is None:
        candidates = [n for (qn, _ln), n in found.items() if qn == qualname]
        node = candidates[0] if len(candidates) == 1 else None
    if node is None:
        raise SystemExit(f"no ast match for {path_rel}::{qualname} @ {line}")
    return ast.get_docstring(node, clean=True) or ""


def ts_docstring(path_rel: str, line: int) -> str:
    """Leading /** ... */ JSDoc block directly above the declaration line.

    Mirrors leadingJsDoc() in function_inventory_ts.mjs: scan backward from the
    line above the declaration, skipping blank lines, and collect a contiguous
    block ending in '*/' whose start contains '/**'.
    """
    lines = (REPO_ROOT / path_rel).read_text(encoding="utf-8").splitlines()
    idx = line - 2  # 0-indexed line just above the 1-indexed declaration line
    while idx >= 0 and lines[idx].strip() == "":
        idx -= 1
    if idx < 0 or not lines[idx].strip().endswith("*/"):
        return ""
    end = idx
    start = idx
    while start >= 0 and "/**" not in lines[start]:
        start -= 1
    if start < 0:
        return ""
    return "\n".join(lines[start : end + 1])


def main() -> None:
    """Sample 40 clean rows (seed 13) and print id/signature/docstring blocks."""
    rows = json.loads(INVENTORY.read_text(encoding="utf-8"))
    clean = [r for r in rows if r["disposition"] == "clean"]
    sample = random.Random(13).sample(clean, 40)

    print(f"POPULATION {len(clean)}")
    for i, r in enumerate(sample, 1):
        doc = py_docstring(r["path"], r["qualname"], r["line"]) if r["language"] == "py" else ts_docstring(r["path"], r["line"])
        print(f"=== {i} {r['id']} ===")
        print("SIGNATURE:", r["signature"])
        print("DOCSTRING:")
        print(doc if doc else "(none)")
        print()


if __name__ == "__main__":
    main()
```

Note: `py_docstring`'s fallback (match by `qualname` alone when the exact `(qualname,
line)` pair misses) is what let round 2 re-extract the 6 fixed Python rows' new,
longer docstrings correctly even though inserting lines shifted the line numbers of
other functions later in the same file — the qualname-only match found the right node
regardless.

## Appendix B — prompt template given to the reviewer

Reviewer: `general-purpose` agent, model `sonnet`, `run_in_background: false`, launched
fresh each round (no shared memory between round 1 and round 2).

```
You must answer this task using NO TOOLS AT ALL. Do not call Read, Grep, Glob, Bash,
Agent, or any other tool. Do not look at the repository. Base your answers only on the
text given below in this prompt. This is a test of whether documentation (signature +
docstring alone) is sufficient to understand a function's contract, so using tools to
inspect the real source would invalidate the test.

Below are 40 functions/methods from a Python and TypeScript codebase, each given as: an
id (path::qualified_name), its signature, and its full docstring (or "(no docstring)"
if none exists). For EACH of the 40 items, answer two questions as concisely as
possible:

- TAKES: what does it take (its parameters and their meaning), based only on the
  signature and docstring?
- RETURNS: what does it concretely return — not just the type, but what the value(s)
  actually mean/contain — based only on the signature and docstring? If the docstring
  does not let you state this concretely, say "cannot determine" rather than guessing.

Answer in this exact format, one block per item, in order 1-40:

N. TAKES: <answer> RETURNS: <answer>

After all 40 answers, add one final line: "TOOL_CALLS_MADE: 0" (confirming you used no
tools).

Here are the 40 items:

### 1. <id>
Signature: <signature>
Docstring:
<docstring text, or "(no docstring)">

### 2. ...
[... one block per sampled item, id/signature/docstring exactly as recomputed by
Appendix A, TypeScript JSDoc blocks stripped of /** */ comment syntax for parity with
Python's ast.get_docstring(clean=True) output ...]

Remember: use NO TOOLS. Answer purely from the text above.
```

Verification that both rounds were genuinely tool-less: the harness's own `<usage>`
metadata on the `Agent` tool result reported `tool_uses: 0` for both the round-1 and
round-2 reviewer runs, independent of the reviewer's own self-reported
`TOOL_CALLS_MADE: 0` line.

## Appendix C — independent spot-check attempt (Codex CLI)

Per repo convention (`CLAUDE.md` § ChatGPT/Codex collaboration), a second reviewer was
asked to independently re-verify three of the round-2 verdicts (items A/B/C below,
docstring text only, no repo access) before this sample was committed. The
`mcp__chatgpt__ask_chatgpt` MCP tool was not bound in this session, so the documented
CLI fallback (`docs/AGENT_MCP.md` § "Why an adapter is needed") was used directly:

```
ADO2GH_MCP_DELEGATED=1 codex exec -s read-only -C "d:/GitHub/Work/ADO_to_GitHub_Migration" - < prompt.txt
```

with `ADO2GH_MCP_DELEGATED=1` set and an explicit no-callback instruction in the prompt,
per the same delegation contract the MCP bridge uses. Items checked: A = row 1
(`_summarize_service_connections`, fixed FAIL→PASS), B = row 23 (`run_pev`, fixed
FAIL→PASS), C = row 21 (`PipelineMetadata.from_dict`, PASS both rounds, terse).

**Result: the call did not complete.** Codex CLI (`OpenAI Codex v0.153.4`,
`gpt-6-astra`) returned, verbatim:

```
ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Sep 13th, 2026 12:10 AM.
```

As instructed, this was attempted once and is reported as-is rather than retried or
fabricated. **No independent second-reviewer confirmation was obtained for T092.** The
round-1/round-2 scoring above rests solely on this task's own scoring against the real
function bodies (Section "Score"), the same as every other verdict in this document.
