"""Tests for generic operator-input requests."""
import pytest

from ado2gh.agents.migration_agent.blockers import outstanding_blockers
from ado2gh.agents.migration_agent.operator_input import (
    assess_operator_input_needed,
    operator_input_from_blockers,
    operator_input_from_validator_failures,
    operator_input_to_form,
)
from ado2gh.models import MigrationScope


def _pipeline_blocked_item(**overrides):
    item = {
        "scope": MigrationScope.PIPELINES.value,
        "label": "Proj/app — Convert pipelines → GitHub Actions",
        "status": "blocked",
        "blocker": "No pipelines in inventory — run pipeline inventory first",
        "repo": "Proj/app",
    }
    item.update(overrides)
    return item


def test_operator_input_from_plan_blockers():
    plan = {"work_items": [_pipeline_blocked_item()]}
    session: dict = {}
    blockers = outstanding_blockers(plan, session)
    request = operator_input_from_blockers(blockers, session)
    assert request.source == "planner"
    assert request.fields[0].name == "resolution"
    assert "run_pipeline_inventory" in request.fields[0].options
    form = operator_input_to_form(request)
    assert form["form_id"].startswith("operator_input_")


def test_assess_operator_input_prefers_validator_failures():
    plan = {"work_items": []}
    session = {"plan_repository_id": "Proj/app"}
    feedback = {
        "failures": [
            {
                "specific_failure": "Service connection mapping missing",
                "recommended_remediation": "Operator must map secrets manually",
            }
        ]
    }
    request = assess_operator_input_needed(
        plan=plan,
        validation_feedback=feedback,
        session=session,
    )
    assert request is not None
    assert request.source == "validator"


def test_blockers_from_baseline_probes():
    from ado2gh.agents.migration_agent.operator_input import (
        blockers_from_baseline_probes,
        parse_github_target_probe,
    )

    assert parse_github_target_probe(error="404 Not Found").get("absent_expected") is True
    blockers = blockers_from_baseline_probes([
        {
            "repo": "proj/repo",
            "github_org": "org",
            "github_repo": "repo",
            "ado_repo": {"id": "abc"},
            "github_target": parse_github_target_probe(error="404 Not Found"),
        },
    ])
    assert blockers == []


def test_validator_failures_without_operator_hints_return_none():
    failures = [{"error": "transient network timeout"}]
    assert operator_input_from_validator_failures(failures, {}) is None


@pytest.mark.asyncio
async def test_validation_escalation_presents_operator_form():
    from ado2gh.agents.migration_agent.nodes import _present_validation_failure_to_operator
    from ado2gh.agents.migration_agent.operator_input import (
        operator_input_from_probe_failures,
        store_operator_input,
    )

    session: dict = {"plan_repository_id": "proj/repo", "dry_run": True, "messages": []}
    blockers = [{
        "repo": "proj/repo",
        "scope": "repo",
        "blocker": "ADO repository could not be verified: 404",
        "key": "repo:proj_repo:probe_failed",
    }]
    store_operator_input(
        session,
        operator_input_from_probe_failures(blockers, session, source="validator"),
    )
    state = {
        "session": session,
        "validation_result": {
            "passed": False,
            "failures": [{
                "operator_input_required": True,
                "specific_failure": "ADO and GitHub repositories are not found (404 errors)",
            }],
        },
        "pev_retry_count": 3,
    }
    session["pev_max_retries_exhausted"] = True
    result = await _present_validation_failure_to_operator(state, session)
    assert result is not None
    assert result.get("pending_form")
    assert session.get("pending_form")
    assert result.get("should_return") is True


def test_validator_endpoint_404_failures_do_not_need_operator_input():
    failures = [
        {
            "error": "404 Client Error for url http://localhost:8080/v1/migrate/wiki",
            "scope": "wiki",
        }
    ]
    assert operator_input_from_validator_failures(failures, {}) is None
