"""Regression check for register entry THR-09-002: plan approval is bound to the plan revision the operator approved.

`plan_approved` was a bare sticky boolean: once set, every later plan — a replan, or a
plan swapped in by a tool — inherited the approval and could execute without ever
being shown to the operator.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key
from ado2gh.agents.migration_agent.hitl.intake import (
    PLAN_APPROVAL_KEY,
    IntakePhase,
    clear_stale_plan_approval,
    determine_intake_phase,
    intake_from_session,
    prepare_form_submission,
    record_plan_approval,
)
from ado2gh.agents.migration_agent.hitl.schemas import MigrationIntakeSchema
from ado2gh.agents.migration_agent.hitl.intake import sync_intake_to_session


def _plan(revision: int = 0) -> dict:
    return {
        "revision": revision,
        "dry_run": True,
        "repos": [{"id": "Proj/app"}],
        "work_items": [{"scope": "repo", "repo": "Proj/app", "status": "ready"}],
    }


def test_record_plan_approval_binds_to_the_current_plan():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)
    assert session["plan_approved"] is True
    assert session[PLAN_APPROVAL_KEY] == plan_revision_key(session["migration_plan"])


def test_approval_survives_the_plan_it_was_given_for():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)
    assert clear_stale_plan_approval(session) is False
    assert session["plan_approved"] is True
    assert determine_intake_phase(session) is not IntakePhase.PLAN_REVIEW


def test_a_replan_requires_a_fresh_approval():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)

    session["migration_plan"] = _plan(revision=1)

    assert clear_stale_plan_approval(session) is True
    assert session["plan_approved"] is False
    assert PLAN_APPROVAL_KEY not in session


def test_determine_intake_phase_reopens_review_for_a_changed_plan():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)

    # A tool swaps in a plan targeting a different repository.
    session["migration_plan"] = {**_plan(), "repos": [{"id": "Proj/other"}]}

    assert determine_intake_phase(session) is IntakePhase.PLAN_REVIEW
    assert session["plan_approved"] is False


def test_intake_from_session_does_not_hydrate_a_stale_approval():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)
    session["migration_plan"] = _plan(revision=2)

    assert intake_from_session(session).plan_confirmed is None


def test_an_unbound_legacy_approval_is_cleared():
    """A session approved before the binding existed re-asks rather than assuming."""
    session = {"migration_plan": _plan(), "plan_approved": True}
    assert clear_stale_plan_approval(session) is True
    assert session["plan_approved"] is False


def test_a_run_that_already_started_keeps_its_approval():
    session = {"migration_plan": _plan()}
    record_plan_approval(session)
    session["pev_execution_started"] = True
    # The executor annotates the plan as it works.
    session["migration_plan"] = {**_plan(), "work_items": [], "repos": []}

    assert clear_stale_plan_approval(session) is False
    assert session["plan_approved"] is True


def test_sync_intake_to_session_binds_the_approval_it_records():
    session = {"migration_plan": _plan()}
    sync_intake_to_session(session, MigrationIntakeSchema(plan_confirmed=True))
    assert session[PLAN_APPROVAL_KEY] == plan_revision_key(session["migration_plan"])


@pytest.mark.asyncio
async def test_confirming_the_plan_review_form_binds_the_approval():
    session = {"migration_plan": _plan()}
    form = {"form_id": "intake_plan_review", "fields": []}
    outcome = await prepare_form_submission(
        session, form, {"plan_confirmed": True}, ensure_repo_valid=None,
    )

    assert outcome["status"] == "plan_ready"
    assert session["plan_approved"] is True
    assert session[PLAN_APPROVAL_KEY] == plan_revision_key(session["migration_plan"])

    # The planner revises the plan; the old approval must not authorise the new one.
    session["migration_plan"] = _plan(revision=1)
    assert determine_intake_phase(session) is IntakePhase.PLAN_REVIEW


def test_assessing_a_revised_plan_revokes_the_old_approval():
    """The planner path reaches the executor edge without an intake turn (THR-09-002)."""
    from ado2gh.agents.migration_agent.hitl.operator_input import assess_operator_input_needed

    session = {"migration_plan": _plan()}
    record_plan_approval(session)

    revised = _plan(revision=1)
    session["migration_plan"] = revised
    assess_operator_input_needed(plan=revised, validation_feedback=None, session=session)

    assert session["plan_approved"] is False


def test_assessing_a_plan_not_yet_stored_on_the_session_revokes_the_approval():
    """A node assesses the new plan before writing it back; the flag must follow it."""
    from ado2gh.agents.migration_agent.hitl.operator_input import assess_operator_input_needed

    session = {"migration_plan": _plan()}
    record_plan_approval(session)

    assess_operator_input_needed(plan=_plan(revision=1), validation_feedback=None, session=session)

    assert session["plan_approved"] is False


def test_assessing_the_approved_plan_keeps_the_approval():
    from ado2gh.agents.migration_agent.hitl.operator_input import assess_operator_input_needed

    session = {"migration_plan": _plan()}
    record_plan_approval(session)

    assess_operator_input_needed(
        plan=session["migration_plan"], validation_feedback=None, session=session,
    )

    assert session["plan_approved"] is True
