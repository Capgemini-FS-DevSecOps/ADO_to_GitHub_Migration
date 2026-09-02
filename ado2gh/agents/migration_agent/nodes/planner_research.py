"""planner_research.py module — planner tool-call execution and multi-turn research loop."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from ado2gh.agents.migration_agent.constants import (
    PLANNER_MAX_RESEARCH_ROUNDS,
    PLANNER_MIN_RESEARCH_TOOL_CALLS,
)
from ado2gh.agents.migration_agent.nodes.streaming import _stream_llm_response
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _emit_tool_call,
    _emit_tool_result,
    _parse_llm_json,
    canonical_repo_id,
)

_PLANNER_RESEARCH_TOOLS = frozenset({
    "get_current_profile",
    "ado_api_query",
    "github_api_query",
    "call_accelerator",
})


async def _execute_planner_tool_call(
    tc: dict[str, Any],
    session: dict[str, Any],
    accel_get: Any,
    session_token: str | None,
) -> dict[str, Any]:
    """Run a single read-only planner tool call."""
    tool_name = str(tc.get("name", ""))
    args = tc.get("arguments") or {}
    if not isinstance(args, dict):
        args = {}

    if tool_name == "get_current_profile":
        from ado2gh.agents.migration_agent.tools.shared_tools import fetch_current_profile

        try:
            result = await fetch_current_profile(
                accel_get,
                session_token=session_token,
                session_getter=lambda: session,
            )
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "ado_api_query" and accel_get:
        try:
            endpoint = str(args.get("endpoint", "")).lstrip("/")
            result = await accel_get(f"/v1/ado/{endpoint}", session_token=session_token)
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "github_api_query" and accel_get:
        try:
            endpoint = str(args.get("endpoint", "")).lstrip("/")
            result = await accel_get(f"/v1/github/{endpoint}", session_token=session_token)
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "call_accelerator" and accel_get:
        ep = str(args.get("endpoint", "")).lstrip("/")
        if not ep:
            return {"tool": tool_name, "arguments": args, "error": "endpoint_required"}
        try:
            result = await accel_get(f"/{ep}", session_token=session_token)
            if (ep.endswith("/discovery") or "/discovery" in ep) and isinstance(result, dict):
                session["discovery_snapshot"] = result
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    return {"tool": tool_name, "arguments": args, "error": "tool_unavailable"}


async def _gather_planner_baseline_context(
    session: dict[str, Any],
    repos: list[dict[str, Any]],
    accel_get: Any,
    session_token: str | None,
) -> list[dict[str, Any]]:
    """Deterministic pre-LLM probes so planning always has source/target signals."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import repo_config_from_discovery

    findings: list[dict[str, Any]] = []
    discovery = session.get("discovery_snapshot") or {}
    discovery_repos = discovery.get("repos") or [] if isinstance(discovery, dict) else []
    pipeline_counts: dict[str, int] = {}
    if isinstance(discovery, dict):
        for pipe in discovery.get("pipelines") or []:
            if not isinstance(pipe, dict):
                continue
            repo_name = str(pipe.get("repo_name") or pipe.get("repository") or "")
            if repo_name:
                pipeline_counts[repo_name] = pipeline_counts.get(repo_name, 0) + 1

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        repo_key = canonical_repo_id(repo)
        entry: dict[str, Any] = {"repo": repo_key}
        try:
            cfg = repo_config_from_discovery(repo, session)
            entry["github_org"] = cfg.gh_org
            entry["github_repo"] = cfg.gh_repo
            if not str(cfg.gh_org or "").strip():
                entry["github_org_missing"] = True
        except Exception as exc:
            entry["config_error"] = str(exc)
            findings.append(entry)
            continue

        entry["discovery_pipeline_count"] = pipeline_counts.get(
            repo.get("repo_name") or repo.get("name") or "",
            int(repo.get("pipeline_count") or 0),
        )
        entry["repo_feature_detection"] = (
            (session.get("repo_feature_detection") or {}).get(repo_key) or {}
        )

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

        project = repo.get("project") or (repo_key.split("/", 1)[0] if "/" in repo_key else "")
        repo_name = repo.get("repo_name") or repo.get("name") or ""
        from ado2gh.agents.migration_agent.utils import find_discovery_repo

        disc_match = find_discovery_repo(repo_key, discovery_repos) if discovery_repos else None
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
                if not entry["ado_repo"].get("id") and disc_match:
                    entry["ado_repo"] = {
                        "id": disc_match.get("id") or disc_match.get("repo_id") or repo_key,
                        "default_branch": disc_match.get("default_branch"),
                        "source": "discovery_snapshot",
                    }
            except Exception as exc:
                if disc_match:
                    entry["ado_repo"] = {
                        "id": disc_match.get("id") or disc_match.get("repo_id") or repo_key,
                        "default_branch": disc_match.get("default_branch"),
                        "source": "discovery_snapshot",
                    }
                else:
                    entry["ado_repo"] = {"error": str(exc)}

        findings.append(entry)

    session["planner_baseline_context"] = findings
    return findings


def _planner_text_indicates_blocker(parsed: dict[str, Any]) -> bool:
    text = " ".join(
        str(parsed.get(key, ""))
        for key in ("thinking", "risk_summary", "reply")
    ).lower()
    hints = (
        "operator",
        "confirmation",
        "missing",
        "not found",
        "blocker",
        "cannot proceed",
        "critical",
        "verify",
    )
    return any(hint in text for hint in hints)


