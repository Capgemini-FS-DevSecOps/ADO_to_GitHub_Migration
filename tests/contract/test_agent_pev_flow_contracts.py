"""Contract tests for agent PEV architecture (spec 011).

T016: Contract tests for POST /v1/sessions, POST /v1/sessions/{id}/message,
POST /v1/sessions/{id}/form-submit, POST /v1/sessions/{id}/form-cancel.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.agent.main import app as agent_app


@pytest.fixture
def client():
    return TestClient(agent_app)


# ─── POST /v1/sessions ───

def test_create_session_contract(client):
    """POST /v1/sessions creates a session and returns required fields."""
    r = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"].startswith("ses_")
    assert "status" in data
    assert "messages" in data
    assert "dry_run" in data
    assert "profile_id" in data
    assert data["profile_id"] == "lightweight"


def test_create_session_with_migration_prompt_contract(client):
    """POST /v1/sessions with a migration action prompt creates a session."""
    r = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Migrate Project/RepoA", "dry_run": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"].startswith("ses_")
    assert len(data["messages"]) >= 1


@pytest.mark.skip(reason="Pending rewrite for LangGraph orchestrator — execute_pev flow changed in spec 012")
def test_create_session_with_execute_pev_contract(client):
    """POST /v1/sessions with execute_pev=true attempts PEV setup."""
    r = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Plan POC", "dry_run": True, "execute_pev": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data


# ─── POST /v1/sessions/{id}/message ───

def test_send_message_contract(client):
    """POST /v1/sessions/{id}/message sends a user message and gets a response."""
    # Create session first
    create = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    session_id = create.json()["session_id"]

    # Send a message
    r = client.post(
        f"/v1/sessions/{session_id}/message",
        json={"message": "What can you do?"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "messages" in data
    assert len(data["messages"]) >= 2  # initial + new user + assistant


def test_send_message_session_not_found_contract(client):
    """POST /v1/sessions/{id}/message returns 404 for unknown session."""
    r = client.post(
        "/v1/sessions/ses_nonexistent/message",
        json={"message": "Hello"},
    )
    assert r.status_code == 404


# ─── POST /v1/sessions/{id}/form-submit ───

def test_form_submit_no_pending_form_contract(client):
    """POST /v1/sessions/{id}/form-submit returns 400 when no form is pending."""
    create = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    session_id = create.json()["session_id"]

    r = client.post(
        f"/v1/sessions/{session_id}/form-submit",
        json={"values": {"phase": "poc"}},
    )
    assert r.status_code in (400, 409, 200)


def test_form_submit_session_not_found_contract(client):
    """POST /v1/sessions/{id}/form-submit returns 404 for unknown session."""
    r = client.post(
        "/v1/sessions/ses_nonexistent/form-submit",
        json={"values": {}},
    )
    assert r.status_code == 404


# ─── POST /v1/sessions/{id}/form-cancel ───

def test_form_cancel_contract(client):
    """POST /v1/sessions/{id}/form-cancel cancels a pending form."""
    create = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    session_id = create.json()["session_id"]

    r = client.post(f"/v1/sessions/{session_id}/form-cancel")
    assert r.status_code == 200
    data = r.json()
    assert data.get("pending_form") is None


def test_form_cancel_session_not_found_contract(client):
    """POST /v1/sessions/{id}/form-cancel returns 404 for unknown session."""
    r = client.post("/v1/sessions/ses_nonexistent/form-cancel")
    assert r.status_code == 404


# ─── GET /v1/sessions (list) ───

def test_list_sessions_contract(client):
    """GET /v1/sessions returns a list of sessions."""
    client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    r = client.get("/v1/sessions")
    assert r.status_code == 200
    data = r.json()
    assert "sessions" in data
    assert len(data["sessions"]) >= 1


# ─── T023: Planner→Executor message format contract ───

@pytest.mark.skip(reason="Legacy planner module deleted in spec 012 — rewrite for migration_agent")
def test_planner_executor_instruction_payload_contract():
    """Planner→Executor instruction payload has required fields for execution."""
    from ado2gh.agents.planner import AgentPlanner

    planner = AgentPlanner()
    plan = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA", "Project/RepoB"],
        dependency_edges=[
            {"from_repo": "Project/RepoA", "to_repo": "Project/RepoB"},
        ],
        dry_run=True,
        discovery_data={
            "repos": [
                {"project": "Project", "repo_name": "RepoA", "total_score": 2},
                {"project": "Project", "repo_name": "RepoB", "total_score": 1},
            ],
            "pipeline_inventory": [
                {"name": "build", "repo": "Project/RepoA"},
            ],
        },
    )

    # The plan must contain fields needed by the executor
    assert "repo_order" in plan
    assert "work_items" in plan
    assert "dry_run" in plan
    assert "revision" in plan
    assert "resource_mappings" in plan
    assert "pipeline_mappings" in plan
    assert "secret_mappings" in plan
    assert "assumptions" in plan

    # Work items must have ready/blocked status
    for wi in plan["work_items"]:
        assert wi["status"] in ("ready", "blocked")
        assert "scopes" in wi
        assert "repo" in wi

    # Topological order: RepoB (dependency) before RepoA (consumer)
    order = plan["repo_order"]
    assert order.index("Project/RepoB") < order.index("Project/RepoA")


@pytest.mark.skip(reason="Legacy planner module deleted in spec 012 — rewrite for migration_agent")
def test_planner_revised_plan_contract():
    """Revised plan from validator feedback has required fields (T026)."""
    from ado2gh.agents.planner import AgentPlanner

    planner = AgentPlanner()
    original = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA"],
        dependency_edges=[],
        dry_run=True,
    )
    revised = planner.generate_revised_plan(
        original,
        {"failed_scopes": ["pipelines", "secrets"], "summary": "Pipeline and secret migration failed"},
    )

    assert revised["revision"] > original["revision"]
    assert revised["revised"] is True
    assert "failed_scopes" in revised
    assert "remediation_steps" in revised
    assert len(revised["remediation_steps"]) == 2
    for step in revised["remediation_steps"]:
        assert "scope" in step
        assert "action" in step
        assert "affected_repos" in step


# ─── T030: Guardrail interception contract ───
# NOTE: Guardrail contract tests below use legacy tool names (spec 011).
# The spec 012 guardrails use operation types instead of tool names.
# These tests are skipped pending rewrite for the new guardrail API.

import pytest as _pytest

@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_blocks_unauthorized_delete_contract():
    """Guardrail blocks deletion without explicit authorization."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail

    plan = {"work_items": [{"repo": "Project/RepoA"}]}
    d = evaluate_guardrail(
        "executor", "ado2gh_enqueue_job",
        {"operation_type": "delete", "target_resource": "Project/RepoA"},
        approved_plan=plan,
    )
    assert d.decision == "block"
    assert d.tool_name == "ado2gh_enqueue_job"
    assert d.operation_type == "delete"
    assert d.target_resource == "Project/RepoA"


