"""LangGraph node for a human-in-the-loop operator prompt (HITL), using interrupt() + Command(resume=...) per 2026 LangGraph docs.

Side effects must run only after interrupt() returns — the node re-executes from the top on resume.
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt


async def human_input_node(state: dict[str, Any]) -> dict[str, Any]:
    """Pause for operator form input; resume value becomes form submission dict.

    Returns:
        The graph update carrying ``form_submission``, or an empty update when no
        form is pending.

    Raises:
        ValueError: The resume payload names a different form than the pending one.
    """
    pending_form = state.get("pending_form")
    session = state.get("session") or {}
    if not pending_form:
        pending_form = session.get("pending_form")
    if not pending_form:
        return {}

    submission = interrupt(
        {
            "kind": "form_request",
            "form": pending_form,
            "title": pending_form.get("title", ""),
            "form_id": pending_form.get("form_id", ""),
        }
    )

    expected_form_id = str(pending_form.get("form_id", "") or "")
    if isinstance(submission, dict):
        resumed_form_id = str(submission.get("form_id") or "")
        if resumed_form_id and resumed_form_id != expected_form_id:
            # setdefault used to relabel a submission meant for another form as an
            # answer to this one; a stated mismatch is rejected instead (THR-09-004).
            raise ValueError(
                f"form_submission_mismatch: resume payload targets form "
                f"'{resumed_form_id}' but '{expected_form_id}' is pending",
            )
        if "values" not in submission and any(k != "form_id" for k in submission):
            submission = {
                "form_id": expected_form_id,
                "values": {k: v for k, v in submission.items() if k != "form_id"},
            }
        else:
            submission.setdefault("form_id", expected_form_id)
    else:
        submission = {
            "form_id": expected_form_id,
            "values": submission,
        }

    session.pop("pending_form", None)
    return {
        "form_submission": submission,
        "pending_form": None,
        "should_return": False,
    }
