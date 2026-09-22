"""planner_research.py module — planner tool-call execution and multi-turn research loop."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

from langchain_core.messages import AIMessage, HumanMessage

from ado2gh.agents.migration_agent.constants import (
    PLANNER_MAX_RESEARCH_ROUNDS,
    PLANNER_MIN_RESEARCH_TOOL_CALLS,
)
from ado2gh.agents.migration_agent.nodes.streaming import _stream_llm_response
from ado2gh.agents.migration_agent.untrusted import fence_untrusted
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _emit_tool_call,
    _emit_tool_result,
    _parse_llm_json,
    canonical_repo_id,
)
from ado2gh.api.proxy_prefixes import ADO_PROXY_PREFIX, GITHUB_PROXY_PREFIX
from ado2gh.audit.redaction import redact_payload
from ado2gh.models import ExecutionMode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.language_models import BaseChatModel

    from ado2gh.agents.migration_agent.hitl.operator_input import OperatorInputRequest

_PLANNER_RESEARCH_TOOLS = frozenset({
    "get_current_profile",
    "ado_api_query",
    "github_api_query",
    "call_accelerator",
})

# The one accelerator route whose body may become the session's durable
# discovery snapshot. Matched whole, not by substring: the snapshot is rendered
# into the *system* prompt on every later turn (THR-04-001), and a substring
# test accepted any model-chosen path ending in "/discovery".
_DISCOVERY_PATH_RE = re.compile(r"^/v1/settings/profiles/[^/]+/discovery$")


def _discovery_snapshot(endpoint: str, result: object) -> dict[str, Any] | None:
    """Decide whether a call_accelerator result may become the discovery snapshot.

    Args:
        endpoint: The endpoint string the model asked for.
        result: The decoded tool result.

    Returns:
        A masked copy of the result when the endpoint is exactly the profile
        discovery route and the body has the shape the snapshot readers expect
        (a ``repos`` list), otherwise ``None``.
    """
    # Matched against the decoded path, because that is what the accelerator
    # routes on: `profiles/a%2Fb/discovery` is two segments there, not one.
    path = "/" + unquote(str(endpoint).split("?", 1)[0]).lstrip("/")
    if not _DISCOVERY_PATH_RE.match(path):
        return None
    if not isinstance(result, dict) or not isinstance(result.get("repos"), list):
        return None
    # Masked here, not at the prompt: the snapshot is durable session state that
    # the graph checkpointer persists (`graph/state.py`, `graph/builder.py`), so
    # the fence on the model's copy never sees these bytes (THR-02-001).
    return cast("dict[str, Any]", redact_payload(result))


async def _execute_planner_tool_call(
    tc: dict[str, Any],
    session: dict[str, Any],
    accel_get: Callable[..., Awaitable[Any]] | None,
    session_token: str | None,
) -> dict[str, Any]:
    """Run a single read-only planner tool call.

    Args:
        tc: Tool-call dict from the model, with ``name`` and ``arguments``.
        session: Session dict; a discovery response is cached back into it.
        accel_get: Accelerator GET callable; when falsy every accelerator-backed
            tool reports itself unavailable.
        session_token: Bearer token forwarded to the accelerator, if any.

    Returns:
        A record of the call carrying ``tool``, ``arguments`` and either
        ``result`` or ``error``. Unknown or unusable tools return
        ``error: "tool_unavailable"`` rather than raising.
    """
    tool_name = str(tc.get("name", ""))
    args = tc.get("arguments") or {}
    if not isinstance(args, dict):
        args = {}

    from ado2gh.agents.migration_agent.tools.shared_tools import join_api_path, tool_error

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
            return {"tool": tool_name, "arguments": args, **tool_error(exc)}

    prefixes = {"ado_api_query": ADO_PROXY_PREFIX, "github_api_query": GITHUB_PROXY_PREFIX}
    if tool_name in prefixes and accel_get:
        try:
            path = join_api_path(prefixes[tool_name], str(args.get("endpoint", "")))
            result = await accel_get(path, session_token=session_token)
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, **tool_error(exc)}

    if tool_name == "call_accelerator" and accel_get:
        endpoint = str(args.get("endpoint", ""))
        if not endpoint.lstrip("/"):
            return {"tool": tool_name, "arguments": args, "error": "endpoint_required"}
        try:
            result = await accel_get(join_api_path("/", endpoint), session_token=session_token)
            snapshot = _discovery_snapshot(endpoint, result)
            if snapshot is not None:
                session["discovery_snapshot"] = snapshot
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, **tool_error(exc)}

    return {"tool": tool_name, "arguments": args, "error": "tool_unavailable"}


async def _gather_planner_baseline_context(
    session: dict[str, Any],
    repos: list[dict[str, Any]],
    accel_get: Callable[..., Awaitable[Any]] | None,
    session_token: str | None,
) -> list[dict[str, Any]]:
    """Run deterministic pre-LLM probes so planning always has source/target signals.

    Args:
        session: Session dict; supplies the discovery snapshot and receives the
            findings under ``planner_baseline_context``.
        repos: Repo dicts the plan will cover.
        accel_get: Accelerator GET callable used for the probes; when falsy the
            findings are limited to what discovery already knows.
        session_token: Bearer token forwarded to the accelerator, if any.

    Returns:
        One finding per repo, carrying the resolved GitHub org/repo, the
        discovered ADO pipeline count and any probe errors — or
        ``config_error`` when the repo config could not be built at all.
    """
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
                gh_path = f"{GITHUB_PROXY_PREFIX}/repos/{entry['github_org']}/{entry['github_repo']}"
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
                    f"{ADO_PROXY_PREFIX}/projects/{project}/repos/{repo_name}",
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
    """Report whether a parsed planner response reads like it hit a blocker.

    Args:
        parsed: The JSON object parsed from the planner's LLM response.

    Returns:
        True when the thinking, risk summary or reply mentions a blocker hint.
        This is a keyword heuristic on free text, not a structured signal.
    """
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
    content: object,
    seen: set[str],
) -> None:
    """Append one planner thinking event, skipping blanks and repeats.

    Args:
        session: Session dict the event is appended to and streamed from.
        content: Raw thinking text; stringified and stripped before use.
        seen: Texts already emitted this loop, updated in place.
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
        subagent="planner",
    )


