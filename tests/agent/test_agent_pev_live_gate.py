"""Agent plan-execute-validate loop (PEV) live gate ordering tests."""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import RunStatus, app


@pytest.fixture
def accel_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "pev_gate.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(accel_app)


def test_pev_dry_run_completes_without_awaiting_approval():
    client = TestClient(app)
    with patch("services.agent.routes.session_routes._accel_post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            {"waves": []},
            {"auto": 0},
            [{"wave_id": 1, "status": "ok"}],
            {"total": 0, "matched": 0},
        ]
        created = client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "dry_run": True, "execute_pev": True},
        )
        sid = created.json()["session_id"]
    import time
    time.sleep(0.2)
    session = client.get(f"/v1/sessions/{sid}").json()
    assert session.get("run_status") != RunStatus.AWAITING_APPROVAL.value
