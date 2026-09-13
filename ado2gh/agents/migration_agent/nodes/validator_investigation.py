"""validator_investigation.py module -- validator baseline probes and LLM evidence-gathering loop."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ado2gh.agents.migration_agent.constants import (
    VALIDATOR_MAX_TOOL_ROUNDS,
    VALIDATOR_MIN_TOOL_CALLS_PIPELINES,
)
from ado2gh.agents.migration_agent.nodes.planner import _planner_text_indicates_blocker
from ado2gh.agents.migration_agent.nodes.streaming import _stream_llm_response
from ado2gh.agents.migration_agent.prompts import get_prompt
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _emit_tool_call,
    _emit_tool_result,
    _parse_llm_json,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.tools import StructuredTool


def _validator_executor_log_summary(executor_result: dict[str, Any]) -> dict[str, Any]:
    """Extract the executor output used as primary evidence in dry-run validation.

    Returns:
        The dry-run flag, per-repo results, failures, skips and pipeline run id
        from the executor result, ready to serialise into the prompt.
    """
    return {
        "dry_run": executor_result.get("dry_run", True),
        "per_repo_results": executor_result.get("per_repo_results"),
        "failures": executor_result.get("failures"),
        "skipped": executor_result.get("skipped"),
        "pipeline_run_id": executor_result.get("pipeline_run_id"),
    }


def _validator_executor_metadata(executor_result: dict[str, Any]) -> dict[str, Any]:
    """Summarise executor metadata for live validation (not evidentiary).

    Returns:
        The repos processed, the failure count and the pipeline run id. Live
        validation must confirm outcomes with API probes, so the executor's own
        claims are deliberately reduced to counts and identifiers here.
    """
    repos = [
        str(r.get("repo") or "")
        for r in (executor_result.get("per_repo_results") or [])
        if isinstance(r, dict) and r.get("repo")
    ]
    return {
        "dry_run": False,
        "repos_processed": repos,
        "failure_count": len(executor_result.get("failures") or []),
        "pipeline_run_id": executor_result.get("pipeline_run_id"),
    }


def _gather_validator_dry_run_evidence(
    executor_result: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build validator findings from executor logs only (dry-run).

    Args:
        executor_result: Executor output for the cycle being validated.
        session: Session dict; the discovery snapshot is read from it and the
            findings are written back under ``validator_baseline_probes``.

    Returns:
        One finding per repo the executor processed, carrying the scopes it
        ran and — for pipeline scopes — the converted workflow count against
        the discovered ADO pipeline count, flagged as
        ``pipeline_conversion_gap`` when pipelines were discovered but nothing
        was converted.
    """
    from ado2gh.agents.migration_agent.utils import canonical_plan_repo_key

    discovery = session.get("discovery_snapshot") or {}
    if isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    pipeline_counts: dict[str, int] = {}
    for pipe in discovery.get("pipelines") or []:
        if not isinstance(pipe, dict):
            continue
        repo_name = str(pipe.get("repo_name") or pipe.get("repository") or "")
        if repo_name:
            pipeline_counts[repo_name] = pipeline_counts.get(repo_name, 0) + 1

    findings: list[dict[str, Any]] = []
    for repo_result in executor_result.get("per_repo_results") or []:
        if not isinstance(repo_result, dict):
            continue
        repo_key = canonical_plan_repo_key(str(repo_result.get("repo") or ""), discovery)
        if not repo_key:
            continue

        scopes = repo_result.get("scopes") or {}
        entry: dict[str, Any] = {
            "repo": repo_key,
            "dry_run": True,
            "scopes_executed": list(scopes.keys()),
            "executor_scopes": scopes,
        }

        pipe_result = scopes.get("pipelines") if isinstance(scopes.get("pipelines"), dict) else {}
        if pipe_result and pipe_result.get("status") not in ("skipped", "pending"):
            wf_files = pipe_result.get("workflow_files") or pipe_result.get("workflows") or []
            entry["executor_workflow_count"] = len(wf_files) if isinstance(wf_files, list) else 0
            repo_short = repo_key.split("/", 1)[-1] if "/" in repo_key else repo_key
            ado_count = pipeline_counts.get(repo_short, 0)
            entry["discovery_pipeline_count"] = ado_count
            if ado_count > 0 and entry["executor_workflow_count"] == 0:
                entry["pipeline_conversion_gap"] = True

        findings.append(entry)

    session["validator_baseline_probes"] = findings
    return findings


