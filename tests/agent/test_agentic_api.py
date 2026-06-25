"""Agentic API route tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.api.agentic_routes import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_create_assignment(client):
    r = client.post(
        "/v1/profiles/p1/assignments",
        json={
            "name": "POC",
            "assignment_type": "poc",
            "execution_phase_id": "poc",
            "repos": [{"ado_project": "P", "ado_repo": "r1"}],
        },
        params={"actor": "coordinator"},
    )
    assert r.status_code == 200
    assert r.json()["repo_count"] == 1


def test_workflow_readiness(client):
    r = client.post(
        "/v1/workflow-readiness",
        json={"repo": "P/r1", "missing_secrets": ["TOKEN"], "missing_envs": []},
    )
    assert r.status_code == 200
    data = r.json()
    assert not data["ready"]
    assert data["log_lines"]


def test_rollback_dry_run(client):
    r = client.post(
        "/v1/rollback",
        json={
            "repos": ["P/r1"],
            "scopes": ["pipelines"],
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


def test_list_assignments_and_audit(client):
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
    listed = client.get("/v1/profiles/p1/assignments")
    assert listed.status_code == 200
    assert len(listed.json()["assignments"]) >= 1
    audit = client.get("/v1/profiles/p1/audit-events")
    assert audit.status_code == 200
