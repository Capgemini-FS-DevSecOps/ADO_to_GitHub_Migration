"""Migration plan routes: build a plan and summarise it for the operator."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ado2gh.agents.migration_agent.route_helpers import (
    _PLANNER_SYSTEM,
    PlanPhaseBody,
    _add_message,
    _build_migration_plan,
    _get_accessible_session,
    _require_operate,
    _session_accel_token,
    _session_payload,
    _session_token_from_request,
)
from ado2gh.agents.migration_agent.runtime.llm_bridge import resolve_langchain_llm
from ado2gh.agents.migration_agent.session.state import (
    is_session_busy,
)

router = APIRouter()


@router.post("/v1/sessions/{session_id}/plan")
async def create_migration_plan(
    session_id: str,
    request: Request,
    body: PlanPhaseBody | None = None,
) -> dict[str, Any]:
    """Planner subagent: LLM + discovery → structured migration_plan on the session."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if is_session_busy(session.get("status")):
        raise HTTPException(status_code=409, detail="session_busy")
    phase = (body.phase if body and body.phase else None) or session.get("plan_phase")
    if phase:
        session["plan_phase"] = phase
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    plan = await _build_migration_plan(session, session_token, phase=phase)
    if not plan.get("blocked"):
        try:
            llm = resolve_langchain_llm(session.get("selected_model_id"))
            from langchain_core.messages import HumanMessage, SystemMessage
            msgs = [SystemMessage(content=_PLANNER_SYSTEM), HumanMessage(content=f"Summarize this migration plan for the operator:\n{json.dumps(plan, indent=2)}")]
            plan["narrative"] = llm.invoke(msgs).content
        except Exception:
            repo_n = len(plan.get("work_items", []))
            if phase:
                plan["narrative"] = f"Migration plan for phase {phase} with {repo_n} repos."
            else:
                plan["narrative"] = f"Migration plan with {repo_n} repos."
    else:
        plan["narrative"] = plan.get("block_reason", "Plan blocked")
    session["migration_plan"] = plan
    session["plan_approved"] = False
    session["status"] = "idle"
    session["subagent"] = "planner"
    _add_message(session_id, "planner", plan["narrative"], kind="progress")

    if not plan.get("blocked"):
        from ado2gh.agents.migration_agent.hitl.forms import (
            _plan_confirmation_reply,
            plan_confirmation_form,
        )

        session["pending_form"] = plan_confirmation_form(session)
        _add_message(session_id, "assistant", _plan_confirmation_reply(session), kind="message")
    session["subagent"] = None
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _session_payload(session_id)


@router.get("/v1/sessions/{session_id}/plan-summary")
def get_plan_summary(session_id: str, request: Request) -> dict[str, Any]:
    """Summarise the session migration plan so the operator can confirm it.

    Lists the repositories in order, the scopes selected per repository, the
    planner assumptions, and any destructive operation that needs its own
    confirmation. ``requires_confirmation`` is true whenever the plan is not a
    dry run, because live execution always needs an explicit go-ahead (CA-001).
    """
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="No migration plan found for this session")

    work_items = plan.get("work_items", [])
    # T076: Highlight destructive operations requiring individual confirmation (FR-054, CA-002)
    DESTRUCTIVE_SCOPES = {"repo_delete", "workflow_delete", "secret_delete", "pipeline_disable"}
    destructive_operations = []
    for wi in work_items:
        for scope in wi.get("scopes", []):
            if scope in DESTRUCTIVE_SCOPES:
                destructive_operations.append({
                    "repo": wi.get("repo", ""),
                    "scope": scope,
                    "requires_individual_confirmation": True,
                })
    summary = {
        "session_id": session_id,
        "dry_run": plan.get("dry_run", True),
        "repos": plan.get("repo_order", []),
        "work_items": [
            {
                "repo": wi.get("repo", ""),
                "scopes": wi.get("scopes", []),
                "status": wi.get("status", "ready"),
                "blocked_reasons": wi.get("blocked_reasons", []),
            }
            for wi in work_items
        ],
        "assumptions": plan.get("assumptions", []),
        "revision": plan.get("revision", 1),
        "requires_confirmation": not plan.get("dry_run", True),
        "destructive_operations": destructive_operations,
    }
    return summary
