"""Agent PEV session API contract smoke tests."""
import pytest
from fastapi.testclient import TestClient

from services.agent.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_create_session(client):
    r = client.post("/v1/sessions", json={"profile_id": "p1", "dry_run": True})
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"].startswith("ses_")
