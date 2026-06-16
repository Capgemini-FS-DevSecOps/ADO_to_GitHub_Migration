"""Additional agentic and agent component tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.agents.executor import AgentExecutor
from ado2gh.agents.validator import AgentValidator
from ado2gh.api.agentic_routes import enforce_live_gate, router
from ado2gh.assignments.resolver import AssignmentResolver
from ado2gh.assignments.store import AssignmentStore
from ado2gh.assignments.models import AssignmentType
from ado2gh.state.db import StateDB


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "more.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_executor_success():
    ex = AgentExecutor({"ado2gh_enqueue_job": lambda p: {"ok": True}})
    out = ex.invoke("ado2gh_enqueue_job", {"dry_run": True}, approver_ok=False)
    assert out["result"]["ok"]


def test_validator_fail_and_remediate():
    v = AgentValidator()
    r = v.validate([{"overall": "FAIL"}], boards_gaps=[{"gap": 1}])
    assert not r["passed"]
    assert v.should_remediate(r, 0, 3)
    assert not v.should_remediate(r, 3, 3)


def test_resolver_wave(tmp_path):
    db = StateDB(str(tmp_path / "rw.db"))
    store = AssignmentStore(db)
    store.create(
        profile_id="p1",
        name="Wave 2",
        assignment_type=AssignmentType.WAVE,
        execution_phase="wave2",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
        wave_number=2,
    )
    a = AssignmentResolver(store).resolve("p1", "migrate wave 2 repos")
    assert a is not None
    assert a.wave_number == 2


def test_gate_override_and_status(client):
    created = client.post(
        "/v1/profiles/p1/assignments",
        json={
            "name": "POC",
            "assignment_type": "poc",
            "execution_phase_id": "poc",
            "repos": [{"ado_project": "P", "ado_repo": "r1"}],
        },
        params={"actor": "coordinator"},
    ).json()
    aid = created["id"]
    status = client.get(f"/v1/assignments/{aid}/gate-status")
    assert status.status_code == 200
    override = client.post(
        f"/v1/assignments/{aid}/gate-override",
        json={"reason": "sign-off", "profile_id": "p1", "actor": "approver"},
    )
    assert override.status_code == 200
    assert override.json()["can_advance"]


def test_dependency_graph_and_history(client):
    client.post(
        "/v1/profiles/p1/assignments",
        json={
            "name": "POC",
            "assignment_type": "poc",
            "execution_phase_id": "poc",
            "repos": [{"ado_project": "P", "ado_repo": "r1"}],
        },
        params={"actor": "coordinator"},
    )
    dg = client.get("/v1/profiles/p1/dependency-graph")
    assert dg.status_code == 200
    hist = client.get("/v1/history/sessions")
    assert hist.status_code == 200


def test_enforce_live_gate_blocks(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gateblock.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)
    from ado2gh.assignments.store import AssignmentStore
    store = AssignmentStore(StateDB(db_path))
    a = store.create(
        profile_id="p1",
        name="x",
        assignment_type=AssignmentType.POC,
        execution_phase="poc",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
    )
    from fastapi import HTTPException
    try:
        enforce_live_gate(a.id, False, db_path)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        pytest.fail("expected gate block")


def test_rollback_request_flow(client):
    req = client.post("/v1/rollback/request", json={"repos": ["P/r1"], "dry_run": True})
    assert req.status_code == 200
    rid = req.json()["request_id"]
    appr = client.post(
        "/v1/rollback/approve",
        json={"request_id": rid, "approved": False, "actor": "approver"},
    )
    assert appr.status_code == 200


def test_assignment_store_get_missing(tmp_path):
    db = StateDB(str(tmp_path / "miss.db"))
    store = AssignmentStore(db)
    assert store.get("missing") is None


def test_workflow_readiness_paths():
    from ado2gh.api.workflow_readiness import check_workflow_readiness
    ok = check_workflow_readiness("P/r1", [], [])
    assert ok["ready"]
    bad = check_workflow_readiness("P/r1", [], ["prod"])
    assert not bad["ready"]
