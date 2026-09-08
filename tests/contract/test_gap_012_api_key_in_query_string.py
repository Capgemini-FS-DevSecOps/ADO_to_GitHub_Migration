"""GAP-012 (GAP-UI-01): LLM provider API key is transmitted in a URL query string.

Reproduction from the gap register's evidence. The console's ``fetchCatalog``
(``apps/migration-ui/src/lib/llmSettings.ts:102-106``) builds a
``URLSearchParams``, calls ``query.set('api_key', params.apiKey)``, and requests
``/v1/settings/llm-models/catalog?<query>``. The accelerator route it targets
(``services/accelerator_api/routes/settings_routes.py:85-91``) declares
``api_key: str = ""`` on a ``@router.get`` handler, which FastAPI binds as a
**query parameter** -- so the provider key is placed in the request URL by
contract, not by accident.

A URL is the least private part of an HTTP request: it lands in browser history,
devtools/HAR exports, and every forward proxy or gateway access log along the
path. This is CWE-598 (use of GET request method with sensitive query strings)
and violates Principle V ("never log, commit or echo tokens/PATs").

The assertion is made against the accelerator's OpenAPI document -- the
authoritative statement of what the service accepts -- so it holds whichever way
T039 closes the gap (moving the key to a POST JSON body, to a header, or to a
server-side reference), and it does not depend on the handler keeping its name.
``tests/contract/test_llm_catalog_contracts.py::test_catalog_anthropic_live_contract``
currently asserts the opposite behaviour and will need updating with the fix.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

# Obviously-fake literal shaped like the real thing (CA-003 -- never a real credential).
FAKE_PROVIDER_KEY = "sk-ant-api03-FAKEKEYFORGAP012-not-a-real-credential"

# Matches whole underscore/dash-delimited words only, so `db_path` is not a hit.
SECRET_PARAM_NAME = re.compile(
    r"(?:^|[_-])(api[_-]?key|key|token|secret|password|pat|credentials?)(?:$|[_-])",
    re.IGNORECASE,
)


@pytest.fixture
def accel_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap012.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _bootstrap_admin(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _secret_query_params(client: TestClient) -> list[str]:
    spec = client.app.openapi()
    hits: list[str] = []
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            for param in operation.get("parameters") or []:
                if param.get("in") != "query":
                    continue
                if SECRET_PARAM_NAME.search(str(param.get("name", ""))):
                    hits.append(f"{method.upper()} {path}?{param['name']}")
    return hits


def test_no_accelerator_route_accepts_a_secret_as_a_query_parameter(accel_client):
    """The URL is logged everywhere; no credential may be declared as a query param."""
    assert _secret_query_params(accel_client) == []


def test_llm_catalog_does_not_declare_api_key_in_the_url(accel_client):
    spec = accel_client.app.openapi()
    catalog = spec["paths"]["/v1/settings/llm-models/catalog"]
    for method, operation in catalog.items():
        names = [
            p["name"] for p in (operation.get("parameters") or []) if p.get("in") == "query"
        ]
        assert "api_key" not in names, f"{method.upper()} still takes api_key in the URL"


def test_api_key_supplied_in_the_query_string_is_not_honoured(accel_client, monkeypatch):
    """Defence in depth: a key that reaches the URL must not be used from there."""
    _bootstrap_admin(accel_client)
    seen: list[dict] = []

    def _spy_list_catalog(**kwargs):
        seen.append(kwargs)
        return {"source": "fallback", "entries": []}

    monkeypatch.setattr(
        "ado2gh.api.llm.model_catalog.list_catalog",
        _spy_list_catalog,
    )
    accel_client.get(
        "/v1/settings/llm-models/catalog",
        params={"provider": "anthropic", "api_key": FAKE_PROVIDER_KEY},
    )
    assert FAKE_PROVIDER_KEY not in str(seen)
