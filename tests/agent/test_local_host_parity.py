"""HTTP dry-run guardrail default."""
from fastapi.testclient import TestClient

from services.agent.main import app


client = TestClient(app)


def test_http_session_defaults_dry_run():
    r = client.post("/v1/sessions", json={"profile_id": "lightweight"})
    assert r.json()["dry_run"] is True
