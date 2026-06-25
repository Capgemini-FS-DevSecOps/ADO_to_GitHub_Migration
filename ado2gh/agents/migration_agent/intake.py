"""Intake merge, routing, and Pydantic form handling for the migration agent."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.forms import sanitize_form
from ado2gh.agents.migration_agent.intake_schema import (
    INTAKE_FIELD_REGISTRY,
    FormIntakeSubmission,
    IntakeFieldSpec,
    IntakePhase,
    MigrationIntakeSchema,
    OperatorMessageAnalysis,
    required_fields_for_phase,
)
from ado2gh.agents.migration_agent.utils import canonical_repo_id, find_discovery_repo


def intake_from_session(session: dict[str, Any]) -> MigrationIntakeSchema:
    """Hydrate intake schema from persisted session fields."""
    repo_ids = session.get("plan_repository_ids")
    return MigrationIntakeSchema(
        repository_id=session.get("plan_repository_id") or None,
        repository_ids=list(repo_ids) if repo_ids else None,
        dry_run=session.get("dry_run") if session.get("execution_mode_confirmed") else None,
        phase=session.get("plan_phase") or None,
        plan_confirmed=True if session.get("plan_approved") else None,
    )


def sync_intake_to_session(session: dict[str, Any], intake: MigrationIntakeSchema) -> None:
    """Write intake schema back to session keys used by the graph."""
    if intake.repository_id:
        session["plan_repository_id"] = intake.repository_id
    if intake.repository_ids:
        session["plan_repository_ids"] = intake.repository_ids
    if intake.dry_run is not None:
        session["dry_run"] = intake.dry_run
        session["execution_mode_confirmed"] = True
    if intake.phase:
        session["plan_phase"] = intake.phase
    if intake.plan_confirmed:
        session["plan_approved"] = True
    if intake.plan_notes:
        plan = session.get("migration_plan") or {}
        if isinstance(plan, dict):
            plan["operator_notes"] = intake.plan_notes
            session["migration_plan"] = plan


def clear_repository_intake(intake: MigrationIntakeSchema) -> MigrationIntakeSchema:
    """Drop repository selection so intake routing can prompt the operator."""
    return intake.model_copy(update={"repository_id": None, "repository_ids": None})


def merge_intake(
    base: MigrationIntakeSchema,
    *updates: dict[str, Any],
) -> MigrationIntakeSchema:
    """Merge extracted fragments into the intake model (later wins)."""
    data = base.model_dump()
    for patch in updates:
        for key, value in (patch or {}).items():
            if value is None:
                continue
            if key in MigrationIntakeSchema.model_fields:
                data[key] = value
    return MigrationIntakeSchema.model_validate(data)


def apply_message_analysis(
    intake: MigrationIntakeSchema,
    analysis: OperatorMessageAnalysis,
) -> MigrationIntakeSchema:
    """Merge LLM analysis into intake."""
    return merge_intake(intake, analysis.intake_patch())


def analysis_from_state(state: dict[str, Any]) -> OperatorMessageAnalysis | None:
    """Load persisted LLM message analysis from graph state."""
    raw = state.get("operator_message_analysis")
    if not raw:
        return None
    return OperatorMessageAnalysis.model_validate(raw)


def normalize_repository_id(
    intake: MigrationIntakeSchema,
    discovery: dict[str, Any] | None,
) -> MigrationIntakeSchema:
    """Canonicalize repository_id using discovery when available."""
    import difflib

    from ado2gh.agents.migration_agent.utils import discovery_repo_names

    repo_id = intake.resolved_repository_id()
    if not repo_id:
        return intake
    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
    match = find_discovery_repo(repo_id, repos)
    if not match and repos:
        names = sorted(discovery_repo_names(repos))
        repo_part = repo_id.split("/")[-1]
        close = difflib.get_close_matches(repo_id, names, n=1, cutoff=0.45)
        if not close:
            close = difflib.get_close_matches(repo_part, names, n=1, cutoff=0.4)
        if close:
            match = find_discovery_repo(close[0], repos)
    if match:
        repo_id = canonical_repo_id(match)
    return intake.model_copy(update={"repository_id": repo_id, "repository_ids": None})


def intake_field_value(intake: MigrationIntakeSchema, field_name: str) -> Any:
    if field_name == "dry_run":
        if intake.dry_run is None:
            return None
        return "true" if intake.dry_run else "false"
    return getattr(intake, field_name, None)


def is_field_missing(intake: MigrationIntakeSchema, field_name: str) -> bool:
    value = intake_field_value(intake, field_name)
    if field_name == "plan_confirmed":
        return value is not True
    return value is None or value == "" or value == []


def missing_intake_fields(
    intake: MigrationIntakeSchema,
    phase: IntakePhase,
) -> list[IntakeFieldSpec]:
    """Return registry specs for required fields that are still blank."""
    missing: list[IntakeFieldSpec] = []
    for name in required_fields_for_phase(phase):
        if is_field_missing(intake, name):
            spec = INTAKE_FIELD_REGISTRY.get(name)
            if spec:
                missing.append(spec)
    return missing


def determine_intake_phase(session: dict[str, Any], *, intent: str = "") -> IntakePhase | None:
    if session.get("pending_cancellation"):
        return IntakePhase.CANCELLATION
    if session.get("migration_plan") and not session.get("plan_approved"):
        return IntakePhase.PLAN_REVIEW
    has_repo = bool(session.get("plan_repository_id") or session.get("plan_repository_ids"))
    if has_repo and not session.get("migration_plan"):
        return IntakePhase.PLANNING
    if intent == "migration_action" or session.get("start_pev"):
        return IntakePhase.PLANNING
    return None


def intake_ready_for_planner(intake: MigrationIntakeSchema) -> bool:
    return bool(
        intake.resolved_repository_id()
        and intake.dry_run is not None
    )


def build_dynamic_form(
    *,
    form_id: str,
    title: str,
    description: str,
    fields: list[IntakeFieldSpec],
    planner_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sanitized dynamic form from intake field specs."""
    desc = description
    if planner_context:
        suggestions = planner_context.get("repo_suggestions") or []
        if suggestions and "repository_id" in {f.name for f in fields}:
            hint = ", ".join(suggestions[:5])
            desc = f"{desc}\n\nSuggestions: {hint}".strip()
        sample = planner_context.get("sample_repos") or []
        if sample and len(sample) <= 8:
            desc = f"{desc}\n\nExamples: {', '.join(sample[:8])}".strip()

    form_fields = []
    for spec in fields:
        field: dict[str, Any] = {
            "name": spec.name,
            "label": spec.label,
            "type": spec.field_type,
            "required": spec.required,
        }
        if spec.field_type == "select" and spec.options:
            from ado2gh.agents.migration_agent.forms import _normalize_option

            field["options"] = [_normalize_option(o) for o in spec.options]
        form_fields.append(field)

    return sanitize_form({
        "form_id": form_id[:60] or "intake",
        "title": title[:80],
        "description": desc[:500],
        "fields": form_fields,
    })


