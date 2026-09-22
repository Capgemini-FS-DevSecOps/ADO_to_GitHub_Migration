"""Regression check for register entry GAP-061 — a stored proxy password must be removable from the console.

The console sent ``proxy_password: proxyPassword || '***'`` on every save
(``apps/migration-ui/src/app/settings/connectivity/page.tsx``), so a blank box always sent
the keep-mask and a password stored once could never be taken away again — an operator who
rotated to an unauthenticated proxy was stuck with a credential the platform keeps using.

``ConnectivityStore.update`` already reads an empty string as "clear" (the same sentinel
``custom_ca_pem`` uses, and distinct from the ``***`` keep-mask), so the fix adds an
explicit, confirmed clear control in the console — covered by
``apps/migration-ui/src/lib/llmSettings.test.ts`` — and makes the route audit the clear,
which it previously filtered out along with the keep-mask (CA-004).

CA-003: every credential below is an obvious fake, and the assertions check that the value
never reaches the audit trail or a response.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.api.connectivity_store import ConnectivityStore
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

FAKE_PASSWORD = "not-a-real-proxy-pass"


@pytest.fixture
def store(tmp_path, monkeypatch) -> ConnectivityStore:
    """Connectivity store writing to an isolated data directory."""
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap061.db"))
    return ConnectivityStore()


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Accelerator client signed in as the bootstrapped admin, with its own state DB."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap061.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    test_client = TestClient(app)
    bootstrap = test_client.post(
        "/v1/auth/bootstrap",
        json={"username": "gap061-admin", "password": "not-a-real-pass-12345", "display_name": "Admin"},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    return test_client


def test_store_clears_the_password_on_the_empty_sentinel(store):
    store.update({"proxy_password": FAKE_PASSWORD}, actor="admin")

    store.update({"proxy_password": ""}, actor="admin")

    assert store.load().proxy_password == ""
    assert store.load().to_public()["proxy_password"] == ""


def test_store_still_keeps_the_password_for_a_blank_form_field(store):
    """The console sends the mask for a blank box, so an ordinary save must not wipe it."""
    store.update({"proxy_password": FAKE_PASSWORD}, actor="admin")

    store.update({"proxy_enabled": True, "proxy_password": "***"}, actor="admin")

    assert store.load().proxy_password == FAKE_PASSWORD


def test_clearing_through_the_route_removes_it_and_reports_it_gone(client):
    client.put("/v1/settings/connectivity", json={"proxy_password": FAKE_PASSWORD})

    cleared = client.put("/v1/settings/connectivity", json={"proxy_password": ""})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["proxy_password"] == ""
    assert client.get("/v1/settings/connectivity").json()["proxy_password"] == ""


def test_the_clear_is_audited_by_name_and_never_by_value(client, tmp_path):
    client.put("/v1/settings/connectivity", json={"proxy_password": FAKE_PASSWORD})

    assert client.put("/v1/settings/connectivity", json={"proxy_password": ""}).status_code == 200

    db = create_state_db(str(tmp_path / "gap061.db"))
    updates = [e for e in db.list_audit_events(limit=20) if e.get("event_type") == "connectivity.updated"]
    assert updates, "clearing a proxy password must leave an audit trail (CA-004)"
    assert "proxy_password" in str(updates[0]), "the cleared field must be named in the audit record"
    assert FAKE_PASSWORD not in str(updates), "no audit record may carry the credential (CA-003)"


def test_an_unchanged_secret_is_not_reported_as_changed(client, tmp_path):
    """The console sends ``custom_ca_pem: ''`` on every save while no CA is stored."""
    client.put(
        "/v1/settings/connectivity",
        json={"proxy_enabled": True, "proxy_password": "***", "custom_ca_pem": ""},
    )

    db = create_state_db(str(tmp_path / "gap061.db"))
    updates = [e for e in db.list_audit_events(limit=20) if e.get("event_type") == "connectivity.updated"]
    assert updates
    assert "proxy_password" not in str(updates[0])
    assert "custom_ca_pem" not in str(updates[0])
