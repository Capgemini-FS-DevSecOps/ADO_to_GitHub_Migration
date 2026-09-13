"""Contract tests for 007 cloud credentials feature."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

CONTRACT_PATHS = [
    "specs/007-cloud-llm-credentials/contracts/cloud-credentials-api.md",
    "specs/007-cloud-llm-credentials/contracts/cloud-credentials-ui.md",
    "specs/007-cloud-llm-credentials/contracts/platform-supplied-llm.md",
    "specs/007-cloud-llm-credentials/contracts/agent-models-api.md",
]

FORBIDDEN_KEY = re.compile(
    r"(secret|password|token|api_key|client_secret|private_key)",
    re.IGNORECASE,
)


def test_contract_files_exist():
    root = Path(__file__).resolve().parents[2]
    for rel in CONTRACT_PATHS:
        assert (root / rel).is_file(), rel


def _assert_no_secrets(payload: object, *, forbidden_literal: str | None = None) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert not FORBIDDEN_KEY.search(key), f"forbidden key {key}"
            if forbidden_literal and isinstance(value, str):
                assert forbidden_literal not in value
            _assert_no_secrets(value, forbidden_literal=forbidden_literal)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secrets(item, forbidden_literal=forbidden_literal)


@pytest.fixture
def accel_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "contract.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _bootstrap_admin(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def test_scan_redacts_secrets(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)
    monkeypatch.setattr(
        "ado2gh.api.credentials.cloud_credential_detector._imds_reachable",
        lambda: False,
    )
    r = accel_client.post("/v1/settings/cloud-credentials/scan")
    assert r.status_code == 200
    data = r.json()
    _assert_no_secrets(data, forbidden_literal="supersecret")
    assert "AWS_SECRET_ACCESS_KEY" not in json.dumps(data)


def test_listing_never_probes_the_host(accel_client, monkeypatch):
    """The audited POST is the only probe: the GET reports the last detection (CA-004)."""
    _bootstrap_admin(accel_client)
    monkeypatch.setattr(
        "ado2gh.api.credentials.cloud_credential_detector._imds_reachable",
        lambda: False,
    )
    from ado2gh.api.credentials import cloud_credentials_store

    # Spy on the host probe itself, not on the store method the removed branch called, so
    # the test still fails if a probe is reintroduced anywhere under the listing route.
    probes: list[int] = []
    real_probe = cloud_credentials_store.scan_all_presence
    monkeypatch.setattr(
        cloud_credentials_store,
        "scan_all_presence",
        lambda: (probes.append(1), real_probe())[1],
    )

    # `?scan=true` is no longer a parameter; FastAPI ignores it and nothing is probed.
    assert accel_client.get("/v1/settings/cloud-credentials").status_code == 200
    assert accel_client.get(
        "/v1/settings/cloud-credentials", params={"scan": "true"}
    ).status_code == 200
    assert probes == []

    assert accel_client.post("/v1/settings/cloud-credentials/scan").status_code == 200
    assert probes, "the audited POST is the one that probes the host"


def test_approve_flow_contract(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)
    monkeypatch.setattr(
        "ado2gh.api.credentials.cloud_credential_detector._imds_reachable",
        lambda: False,
    )
    accel_client.post("/v1/settings/cloud-credentials/scan")
    monkeypatch.setattr(
        "ado2gh.api.credentials.cloud_credential_probe.probe_provider",
        lambda *_: {"status": "passed", "category": None, "message": "ok"},
    )
    r = accel_client.post("/v1/settings/cloud-credentials/aws/approve", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    _assert_no_secrets(r.json())


def test_operator_forbidden(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)
    reg = accel_client.post(
        "/v1/auth/register",
        json={
            "username": "operator",
            "password": "twelve-char-pass",
            "display_name": "Op",
        },
    )
    assert reg.status_code == 201
    users = accel_client.get("/v1/auth/users").json()["users"]
    user_id = next(u["id"] for u in users if u["username"] == "operator")
    assert accel_client.post(f"/v1/auth/users/{user_id}/approve").status_code == 200
    login = accel_client.post(
        "/v1/auth/login",
        json={"username": "operator", "password": "twelve-char-pass"},
    )
    assert login.status_code == 200
    r = accel_client.get("/v1/settings/cloud-credentials")
    assert r.status_code == 403
