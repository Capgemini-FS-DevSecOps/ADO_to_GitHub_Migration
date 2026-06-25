"""Planner/validator operator-input requests — schema-driven forms and resolutions."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ado2gh.agents.migration_agent.blockers import (
    apply_skip_blocked_scopes,
    outstanding_blockers,
    record_declined_blockers,
)
from ado2gh.agents.migration_agent.forms import sanitize_form
from ado2gh.agents.migration_agent.intake_schema import IntakeFieldSpec
from ado2gh.agents.migration_agent.operator_input_schema import (
    OperatorInputFieldSpec,
    OperatorInputRequest,
)
from ado2gh.models import MigrationScope


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:48]


def request_id_for_blockers(blockers: list[dict[str, Any]]) -> str:
    keys = sorted(str(b.get("key", "")) for b in blockers)
    digest = hashlib.sha256("|".join(keys).encode()).hexdigest()[:10]
    return f"blockers_{digest}"


def _field_specs_from_operator_fields(
    fields: list[OperatorInputFieldSpec],
) -> list[IntakeFieldSpec]:
    return [
        IntakeFieldSpec(
            name=f.name,
            label=f.label,
            field_type=f.field_type,
            description=f.description,
            options=f.options,
            required=f.required,
        )
        for f in fields
    ]


def operator_input_to_form(request: OperatorInputRequest) -> dict[str, Any]:
    """Build a sanitized dynamic form from a pydantic operator-input request."""
    from ado2gh.agents.migration_agent.intake import build_dynamic_form

    specs = _field_specs_from_operator_fields(request.fields)
    return sanitize_form(
        build_dynamic_form(
            form_id=request.form_id(),
            title=request.title,
            description=request.description,
            fields=specs,
            planner_context=request.context or None,
        )
    )


def operator_input_from_blockers(
    blockers: list[dict[str, Any]],
    session: dict[str, Any],
    *,
    source: str = "planner",
) -> OperatorInputRequest:
    """Heuristic operator-input request for blocked plan work items."""
    primary = blockers[0] if blockers else {}
    repo = primary.get("repo") or session.get("plan_repository_id") or "the selected repository"
    blocker_text = str(primary.get("blocker") or "Migration is blocked.")
    keys = [str(b.get("key")) for b in blockers if b.get("key")]

    fields: list[OperatorInputFieldSpec] = []
    options: list[str] = ["skip_blocked_scope", "replan"]

    if any("inventory" in str(b.get("blocker", "")).lower() for b in blockers):
        options = ["run_pipeline_inventory", "skip_blocked_scope", "replan"]

    fields.append(
        OperatorInputFieldSpec(
            name="resolution",
            label="How should we proceed?",
            field_type="select",
            description="Planner recommendation: resolve the blocker or skip affected scope.",
            options=options,
            required=True,
        )
    )

    if any(
        b.get("scope") == MigrationScope.SECRETS.value
        or "secret" in str(b.get("blocker", "")).lower()
        for b in blockers
    ):
        fields.append(
            OperatorInputFieldSpec(
                name="operator_notes",
                label="Notes or mappings",
                field_type="textarea",
                description="Optional secret names, mappings, or instructions for the planner.",
                required=False,
            )
        )

    return OperatorInputRequest(
        request_id=request_id_for_blockers(blockers),
        source=source,  # type: ignore[arg-type]
        title="Migration blocker — operator decision required",
        description=(
            f"**{repo}** — {blocker_text}\n\n"
            "The planner needs your decision before continuing."
        ),
        blocker_keys=keys,
        fields=fields,
        context={"blockers": blockers[:8], "repo": repo},
    )


def blockers_from_baseline_probes(
    baseline_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn pre-plan API probe results into planner blockers."""
    blockers: list[dict[str, Any]] = []
    for entry in baseline_findings:
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo") or "").strip()
        if not repo:
            continue
        issues: list[str] = []
        if entry.get("config_error"):
            issues.append(str(entry["config_error"]))
        if entry.get("github_org_missing") or not str(entry.get("github_org") or "").strip():
            issues.append(
                "GitHub organization is not configured on the active profile or migration plan."
            )
        ado = entry.get("ado_repo")
        if isinstance(ado, dict):
            if ado.get("error"):
                issues.append(f"ADO repository could not be verified: {ado['error']}")
            elif not ado.get("id"):
                issues.append("ADO repository was not found at the expected project/repo path.")
        gh = entry.get("github_target")
        if isinstance(gh, dict) and entry.get("github_org") and entry.get("github_repo"):
            if gh.get("error"):
                issues.append(f"GitHub target could not be verified: {gh['error']}")
            elif gh.get("exists") is False:
                issues.append(
                    f"GitHub repository `{entry.get('github_org')}/{entry.get('github_repo')}` "
                    "does not exist yet."
                )
        if not issues:
            continue
        blocker_text = "; ".join(issues)
        blockers.append({
            "repo": repo,
            "scope": "repo",
            "blocker": blocker_text,
            "key": f"repo:{_slug(repo)}:probe_failed",
        })
    return blockers