def _validator_has_pipeline_scope(executor_result: dict[str, Any]) -> bool:
    """Report whether any repo actually ran the pipelines scope.

    Args:
        executor_result: Executor output for the cycle being validated.

    Returns:
        True when at least one repo has a pipelines scope that was neither
        skipped nor left pending.
    """
    for repo_result in executor_result.get("per_repo_results") or []:
        scopes = repo_result.get("scopes") or {}
        if "pipelines" in scopes and scopes["pipelines"].get("status") not in ("skipped", "pending"):
            return True
    return False


def _validator_text_indicates_blocker(parsed: dict[str, Any]) -> bool:
    """Report whether a parsed validator response describes a blocker.

    Args:
        parsed: The JSON object parsed from the validator's LLM response.

    Returns:
        True when the response names a blocker, using the same rules as the
        planner.
    """
    return _planner_text_indicates_blocker(parsed)


def _validator_append_thinking(
    session: dict[str, Any],
    content: object,
    seen: set[str],
) -> None:
    """Append one validator thinking event, skipping blanks and repeats.

    Args:
        session: Session dict the event is appended to and streamed from.
        content: Raw thinking text; stringified and stripped before use.
        seen: Texts already emitted this round, updated in place.
    """
    text = str(content or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    _append_and_stream(
        session,
        role="system",
        content=text,
        kind="thinking",
        subagent="validator",
    )


async def _gather_validator_baseline_probes(
    session: dict[str, Any],
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    accel_get: Callable[..., Awaitable[Any]] | None,
    session_token: str | None,
) -> list[dict[str, Any]]:
    """Run deterministic pre-LLM validation probes for the executed repos.

    Args:
        session: Session dict; supplies the discovery snapshot and the
            ``dry_run`` fallback.
        executor_result: Executor output for the cycle being validated.
        migration_plan: The approved plan, used to resolve repos missing from
            discovery. May be None or a non-dict, which is ignored.
        accel_get: Accelerator GET callable used to build validator tools;
            when falsy the live probes degrade to discovery-only findings.
        session_token: Bearer token forwarded to the accelerator, if any.

    Returns:
        One finding per repo the executor processed. In dry-run mode these come
        from executor logs; in live mode each entry adds the resolved GitHub
        org/repo and whatever the probe tools returned.
    """
    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )
    if dry_run:
        return _gather_validator_dry_run_evidence(executor_result, session)

    from ado2gh.agents.migration_agent.nodes.executor.plan import repo_config_from_discovery
    from ado2gh.agents.migration_agent.utils import canonical_plan_repo_key, find_discovery_repo

    findings: list[dict[str, Any]] = []
    discovery = session.get("discovery_snapshot") or {}
    if isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    repos_list = discovery.get("repos") or []
    tools_by_name: dict[str, Any] = {}
    if accel_get:
        try:
            from ado2gh.agents.migration_agent.tools import get_validator_tools

            validator_tools = get_validator_tools(
                accel_get,
                session_token,
                session_getter=lambda: session,
            )
            tools_by_name = {t.name: t for t in validator_tools if getattr(t, "name", None)}
        except Exception:
            pass

    for repo_result in executor_result.get("per_repo_results") or []:
        if not isinstance(repo_result, dict):
            continue
        repo_key = canonical_plan_repo_key(str(repo_result.get("repo") or ""), discovery)
        if not repo_key:
            continue

        entry: dict[str, Any] = {
            "repo": repo_key,
            "scopes_executed": list((repo_result.get("scopes") or {}).keys()),
        }
        repo_dict = find_discovery_repo(repo_key, repos_list)
        if not repo_dict and isinstance(migration_plan, dict):
            from ado2gh.agents.migration_agent.utils import canonical_repo_id

            for plan_repo in migration_plan.get("repos") or []:
                if isinstance(plan_repo, dict) and canonical_repo_id(plan_repo) == repo_key:
                    repo_dict = plan_repo
                    break

        if not repo_dict:
            entry["discovery_missing"] = True
            findings.append(entry)
            continue

        try:
            cfg = repo_config_from_discovery(repo_dict, session)
            entry["github_org"] = cfg.gh_org
            entry["github_repo"] = cfg.gh_repo
            if not str(cfg.gh_org or "").strip():
                entry["github_org_missing"] = True
        except Exception as exc:
            entry["config_error"] = str(exc)
            findings.append(entry)
            continue

        if accel_get and entry.get("github_org") and entry.get("github_repo"):
            from ado2gh.agents.migration_agent.hitl.operator_input import parse_github_target_probe

            try:
                gh_path = f"/v1/github/repos/{entry['github_org']}/{entry['github_repo']}"
                gh_resp = await accel_get(gh_path, session_token=session_token)
                entry["github_target"] = parse_github_target_probe(
                    gh_resp if isinstance(gh_resp, dict) else None,
                )
            except Exception as exc:
                entry["github_target"] = parse_github_target_probe(error=exc)

        project = repo_dict.get("project") or (repo_key.split("/", 1)[0] if "/" in repo_key else "")
        repo_name = repo_dict.get("repo_name") or repo_dict.get("name") or ""
        if accel_get and project and repo_name:
            try:
                ado_resp = await accel_get(
                    f"/v1/ado/projects/{project}/repos/{repo_name}",
                    session_token=session_token,
                )
                entry["ado_repo"] = {
                    "id": (ado_resp or {}).get("id") if isinstance(ado_resp, dict) else None,
                    "default_branch": (ado_resp or {}).get("defaultBranch") if isinstance(ado_resp, dict) else None,
                }
            except Exception as exc:
                entry["ado_repo"] = {"error": str(exc)}

        scopes = repo_result.get("scopes") or {}
        pipe_result = scopes.get("pipelines") if isinstance(scopes.get("pipelines"), dict) else {}
        if pipe_result and pipe_result.get("status") not in ("skipped", "pending"):
            if tools_by_name.get("list_ado_pipelines") and project and repo_name:
                try:
                    ado_probe = await _invoke_validator_tool(
                        tools_by_name["list_ado_pipelines"],
                        {"project": project, "repo_name": repo_name},
                    )
                    entry["ado_pipelines_probe"] = ado_probe
                except Exception as exc:
                    entry["ado_pipelines_probe"] = {"error": str(exc)}

            ado_count = int((entry.get("ado_pipelines_probe") or {}).get("pipeline_count") or 0)

            if (
                tools_by_name.get("list_github_workflows")
                and entry.get("github_org")
                and entry.get("github_repo")
            ):
                try:
                    gh_probe = await _invoke_validator_tool(
                        tools_by_name["list_github_workflows"],
                        {
                            "github_org": entry["github_org"],
                            "github_repo": entry["github_repo"],
                        },
                    )
                    entry["github_workflows_probe"] = gh_probe
                    # _invoke_validator_tool is declared -> object (genuinely
                    # dynamic dispatch); this tool's result is a mapping in
                    # practice, per its own docstring ("usually a
                    # JSON-serialisable mapping").
                    gh_probe_dict = cast("dict[str, Any]", gh_probe or {})
                    gh_count = int(gh_probe_dict.get("workflow_count") or 0)
                    entry["github_workflow_count"] = gh_count
                    entry["ado_pipeline_count"] = ado_count
                    if ado_count > 0 and gh_count == 0:
                        entry["pipeline_count_mismatch"] = True
                except Exception as exc:
                    entry["github_workflows_probe"] = {"error": str(exc)}

        findings.append(entry)

    session["validator_baseline_probes"] = findings
    return findings


