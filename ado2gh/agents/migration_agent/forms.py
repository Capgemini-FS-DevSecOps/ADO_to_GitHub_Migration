"""Dynamic form builders and guardrails for the migration agent.

Forms can be constructed by the LLM or by heuristic builders. All forms pass
through ``sanitize_form`` which enforces length limits and structural validity.
"""
from __future__ import annotations

from typing import Any

MAX_TITLE_LEN = 80
MAX_DESCRIPTION_LEN = 500
MAX_FIELD_LABEL_LEN = 60
MAX_FIELDS = 8
MAX_OPTIONS = 10

OPERATOR_RESOLUTION_LABELS: dict[str, str] = {
    "fix_repository_id": "Correct repository ID",
    "confirm_github_repo_create": "Confirm GitHub repo should be created",
    "skip_blocked_scope": "Skip blocked scope and continue",
    "replan": "Replan with updated context",
    "run_pipeline_inventory": "Run pipeline inventory first",
    "stop": "Stop migration",
    "rollback": "Rollback and stop",
    "continue": "Continue anyway",
}


def _normalize_option(opt: Any) -> dict[str, str]:
    if isinstance(opt, dict):
        value = str(opt.get("value") or opt.get("label") or "").strip()
        label = str(opt.get("label") or value).strip()
        return {"value": value[:60], "label": label[:60]}
    text = str(opt).strip()
    return {
        "value": text[:60],
        "label": OPERATOR_RESOLUTION_LABELS.get(text, text.replace("_", " ").title())[:60],
    }


def sanitize_form(form: dict[str, Any]) -> dict[str, Any]:
    """Enforce guardrails on a dynamically-constructed form.

    Truncates titles, descriptions, labels, and option lists to keep forms
    compact and readable in the UI. Ensures required structural keys exist.
    """
    form_id = str(form.get("form_id", "custom"))[:60]
    title = str(form.get("title", "Input required"))[:MAX_TITLE_LEN]
    description = str(form.get("description", ""))[:MAX_DESCRIPTION_LEN]

    raw_fields = form.get("fields", [])[:MAX_FIELDS]
    fields = []
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", "field"))[:40]
        field_type = f.get("type") or f.get("field_type") or "text"
        field = {
            "name": name,
            "label": str(f.get("label", name))[:MAX_FIELD_LABEL_LEN],
            "type": field_type,
            "required": bool(f.get("required", False)),
        }
        opts = f.get("options")
        if opts and isinstance(opts, list):
            field["options"] = [_normalize_option(o) for o in opts[:MAX_OPTIONS]]
        fields.append(field)

    if not fields:
        fields = [{"name": "input", "label": "Input", "type": "text", "required": True}]

    return {
        "form_id": form_id,
        "title": title,
        "description": description,
        "fields": fields,
    }


def plan_confirmation_form(session: dict[str, Any]) -> dict[str, Any]:
    """Build the plan confirmation form shown after a migration plan is generated."""
    from ado2gh.agents.migration_agent.pipeline_plan import resolve_migration_phase

    plan = session.get("migration_plan") or {}
    dry_run = session.get("dry_run", True)
    repos = plan.get("repos", [])
    repo_count = plan.get("repo_count", len(repos))
    phase = resolve_migration_phase(session, plan)
    destructive = [r for r in repos if r.get("destructive")]
    repo_id = session.get("plan_repository_id") or plan.get("repository_id")
    if repo_id:
        title = f"Confirm migration plan — {repo_id}"
    elif phase:
        title = f"Confirm migration plan — phase {phase}"
    else:
        title = "Confirm migration plan"
    return {
        "form_id": "plan_confirmation",
        "title": title,
        "description": (
            f"{repo_count} repo(s) queued for "
            f"{'**dry-run**' if dry_run else '**live**'} migration."
            + (f" {len(destructive)} repo(s) have destructive operations." if destructive else "")
        ),
        "fields": [
            {
                "name": "plan_confirmed",
                "label": "Confirm the plan is correct",
                "type": "checkbox",
                "required": True,
            },
            {
                "name": "confirm_execute",
                "label": "Start migration immediately after confirmation",
                "type": "checkbox",
                "required": False,
            },
            {
                "name": "plan_notes",
                "label": "Notes (describe changes or skip)",
                "type": "textarea",
                "required": False,
            },
        ],
    }


def execution_mode_form(repository_id: str | None = None) -> dict[str, Any]:
    """Build a form asking the operator to choose dry-run or live execution."""
    repo_hint = f" for {repository_id}" if repository_id else ""
    return {
        "form_id": "execution_mode",
        "title": "Dry-run or live?",
        "description": f"Choose how to run the migration{repo_hint}.",
        "fields": [
            {
                "name": "mode",
                "label": "Mode",
                "type": "select",
                "options": ["dry-run", "live"],
                "required": True,
            },
        ],
    }


def repo_selection_form(
    available_repos: list[str] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build a form asking the operator to specify a repository."""
    description = "Which repository would you like to migrate? Enter as Project/RepoName."
    if error_message:
        description += f"\n\n**Error**: {error_message}"
    return {
        "form_id": "repo_selection",
        "title": "Repository required",
        "description": description,
        "fields": [
            {
                "name": "repository_id",
                "label": "Repository",
                "type": "text",
                "required": True,
            },
        ],
    }


def phase_selection_form(available_phases: list[str] | None = None) -> dict[str, Any]:
    """Build a form asking the operator to select a migration phase."""
    return {
        "form_id": "phase_selection",
        "title": "Phase selection required",
        "description": "Which migration phase would you like to run?",
        "fields": [
            {
                "name": "phase",
                "label": "Phase",
                "type": "select",
                "required": True,
                "options": available_phases or ["poc", "pilot", "wave1", "wave2", "wave3"],
            },
        ],
    }


def inventory_gaps_form(missing_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a form showing inventory gaps that need operator attention."""
    return {
        "form_id": "inventory_gaps",
        "title": "Inventory gaps detected",
        "description": (
            "The following items are missing from discovery. "
            "Please provide values or skip."
        ),
        "fields": [
            {
                "name": item["name"],
                "label": item.get("label", item["name"]),
                "type": "text",
                "required": item.get("required", False),
            }
            for item in missing_items
        ],
    }


def cancellation_form() -> dict[str, Any]:
    """Build a form presenting rollback/stop options on cancellation."""
    return {
        "form_id": "cancellation_options",
        "title": "Migration cancellation",
        "description": (
            "Do you want to stop the migration or also rollback "
            "(delete GitHub resources created in this session)?"
        ),
        "fields": [
            {
                "name": "action",
                "label": "Action",
                "type": "select",
                "required": True,
                "options": ["stop", "rollback"],
            },
        ],
    }


# ─── Plan confirmation reply helpers (T073) ───────────────────────────────

def _plan_confirmation_reply(session: dict[str, Any]) -> str:
    """Generate the assistant reply when presenting the plan confirmation form."""
    from ado2gh.agents.migration_agent.pipeline_plan import plan_confirmation_summary

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
    from ado2gh.agents.migration_agent.pipeline_plan import plan_confirmation_summary, resolve_migration_phase

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