async def consult_planner_context(
    session: dict[str, Any],
    accel_get: Any,
    session_token: str | None,
    *,
    intake: MigrationIntakeSchema | None = None,
    requests_new_migration: bool = False,
) -> dict[str, Any]:
    """Load discovery via planner path and return context for missing-field prompts."""
    from ado2gh.agents.migration_agent.utils import load_discovery_snapshot, discovery_repo_names
    import difflib

    await load_discovery_snapshot(session, accel_get, session_token=session_token)
    discovery = session.get("discovery_snapshot") or {}
    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
    names = sorted(discovery_repo_names(repos))
    ctx: dict[str, Any] = {
        "repo_count": len(repos),
        "sample_repos": names[:10],
        "discovery_loaded": bool(repos),
    }

    requested = (intake.resolved_repository_id() if intake else None) or ""
    last_completed = str(session.get("last_completed_repository_id") or "").strip()
    if requested and names:
        ctx["repo_suggestions"] = difflib.get_close_matches(requested, list(names), n=5, cutoff=0.4)
    elif requests_new_migration and names:
        remaining = [n for n in names if n != last_completed]
        if last_completed:
            short = last_completed.split("/")[-1]
            remaining = [n for n in remaining if not n.endswith(f"/{short}") and n != short]
        ctx["repo_suggestions"] = remaining[:8] or names[:8]
    elif names:
        ctx["repo_suggestions"] = sorted(names)[:8]

    missing: list[str] = []
    if intake:
        if is_field_missing(intake, "repository_id"):
            missing.append("repository_id")
        if is_field_missing(intake, "dry_run"):
            missing.append("dry_run")
    ctx["missing_parameters"] = missing
    return ctx


def _format_plan_review_submission(submission: FormIntakeSubmission) -> str:
    """Human-readable plan review summary — omit optional notes when confirming."""
    if submission.plan_confirmed:
        parts = ["Plan confirmed"]
        if submission.confirm_execute:
            parts.append("start migration")
        return ", ".join(parts)
    if submission.plan_notes:
        return f"Requested plan changes: {submission.plan_notes}"
    return "Plan review submitted"


def format_form_submission_summary(
    values: dict[str, Any],
    *,
    form_id: str | None = None,
) -> str:
    """Human-readable form summary from validated Pydantic submission."""
    submission = FormIntakeSubmission.from_raw_values(values)
    if form_id in ("intake_plan_review", "plan_confirmation"):
        return _format_plan_review_submission(submission)
    parts: list[str] = []
    for key, value in submission.model_dump(exclude_none=True).items():
        if key == "plan_notes" and not value:
            continue
        if isinstance(value, bool):
            parts.append(f"{key}: {str(value).lower()}")
        else:
            parts.append(f"{key}: {value}")
    return ", ".join(parts)