def _build_validator_investigation_context(
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    session: dict[str, Any],
    baseline_failures: list[dict[str, Any]],
    baseline_findings: list[dict[str, Any]] | None = None,
) -> str:
    """Build the human message that briefs the validator LLM.

    Returns:
        A single prompt string: executor evidence (logs in dry run, metadata
        only when live), any deterministic baseline findings and failures, the
        GitHub org, and the tool-call expectation when a pipeline scope ran.
    """
    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    if dry_run:
        executor_log_json = json.dumps(
            _validator_executor_log_summary(executor_result), default=str
        )[:12000]
        parts = [
            "DRY RUN — no live GitHub/ADO writes were performed.",
            "Validate using executor logs and simulated scope output below. "
            "Do not require GitHub API proof of published workflows or mirrored repos.",
            "Dry run: true",
            f"Executor output (primary evidence): {executor_log_json}",
        ]
    else:
        executor_metadata_json = json.dumps(
            _validator_executor_metadata(executor_result), default=str
        )[:4000]
        parts = [
            "LIVE RUN — verify outcomes exclusively via tool/API evidence.",
            "Do NOT treat executor status fields, scope summaries, or workflow_files "
            "from executor logs as proof of success.",
            "Dry run: false",
            f"Executor metadata only (not evidentiary): {executor_metadata_json}",
        ]

    if migration_plan:
        plan_summary_json = json.dumps(
            {
                "repos": migration_plan.get("repos"),
                "work_items": migration_plan.get("work_items"),
                "revision": migration_plan.get("revision"),
            },
            default=str,
        )[:6000]
        parts.append(f"Migration plan: {plan_summary_json}")
    if baseline_findings:
        label = (
            "Dry-run executor evidence (auto)"
            if dry_run
            else "Deterministic baseline probes (auto)"
        )
        parts.append(
            f"{label}: {json.dumps(baseline_findings[:8], default=str)[:6000]}"
        )
    if baseline_failures:
        parts.append(f"Deterministic baseline failures: {json.dumps(baseline_failures[:10], default=str)}")
    profile = session.get("profile") or {}
    if isinstance(profile, dict) and profile.get("github_org"):
        parts.append(f"GitHub org: {profile.get('github_org')}")
    if _validator_has_pipeline_scope(executor_result):
        if dry_run:
            parts.append(
                "Pipeline scope ran in dry-run — review executor workflow_files and "
                "local validation stats in executor_scopes; API workflow listing is optional."
            )
        else:
            parts.append(
                f"Pipeline scope executed in live mode — perform at least "
                f"{VALIDATOR_MIN_TOOL_CALLS_PIPELINES} tool calls "
                "(list/fetch/validate workflows on GitHub) before concluding."
            )
    return "\n\n".join(parts)