def _planner_operator_input_return(
    session: dict[str, Any],
    plan: dict[str, Any],
    migration_queue: dict[str, Any],
    iteration: int,
    op_req: OperatorInputRequest,
) -> dict[str, Any]:
    """Build the planner return that hands an operator decision to the orchestrator.

    Args:
        session: Session dict; the request is stored on it and announced.
        plan: The plan produced so far, carried through unchanged.
        migration_queue: The batch queue, carried through unchanged.
        iteration: Current plan-execute-validate loop (PEV) iteration count.
        op_req: The operator-input request, dumped to a dict when it is a
            Pydantic model.

    Returns:
        An ``AgentState`` update that routes to the orchestrator with the
        request attached as both ``pending_clarification`` and
        ``pending_operator_input``, and execution held back.
    """
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
    conversation: list[Any],
    *,
    llm: BaseChatModel,
    validation_feedback: dict[str, Any] | None = None,
    mode: ExecutionMode = ExecutionMode.DRY_RUN,
) -> dict[str, Any]:
    """Run the multi-turn planner loop: tool research, then final plan JSON.

    Args:
        state: Graph state; the session, model capabilities, accelerator getter
            and session token are read from it.
        conversation: Message list for the loop, extended in place with each
            round's assistant reply and tool results.
        llm: Chat model to stream — the planner passes the tool-bound model, so
            this is not read off ``state``.
        validation_feedback: Feedback from a failed plan-execute-validate loop
            cycle; its presence marks this as a replan and lifts the
            minimum-probe requirement.
        mode: Execution mode the plan targets; only changes the wording of the
            probe hint given to the model. Defaults to dry run (CA-001).

    Returns:
        The last parsed JSON object from the model: the plan once it carries
        ``repos``, an ``operator_input_request`` when the planner needs a
        decision, or whatever the final round produced if the loop ran out of
        rounds.
    """
    session = state.get("session") or {}
    capabilities = state.get("capabilities")
    accel_get = state.get("accel_get")
    session_token = state.get("session_token")
    dry_run = mode is ExecutionMode.DRY_RUN
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
                    snapshot = _discovery_snapshot(str(args.get("endpoint", "")), result_content)
                    if snapshot is not None:
                        session["discovery_snapshot"] = snapshot
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
                content=(
                    f"research_tool_calls_so_far: {research_tool_calls}, "
                    f"min_required: {PLANNER_MIN_RESEARCH_TOOL_CALLS}.\n"
                    + fence_untrusted("planner_tool_results", {"tool_results": batch_results})
                )
            )
        )

    return last_parsed