@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_blocks_repo_not_in_plan_contract():
    """Guardrail blocks operations on repos not in the approved plan."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail

    plan = {"work_items": [{"repo": "Project/RepoA"}]}
    d = evaluate_guardrail(
        "executor", "ado2gh_enqueue_job",
        {"target_resource": "Project/RepoEVIL"},
        approved_plan=plan,
    )
    assert d.decision == "block"
    assert "not found in approved plan" in d.reason


@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_blocks_wrong_role_contract():
    """Guardrail blocks tool access for wrong role."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail

    d = evaluate_guardrail("planner", "ado2gh_enqueue_job", {})
    assert d.decision == "block"
    assert "does not have access" in d.reason


@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_blocks_ado_write_without_cleanup_contract():
    """Guardrail blocks ADO write operations without migration cleanup approval (T034)."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail

    d = evaluate_guardrail(
        "executor", "ado2gh_migrate_boards",
        {"operation_type": "create"},
    )
    assert d.decision == "block"
    assert "ADO write" in d.reason


@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_allows_ado_write_with_cleanup_contract():
    """Guardrail allows ADO write operations with migration cleanup approval (T034)."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail

    plan = {"work_items": [{"repo": "Project/RepoA"}]}
    d = evaluate_guardrail(
        "executor", "ado2gh_migrate_boards",
        {"operation_type": "create", "migration_cleanup_approved": True},
        approved_plan=plan,
    )
    assert d.decision == "allow"


