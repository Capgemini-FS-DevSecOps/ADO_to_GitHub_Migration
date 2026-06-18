"""Audit history RBAC — operators see only their own events."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.api.agentic_routes import router
from ado2gh.api.audit_access import resolve_audit_actor_filter
from ado2gh.assignments.audit import AuditWriter
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def authed_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    db_path = tmp_path / "audit_rbac.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", "AdminPass12345!", "Admin")
    svc.create_user("operator1", "OpPass12345!", PlatformRole.OPERATOR, "Operator One")

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request, call_next):
        from ado2gh.auth.service import permissions_for

        token = request.cookies.get("ado2gh_session", "")
        session = svc.get_session(token)
        if session:
            request.state.platform_user = session.user
            request.state.permissions = permissions_for(session.user.role)
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    accel = TestClient(accel_app)

    admin_login = accel.post("/v1/auth/login", json={"username": "admin", "password": "AdminPass12345!"})
    op_login = accel.post("/v1/auth/login", json={"username": "operator1", "password": "OpPass12345!"})
    return {
        "client": client,
        "admin_cookie": admin_login.cookies.get("ado2gh_session"),
        "operator_cookie": op_login.cookies.get("ado2gh_session"),
        "db_path": db_path,
    }


def test_resolve_audit_actor_filter_operator_forced(monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    user = PlatformUser("u1", "operator1", PlatformRole.OPERATOR, "Operator One")
    assert resolve_audit_actor_filter(user, "admin") == "operator1"


def test_resolve_audit_actor_filter_admin_honors_request(monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    user = PlatformUser("u1", "admin", PlatformRole.ADMIN, "Admin")
    assert resolve_audit_actor_filter(user, "operator1") == "operator1"


def test_operator_history_scoped_to_self(authed_client):
    db = create_state_db(str(authed_client["db_path"]))
    writer = AuditWriter(db)
    writer.write("user.login", "p1", actor="operator1")
    writer.write("user.login", "p1", actor="admin")

    client = authed_client["client"]
    client.cookies.set("ado2gh_session", authed_client["operator_cookie"])
    hist = client.get("/v1/history/sessions?profile_id=p1")
    assert hist.status_code == 200
    body = hist.json()
    assert body["can_view_all"] is False
    assert body["scoped_to_actor"] == "operator1"
    assert body["total"] == 1
    assert body["sessions"][0]["actor"] == "operator1"


def test_admin_history_sees_all(authed_client):
    db = create_state_db(str(authed_client["db_path"]))
    writer = AuditWriter(db)
    writer.write("user.login", "p1", actor="operator1")
    writer.write("user.login", "p1", actor="admin")

    client = authed_client["client"]
    client.cookies.set("ado2gh_session", authed_client["admin_cookie"])
    hist = client.get("/v1/history/sessions?profile_id=p1")
    assert hist.status_code == 200
    body = hist.json()
    assert body["can_view_all"] is True
    assert body["total"] == 2