def build_form_submit_prompt(values: dict[str, Any]) -> str:
    summary = format_form_submission_summary(values)
    return f"Operator submitted form values: {summary}. Continue migration intake using the information schema."


def apply_form_values_to_intake(
    session: dict[str, Any],
    values: dict[str, Any],
) -> MigrationIntakeSchema:
    """Merge Pydantic-validated form submission into intake and persist to session."""
    submission = FormIntakeSubmission.from_raw_values(values)
    intake = merge_intake(
        intake_from_session(session),
        submission.model_dump(exclude_none=True),
    )
    intake = normalize_repository_id(intake, session.get("discovery_snapshot"))
    sync_intake_to_session(session, intake)
    return intake


def plan_review_fields() -> list[IntakeFieldSpec]:
    """Fields presented after the planner builds a migration plan."""
    return [
        INTAKE_FIELD_REGISTRY["plan_confirmed"],
        INTAKE_FIELD_REGISTRY["confirm_execute"],
        INTAKE_FIELD_REGISTRY["plan_notes"],
    ]


def build_plan_review_form(session: dict[str, Any]) -> dict[str, Any]:
    """Schema-driven plan confirmation form."""
    plan = session.get("migration_plan") or {}
    repo_id = session.get("plan_repository_id") or plan.get("repository_id")
    title = f"Confirm migration plan — {repo_id}" if repo_id else "Confirm migration plan"
    repo_count = plan.get("repo_count", len(plan.get("repos", [])))
    mode = "dry-run" if session.get("dry_run", True) else "live"
    if repo_id:
        description = f"Review and confirm the {mode} plan for `{repo_id}`."
    else:
        description = f"Review and confirm the {mode} plan for {repo_count} repository(ies)."
    return build_dynamic_form(
        form_id="intake_plan_review",
        title=title,
        description=description[:300],
        fields=plan_review_fields(),
    )


