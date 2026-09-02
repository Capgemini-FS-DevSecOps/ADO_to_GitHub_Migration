"""orchestrator_tools.py module."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.nodes.intent import _begin_new_agent_migration
from ado2gh.agents.migration_agent.utils import (
    _append_event,
    _drain_message_queue,
    _emit_tool_call,
    _emit_tool_result,
    _has_queued_messages,
)

# ─── Orchestrator tool execution (inlined — no separate graph node) ───

def _planner_handoff_state_clear() -> dict[str, Any]:
    """Drop stale graph plan/PEV fields when invoke_planner requests a fresh plan."""
    return {
        "migration_plan": None,
        "migration_queue": None,
        "executor_result": None,
        "validation_result": None,
        "validation_feedback": None,
        "pending_clarification": None,
        "pending_operator_input": None,
    }


async def _apply_orchestrator_tools(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Run orchestrator tool calls inline before graph routing."""
    tool_calls = result.get("tool_calls") or []
    if not tool_calls or result.get("should_return"):
        return result
    merged_state = {**state, **result}
    tool_out = await _execute_orchestrator_tools(merged_state, tool_calls)
    result = {**result, **tool_out, "tool_calls": []}
    if tool_out.get("pending_form"):
        result["should_return"] = True
    return result


async def _execute_orchestrator_tools(
    state: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute orchestrator tool calls (forms, planner handoff, read-only queries)."""
    session = state.get("session") or {}
    accel_get = state.get("accel_get")
    build_plan = state.get("build_plan")
    session_token = state.get("session_token")

    results = []
    start_pev = False
    pending_form = None
    should_return = False
    reply = None
    planner_handoff_clear: dict[str, Any] = {}

    for tc in tool_calls:
        tool_name = tc.get("name", "")
        args = tc.get("arguments", {}) or {}
        if not isinstance(args, dict):
            args = {}

        is_cached_discovery = (
            tool_name == "call_accelerator"
            and "/discovery" in str(args.get("endpoint", ""))
            and session.get("discovery_snapshot")
        )
        if not is_cached_discovery:
            _emit_tool_call(session, tool_name, subagent="orchestrator", arguments=args)

        if tool_name == "get_current_profile":
            from ado2gh.agents.migration_agent.tools.shared_tools import fetch_current_profile

            try:
                result = await fetch_current_profile(
                    accel_get,
                    session_token=session_token,
                    session_getter=lambda: session,
                )
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "ado_api_query" and accel_get:
            try:
                endpoint = str(args.get("endpoint", "")).lstrip("/")
                result = await accel_get(f"/v1/ado/{endpoint}", session_token=session_token)
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "github_api_query" and accel_get:
            try:
                endpoint = str(args.get("endpoint", "")).lstrip("/")
                result = await accel_get(f"/v1/github/{endpoint}", session_token=session_token)
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "generate_plan" and build_plan:
            try:
                phase = args.get("phase") or session.get("plan_phase")
                repo_id = args.get("repository_id") or session.get("plan_repository_id")
                result = await build_plan(
                    session,
                    session_token,
                    phase=phase,
                    repository_id=repo_id,
                )
                if result and result.get("error"):
                    results.append({"tool": tool_name, "error": result.get("message", str(result.get("error")))})
                    state["messages"] = state.get("messages", []) + [{
                        "role": "tool",
                        "content": f"Tool '{tool_name}' failed: {result.get('message', '')}",
                    }]
                else:
                    session["migration_plan"] = result
                    results.append({"tool": tool_name, "result": result})
                    state["messages"] = state.get("messages", []) + [{
                        "role": "tool",
                        "content": f"Tool '{tool_name}' succeeded. Plan built with {len(result.get('work_items', []))} work items.",
                    }]
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
                state["messages"] = state.get("messages", []) + [{
                    "role": "tool",
                    "content": f"Tool '{tool_name}' raised exception: {e}",
                }]
        elif tool_name == "invoke_planner":
            from ado2gh.agents.migration_agent.hitl.intake import (
                analysis_from_state,
                apply_message_analysis,
                intake_from_session,
                intake_ready_for_planner,
                resolve_intake_routing,
                sync_intake_to_session,
            )
            from ado2gh.agents.migration_agent.hitl.intake_guardrails import (
                apply_analysis_guardrails,
                repository_confirmed_for_turn,
            )

            user_message = state.get("user_message", "") or ""
            intake = intake_from_session(session)
            analysis = analysis_from_state(state)
            if analysis:
                analysis = apply_analysis_guardrails(analysis, user_message)
                intake = apply_message_analysis(intake, analysis)
            repo_for_form = (
                args.get("repository_id")
                or intake.resolved_repository_id()
                or session.get("plan_repository_id", "")
            )
            if repo_for_form and not intake.resolved_repository_id():
                intake = intake.model_copy(update={"repository_id": repo_for_form})
            sync_intake_to_session(session, intake)
            repo_id = (
                args.get("repository_id")
                or intake.resolved_repository_id()
                or session.get("plan_repository_id", "")
            )
            if repo_id and "/" not in repo_id:
                from ado2gh.agents.migration_agent.utils import canonical_repo_id, find_discovery_repo

                discovery = session.get("discovery_snapshot") or {}
                repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
                match = find_discovery_repo(repo_id, repos)
                if match:
                    repo_id = canonical_repo_id(match)
                    session["plan_repository_id"] = repo_id
            if not intake_ready_for_planner(intake):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=user_message,
                    analysis=analysis,
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                    results.append({
                        "tool": tool_name,
                        "result": {"status": "awaiting_intake", "missing": routing.get("missing_fields")},
                    })
                    if not is_cached_discovery:
                        _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                    continue
            if not repo_id:
                repo_id = (
                    args.get("repository_id")
                    or intake.resolved_repository_id()
                    or session.get("plan_repository_id", "")
                )
            if not repository_confirmed_for_turn(repo_id, analysis, session=session):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=user_message,
                    analysis=analysis,
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                results.append({
                    "tool": tool_name,
                    "result": {
                        "status": "awaiting_intake",
                        "error": "repository_not_named_in_message",
                        "missing": routing.get("missing_fields") if routing else ["repository_id"],
                    },
                })
                if not is_cached_discovery:
                    _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                continue
            dry_run = session.get("dry_run", args.get("dry_run", True))
            phase = args.get("phase")
            session["plan_repository_id"] = repo_id
            session["dry_run"] = dry_run
            if phase:
                session["plan_phase"] = phase
            _begin_new_agent_migration(session, repository_id=repo_id)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {
                "status": "invoking_planner",
                "repository_id": repo_id,
                "dry_run": dry_run,
            }})
        elif tool_name == "invoke_bulk_planner":
            from ado2gh.agents.migration_agent.hitl.intake import (
                analysis_from_state,
                intake_from_session,
                intake_ready_for_planner,
                resolve_intake_routing,
            )

            intake = intake_from_session(session)
            if not intake_ready_for_planner(intake):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=state.get("user_message", "") or "",
                    analysis=analysis_from_state(state),
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                    results.append({
                        "tool": tool_name,
                        "result": {"status": "awaiting_intake", "missing": routing.get("missing_fields")},
                    })
                    if not is_cached_discovery:
                        _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                    continue
            # Orchestrator is handing off to the Planner for bulk migration
            repo_ids = args.get("repository_ids", [])
            if not repo_ids:
                # Fallback: extract repo IDs from user message
                user_msg = state.get("user_message", "") or ""
                import re as _re
                repo_ids = _re.findall(r'([\w.-]+/[\w.-]+)', user_msg)
            # Prefer session's dry_run over LLM's argument
            dry_run = session.get("dry_run", args.get("dry_run", True))
            phase = args.get("phase")
            session["plan_repository_ids"] = repo_ids
            session["dry_run"] = dry_run
            if phase:
                session["plan_phase"] = phase
            _begin_new_agent_migration(session)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {
                "status": "invoking_bulk_planner",
                "repository_ids": repo_ids,
                "dry_run": dry_run,
            }})
        elif tool_name == "run_migration_pev":
            _begin_new_agent_migration(session)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {"status": "pev_starting"}})
        elif tool_name == "fetch_migration_status" and accel_get:
            run_id = session.get("run_id")
            if run_id and accel_get:
                try:
                    result = await accel_get(
                        f"/v1/pipeline/runs/{run_id}",
                        session_token=session_token,
                    )
                    results.append({"tool": tool_name, "result": result})
                except Exception as e:
                    results.append({"tool": tool_name, "error": str(e)})
            else:
                results.append({"tool": tool_name, "result": {"status": "no_active_run"}})
        elif tool_name == "request_user_input":
            from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
            # Apply guardrails to LLM-generated form (truncation, field limits)
            form_args = sanitize_form(dict(args))
            pending_form = form_args
            results.append({"tool": tool_name, "result": {"form": form_args}})
        else:
            results.append({"tool": tool_name, "error": f"Unknown tool: {tool_name}"})

        if not is_cached_discovery and results and results[-1].get("tool") == tool_name:
            _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")

    # Process queued messages after tool execution
    if _has_queued_messages(session):
        queued = _drain_message_queue(session)
        for msg in queued:
            _append_event(session, role="user", content=msg, kind="message")

    return {
        "start_pev": start_pev,
        "pending_form": pending_form,
        "should_return": should_return or bool(pending_form),
        "reply": reply,
        **planner_handoff_clear,
    }


async def execute_tools_node(state: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias — tools run inside orchestrator_node."""
    return await _execute_orchestrator_tools(state, state.get("tool_calls") or [])


async def _execute_rollback(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """T101: Execute rollback by deleting GitHub resources from rollback_records."""
    rollback_records = state.get("rollback_records", [])
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")

    if not rollback_records:
        _append_event(session, role="system", content="Rollback: no resources to rollback.", kind="thinking", subagent="executor")
        return {"rollback_complete": True, "rollback_count": 0}

    _append_event(
        session,
        role="system",
        content=f"Rollback: starting deletion of {len(rollback_records)} resources…",
        kind="thinking",
        subagent="executor",
    )

    deleted_count = 0
    failed_count = 0

    for record in rollback_records:
        resource_type = record.get("resource_type", "")
        resource_name = record.get("resource_name", "")
        github_org = record.get("github_org", "")

        _append_event(
            session,
            role="system",
            content=f"Rollback: deleting {resource_type} {resource_name}…",
            kind="thinking",
            subagent="executor",
        )

        if not accel_post:
            _append_event(
                session,
                role="system",
                content=f"Rollback: accelerator unavailable for {resource_name}",
                kind="thinking",
                subagent="executor",
            )
            failed_count += 1
            continue

        try:
            # Call accelerator rollback endpoint
            await accel_post(
                "/v1/sessions/{session_id}/rollback",
                json={
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "github_org": github_org,
                },
                session_token=session_token,
            )
            deleted_count += 1
            _append_event(
                session,
                role="system",
                content=f"Rollback: deleted {resource_type} {resource_name}",
                kind="thinking",
                subagent="executor",
            )
        except Exception as e:
            failed_count += 1
            _append_event(
                session,
                role="system",
                content=f"Rollback: failed to delete {resource_name} - {str(e)}",
                kind="thinking",
                subagent="executor",
            )

    _append_event(
        session,
        role="system",
        content=f"Rollback: complete — {deleted_count} deleted, {failed_count} failed.",
        kind="thinking",
        subagent="executor",
    )

    return {
        "rollback_complete": True,
        "rollback_count": deleted_count,
        "rollback_failed": failed_count,
    }