def validation_failures_from_baseline_probes(
    baseline_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn validator baseline probe results into structured validation failures."""
    failures: list[dict[str, Any]] = []
    for blocker in blockers_from_baseline_probes(baseline_findings):
        failures.append({
            "repo": blocker.get("repo"),
            "scope": blocker.get("scope") or "validation",
            "specific_failure": blocker.get("blocker"),
            "operator_input_required": True,
            "source": "validator_baseline_probe",
            "recommended_remediation": (
                "Fix repository configuration, confirm GitHub repo creation, or skip the blocked scope."
            ),
        })
    for blocker in blockers_from_validator_baseline_probes(baseline_findings):
        key = str(blocker.get("key") or "")
        if any(str(f.get("specific_failure")) == str(blocker.get("blocker")) for f in failures):
            continue
        failures.append({
            "repo": blocker.get("repo"),
            "scope": blocker.get("scope") or "validation",
            "specific_failure": blocker.get("blocker"),
            "operator_input_required": True,
            "source": "validator_baseline_probe",
            "recommended_remediation": blocker.get("recommended_remediation")
            or "Review pipeline conversion evidence and replan if needed.",
        })
    return failures


def blockers_from_validator_baseline_probes(
    baseline_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validator-specific blockers from pipeline probes and discovery gaps."""
    blockers: list[dict[str, Any]] = []
    for entry in baseline_findings:
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo") or "").strip()
        if not repo:
            continue
        if entry.get("discovery_missing"):
            blockers.append({
                "repo": repo,
                "scope": "validation",
                "blocker": (
                    "Executed repository is not present in the discovery snapshot — "
                    "validation cannot verify ADO/GitHub targets."
                ),
                "key": f"validation:{_slug(repo)}:discovery_missing",
                "recommended_remediation": "Re-run discovery or fix repository_id in the migration plan.",
            })
        if entry.get("pipeline_conversion_gap"):
            ado_count = int((entry.get("ado_pipelines_probe") or {}).get("pipeline_count") or 0)
            exec_count = int(entry.get("executor_workflow_count") or 0)
            blockers.append({
                "repo": repo,
                "scope": "pipelines",
                "blocker": (
                    f"ADO reports {ado_count} pipeline(s) but executor produced "
                    f"{exec_count} workflow file(s) — conversion may be incomplete."
                ),
                "key": f"pipelines:{_slug(repo)}:conversion_gap",
                "recommended_remediation": (
                    "Re-run pipeline conversion with template resolution enabled or replan pipeline scope."
                ),
            })
        if entry.get("pipeline_count_mismatch"):
            ado_count = int(entry.get("ado_pipeline_count") or 0)
            gh_count = int(entry.get("github_workflow_count") or 0)
            blockers.append({
                "repo": repo,
                "scope": "pipelines",
                "blocker": (
                    f"Pipeline parity check failed: {ado_count} ADO pipeline(s) vs "
                    f"{gh_count} GitHub workflow file(s) on the target repo."
                ),
                "key": f"pipelines:{_slug(repo)}:count_mismatch",
                "recommended_remediation": (
                    "Inspect converted workflows on GitHub and re-run conversion for missing pipelines."
                ),
            })
        ado_probe = entry.get("ado_pipelines_probe")
        if isinstance(ado_probe, dict) and ado_probe.get("error"):
            blockers.append({
                "repo": repo,
                "scope": "pipelines",
                "blocker": f"Could not list ADO pipelines: {ado_probe['error']}",
                "key": f"pipelines:{_slug(repo)}:ado_list_failed",
                "recommended_remediation": "Verify ADO credentials and repository path, then retry validation.",
            })
    return blockers


def operator_input_from_probe_failures(
    blockers: list[dict[str, Any]],
    session: dict[str, Any],
    *,
    source: str = "planner",
) -> OperatorInputRequest:
    """Operator form when baseline probes show missing ADO/GitHub targets."""
    primary = blockers[0] if blockers else {}
    repo = primary.get("repo") or session.get("plan_repository_id") or "the selected repository"
    keys = [str(b.get("key")) for b in blockers if b.get("key")]
    details = "\n".join(f"- **{b.get('repo', repo)}**: {b.get('blocker', '')}" for b in blockers[:6])

    options = [
        "fix_repository_id",
        "confirm_github_repo_create",
        "skip_blocked_scope",
        "replan",
    ]
    title = (
        "Validation blocker — operator decision required"
        if source == "validator"
        else "Repository verification failed — operator decision required"
    )
    intro = (
        "Validation cannot complete until source and target repositories are confirmed."
        if source == "validator"
        else "Planning cannot continue until source and target repositories are confirmed."
    )
    return OperatorInputRequest(
        request_id=request_id_for_blockers(blockers),
        source=source,  # type: ignore[arg-type]
        title=title,
        description=(
            f"{intro}\n\n"
            f"{details}\n\n"
            "Choose how to proceed:"
        ),
        blocker_keys=keys,
        fields=[
            OperatorInputFieldSpec(
                name="resolution",
                label="How should we proceed?",
                field_type="select",
                description=(
                    "Fix the repository ID, confirm GitHub repo creation intent, skip, or replan."
                ),
                options=options,
                required=True,
            ),
            OperatorInputFieldSpec(
                name="operator_notes",
                label="Notes",
                field_type="textarea",
                description="Correct repository path, GitHub org/repo, or other context.",
                required=False,
            ),
        ],
        context={"blockers": blockers[:8], "repo": repo, "probe_failures": True},
    )


def _failure_needs_operator(failure: Any) -> bool:
    if not isinstance(failure, dict):
        return False
    if failure.get("operator_input_required"):
        return True
    if is_fr036_failure(failure):
        return True
    text = " ".join(
        str(failure.get(key, ""))
        for key in (
            "specific_failure",
            "error",
            "recommended_remediation",
            "remediation",
            "failure",
        )
    ).lower()
    benign_hints = (
        "endpoint unavailable",
        "accelerator_unavailable",
        "404",
        "not found",
        "not included in migration scopes",
    )
    if any(hint in text for hint in benign_hints):
        return False
    hints = (
        "operator",
        "manual",
        "inventory",
        "secret",
        "service connection",
        "mapping",
        "blocked",
        "missing",
        "locked",
        "another session",
    )
    return any(hint in text for hint in hints)


def is_fr036_failure(failure: Any) -> bool:
    """True when failure is the one-live-migration-per-repo guard (FR-036)."""
    if isinstance(failure, dict):
        code = str(failure.get("error_code", "")).lower()
        if code in ("migration_in_progress", "fr036", "active_live_migration"):
            return True
        text = " ".join(
            str(failure.get(key, ""))
            for key in (
                "specific_failure",
                "error",
                "failure",
                "message",
                "detail",
            )
        )
    else:
        text = str(failure)
    lower = text.lower()
    return (
        "fr-036" in lower
        or "active live migration" in lower
        or "migration in progress for" in lower
    )


def fr036_operator_message(failures: list[Any]) -> str | None:
    """Deterministic operator chat text for FR-036 live migration conflicts."""
    for failure in failures:
        if not is_fr036_failure(failure):
            continue
        repo = ""
        holder_run = ""
        if isinstance(failure, dict):
            repo = str(failure.get("repo") or "").strip()
            holder_run = str(failure.get("holder_run_id") or "").strip()
        if not repo:
            repo = "the repository"
        if holder_run:
            return (
                f"Could not start **live** migration for **{repo}**: another pipeline run "
                f"(`{holder_run}`) is already active for this repository (FR-036). "
                "Cancel or wait for that run to finish, then confirm the plan again."
            )
        return (
            f"Could not start **live** migration for **{repo}**: the platform still has an "
            "active live migration recorded for this repository (FR-036). "
            "Check **Settings → History** for another running migration on this repo, "
            "or clear stale state if a prior live run was interrupted — then confirm the plan again."
        )
    return None


def build_repo_lock_failure(
    repo_id: str,
    lock_holder_session_id: str | None,
) -> dict[str, Any]:
    """Structured executor/validator failure when a repo lock cannot be acquired."""
    holder = (lock_holder_session_id or "").strip() or None
    if holder:
        specific = f"Repository {repo_id} is locked by agent session {holder}."
    else:
        specific = f"Repository {repo_id} is locked by another agent session."
    return {
        "repo": repo_id,
        "error": "locked",
        "error_code": "repo_locked",
        "lock_holder_session_id": holder,
        "specific_failure": specific,
        "operator_input_required": True,
    }


def repo_lock_operator_message(failures: list[Any]) -> str | None:
    """Deterministic operator chat text for repo lock failures (no LLM required)."""
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        if str(failure.get("error", "")).lower() != "locked":
            if str(failure.get("error_code", "")).lower() != "repo_locked":
                continue
        repo = str(failure.get("repo") or "the repository")
        holder = str(failure.get("lock_holder_session_id") or "").strip()
        if holder:
            return (
                f"Could not migrate **{repo}**: it is locked by agent session "
                f"`{holder}`. Close that chat or wait for its migration to finish, "
                "then confirm the plan again."
            )
        return (
            f"Could not migrate **{repo}**: it is locked by another agent session. "
            "Close other agent chats for this repository or wait for them to finish, "
            "then confirm the plan again."
        )
    return None


def failures_require_operator_escalation(failures: list[Any]) -> bool:
    """True when validator/executor failures need an operator-facing message immediately."""
    if not failures:
        return False
    for failure in failures:
        if not isinstance(failure, dict):
            if "locked" in str(failure).lower():
                return True
            if is_fr036_failure(failure):
                return True
            continue
        if is_fr036_failure(failure):
            return True
        if str(failure.get("error", "")).lower() == "locked":
            return True
        if str(failure.get("error_code", "")).lower() == "repo_locked":
            return True
        if str(failure.get("reason", "")).lower() == "locked":
            return True
        if _failure_needs_operator(failure):
            return True
    return False


def operator_input_from_validator_failures(
    failures: list[Any],
    session: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> OperatorInputRequest | None:
    """Build operator-input request when validator failures need human decision."""
    operator_failures = [f for f in failures if _failure_needs_operator(f)]
    if not operator_failures:
        return None

    repo = (
        session.get("plan_repository_id")
        or (plan or {}).get("repository_id")
        or "the migration"
    )
    primary = operator_failures[0] if isinstance(operator_failures[0], dict) else {}
    summary = str(
        primary.get("specific_failure")
        or primary.get("error")
        or primary.get("failure")
        or "Validation could not complete automatically."
    )
    request_id = f"validator_{_slug(summary)[:32] or 'failure'}"

    return OperatorInputRequest(
        request_id=request_id,
        source="validator",
        title="Validation blocker — operator decision required",
        description=(
            f"Validator reported an issue for **{repo}**:\n\n{summary}\n\n"
            "Choose whether to retry after remediation, skip affected scope, or replan."
        ),
        blocker_keys=[request_id],
        fields=[
            OperatorInputFieldSpec(
                name="resolution",
                label="How should we proceed?",
                field_type="select",
                options=["replan", "skip_blocked_scope", "continue"],
                required=True,
            ),
            OperatorInputFieldSpec(
                name="operator_notes",
                label="Notes for the planner",
                field_type="textarea",
                required=False,
            ),
        ],
        context={"failures": operator_failures[:8]},
    )


def assess_operator_input_needed(
    *,
    plan: dict[str, Any],
    validation_feedback: dict[str, Any] | None,
    session: dict[str, Any],
    planner_request: dict[str, Any] | None = None,
) -> OperatorInputRequest | None:
    """Return an operator-input request when planner or validator needs a decision."""
    if planner_request:
        try:
            return OperatorInputRequest.model_validate(planner_request)
        except Exception:
            pass

    if validation_feedback:
        failures = validation_feedback.get("failures") or []
        validator_req = operator_input_from_validator_failures(
            failures, session, plan=plan,
        )
        if validator_req:
            return validator_req

    blockers = outstanding_blockers(plan, session)
    if blockers:
        return operator_input_from_blockers(blockers, session)
    return None


def store_operator_input(session: dict[str, Any], request: OperatorInputRequest) -> None:
    session["pending_operator_input"] = request.model_dump()


def clear_operator_input(session: dict[str, Any]) -> None:
    session.pop("pending_operator_input", None)


def pending_operator_input(session: dict[str, Any]) -> OperatorInputRequest | None:
    raw = session.get("pending_operator_input")
    if not raw:
        return None
    try:
        return OperatorInputRequest.model_validate(raw)
    except Exception:
        return None


async def apply_operator_input_resolution(
    session: dict[str, Any],
    request: OperatorInputRequest,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Apply operator form values and return the next orchestrator action."""
    resolution = str(values.get("resolution") or values.get("action") or "").strip()
    notes = str(values.get("operator_notes") or "").strip()

    if notes:
        plan = session.get("migration_plan") or {}
        if isinstance(plan, dict):
            plan["operator_notes"] = notes
            session["migration_plan"] = plan

    if resolution == "run_pipeline_inventory":
        clear_operator_input(session)
        session.pop("plan_review_presented", None)
        return {
            "status": "operator_remediate",
            "remediation": "run_pipeline_inventory",
            "message": (
                "Operator chose to run pipeline inventory. "
                "Refresh discovery and rebuild the migration plan."
            ),
        }

    if resolution == "skip_blocked_scope":
        plan = session.get("migration_plan") or {}
        if isinstance(plan, dict) and request.blocker_keys:
            plan = apply_skip_blocked_scopes(plan, blocker_keys=request.blocker_keys)
            record_declined_blockers(session, request.blocker_keys)
            session["migration_plan"] = plan
        clear_operator_input(session)
        session.pop("plan_approved", None)
        session.pop("plan_review_presented", None)
        return {
            "status": "operator_input_continue",
            "message": (
                "Operator chose to skip blocked scope(s). "
                "Present the updated plan for confirmation."
            ),
        }

    if resolution == "replan":
        clear_operator_input(session)
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        feedback = notes or "Operator requested replan after blocker."
        return {
            "status": "operator_replan",
            "message": f"Revise the migration plan. Operator feedback: {feedback}",
        }

    if resolution == "fix_repository_id":
        clear_operator_input(session)
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        if notes:
            session["operator_repo_correction"] = notes
        return {
            "status": "operator_remediate",
            "remediation": "fix_repository_id",
            "message": (
                "Operator will correct the repository ID or profile mapping. "
                "Collect the updated repository and re-invoke the planner."
            ),
        }

    if resolution == "confirm_github_repo_create":
        clear_operator_input(session)
        session["github_repo_create_confirmed"] = True
        session.pop("plan_approved", None)
        session.pop("plan_review_presented", None)
        if notes:
            session["operator_notes"] = notes
        return {
            "status": "operator_replan",
            "message": (
                "Operator confirmed GitHub target repo may be created during migration. "
                "Rebuild the plan with that assumption."
            ),
        }

    if resolution == "continue":
        clear_operator_input(session)
        return {
            "status": "operator_input_continue",
            "message": "Operator approved continuing with the current plan context.",
        }

    return {
        "status": "operator_input_unresolved",
        "reply": "Select how to proceed.",
        "form": operator_input_to_form(request),
    }