def build_repo_error_form(error_message: str, planner_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dynamic form when repository validation fails."""
    return build_dynamic_form(
        form_id="intake_repository_id",
        title="Repository not found",
        description=error_message,
        fields=[INTAKE_FIELD_REGISTRY["repository_id"]],
        planner_context=planner_context,
    )


async def prepare_form_submission(
    session: dict[str, Any],
    form: dict[str, Any],
    values: dict[str, Any],
    *,
    ensure_repo_valid: Any,
) -> dict[str, Any]:
    """Map form values → intake schema and decide the next orchestrator action."""
    form_id = str(form.get("form_id") or "")

    if form_id.startswith("operator_input_"):
        from ado2gh.agents.migration_agent.operator_input import (
            apply_operator_input_resolution,
            pending_operator_input,
        )

        request = pending_operator_input(session)
        if request is None:
            return {
                "status": "operator_input_unresolved",
                "reply": "Operator input session expired. Re-run planning if needed.",
            }
        return await apply_operator_input_resolution(session, request, values)

    if form_id == "inventory_gaps":
        return {"status": "inventory_gaps", "values": values}

    intake = apply_form_values_to_intake(session, values)
    submission = FormIntakeSubmission.from_raw_values(values)

    if submission.repository_id and intake.resolved_repository_id():
        validation_error = await ensure_repo_valid(intake.resolved_repository_id())
        if validation_error:
            return {
                "status": "repo_invalid",
                "validation_error": validation_error,
                "form": build_repo_error_form(validation_error),
            }
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("pev_execution_completed", None)
        session.pop("pev_execution_started", None)
        session.pop("pev_max_retries_exhausted", None)
        session.pop("start_execution", None)

    if session.get("migration_plan"):
        if intake.plan_notes and not intake.plan_confirmed:
            session.pop("migration_plan", None)
            session["plan_approved"] = False
            return {
                "status": "plan_revise",
                "message": f"Revise the migration plan. Operator feedback: {intake.plan_notes}",
            }
        if not intake.plan_confirmed:
            return {
                "status": "plan_unconfirmed",
                "form": build_plan_review_form(session),
                "reply": "Confirm the plan is correct, or describe changes in Notes without checking confirm.",
            }
        session["plan_approved"] = True
        plan = session.get("migration_plan") or {}
        if intake.plan_notes:
            plan["operator_notes"] = intake.plan_notes
            session["migration_plan"] = plan
        if intake.confirm_execute:
            from ado2gh.agents.migration_agent.blockers import needs_blocker_resolution, outstanding_blockers
            from ado2gh.agents.migration_agent.operator_input import (
                operator_input_from_blockers,
                operator_input_to_form,
                store_operator_input,
            )

            if needs_blocker_resolution(plan, session):
                blockers = outstanding_blockers(plan, session)
                request = operator_input_from_blockers(blockers, session)
                store_operator_input(session, request)
                return {
                    "status": "operator_input_unresolved",
                    "reply": "Resolve blockers before executing.",
                    "form": operator_input_to_form(request),
                }
            if plan.get("blocked"):
                return {
                    "status": "plan_blocked",
                    "reply": plan.get("block_reason", "migration_plan_blocked"),
                }
            return {"status": "plan_execute", "confirm_execute": True}
        return {"status": "plan_ready"}

    return {
        "status": "continue",
        "message": build_form_submit_prompt(values),
        "intake": intake,
    }


async def resolve_intake_routing(
    state: dict[str, Any],
    session: dict[str, Any],
    *,
    intent: str,
    user_message: str = "",
    analysis: OperatorMessageAnalysis | None = None,
) -> dict[str, Any]:
    """Merge LLM analysis → missing fields → next action for orchestrator."""
    from ado2gh.agents.migration_agent.intake_guardrails import apply_analysis_guardrails

    if analysis is not None:
        analysis = apply_analysis_guardrails(analysis, user_message)

    phase = determine_intake_phase(session, intent=intent)
    planning_from_message = (
        phase == IntakePhase.PLANNING
        and intent == "migration_action"
        and analysis is not None
        and not session.get("migration_plan")
        and not (
            session.get("plan_repository_id")
            or session.get("plan_repository_ids")
        )
    )

    if planning_from_message:
        intake = MigrationIntakeSchema(
            dry_run=analysis.dry_run if analysis.dry_run is not None else True,
        )
        if analysis.requests_new_migration and not analysis.repository_id:
            from ado2gh.agents.migration_agent.session_state import reset_session_for_new_migration

            reset_session_for_new_migration(session)
            session.pop("plan_repository_id", None)
        intake = apply_message_analysis(intake, analysis)
    else:
        intake = intake_from_session(session)
        if analysis is not None:
            intake = apply_message_analysis(intake, analysis)
            if analysis.requests_new_migration and not analysis.repository_id:
                from ado2gh.agents.migration_agent.session_state import reset_session_for_new_migration

                intake = clear_repository_intake(intake)
                reset_session_for_new_migration(session)
                session.pop("plan_repository_id", None)

    if phase is None:
        sync_intake_to_session(session, intake)
        return {"intake": intake, "action": "none"}

    accel_get = state.get("accel_get")
    requests_new = bool(analysis and analysis.requests_new_migration)
    planner_ctx = await consult_planner_context(
        session,
        accel_get,
        state.get("session_token"),
        intake=intake,
        requests_new_migration=requests_new,
    )
    intake = normalize_repository_id(intake, session.get("discovery_snapshot"))
    requested_repo = intake.resolved_repository_id()
    if requested_repo and intent == "migration_action":
        from ado2gh.agents.migration_agent.session_state import maybe_reset_for_migration_request

        merged_intake = intake
        maybe_reset_for_migration_request(session, requested_repo)
        intake = merge_intake(
            intake_from_session(session),
            merged_intake.model_dump(exclude_none=True),
        )
        intake = normalize_repository_id(intake, session.get("discovery_snapshot"))
    sync_intake_to_session(session, intake)

    missing = missing_intake_fields(intake, phase)
    if not missing:
        if phase == IntakePhase.PLANNING and intake_ready_for_planner(intake):
            return {
                "intake": intake,
                "action": "invoke_planner",
                "repository_id": intake.resolved_repository_id(),
                "dry_run": intake.dry_run,
            }
        if phase == IntakePhase.PLAN_REVIEW and intake.plan_confirmed:
            return {"intake": intake, "action": "plan_confirmed", "confirm_execute": intake.confirm_execute}
        return {"intake": intake, "action": "none"}

    primary = missing[0]
    titles = {
        IntakePhase.PLANNING: "Migration details needed",
        IntakePhase.PLAN_REVIEW: "Review migration plan",
        IntakePhase.CANCELLATION: "Cancellation",
    }
    description = primary.description or f"Provide {primary.label.lower()}."
    if primary.name == "repository_id" and requests_new:
        titles[IntakePhase.PLANNING] = "Which repository should we migrate next?"
        description = (
            "Name the repository in Project/RepoName format, or choose from the suggestions below."
        )
    form = build_dynamic_form(
        form_id=f"intake_{primary.name}",
        title=titles.get(phase, "Input required"),
        description=description,
        fields=missing[:3],
        planner_context=planner_ctx,
    )
    return {
        "intake": intake,
        "action": "request_form",
        "form": form,
        "planner_context": planner_ctx,
        "missing_fields": [f.name for f in missing],
    }
