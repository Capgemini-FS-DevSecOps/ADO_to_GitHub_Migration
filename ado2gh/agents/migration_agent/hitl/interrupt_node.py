"""LangGraph HITL node using interrupt() + Command(resume=...) per 2026 LangGraph docs.

Side effects must run only after interrupt() returns — the node re-executes from the top on resume.
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt


async def human_input_node(state: dict[str, Any]) -> dict[str, Any]:
    """Pause for operator form input; resume value becomes form submission dict."""
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

    if isinstance(submission, dict):
        submission.setdefault("form_id", pending_form.get("form_id", ""))
        if "values" not in submission and any(k != "form_id" for k in submission):
            submission = {"form_id": submission.get("form_id", ""), "values": submission}
    else:
        submission = {
            "form_id": pending_form.get("form_id", ""),
            "values": submission,
        }

    session.pop("pending_form", None)
    return {
        "form_submission": submission,
        "pending_form": None,
        "should_return": False,
    }
