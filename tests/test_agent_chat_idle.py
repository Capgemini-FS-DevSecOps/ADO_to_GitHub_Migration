"""Agent chat sessions should not invoke accelerator until PEV is explicitly started."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from services.agent.main import app


def test_create_session_chat_only_does_not_call_accelerator():
    client = TestClient(app)
    with patch("services.agent.main._accel_post", new_callable=AsyncMock) as mock_post:
        r = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "idle"
    mock_post.assert_not_called()


def test_run_pev_endpoint_starts_accelerator_calls():
    client = TestClient(app)
    with patch("services.agent.main._accel_post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            {"waves": []},
            {"auto": 0},
            [{"wave_id": 1, "status": "ok"}],
            {"total": 0, "matched": 0},
        ]
        created = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
        )
        sid = created.json()["session_id"]
        run = client.post(f"/v1/sessions/{sid}/run-pev")
    assert run.status_code == 200
    assert run.json()["status"] == "planning"
    assert mock_post.await_count >= 1
