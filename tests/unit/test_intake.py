"""Tests for migration intake information schema, Pydantic forms, and routing."""
from __future__ import annotations

from ado2gh.agents.migration_agent.intake import (
    apply_form_values_to_intake,
    apply_message_analysis,
    format_form_submission_summary,
    intake_from_session,
    intake_ready_for_planner,
    merge_intake,
    missing_intake_fields,
    determine_intake_phase,
    build_dynamic_form,
    normalize_repository_id,
    resolve_intake_routing,
)
from ado2gh.agents.migration_agent.intake_schema import (
    FormIntakeSubmission,
    IntakePhase,
    MigrationIntakeSchema,
    OperatorIntent,
    OperatorMessageAnalysis,
)


def test_operator_message_analysis_intake_patch():
    analysis = OperatorMessageAnalysis(
        reasoning="Operator wants a live migration for project/repo-a.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="project/repo-a",
        repository_named_in_message=True,
        dry_run=False,
    )
    intake = apply_message_analysis(MigrationIntakeSchema(), analysis)
    assert intake.repository_id == "project/repo-a"
    assert intake.dry_run is False


def test_operator_message_analysis_vague_repo():
    analysis = OperatorMessageAnalysis(
        reasoning="Operator wants another repo migrated live but did not name it.",
        intent=OperatorIntent.MIGRATION_ACTION,
        dry_run=False,
        requests_new_migration=True,
    )
    patch = analysis.intake_patch()
    assert "repository_id" not in patch
    assert patch["dry_run"] is False
    assert analysis.requests_new_migration is True


def test_form_intake_submission_maps_aliases():
    submission = FormIntakeSubmission.from_raw_values({
        "repository": "proj/repo",
        "mode": "live",
    })
    assert submission.repository_id == "proj/repo"
    assert submission.dry_run is False


def test_form_intake_submission_coerces_dry_run_strings():
    submission = FormIntakeSubmission.from_raw_values({"dry_run": "true"})
    assert submission.dry_run is True
    submission_live = FormIntakeSubmission.from_raw_values({"dry_run": "false"})
    assert submission_live.dry_run is False


def test_merge_intake_later_wins():
    base = MigrationIntakeSchema(repository_id="a/b", dry_run=True)
    merged = merge_intake(base, {"dry_run": False})
    assert merged.dry_run is False
    assert merged.repository_id == "a/b"


def test_intake_from_session_hydrates_fields():
    session = {
        "plan_repository_id": "proj/repo",
        "dry_run": False,
        "execution_mode_confirmed": True,
        "plan_phase": "poc",
        "plan_approved": True,
    }
    intake = intake_from_session(session)
    assert intake.resolved_repository_id() == "proj/repo"
    assert intake.dry_run is False
    assert intake.phase == "poc"
    assert intake.plan_confirmed is True


def test_missing_fields_planning_phase():
    intake = MigrationIntakeSchema(repository_id="a/b")
    missing = missing_intake_fields(intake, IntakePhase.PLANNING)
    assert [f.name for f in missing] == ["dry_run"]


def test_intake_ready_for_planner():
    assert not intake_ready_for_planner(MigrationIntakeSchema(repository_id="a/b"))
    assert intake_ready_for_planner(
        MigrationIntakeSchema(repository_id="a/b", dry_run=True)
    )


def test_determine_intake_phase_plan_review():
    session = {"migration_plan": {"repos": []}, "plan_approved": False}
    assert determine_intake_phase(session) == IntakePhase.PLAN_REVIEW


def test_format_form_submission_summary_from_pydantic():
    summary = format_form_submission_summary({
        "repository_id": "azure-pipelines/app",
        "dry_run": "dry-run",
    })
    assert summary == "repository_id: azure-pipelines/app, dry_run: true"


def test_format_plan_review_submission_confirmed_omits_notes():
    summary = format_form_submission_summary(
        {
            "plan_confirmed": True,
            "confirm_execute": True,
            "plan_notes": "Do a live migration not dry run",
        },
        form_id="intake_plan_review",
    )
    assert summary == "Plan confirmed, start migration"
    assert "plan_notes" not in summary


def test_format_plan_review_submission_revision_shows_notes():
    summary = format_form_submission_summary(
        {"plan_confirmed": False, "plan_notes": "Add wiki scope"},
        form_id="intake_plan_review",
    )
    assert summary == "Requested plan changes: Add wiki scope"


def test_apply_form_values_syncs_session():
    session: dict = {}
    intake = apply_form_values_to_intake(session, {"repository_id": "p/r", "mode": "dry-run"})
    assert intake.repository_id == "p/r"
    assert session["plan_repository_id"] == "p/r"
    assert session["dry_run"] is True
    assert session["execution_mode_confirmed"] is True


def test_determine_intake_phase_planning_when_repo_selected():
    session = {
        "plan_repository_id": "azure-pipelines/bicep-template-migration",
    }
    assert determine_intake_phase(session, intent="general_chat") == IntakePhase.PLANNING


def test_build_dynamic_form_uses_schema_fields():
    from ado2gh.agents.migration_agent.intake_schema import INTAKE_FIELD_REGISTRY

    form = build_dynamic_form(
        form_id="intake_repository_id",
        title="Repository needed",
        description="Enter repo",
        fields=[INTAKE_FIELD_REGISTRY["repository_id"]],
        planner_context={"repo_suggestions": ["a/b", "a/c"]},
    )
    assert form["form_id"] == "intake_repository_id"
    assert form["fields"][0]["name"] == "repository_id"
    assert "a/b" in form["description"]


def test_normalize_repository_id_fuzzy_match():
    discovery = {
        "repos": [
            {
                "project": "azure-pipelines",
                "repo_name": "bicep-template-migration",
                "name": "bicep-template-migration",
            }
        ]
    }
    intake = MigrationIntakeSchema(repository_id="azure-pipelines-bicep-template")
    normalized = normalize_repository_id(intake, discovery)
    assert normalized.repository_id == "azure-pipelines/bicep-template-migration"


def test_resolve_intake_keeps_named_repo_when_requests_new_migration():
    import asyncio

    session = {
        "plan_repository_id": "azure-pipelines/old-repo",
        "dry_run": False,
        "execution_mode_confirmed": True,
        "last_completed_repository_id": "azure-pipelines/old-repo",
    }
    analysis = OperatorMessageAnalysis(
        reasoning="Operator named bicep-template repo for live migration.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="azure-pipelines-bicep-template",
        repository_named_in_message=True,
        dry_run=False,
        requests_new_migration=True,
    )
    routing = asyncio.run(
        resolve_intake_routing(
            {"accel_get": None, "session_token": None},
            session,
            intent="migration_action",
            user_message="migrate azure-pipelines-bicep-template live",
            analysis=analysis,
        )
    )
    assert routing["intake"].repository_id == "azure-pipelines-bicep-template"
