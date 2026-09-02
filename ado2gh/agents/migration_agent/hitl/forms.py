"""Dynamic form builders and guardrails for the migration agent.

Forms are constructed by the orchestrator LLM or enriched from session context.
All forms pass through ``sanitize_form`` which enforces limits while preserving
LLM-provided recommendations (options, placeholders, recommended_value).
"""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.hitl.form_fields import normalize_form_option

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
            "required": bool(f.get("required", False)),
        }
        for key in ("description", "placeholder", "recommended_value"):
            val = f.get(key)
            if val is not None and str(val).strip():
                limit = 200 if key == "description" else 120
                field[key] = str(val).strip()[:limit]
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
    mode = "dry-run" if session.get("dry_run", True) else "live"
    phase_clause = f" for phase {phase}" if phase else ""
    if not session.get("dry_run", True) and session_requires_live_approval(session):
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
            if not session.get("dry_run", True)
            else "Say **execute dry-run** to run the pipeline, or switch to **live** mode to migrate for real."
        )
    )


def session_requires_live_approval(session: dict[str, Any]) -> bool:
    """Check if session requires live approval."""
    if session.get("dry_run", True):
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