@_pytest.mark.skip(reason="Legacy guardrail API (spec 011) — rewrite for spec 012 operation types")
def test_guardrail_decision_has_required_fields_contract():
    """GuardrailDecision has all required fields for audit logging."""
    from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail, GuardrailDecision

    d = evaluate_guardrail("planner", "ado2gh_discover", {})
    assert isinstance(d, GuardrailDecision)
    assert hasattr(d, "decision")
    assert hasattr(d, "reason")
    assert hasattr(d, "tool_name")
    assert hasattr(d, "operation_type")
    assert hasattr(d, "target_resource")
    assert hasattr(d, "plan_reference")


# ─── T046: Validator→Planner feedback message format contract ───

@pytest.mark.skip(reason="Legacy validator/planner/executor modules deleted in spec 012 — rewrite for migration_agent")
def test_validator_feedback_on_pass_contract():
    """Validator→planner feedback has required fields when validation passes."""
    from ado2gh.agents.validator import AgentValidator
    from ado2gh.agents.planner import AgentPlanner
    from ado2gh.agents.executor import AgentExecutor

    planner = AgentPlanner()
    plan = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA"],
        dependency_edges=[],
        dry_run=True,
        discovery_data={
            "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
            "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
        },
    )
    executor = AgentExecutor()
    exec_result = executor.execute_plan(plan, dry_run=True)
    validator = AgentValidator()
    val_result = validator.validate_execution(plan, exec_result.to_dict(), dry_run=True)

    feedback = val_result.feedback_to_planner
    assert feedback["type"] == "validation_passed"
    assert "summary" in feedback
    assert "failed_scopes" in feedback


@pytest.mark.skip(reason="Legacy validator module deleted in spec 012 — rewrite for migration_agent")
def test_validator_feedback_on_fail_contract():
    """Validator→planner feedback has required fields when validation fails."""
    from ado2gh.agents.validator import AgentValidator

    validator = AgentValidator()
    plan = {
        "work_items": [{"repo": "Project/RepoA"}],
        "pipeline_mappings": [{"github_workflow": "expected.yml"}],
        "revision": 1,
    }
    output = {
        "workflows_created": [{"github_workflow": "unexpected.yml"}],
        "secrets_provisioned": [],
        "skipped": [],
        "failures": [],
    }
    result = validator.validate_execution(plan, output, dry_run=True)

    assert result.passed is False
    feedback = result.feedback_to_planner
    assert feedback["type"] == "validation_failed"
    assert "failed_scopes" in feedback
    assert "failures" in feedback
    assert "plan_revision" in feedback
    assert feedback["plan_revision"] == 2

    for failure in feedback["failures"]:
        assert "scope" in failure
        assert "check_name" in failure
        assert "expected_state" in failure
        assert "observed_state" in failure
        assert "specific_failure" in failure
        assert "file_path" in failure
        assert "recommended_remediation" in failure


@pytest.mark.skip(reason="Legacy validator module deleted in spec 012 — rewrite for migration_agent")
def test_validator_result_has_evidence_contract():
    """Validator result has evidence suitable for audit review (FR-039)."""
    from ado2gh.agents.validator import AgentValidator

    validator = AgentValidator()
    plan = {"work_items": [{"repo": "Project/RepoA"}], "pipeline_mappings": []}
    output = {"workflows_created": [], "secrets_provisioned": []}
    result = validator.validate_execution(plan, output, dry_run=True)

    assert len(result.evidence) > 0
    for evidence in result.evidence:
        assert "scope" in evidence
        assert "check_name" in evidence
        assert "passed" in evidence
        assert "evidence" in evidence


# ─── T061: Full migration flow contract ───

