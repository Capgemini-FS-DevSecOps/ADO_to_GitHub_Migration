"""Dynamic form builders and guardrails for the migration agent.

Forms are constructed by the orchestrator LLM or enriched from session context.
All forms pass through ``sanitize_form`` which enforces limits while preserving
LLM-provided recommendations (options, placeholders, recommended_value).
"""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.hitl.form_fields import (
    default_required_for_type,
    normalize_form_option,
    normalize_recommended_value,
)
from ado2gh.agents.migration_agent.utils import coerce_dry_run

MAX_TITLE_LEN = 80
MAX_DESCRIPTION_LEN = 500
MAX_FIELD_LABEL_LEN = 60
MAX_FIELDS = 8
MAX_OPTIONS = 10


def sanitize_form(form: dict[str, Any]) -> dict[str, Any]:
    """Enforce guardrails on a dynamically-constructed form."""
    form_id = str(form.get("form_id", "custom"))[:60]
    title = str(form.get("title", "Input required"))[:MAX_TITLE_LEN]
    description = str(form.get("description", ""))[:MAX_DESCRIPTION_LEN]

    raw_fields = form.get("fields", [])[:MAX_FIELDS]
    fields: list[dict[str, Any]] = []
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", "field"))[:40]
        field_type = f.get("type") or f.get("field_type") or "text"
        field: dict[str, Any] = {
            "name": name,
            "label": str(f.get("label", name))[:MAX_FIELD_LABEL_LEN],
            "type": field_type,
            "required": (
                bool(f["required"])
                if f.get("required") is not None
                else default_required_for_type(field_type)
            ),
        }
        for key in ("description", "placeholder"):
            val = f.get(key)
            if val is not None and str(val).strip():
                limit = 200 if key == "description" else 120
                field[key] = str(val).strip()[:limit]
        recommendation = normalize_recommended_value(f.get("recommended_value"))
        if recommendation is not None:
            field["recommended_value"] = recommendation
        opts = f.get("options")
        if opts and isinstance(opts, list):
            field["options"] = [normalize_form_option(o) for o in opts[:MAX_OPTIONS]]
        fields.append(field)

    if not fields:
        fields = [{
            "name": "input",
            "label": "Input",
            "type": "text",
            "required": True,
            "placeholder": "Enter a value",
        }]

    return {
        "form_id": form_id,
        "title": title,
        "description": description,
        "fields": fields,
    }


def _value_is_blank(value: object) -> bool:
    """Report whether a submitted value left the question unanswered.

    ``False`` is an answer (an unticked checkbox), so only None, blank text and
    empty collections count as blank.

    Returns:
        True when the value carries nothing the agent can act on.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, tuple, set, dict)) and not value


def missing_required_form_fields(
    form: dict[str, Any],
    values: dict[str, Any] | None,
) -> list[str]:
    """List the required fields of ``form`` that the submission left blank.

    ``required`` was advertised to the console but never checked server-side, so a
    submission that skipped a required field was accepted and the agent planned on
    the gap (THR-09-003). Canonical names recovered by
    :class:`FormIntakeSubmission` count as answered, so the console's aliases
    (``repository``, ``mode``) do not read as missing.

    Returns:
        The names of unanswered required fields, in form order.
    """
    from ado2gh.agents.migration_agent.hitl.schemas import FormIntakeSubmission

    submitted = values or {}
    answered = {k for k, v in submitted.items() if not _value_is_blank(v)}
    try:
        canonical = FormIntakeSubmission.from_raw_values(submitted).model_dump(exclude_none=True)
    except Exception:
        canonical = {}
    answered |= {k for k, v in canonical.items() if not _value_is_blank(v)}
    return [
        str(f.get("name") or "")
        for f in form.get("fields") or []
        if isinstance(f, dict) and f.get("required") and str(f.get("name") or "") not in answered
    ]


def plan_confirmation_form(session: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias — schema-driven plan review form."""
    from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form

    return build_plan_review_form(session)


def _plan_confirmation_reply(session: dict[str, Any]) -> str:
    """Generate the assistant reply when presenting the plan confirmation form."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import plan_confirmation_summary

    plan = session.get("migration_plan") or {}
    if plan.get("blocked"):
        return plan.get("block_reason", "Migration plan is blocked.")
    summary = plan.get("confirmation_summary") or plan_confirmation_summary(plan, session)
    prefix = (
        "I've prepared a migration plan. **Please review** the targets and scope, "
        "then confirm or describe changes below.\n\n"
    )
    return prefix + summary


def migration_ready_reply(session: dict[str, Any]) -> str:
    """Generate the assistant reply when the migration plan is ready."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import plan_confirmation_summary, resolve_migration_phase

    plan = session.get("migration_plan") or {}
    if plan.get("blocked"):
        return plan.get("block_reason", "Migration plan is blocked.")
    summary = plan.get("confirmation_summary") or plan_confirmation_summary(plan, session)
    count = plan.get("repo_count", 0)
    phase = resolve_migration_phase(session, plan)
    dry_run = coerce_dry_run(session.get("dry_run"), default=True)
    mode = "dry-run" if dry_run else "live"
    phase_clause = f" for phase {phase}" if phase else ""
    if not dry_run and session_requires_live_approval(session):
        role = session.get("user_role", "operator")
        return (
            f"Migration plan ready{phase_clause} ({count} repo(s), live). "
            f"As **{role}**, you need platform approval before live execution can start. "
            "Request approval or switch to a dry-run."
        )
    if not session.get("plan_approved"):
        return (
            f"Migration plan ready{phase_clause} ({count} repo(s), {mode}). "
            "**Confirm the plan** in the form below (or describe changes) before execution starts.\n\n"
            + summary
        )
    return (
        f"Plan confirmed{phase_clause} ({count} repo(s), {mode}). "
        + (
            "Say **execute** or **migrate live** to run the pipeline."
            if not dry_run
            else "Say **execute dry-run** to run the pipeline, or switch to **live** mode to migrate for real."
        )
    )


def session_requires_live_approval(session: dict[str, Any]) -> bool:
    """Check if session requires live approval."""
    # A malformed flag ("false", 0) must not read as dry-run and waive the
    # approval this function exists to demand (GAP-076, CA-001).
    if coerce_dry_run(session.get("dry_run"), default=True):
        return False
    from ado2gh.auth.service import auth_enabled
    if not auth_enabled():
        return False
    if session.get("live_approval_status") == "approved":
        return False
    from ado2gh.agents.migration_agent.policies import can_execute_live_without_approval
    if can_execute_live_without_approval(session):
        return False
    from ado2gh.auth.models import PlatformRole
    role = session.get("user_role")
    return role not in (PlatformRole.ADMIN.value, PlatformRole.APPROVER.value)
