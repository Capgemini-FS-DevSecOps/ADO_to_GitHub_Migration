"""Unified live execution approval queue tests."""
import logging

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from ado2gh.auth.service import AuthService
from ado2gh.core.scopes.base import ScopeResult
from ado2gh.state.audit_query import AuditEventFilters
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
        AuditEventFilters(event_type="platform.live_execution.notify_failed"), limit=10,
    )
    assert events, (
        "the agent rejected the resume notification and the approval was still "
        "recorded as a plain success — nothing tells the operator the session is stuck"
    )
    assert events[0]["actor"] == "admin"
    # The internal shared secret must never reach a log line or an audit payload (CA-003).
    assert "shared-secret-value" not in caplog.text
    assert "shared-secret-value" not in events[0]["payload_json"]


_GIT_MIRROR_LIVE = {
    "project": "Proj", "repo_name": "repo",
    "github_org": "acme", "github_repo": "repo", "dry_run": False,
}


def test_feature_route_approval_is_executable_and_audited(client, tmp_path):
    """Approving a ``/v1/migrate/*`` live run must decide it, not 500 (GAP-067).

    ``migrate_guard`` queues these under ``scope_type="migrate_job"`` with a context
    naming the route, while the registered migrate executor builds a
    ``RunWaveRequest`` from whatever context it is handed. For a feature route that
    context has no ``config_path``, so approving raised ``ValidationError`` out of
    ``_resume_scope`` — after the decision row was committed and before the
    ``platform.live_execution.approved`` audit was written. The approver saw a 500,
    the approval stood, and nothing recorded who granted it (CA-004).

    What a feature-route approval buys is the grant: the guard admits the caller's
    retry of that one route. So the round trip below is approve, then replay.
    """
    _bootstrap(client)
    _create_user(client, "op2", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op2", "password": "twelve-char-pass"})

    parked = client.post("/v1/migrate/git-mirror", json=_GIT_MIRROR_LIVE)
    assert parked.status_code == 403, (
        f"the live feature route was not parked for approval (HTTP {parked.status_code})"
    )
    approval_id = parked.json()["detail"]["approval_id"]

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "admin", "password": "twelve-char-pass"})
    approved = client.post(
        f"/v1/platform/approvals/{approval_id}/approve",
        json={"reason": "GAP-067: release the feature-route migration"},
    )

    assert approved.status_code == 200, (
        f"approving a feature-route live migration failed with HTTP "
        f"{approved.status_code}; the decision is already committed, so the approver "
        f"cannot retry and cannot tell whether it took"
    )
    events = create_state_db(str(tmp_path / "approvals.db")).search_audit_events(
        AuditEventFilters(event_type="platform.live_execution.approved"), limit=10,
    )
    assert any(approval_id in (e["payload_json"] or "") for e in events), (
        "the approval was granted with no platform.live_execution.approved record; "
        "nothing names who released the live migration"
    )

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op2", "password": "twelve-char-pass"})
    with patch("services.accelerator_api.routes.migrate_routes._get_clients") as clients:
        clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        with patch(
            "services.accelerator_api.routes.migrate_routes._load_global_cfg",
            return_value={"migration_strategy": "mirror"},
        ):
            with patch(
                "services.accelerator_api.routes.migrate_routes._state_db",
                return_value=MagicMock(),
            ):
                with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler:
                    handler.return_value.migrate.return_value = ScopeResult(
                        stats={"dry_run": False, "strategy": "mirror"}, failed=0,
                    )
                    replayed = client.post("/v1/migrate/git-mirror", json=_GIT_MIRROR_LIVE)

    assert replayed.status_code == 200, (
        f"the approved caller was still refused its own live migration (HTTP "
        f"{replayed.status_code}); the approval bought nothing"
    )


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