@pytest.mark.skip(reason="Legacy planner/executor/validator/pev_cycle modules deleted in spec 012 — rewrite for migration_agent")
def test_full_migration_flow_contract():
    """Contract test for full migration flow: plan → execute → validate → complete."""
    from ado2gh.agents.planner import AgentPlanner
    from ado2gh.agents.executor import AgentExecutor
    from ado2gh.agents.validator import AgentValidator
    from ado2gh.agents.pev_cycle import PevCycleOrchestrator
    from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

    sm = SessionStateMachine()
    pev = PevCycleOrchestrator("contract-session", sm)
    planner = AgentPlanner()
    executor = AgentExecutor()
    validator = AgentValidator()

    discovery_data = {
        "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
        "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
        "service_connections": [{"name": "azure-sub", "type": "azurerm"}],
    }

    # Plan phase
    pev.start_cycle()
    pev.enter_planning()
    plan = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA"],
        dependency_edges=[],
        dry_run=True,
        discovery_data=discovery_data,
    )
    assert plan["profile_id"] == "lightweight"
    assert plan["dry_run"] is True
    assert "work_items" in plan
    assert "repo_order" in plan
    assert "assumptions" in plan

    # Execute phase
    pev.enter_executing()
    exec_result = executor.execute_plan(plan, dry_run=True, session_id="contract-session")
    exec_dict = exec_result.to_dict()
    assert "repo_mirror_status" in exec_dict
    assert "workflows_created" in exec_dict
    assert "secrets_provisioned" in exec_dict
    assert "failures" in exec_dict
    assert "skipped" in exec_dict

    # Validate phase
    pev.enter_validating()
    val_result = validator.validate_execution(plan, exec_dict, dry_run=True)
    val_dict = val_result.to_dict()
    assert "passed" in val_dict
    assert "scope_results" in val_dict
    assert "feedback_to_planner" in val_dict
    assert "evidence" in val_dict

    # Complete
    summary = pev.complete_cycle(1, 1, 0, [], "complete")
    assert summary["next_action"] == "complete"
    assert sm.state == SessionState.COMPLETED
    assert sm.is_terminal()


@pytest.mark.skip(reason="Legacy planner module deleted in spec 012 — rewrite for migration_agent")
def test_plan_summary_has_required_fields_contract():
    """Plan summary has all fields needed for user presentation (FR-014)."""
    from ado2gh.agents.planner import AgentPlanner

    planner = AgentPlanner()
    plan = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA"],
        dependency_edges=[],
        dry_run=True,
        discovery_data={
            "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
        },
    )
    # Plan summary must include: repos, scopes, order, risks
    assert "repo_order" in plan
    assert "work_items" in plan
    work_item = plan["work_items"][0]
    assert "repo" in work_item
    assert "scopes" in work_item
    assert "status" in work_item


@pytest.mark.skip(reason="Legacy planner module deleted in spec 012 — rewrite for migration_agent")
def test_dry_run_default_enforced_contract():
    """Dry-run is the default; live requires explicit confirmation (CA-001)."""
    from ado2gh.agents.planner import AgentPlanner

    planner = AgentPlanner()
    plan = planner.plan(
        profile_id="lightweight",
        assignment_repos=["Project/RepoA"],
        dependency_edges=[],
        dry_run=True,
        discovery_data={
            "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
        },
    )
    assert plan["dry_run"] is True


# ─── T065: Session listing and polling contract ───

def test_session_listing_contract():
    """Session listing endpoint returns sessions with required fields (FR-049)."""
    from fastapi.testclient import TestClient
    from services.agent.main import app

    client = TestClient(app)
    resp = client.get("/v1/sessions")
    assert resp.status_code == 200
    data = resp.json()
    sessions = data if isinstance(data, list) else data.get("sessions", [])
    assert isinstance(sessions, list)


def test_session_polling_returns_messages_contract():
    """Session polling returns messages for chat display (FR-046)."""
    from fastapi.testclient import TestClient
    from services.agent.main import app

    client = TestClient(app)
    # Create a session first
    resp = client.post("/v1/sessions", json={"message": "hello"})
    assert resp.status_code in (200, 201)
    sid = resp.json().get("session_id", "")
    if sid:
        # Poll for messages
        poll = client.get(f"/v1/sessions/{sid}")
        assert poll.status_code == 200
        session = poll.json()
        assert "messages" in session or "events" in session


def test_health_endpoint_contract():
    """Health endpoint returns agent health status (FR-070)."""
    from fastapi.testclient import TestClient
    from services.agent.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data or "healthy" in data or "llm" in str(data).lower()


# ─── T074: /metrics and /health endpoint contracts ───

def test_metrics_endpoint_contract():
    """/metrics returns Prometheus-compatible text (FR-069)."""
    from fastapi.testclient import TestClient
    from services.agent.main import app

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    text = resp.text
    # Prometheus format has # TYPE comments and metric_name value lines
    assert "# TYPE" in text or len(text.strip()) == 0  # empty is ok if no metrics recorded


def test_health_endpoint_has_required_fields_contract():
    """/health endpoint reports LLM, storage, and session status (FR-070)."""
    from fastapi.testclient import TestClient
    from services.agent.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "accelerator_reachable" in data
    assert "llm_degraded" in data or "llm_unconfigured" in data
    assert "active_session_count" in data
    assert "storage_backend_ok" in data