def _planner_append_thinking(
    session: dict[str, Any],
    content: Any,
    seen: set[str],
) -> None:
    text = str(content or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    _append_and_stream(
        session,
        role="system",
        content=text,
        kind="thinking",
        subagent="planner",
    )


def _planner_operator_input_return(
    session: dict[str, Any],
    plan: dict[str, Any],
    migration_queue: dict[str, Any],
    iteration: int,
    op_req: Any,
) -> dict[str, Any]:
    from ado2gh.agents.migration_agent.hitl.operator_input import store_operator_input

    store_operator_input(session, op_req)
    _append_and_stream(
        session,
        role="system",
        content="Planner: operator decision required — handing to orchestrator.",
        subagent="planner",
    )
    payload = op_req.model_dump() if hasattr(op_req, "model_dump") else op_req
    return {
        "migration_plan": plan,
        "migration_queue": migration_queue,
        "iteration": iteration,
        "pending_clarification": {
            "message_type": "operator_input_required",
            "payload": payload,
        },
        "pending_operator_input": payload,
        "planner_next": "orchestrator",
        "should_return": False,
        "start_pev": False,
    }


async def _run_planner_research_loop(
    state: dict[str, Any],
    session: dict[str, Any],
    llm: Any,
    conversation: list[Any],
    *,
    capabilities: Any = None,
    accel_get: Any = None,
    session_token: str | None = None,
    validation_feedback: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Multi-turn planner loop: tool research → final plan JSON."""
    research_tool_calls = 0
    last_parsed: dict[str, Any] = {}
    replan = bool(validation_feedback)
    seen_thinking: set[str] = set()

    for round_idx in range(PLANNER_MAX_RESEARCH_ROUNDS):
        response_text = await _stream_llm_response(
            llm,
            conversation,
            state,
            subagent="planner",
            capabilities=capabilities,
        )
        last_parsed = _parse_llm_json(response_text)
        _planner_append_thinking(session, last_parsed.get("thinking"), seen_thinking)

        if last_parsed.get("operator_input_request"):
            return last_parsed

        tool_calls = last_parsed.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if last_parsed.get("repos"):
            research_ok = (
                replan
                or last_parsed.get("research_complete")
                or research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS
            )
            if research_ok:
                return last_parsed
            conversation.append(AIMessage(content=response_text))
            conversation.append(
                HumanMessage(
                    content=(
                        f"Complete at least {PLANNER_MIN_RESEARCH_TOOL_CALLS} read-only API "
                        "probes (ADO pipelines, GitHub target repo, dependencies) using "
                        "tool_calls before finalizing. Then set research_complete: true "
                        "with the plan JSON."
                    )
                )
            )
            continue

        if not tool_calls:
            if (
                research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS
                or round_idx >= 1
            ) and _planner_text_indicates_blocker(last_parsed):
                return last_parsed
            if round_idx >= PLANNER_MAX_RESEARCH_ROUNDS - 1:
                break
            conversation.append(AIMessage(content=response_text or "{}"))
            if research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS:
                conversation.append(
                    HumanMessage(
                        content=(
                            "Research probes are complete. If migration cannot proceed, "
                            "respond with operator_input_request JSON (not repeated thinking). "
                            "Otherwise use tool_calls for any remaining checks."
                        )
                    )
                )
            else:
                if dry_run:
                    probe_hint = (
                        "Use tool_calls to research ADO source and expected GitHub targets. "
                        "Dry-run execution is simulated — focus on readiness and blockers."
                    )
                else:
                    probe_hint = (
                        "Use tool_calls to research ADO source and GitHub target state. "
                        "Live execution will write to GitHub — identify blockers now."
                    )
                conversation.append(HumanMessage(content=probe_hint))
            continue

        conversation.append(AIMessage(content=response_text or json.dumps(last_parsed)))
        batch_results: list[dict[str, Any]] = []
        research_tcs = [
            tc
            for tc in tool_calls
            if isinstance(tc, dict) and str(tc.get("name", "")) in _PLANNER_RESEARCH_TOOLS
        ]
        other_tcs = [
            tc
            for tc in tool_calls
            if isinstance(tc, dict) and str(tc.get("name", "")) not in _PLANNER_RESEARCH_TOOLS
        ]

        if research_tcs:
            from ado2gh.agents.migration_agent.nodes.read_tools import invoke_read_tools
            from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools

            planner_tools = get_planner_tools(
                accel_get=accel_get,
                session_token=session_token,
                session_getter=lambda: session,
            )
            tool_messages = await invoke_read_tools(research_tcs, planner_tools)
            for tc, tm in zip(research_tcs, tool_messages):
                research_tool_calls += 1
                tool_name = str(tc.get("name", ""))
                args = tc.get("arguments") or {}
                _emit_tool_call(session, tool_name, subagent="planner", arguments=args)
                result_content: Any = tm.content
                if isinstance(result_content, str):
                    try:
                        result_content = json.loads(result_content)
                    except json.JSONDecodeError:
                        pass
                if tool_name == "call_accelerator":
                    ep = str(args.get("endpoint", ""))
                    if (ep.endswith("/discovery") or "/discovery" in ep) and isinstance(
                        result_content, dict
                    ):
                        session["discovery_snapshot"] = result_content
                _emit_tool_result(
                    session,
                    tool_name,
                    result_content if isinstance(result_content, dict) else {"result": result_content},
                    subagent="planner",
                )
                batch_results.append(
                    {"tool": tool_name, "arguments": args, "result": result_content}
                )

        for tc in other_tcs:
            batch_results.append(
                await _execute_planner_tool_call(tc, session, accel_get, session_token)
            )

        conversation.append(
            HumanMessage(
                content=json.dumps(
                    {
                        "tool_results": batch_results,
                        "research_tool_calls_so_far": research_tool_calls,
                        "min_required": PLANNER_MIN_RESEARCH_TOOL_CALLS,
                    },
                    default=str,
                )[:12000]
            )
        )

    return last_parsed
