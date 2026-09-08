"""Unified live execution approval queue tests."""
import logging

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _bootstrap(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _create_user(client: TestClient, username: str, role: str) -> None:
    client.post(
        "/v1/auth/users",
        json={
            "username": username,
            "password": "twelve-char-pass",
            "role": role,
            "display_name": username,
        },
    )


def test_operator_migrate_live_blocked_until_approval(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    login = client.post(
        "/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"},
    )
    assert login.status_code == 200
    assert client.get("/v1/auth/session").json()["user"]["role"] == "operator"
    r = client.post(
        "/v1/migrate",
        json={"config_path": "migration.yaml", "dry_run": False, "wave_id": 1},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "awaiting_approval"
    assert detail["approval_id"].startswith("lve_")


def test_create_and_approve_agent_session_approval(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    _create_user(client, "ap1", "approver")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    created = client.post(
        "/v1/platform/approvals",
        json={
            "scope_type": "agent_session",
            "scope_id": "sess_test01",
            "reason_request": "Ready for live",
        },
    )
    assert created.status_code == 200
    approval_id = created.json()["id"]
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "ap1", "password": "twelve-char-pass"})
    with patch("ado2gh.api.live_approval_store.httpx.post") as mock_post:
        approved = client.post(
            f"/v1/platform/approvals/{approval_id}/approve",
            json={"reason": "Approved for POC"},
        )
        mock_post.assert_called()
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_duplicate_pending_is_idempotent(client):
    _bootstrap(client)
    body = {
        "scope_type": "agent_session",
        "scope_id": "sess_dup",
        "reason_request": "first",
    }
    first = client.post("/v1/platform/approvals", json=body)
    second = client.post("/v1/platform/approvals", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_deny_agent_session_approval(client):
    _bootstrap(client)
    created = client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "sess_deny", "reason_request": "live"},
    )
    approval_id = created.json()["id"]
    denied = client.post(
        f"/v1/platform/approvals/{approval_id}/deny",
        json={"reason": "Not ready"},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"


def test_failed_agent_notify_is_recorded_not_swallowed(client, tmp_path, monkeypatch, caplog):
    """A 401 from the agent must not leave the operator looking at a clean approval.

    The agent's /v1/internal/ range fails closed on a missing or mismatched
    ADO2GH_INTERNAL_TOKEN, so the notify that resumes the session can be rejected
    outright. The decision still stands, but the session parks until someone
    notices — which they only can if the failure is on the record.
    """
    monkeypatch.setenv("ADO2GH_INTERNAL_TOKEN", "shared-secret-value")
    _bootstrap(client)
    created = client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "sess_notify_fail"},
    )
    approval_id = created.json()["id"]

    unauthorized = MagicMock(status_code=401, is_success=False)
    with patch("ado2gh.api.live_approval_store.httpx.post", return_value=unauthorized):
        with caplog.at_level(logging.ERROR, logger="ado2gh.api.live_approval_store"):
            approved = client.post(
                f"/v1/platform/approvals/{approval_id}/approve",
                json={"reason": "go live"},
            )

    # The decision is already persisted, so the caller still gets a clean 200 ...
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    # ... but the failed notify is surfaced in both places an operator looks.
    assert "401" in caplog.text, "a rejected notify left no error in the log"
    events = create_state_db(str(tmp_path / "approvals.db")).search_audit_events(
        event_type="platform.live_execution.notify_failed", limit=10,
    )
    assert events, (
        "the agent rejected the resume notification and the approval was still "
        "recorded as a plain success — nothing tells the operator the session is stuck"
    )
    assert events[0]["actor"] == "admin"
    # CA-003: the shared secret never reaches a log line or an audit payload.
    assert "shared-secret-value" not in caplog.text
    assert "shared-secret-value" not in events[0]["payload_json"]


def test_approve_idempotent_conflict(client):
    _bootstrap(client)
    created = client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "sess_idem", "reason_request": "go"},
    )
    approval_id = created.json()["id"]
    first = client.post(
        f"/v1/platform/approvals/{approval_id}/approve",
        json={"reason": "once"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/platform/approvals/{approval_id}/approve",
        json={"reason": "twice"},
    )
    assert second.status_code == 409