async def _invoke_validator_tool(
    tool: StructuredTool,
    args: dict[str, Any],
) -> object:
    """Invoke a LangChain validator tool (async or sync).

    Args:
        tool: The tool object to call.
        args: Keyword arguments for the tool.

    Returns:
        Whatever the tool returned, usually a JSON-serialisable mapping.

    Raises:
        RuntimeError: The object exposes none of the supported call
            conventions.
    """
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(args)
    # getattr, not attribute access: the caller resolves tools out of a
    # dict[str, Any], so an object that is not a StructuredTool does reach this
    # helper — and the RuntimeError below is the documented outcome for it.
    # Reading tool.coroutine directly turned that into an AttributeError.
    coroutine = getattr(tool, "coroutine", None)
    if coroutine:
        return await coroutine(**args)
    func = getattr(tool, "func", None)
    if func:
        return func(**args)
    raise RuntimeError(f"Tool {getattr(tool, 'name', '?')} is not invokable")


async def _execute_validator_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run a batch of validator tool calls, streaming call and result events.

    Args:
        tool_calls: Tool-call dicts from the model, with ``name`` and
            ``arguments``.
        tools_by_name: Available validator tools keyed by tool name.
        session: Session dict the tool events are streamed through.

    Returns:
        One entry per call, in request order, holding either ``result`` or
        ``error``; an unknown tool name yields ``{"error": "unknown_tool"}``
        rather than raising.
    """
    results: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = str(tc.get("name") or "")
        args = tc.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        tool = tools_by_name.get(name)
        if not tool:
            results.append({"tool": name, "error": "unknown_tool"})
            continue
        _emit_tool_call(session, name, subagent="validator", arguments=args)
        try:
            out = await _invoke_validator_tool(tool, args)
            entry = {"tool": name, "result": out}
            results.append(entry)
            _emit_tool_result(session, name, entry, subagent="validator")
        except Exception as exc:
            entry = {"tool": name, "error": str(exc)}
            results.append(entry)
            _emit_tool_result(session, name, entry, subagent="validator")
    return results


def _normalize_validator_failure(raw: object, *, default_repo: str = "") -> dict[str, Any] | None:
    """Coerce a model-produced failure into the shape the planner consumes.

    Args:
        raw: A candidate failure object from the validator's JSON response.
        default_repo: Repo id to attach when the entry does not name one.

    Returns:
        The normalised failure dict, or None when the input is not a dict or
        carries no failure text worth reporting.
    """
    if not isinstance(raw, dict):
        return None
    specific = str(
        raw.get("specific_failure")
        or raw.get("failure")
        or raw.get("error")
        or ""
    ).strip()
    if not specific:
        return None
    return {
        "repo": raw.get("repo") or default_repo,
        "scope": raw.get("scope") or "",
        "expected_state": raw.get("expected_state"),
        "observed_state": raw.get("observed_state"),
        "specific_failure": specific,
        "file_path": raw.get("file_path"),
        "recommended_remediation": raw.get("recommended_remediation") or raw.get("remediation"),
        "error": specific,
        "source": raw.get("source") or "validator_llm",
        "operator_input_required": raw.get("operator_input_required"),
    }


async def _run_validator_llm_investigation(
    state: dict[str, Any],
    *,
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    baseline_failures: list[dict[str, Any]],
    baseline_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run multi-round LLM validation with read-only tools.

    Args:
        state: Graph state; supplies the LLM, session, accelerator getter,
            session token and model capabilities.
        executor_result: Executor output for the cycle being validated.
        migration_plan: The approved plan, used for repo resolution.
        baseline_failures: Failures the deterministic probes already found.
        baseline_findings: Deterministic probe findings, when they ran.

    Returns:
        The parsed validation verdict, or None when no LLM is configured or the
        rounds produced no usable JSON.
    """
    llm = state.get("llm")
    if not llm or state.get("llm_unconfigured"):
        return None

    session = state.get("session") or {}
    accel_get = state.get("accel_get")
    session_token = state.get("session_token")
    capabilities = state.get("capabilities")

    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    try:
        from ado2gh.agents.migration_agent.tools import get_validator_tools

        validator_tools = get_validator_tools(
            accel_get,
            session_token,
            session_getter=lambda: session,
        )
    except Exception:
        return None

    if not validator_tools:
        return None

    tools_by_name = {t.name: t for t in validator_tools if getattr(t, "name", None)}
    system_prompt = get_prompt("validator")
    context = _build_validator_investigation_context(
        executor_result,
        migration_plan,
        session,
        baseline_failures,
        baseline_findings=baseline_findings,
    )

    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    llm_with_tools = llm
    if capabilities and getattr(capabilities, "supports_tool_calling", False):
        try:
            llm_with_tools = llm.bind_tools(validator_tools)
        except Exception:
            pass

    parsed_report: dict[str, Any] | None = None
    tool_calls_total = 0
    seen_thinking: set[str] = set()
    pipeline_scope = _validator_has_pipeline_scope(executor_result)
    min_tool_calls = (
        0
        if dry_run
        else (VALIDATOR_MIN_TOOL_CALLS_PIPELINES if pipeline_scope else 1)
    )

    for round_idx in range(VALIDATOR_MAX_TOOL_ROUNDS):
        _append_and_stream(
            session,
            role="system",
            content=f"Validator: evidence gathering round {round_idx + 1}/{VALIDATOR_MAX_TOOL_ROUNDS}…",
            subagent="validator",
        )
        response_text = await _stream_llm_response(
            llm_with_tools,
            messages,
            state,
            subagent="validator",
            capabilities=capabilities,
        )
        parsed = _parse_llm_json(response_text) or {}
        _validator_append_thinking(session, parsed.get("thinking"), seen_thinking)

        if parsed.get("operator_input_request"):
            return {"operator_input_request": parsed["operator_input_request"]}

        report = parsed.get("validation_report")
        if isinstance(report, dict):
            parsed_report = report
            break
        if parsed.get("passed") is not None and not parsed.get("tool_calls"):
            parsed_report = parsed
            break

        tool_calls = parsed.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if not tool_calls:
            if parsed.get("failures") or parsed.get("analysis"):
                parsed_report = parsed
                break
            if (
                tool_calls_total >= min_tool_calls or round_idx >= 1
            ) and _validator_text_indicates_blocker(parsed):
                parsed_report = {
                    "passed": False,
                    "analysis": str(parsed.get("thinking") or parsed.get("analysis") or "").strip(),
                    "failures": parsed.get("failures")
                    or [{
                        "scope": "validation",
                        "specific_failure": str(parsed.get("thinking") or "Validator could not verify migration outcomes."),
                        "operator_input_required": True,
                        "source": "validator_llm",
                    }],
                }
                break
            if round_idx >= VALIDATOR_MAX_TOOL_ROUNDS - 1:
                break
            messages.append(AIMessage(content=response_text or "{}"))
            if tool_calls_total >= min_tool_calls:
                messages.append(
                    HumanMessage(
                        content=(
                            "Evidence gathering is sufficient. If validation cannot complete, "
                            "respond with operator_input_request JSON (not repeated thinking). "
                            "Otherwise emit validation_report with tool-backed evidence."
                        )
                    )
                )
            else:
                nudge = (
                    "Summarize validation from executor logs and simulated scope output. "
                    "API tool_calls are optional in dry-run."
                    if dry_run
                    else (
                        "Use tool_calls to verify ADO/GitHub state independently. "
                        "Do not conclude from executor status or logs alone."
                    )
                )
                messages.append(
                    HumanMessage(content=nudge)
                )
            continue

        tool_calls_total += len(tool_calls)
        tool_results = await _execute_validator_tool_calls(tool_calls, tools_by_name, session)
        messages.append(AIMessage(content=response_text))
        messages.append(
            HumanMessage(
                content=(
                    "Tool results (continue investigation or emit validation_report when done):\n"
                    + json.dumps(
                        {
                            "tool_results": tool_results,
                            "tool_calls_total": tool_calls_total,
                            "min_required": min_tool_calls,
                        },
                        default=str,
                    )[:12000]
                ),
            ),
        )

    if parsed_report is None:
        return None

    parsed_report.setdefault("tool_calls_total", tool_calls_total)
    if pipeline_scope and not dry_run:
        if tool_calls_total < VALIDATOR_MIN_TOOL_CALLS_PIPELINES and parsed_report.get("passed", True):
            parsed_report["passed"] = False
            parsed_report.setdefault("analysis", "")
            parsed_report["analysis"] += (
                f" Insufficient tool evidence ({tool_calls_total} calls; "
                f"need {VALIDATOR_MIN_TOOL_CALLS_PIPELINES}+ for pipeline validation)."
            )
            parsed_report.setdefault("failures", []).append({
                "scope": "pipelines",
                "specific_failure": "Validator did not complete required API/tool checks for pipelines",
                "operator_input_required": True,
                "recommended_remediation": (
                    "Re-run validation with list_ado_pipelines, list_github_workflows, "
                    "fetch_github_workflow, and validate_workflow_syntax"
                ),
            })
    return parsed_report
