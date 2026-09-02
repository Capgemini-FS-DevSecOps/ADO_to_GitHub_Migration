"""Tests for LLM-structured intake guardrails (no phrase-list heuristics)."""
from __future__ import annotations

import asyncio

from ado2gh.agents.migration_agent.hitl.intake import resolve_intake_routing
from ado2gh.agents.migration_agent.hitl.intake_guardrails import (
    apply_analysis_guardrails,
    repository_confirmed_for_turn,
)
from ado2gh.agents.migration_agent.hitl.schemas import (
    OperatorIntent,
    OperatorMessageAnalysis,
)


def test_operator_message_analysis_strips_repo_when_not_named():
    analysis = OperatorMessageAnalysis(
        reasoning="Message is vague; no repo named.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="infrastructure-as-code/iac-infra-az-storage-account",
        repository_named_in_message=False,
        dry_run=True,
    )
    assert analysis.repository_id is None


def test_operator_message_analysis_keeps_repo_when_named():
    analysis = OperatorMessageAnalysis(
        reasoning="Operator named the repo.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="proj/app",
        repository_named_in_message=True,
        dry_run=True,
    )
    assert analysis.repository_id == "proj/app"


def test_apply_analysis_guardrails_revalidates_policy():
    raw = OperatorMessageAnalysis(
        reasoning="Hallucinated from session.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="infrastructure-as-code/iac-infra-az-storage-account",
        repository_named_in_message=False,
        dry_run=True,
    )
    guarded = apply_analysis_guardrails(raw, "Migrate stuff")
    assert guarded.repository_id is None


def test_repository_confirmed_for_turn_requires_named_flag():
    analysis = OperatorMessageAnalysis(
        reasoning="Named repo.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="proj/app",
        repository_named_in_message=True,
    )
    assert repository_confirmed_for_turn("proj/app", analysis)
    assert not repository_confirmed_for_turn("proj/app", analysis.model_copy(
        update={"repository_named_in_message": False, "repository_id": None},
    ))


def test_repository_confirmed_for_turn_accepts_session_repo():
    session = {"plan_repository_id": "azure-pipelines/script-migration"}
    analysis = OperatorMessageAnalysis(
        reasoning="Operator chose dry run; repo was set on a prior form turn.",
        intent=OperatorIntent.MIGRATION_ACTION,
        dry_run=True,
        repository_named_in_message=False,
    )
    assert repository_confirmed_for_turn(
        "azure-pipelines/script-migration",
        analysis,
        session=session,
    )


def test_resolve_intake_vague_message_requests_form_not_planner():
    session = {
        "plan_repository_id": "infrastructure-as-code/iac-infra-az-storage-account",
        "dry_run": True,
        "execution_mode_confirmed": True,
    }
    analysis = OperatorMessageAnalysis(
        reasoning="Operator message is vague and requests another migration without naming a repo.",
        intent=OperatorIntent.MIGRATION_ACTION,
        repository_id="infrastructure-as-code/iac-infra-az-storage-account",
        repository_named_in_message=False,
        dry_run=True,
        requests_new_migration=True,
    )

    routing = asyncio.run(
        resolve_intake_routing(
            {"accel_get": None, "session_token": None},
            session,
            intent="migration_action",
            user_message="Migrate stuff",
            analysis=analysis,
        )
    )

    assert routing["action"] == "request_form"
    assert "repository_id" in routing.get("missing_fields", [])
    assert routing["intake"].repository_id is None


def test_resolve_intake_follow_up_dry_run_keeps_form_repo():
    session = {
        "plan_repository_id": "azure-pipelines/azure-pipelines-script-migration",
    }
    analysis = OperatorMessageAnalysis(
        reasoning="Operator chose dry run; repository was already submitted via form.",
        intent=OperatorIntent.MIGRATION_ACTION,
        dry_run=True,
        repository_named_in_message=False,
    )

    routing = asyncio.run(
        resolve_intake_routing(
            {"accel_get": None, "session_token": None},
            session,
            intent="migration_action",
            user_message="dry run",
            analysis=analysis,
        )
    )

    assert routing["action"] == "invoke_planner"
    assert routing["repository_id"] == "azure-pipelines/azure-pipelines-script-migration"
    assert routing["dry_run"] is True
