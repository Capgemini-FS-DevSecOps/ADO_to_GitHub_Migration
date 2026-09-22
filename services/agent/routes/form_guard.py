"""Guard against a form submission answering a form the agent has since replaced."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ado2gh.audit.events import AuditEvent
from services.agent.routes._helpers import FormSubmitRequest, _audit

#: Audit event name for a form submission refused because it answers a form
#: (or a plan revision) the agent has since replaced (register item GAP-136).
STALE_FORM_SUBMISSION_EVENT = AuditEvent.AGENT_FORM_STALE_SUBMISSION_REFUSED.value


def reject_if_stale_form(
    session_id: str, session: dict[str, Any], form: dict[str, Any], req: FormSubmitRequest,
) -> None:
    """Raise 409 ``stale_form`` for a form instance or plan revision that is stale (GAP-136).

    A stale browser tab can still hold an old form after the agent moved on, or after
    the plan it was reviewing got replaced under it. Checked before the pending form
    is cleared, so the operator can resubmit against whatever is actually pending.
    Omitting ``form_instance_id`` (an older console) is never rejected on that basis
    alone.
    """
    from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key

    expected_id = str(form.get("instance_id") or "")
    submitted_id = req.form_instance_id
    plan = session.get("migration_plan")
    current_revision = plan_revision_key(plan) if isinstance(plan, dict) else ""
    mismatched = bool(submitted_id and submitted_id != expected_id)
    stale_plan = bool(form.get("plan_revision") and form["plan_revision"] != current_revision)
    if not mismatched and not stale_plan:
        return

    _audit.record(
        STALE_FORM_SUBMISSION_EVENT,
        session_id=session_id,
        metadata={
            "session_id": session_id,
            "form_id": str(form.get("form_id") or ""),
            "expected_instance_id": expected_id,
            "submitted_instance_id": submitted_id or "",
        },
    )
    raise HTTPException(status_code=409, detail="stale_form")
