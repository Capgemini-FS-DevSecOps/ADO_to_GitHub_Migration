"""Operator escalation for validation failures (e.g. repo lock)."""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.operator_input import (
    build_repo_lock_failure,
    failures_require_operator_escalation,
    fr036_operator_message,
    is_fr036_failure,
    repo_lock_operator_message,
)
from ado2gh.agents.migration_agent.nodes import (
    _executor_result_for_repo_lock,
    _planner_post_validation_handoff,
)


def test_build_repo_lock_failure_includes_holder():
    failure = build_repo_lock_failure("proj/app", "ses_abc123")
    assert failure["error"] == "locked"
    assert failure["lock_holder_session_id"] == "ses_abc123"
    assert "ses_abc123" in failure["specific_failure"]


def test_repo_lock_operator_message():
    failures = [build_repo_lock_failure("proj/app", "ses_holder")]
    msg = repo_lock_operator_message(failures)
    assert msg is not None
    assert "proj/app" in msg
    assert "ses_holder" in msg


def test_failures_require_operator_escalation_for_repo_lock():
    failures = [{"repo": "proj/app", "error": "locked"}]
    assert failures_require_operator_escalation(failures) is True


def test_failures_require_operator_escalation_for_repo_lock_error_code():
    failures = [{"repo": "proj/app", "error_code": "repo_locked", "specific_failure": "locked"}]
    assert failures_require_operator_escalation(failures) is True


def test_repo_lock_operator_message_from_validator_shape():
    failures = [{
        "repo": "proj/app",
        "error_code": "repo_locked",
        "lock_holder_session_id": "ses_holder",
        "specific_failure": "Repository proj/app is locked by agent session ses_holder.",
    }]
    msg = repo_lock_operator_message(failures)
    assert msg is not None
    assert "ses_holder" in msg


def test_is_fr036_failure_detects_error_code():
    assert is_fr036_failure({"error_code": "migration_in_progress"})
    assert is_fr036_failure({"error": "repo already has active live migration (FR-036)"})


def test_fr036_operator_message():
    failures = [{
        "repo": "azure-pipelines/build-migration",
        "error": "repo already has active live migration (FR-036)",
        "error_code": "migration_in_progress",
    }]
    msg = fr036_operator_message(failures)
    assert msg is not None
    assert "live" in msg.lower()
    assert "build-migration" in msg


def test_failures_require_operator_escalation_for_fr036():
    failures = [{"error_code": "migration_in_progress", "repo": "p/r"}]
    assert failures_require_operator_escalation(failures) is True


def test_executor_repo_lock_does_not_advance_queue_index():
    session: dict = {"session_id": "ses_current", "messages": []}
    migration_queue = {
        "items": [{"repo_id": "proj/app", "work_items": []}],
        "current_index": 0,
        "completed": [],
        "failed": [],
    }
    result = _executor_result_for_repo_lock(
        session,
        repo_id="proj/app",
        lock_holder_session_id="ses_other",
        dry_run=True,
        iteration=1,
        migration_queue=migration_queue,
        failed=migration_queue["failed"],
    )
    assert migration_queue["current_index"] == 0
    assert result["executor_result"]["failures"][0]["lock_holder_session_id"] == "ses_other"


@pytest.mark.asyncio
async def test_planner_routes_lock_failure_to_orchestrator():
    session = {"dry_run": True, "plan_approved": True}
    failure = build_repo_lock_failure("proj/app", "ses_other")
    state = {
        "validation_result": {
            "passed": False,
            "failures": [failure],
            "dry_run": True,
        },
        "validation_feedback": {
            "failures": [failure],
            "retry_recommended": False,
            "escalate": True,
        },
        "session": session,
    }
    result = await _planner_post_validation_handoff(state, session)
    assert result is not None
    assert result["planner_next"] == "orchestrator"
    assert result.get("start_execution") is False
    assert "start_execution" not in session
