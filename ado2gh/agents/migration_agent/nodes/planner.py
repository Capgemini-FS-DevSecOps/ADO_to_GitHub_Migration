"""planner.py module."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ado2gh.agents.migration_agent.nodes._common import (
    _is_pev_max_retries_exhausted,
    _transition_session,
)
from ado2gh.agents.migration_agent.nodes.planner_plan_builders import (
    _advance_migration_queue,  # noqa: F401 -- re-exported for nodes.planner consumers
    _build_heuristic_plan,
    _build_migration_plan_from_llm,
    _build_migration_queue_from_plan,
)

# ─── Node: planner ────────────────────────────────────────────────────
from ado2gh.agents.migration_agent.nodes.planner_research import (
    _gather_planner_baseline_context,
    _planner_operator_input_return,
    _planner_text_indicates_blocker,
    _run_planner_research_loop,
)
from ado2gh.agents.migration_agent.prompts import get_prompt
from ado2gh.agents.migration_agent.runtime.context_window import build_context_with_cycle_summaries
from ado2gh.agents.migration_agent.session.state import SessionState
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    canonical_repo_id,
    find_discovery_repo,
    normalize_discovery_repo,
    repo_not_found_clarification,
)


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Planner node — generates structured migration plans.

    Binds planner tools via bind_tools(), uses astream() for planning thoughts,
    generates MigrationPlan with topological sort, stores in AgentState.migration_plan.
    Handles validation_feedback for revised plans and sets pending_clarification
    when discovery data is insufficient.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.PLANNING)
    try:
        return await _planner_node_impl(state, session)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        _append_and_stream(
            session,
            role="system",
            content=f"Planner error: {exc}\n{tb}",
            subagent="planner",
        )
        return {
            "error": str(exc),
            "should_return": True,
            "pending_clarification": None,
        }


async def _planner_post_validation_handoff(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any] | None:
    """Route validator outcomes through planner (validator → planner only)."""
    validation = state.get("validation_result")
    if validation is None:
        return None

    feedback = state.get("validation_feedback")
    passed = bool(validation.get("passed"))

    if passed and not feedback:
        migration_queue = state.get("migration_queue")
        if migration_queue:
            queue_items = migration_queue.get("items", [])
            current_index = int(migration_queue.get("current_index", 0) or 0)
            if queue_items and current_index < len(queue_items):
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Planner: validation passed — continuing queue "
                        f"({current_index + 1}/{len(queue_items)})."
                    ),
                    subagent="planner",
                )
                return {
                    "planner_next": "executor",
                    "start_execution": True,
                    "migration_plan": state.get("migration_plan") or session.get("migration_plan"),
                    "migration_queue": migration_queue,
                    "should_return": False,
                }
            if queue_items:
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: migration queue complete — handing results to orchestrator.",
                    subagent="planner",
                )
        _append_and_stream(
            session,
            role="system",
            content="Planner: validation complete — handing results to orchestrator.",
            subagent="planner",
        )
        return {"planner_next": "orchestrator", "should_return": False}

    if feedback:
        failures = (
            (feedback.get("failures") or [])
            if isinstance(feedback, dict)
            else []
        )
        dry_run = bool(
            (validation or {}).get("dry_run", session.get("dry_run", True))
        )
        from ado2gh.agents.migration_agent.hitl.operator_input import failures_require_operator_escalation
        from ado2gh.agents.migration_agent.nodes.validator import _all_failures_benign

        if failures and failures_require_operator_escalation(failures):
            session.pop("start_execution", None)
            _append_and_stream(
                session,
                role="system",
                content="Planner: operator action required — handing results to orchestrator.",
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "start_execution": False,
            }
        if failures and _all_failures_benign(failures, dry_run=dry_run):
            _append_and_stream(
                session,
                role="system",
                content=(
                    "Planner: validation reported only non-retryable dry-run skips — "
                    "continuing without replan."
                ),
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "validation_feedback": None,
            }
        if isinstance(feedback, dict) and (
            feedback.get("escalate") or not feedback.get("retry_recommended", True)
        ):
            session.pop("start_execution", None)
            session["pev_max_retries_exhausted"] = True
            _append_and_stream(
                session,
                role="system",
                content="Planner: validation failed after max retries — handing to orchestrator.",
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "start_execution": False,
            }
        return None

    session.pop("start_execution", None)
    session["pev_max_retries_exhausted"] = True
    _append_and_stream(
        session,
        role="system",
        content="Planner: validation failed after max retries — handing to orchestrator.",
        subagent="planner",
    )
    return {
        "planner_next": "orchestrator",
        "should_return": False,
        "start_execution": False,
    }


async def _planner_node_impl(state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Planner node implementation — wrapped by planner_node for error handling."""
    post_validation = await _planner_post_validation_handoff(state, session)
    if post_validation is not None:
        return post_validation

    validation_feedback = state.get("validation_feedback")
    if validation_feedback and isinstance(validation_feedback, str):
        try:
            validation_feedback = json.loads(validation_feedback)
        except Exception:
            validation_feedback = None
    if (
        isinstance(validation_feedback, dict)
        and validation_feedback.get("failures")
        and not session.get("pending_operator_input")
    ):
        dry_run = bool(session.get("dry_run", True))
        from ado2gh.agents.migration_agent.nodes.validator import _all_failures_benign

        if _all_failures_benign(validation_feedback["failures"], dry_run=dry_run):
            validation_feedback = None
        else:
            from ado2gh.agents.migration_agent.hitl.operator_input import (
                assess_operator_input_needed,
                store_operator_input,
            )

            existing_plan = state.get("migration_plan") or session.get("migration_plan") or {}
            if isinstance(existing_plan, str):
                try:
                    existing_plan = json.loads(existing_plan)
                except Exception:
                    existing_plan = {}
            op_req = assess_operator_input_needed(
                plan=existing_plan if isinstance(existing_plan, dict) else {},
                validation_feedback=validation_feedback,
                session=session,
            )
            if op_req:
                store_operator_input(session, op_req)
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: validator blocker requires operator input.",
                    subagent="planner",
                )
                return {
                    "pending_clarification": {
                        "message_type": "operator_input_required",
                        "payload": op_req.model_dump(),
                    },
                    "pending_operator_input": op_req.model_dump(),
                    "planner_next": "orchestrator",
                    "should_return": False,
                }

    # Approved execution handoff from orchestrator — pass existing plan through to executor
    existing_plan = state.get("migration_plan") or session.get("migration_plan")
    if (
        state.get("start_execution")
        and session.get("plan_approved")
        and existing_plan
        and not session.get("pev_max_retries_exhausted")
        and not _is_pev_max_retries_exhausted(state, session)
    ):
        if isinstance(existing_plan, str):
            try:
                existing_plan = json.loads(existing_plan)
            except Exception:
                existing_plan = None
        if isinstance(existing_plan, dict) and existing_plan.get("repos"):
            _append_and_stream(
                session,
                role="system",
                content="Planner: approved plan ready — handing off to executor.",
                subagent="planner",
            )
            return {
                "migration_plan": existing_plan,
                "migration_queue": state.get("migration_queue") or session.get("migration_queue"),
                "start_execution": True,
                "planner_next": "executor",
                "should_return": False,
            }

    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    capabilities = state.get("capabilities")
    accel_get = state.get("accel_get")
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")
    validation_feedback = state.get("validation_feedback")
    if validation_feedback and isinstance(validation_feedback, str):
        try:
            validation_feedback = json.loads(validation_feedback)
        except Exception:
            validation_feedback = None
    iteration = state.get("iteration", 0)

    _append_and_stream(
        session,
        role="system",
        content="Planner: generating migration plan…",
        subagent="planner",
    )

    # Increment iteration for PEV cycle tracking
    iteration += 1

    # If we have validation feedback, this is a revised plan
    revision = 0
    existing_plan = state.get("migration_plan") or session.get("migration_plan")
    if existing_plan and isinstance(existing_plan, str):
        try:
            existing_plan = json.loads(existing_plan)
        except Exception:
            existing_plan = None
    if existing_plan and isinstance(existing_plan, dict):
        revision = existing_plan.get("revision", 0)
    if validation_feedback:
        revision += 1
        _append_and_stream(
            session,
            role="system",
            content=f"Planner: revising plan (revision {revision}) based on validator feedback…",
            subagent="planner",
        )

    # Load discovery data if not already in session
    discovery = session.get("discovery_snapshot")
    if discovery and isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = None
    if not discovery:
        if accel_get:
            try:
                profile_id = session.get("profile_id", "lightweight")
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: loading discovery data…",
                    subagent="planner",
                )
                discovery = await accel_get(
                    f"/v1/settings/profiles/{profile_id}/discovery",
                    session_token=session_token,
                )
                if isinstance(discovery, dict):
                    session["discovery_snapshot"] = discovery
                    from datetime import datetime, timezone
                    session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
                    from ado2gh.agents.migration_agent.utils import hydrate_session_target_org

                    hydrate_session_target_org(session, discovery)
            except Exception as e:
                _append_and_stream(
                    session,
                    role="system",
                    content=f"Planner: failed to load discovery data: {e}",
                    subagent="planner",
                )
                discovery = {}
        else:
            discovery = {}

    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []

    # Fallback: if discovery failed but we have requested repo(s), create
    # synthetic repo entries so the plan isn't empty.
    # Support both single-repo (plan_repository_id) and bulk (plan_repository_ids) modes.
    requested_repos = session.get("plan_repository_ids") or state.get("plan_repository_ids")
    if not requested_repos:
        single_repo = session.get("plan_repository_id") or state.get("plan_repository_id")
        if single_repo:
            requested_repos = [single_repo]

    if not repos and requested_repos and not session.get("discovery_fetched_at"):
        synth_repos = []
        for repo_id in requested_repos:
            parts = repo_id.split("/")
            repo_name = parts[-1] if len(parts) > 1 else repo_id
            project = parts[0] if len(parts) > 1 else ""
            synth_repos.append({
                "id": repo_id,
                "name": repo_name,
                "project": project,
                "repo_name": repo_name,
            })
        repos = synth_repos
        _append_and_stream(
            session,
            role="system",
            content=(
                f"Planner: discovery unavailable — using {len(requested_repos)} requested repo(s) directly: "
                f"{', '.join(requested_repos)}."
            ),
            subagent="planner",
        )
    elif repos and requested_repos:
        # Filter repos to only the requested ones; reject unknown repos when discovery loaded
        filtered = []
        not_found: list[str] = []
        for req_id in requested_repos:
            match = find_discovery_repo(req_id, repos)
            if match:
                filtered.append(normalize_discovery_repo(match))
            else:
                not_found.append(req_id)

        if not_found:
            clarification = repo_not_found_clarification(not_found[0], discovery if isinstance(discovery, dict) else None)
            _append_and_stream(
                session,
                role="system",
                content=clarification["payload"]["message"],
                subagent="planner",
            )
            return {
                "pending_clarification": clarification,
                "iteration": iteration,
                "should_return": False,
            }
        repos = filtered

    if not repos:
        clarification = {
            "to_role": "orchestrator",
            "type": "clarification_request",
            "payload": {
                "message": (
                    "Planner needs discovery data or a specific repository to build a migration plan. "
                    "Run discovery on the profile or specify which repo to migrate."
                ),
                "error": "no_repos",
            },
        }
        _append_and_stream(
            session,
            role="system",
            content=clarification["payload"]["message"],
            subagent="planner",
        )
        return {
            "pending_clarification": clarification,
            "iteration": iteration,
            "should_return": False,
        }

    repo_keys = [
        canonical_repo_id(r) if isinstance(r, dict) else str(r)
        for r in repos
        if (canonical_repo_id(r) if isinstance(r, dict) else str(r))
    ]
    from ado2gh.agents.migration_agent.nodes.executor.scope import ensure_repo_feature_detection
    await ensure_repo_feature_detection(session, repo_keys, accel_get, session_token)

    baseline_findings = await _gather_planner_baseline_context(
        session, [r for r in repos if isinstance(r, dict)], accel_get, session_token,
    )

    from ado2gh.agents.migration_agent.hitl.operator_input import (
        assess_operator_input_needed,
        blockers_from_baseline_probes,
        operator_input_from_probe_failures,
        store_operator_input,
    )

    probe_blockers = blockers_from_baseline_probes(baseline_findings)
    if probe_blockers and not validation_feedback:
        plan = _build_heuristic_plan(repos, session, revision)
        from ado2gh.agents.migration_agent.nodes.executor.plan import finalize_agent_migration_plan
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        migration_queue = _build_migration_queue_from_plan(plan)
        op_req = operator_input_from_probe_failures(probe_blockers, session)
        _append_and_stream(
            session,
            role="system",
            content=(
                "Planner: repository verification failed — requesting operator decision "
                "before building an execution plan."
            ),
            subagent="planner",
        )
        return _planner_operator_input_return(
            session, plan, migration_queue, iteration, op_req,
        )

    parsed: dict[str, Any] = {}
    # Build plan using LLM if available
    if llm and not llm_unconfigured:
        from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools

        planner_tools = get_planner_tools(
            accel_get=accel_get,
            accel_post=accel_post,
            session_token=session_token,
            session_getter=lambda: session,
        )
        system_prompt = get_prompt("planner")

        # Build context with discovery data and validation feedback
        dry_run = bool(session.get("dry_run", True))
        context_parts = [
            f"Target repository keys: {json.dumps(repo_keys, default=str)}",
            f"Discovery repos (sample): {json.dumps(repos[:20], default=str)}",
            f"Baseline research (auto): {json.dumps(baseline_findings, default=str)[:6000]}",
            f"Dry run: {dry_run}",
        ]
        if dry_run:
            context_parts.append(
                "DRY RUN: Plan for simulated execution. The Executor will not publish to GitHub; "
                "the Validator will judge executor logs and simulated workflow output, not live targets."
            )
        else:
            context_parts.append(
                "LIVE RUN: Plan for real execution. The Executor performs writes; "
                "the Validator verifies outcomes via APIs only — not executor status logs."
            )
        context_parts.append(
            "Surface blockers and assumptions before execution."
        )
        if validation_feedback:
            context_parts.append(f"Validator feedback: {json.dumps(validation_feedback, default=str)}")
        if existing_plan:
            context_parts.append(f"Existing plan revision {revision - 1}: {json.dumps(existing_plan, default=str)[:2000]}")

        # T097: Apply context window trimming
        existing_messages = state.get("messages", [])
        cycle_summaries = state.get("cycle_summaries", [])
        max_budget = state.get("max_token_budget", 32000)

        new_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n".join(context_parts)),
        ]

        all_messages = existing_messages + new_messages
        trimmed_messages = build_context_with_cycle_summaries(
            all_messages, cycle_summaries, max_budget
        )

        llm_with_tools = llm
        if capabilities and capabilities.supports_tool_calling and planner_tools:
            try:
                llm_with_tools = llm.bind_tools(planner_tools)
            except Exception:
                pass

        parsed = await _run_planner_research_loop(
            state,
            session,
            llm_with_tools,
            trimmed_messages,
            capabilities=capabilities,
            accel_get=accel_get,
            session_token=session_token,
            validation_feedback=validation_feedback if isinstance(validation_feedback, dict) else None,
            dry_run=dry_run,
        )

        if parsed.get("repos"):
            plan = _build_migration_plan_from_llm(parsed, session, revision)
            if parsed.get("risk_summary"):
                assumptions = list(plan.get("assumptions") or [])
                assumptions.append(f"Planner risk summary: {parsed['risk_summary']}")
                plan["assumptions"] = assumptions
        else:
            _append_and_stream(
                session,
                role="system",
                content=(
                    "Planner: research loop did not return a structured plan — "
                    "building deterministic plan from discovery and baseline probes."
                ),
                subagent="planner",
            )
            plan = _build_heuristic_plan(repos, session, revision)
            if baseline_findings:
                plan.setdefault("assumptions", []).append(
                    f"Baseline probes: {json.dumps(baseline_findings, default=str)[:1500]}"
                )
    else:
        # No LLM — use heuristic plan builder
        plan = _build_heuristic_plan(repos, session, revision)
        if baseline_findings:
            plan.setdefault("assumptions", []).append(
                f"Baseline probes: {json.dumps(baseline_findings, default=str)[:1500]}"
            )

    # Store plan in state and session
    session["migration_plan"] = plan
    from ado2gh.agents.migration_agent.nodes.executor.plan import finalize_agent_migration_plan
    plan = finalize_agent_migration_plan(plan, session)
    session["migration_plan"] = plan

    migration_queue = _build_migration_queue_from_plan(plan)
    queue_items = migration_queue.get("items", [])

    planner_op_request = parsed.get("operator_input_request") if (llm and parsed) else None
    if not planner_op_request and parsed and _planner_text_indicates_blocker(parsed):
        blocker_list = blockers_from_baseline_probes(baseline_findings)
        if not blocker_list:
            repo = repo_keys[0] if repo_keys else session.get("plan_repository_id", "")
            blocker_list = [{
                "repo": repo,
                "scope": "repo",
                "blocker": str(parsed.get("thinking") or "Planner could not verify repositories."),
                "key": f"repo:{repo}:planner_blocker",
            }]
        planner_op_request = operator_input_from_probe_failures(blocker_list, session).model_dump()

    vf_dict = validation_feedback if isinstance(validation_feedback, dict) else None
    op_req = assess_operator_input_needed(
        plan=plan,
        validation_feedback=vf_dict,
        session=session,
        planner_request=planner_op_request,
    )
    if op_req and not session.get("plan_approved"):
        return _planner_operator_input_return(
            session, plan, migration_queue, iteration, op_req,
        )

    _append_and_stream(
        session,
        role="system",
        content=f"Planner: plan generated — {len(plan.get('repos', []))} repo(s), revision {revision}. Queue: {len(queue_items)} item(s).",
        subagent="planner",
    )

    return {
        "migration_plan": plan,
        "migration_queue": migration_queue,
        "iteration": iteration,
        "pending_clarification": None,
        "should_return": False,
        "start_pev": False,
        "planner_next": "orchestrator",
    }


