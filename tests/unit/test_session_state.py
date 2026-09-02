"""Unit tests for the five-state session activity model."""
import pytest

from ado2gh.agents.migration_agent.session.state import (
    SessionState,
    SessionStateMachine,
    is_session_busy,
    normalize_session_status,
    release_session_for_chat,
    reset_session_for_new_migration,
    maybe_reset_for_migration_request,
    set_session_idle,
    set_session_phase,
    OrchestratorResult,
)


def test_normalize_legacy_statuses_to_idle():
    assert normalize_session_status("awaiting_approval") == "idle"
    assert normalize_session_status("completed") == "idle"
    assert normalize_session_status("failed") == "idle"
    assert normalize_session_status("running") == "idle"


def test_is_session_busy_only_for_agent_phases():
    assert is_session_busy("thinking") is True
    assert is_session_busy("planning") is True
    assert is_session_busy("executing") is True
    assert is_session_busy("validating") is True
    assert is_session_busy("idle") is False
    assert is_session_busy("awaiting_approval") is False


def test_direct_phase_assignment():
    session: dict = {"status": "idle"}
    set_session_phase(session, SessionState.THINKING)
    assert session["status"] == "thinking"
    set_session_phase(session, SessionState.PLANNING)
    assert session["status"] == "planning"
    set_session_idle(session)
    assert session["status"] == "idle"


def test_state_machine_allows_any_transition():
    sm = SessionStateMachine()
    sm.transition(SessionState.EXECUTING)
    assert sm.state == SessionState.EXECUTING
    sm.transition(SessionState.THINKING)
    assert sm.state == SessionState.THINKING


def test_release_session_for_chat_sets_idle():
    session = {"status": "planning", "start_execution": True, "start_pev": True}
    release_session_for_chat(session)
    assert session["status"] == "idle"
    assert session["pev_execution_completed"] is True
    assert "start_execution" not in session


def test_release_session_for_chat_failed_outcome_still_idle():
    session = {"status": "validating", "start_execution": True}
    release_session_for_chat(session, outcome="failed")
    assert session["status"] == "idle"
    assert session.get("last_pev_outcome") == "failed"


def test_orchestrator_result_defaults():
    result = OrchestratorResult()
    assert result.reply == ""
    assert result.start_pev is False
    assert result.tasks == []
    assert result.pending_form is None


def test_reset_session_for_new_migration_clears_plan_and_sets_repo():
    session = {
        "status": "validating",
        "plan_repository_id": "azure-pipelines/old-repo",
        "migration_plan": {"repos": []},
        "plan_approved": True,
        "executor_result": {"failures": []},
    }
    reset_session_for_new_migration(
        session,
        repository_id="azure-pipelines/new-repo",
    )
    assert session["status"] == "idle"
    assert session["plan_repository_id"] == "azure-pipelines/new-repo"
    assert "migration_plan" not in session
    assert session["plan_approved"] is False


def test_maybe_reset_for_migration_request_on_repo_change():
    session = {
        "plan_repository_id": "azure-pipelines/script-migration",
        "migration_plan": {"repository_id": "azure-pipelines/script-migration"},
    }
    assert maybe_reset_for_migration_request(
        session,
        "azure-pipelines/build-migration",
    )
    assert session["plan_repository_id"] == "azure-pipelines/build-migration"
    assert "migration_plan" not in session
