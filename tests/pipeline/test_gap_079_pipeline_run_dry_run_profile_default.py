"""GAP-079 — an omitted ``dry_run`` on a console pipeline run ignored the profile default.

``PipelineRunStartRequest.dry_run`` was declared ``bool = True``. Pydantic fills in a
concrete default for every field it is not given, so ``req.dry_run`` could never be
``None`` — the fallback in ``start_pipeline_run``,
``req.dry_run if req.dry_run is not None else adv.dry_run_default``, was dead code, and a
console run that named no mode always ran as a dry run regardless of what an operator had
set the deployment's ``dry_run_default`` to.

The fix makes the field ``Optional[bool] = None``, matching the pattern already used by
``SessionRequest.dry_run`` (``services/agent/routes/_helpers.py``). The two tests below
prove the field is now genuinely three-state: an omitted body key defers to the profile
setting, and an explicit value still overrides it either way.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

ADMIN_PASSWORD = "AdminPass12345!"


@pytest.fixture
def operator_client(tmp_path, monkeypatch):
    """Authenticated OPERATOR against the accelerator, with the runner stubbed out."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap079.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from ado2gh.api.pipeline_runner import PipelineRunStore
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app
    from services.accelerator_api.routes import _shared

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", ADMIN_PASSWORD, "Admin")
    svc.create_user("operator1", "OpPass12345!", PlatformRole.OPERATOR, "Operator One")

    monkeypatch.setattr(_shared._settings, "path", tmp_path / "ui_settings.json")
    _shared._settings.setup_profile(
        {
            "name": "GAP-079 profile",
            "ado_org_url": "https://dev.azure.com/fake-org",
            "ado_pat": "fake-ado-pat",
            "gh_org": "fake-gh-org",
            "github_token": "fake-gh-token",
        },
        role="admin",
    )

    PipelineRunStore._runs.clear()
    client = TestClient(app)
    login = client.post(
        "/v1/auth/login", json={"username": "operator1", "password": "OpPass12345!"},
    )
    assert login.status_code == 200
    client.cookies.set("ado2gh_session", login.cookies.get("ado2gh_session"))

    with patch.object(_shared._runner, "start_async") as start_async:
        yield client, start_async, _shared._settings
    PipelineRunStore._runs.clear()


def test_omitted_dry_run_defers_to_profile_default(operator_client):
    """The core regression: no ``dry_run`` key at all must reach the profile default."""
    client, _start_async, settings = operator_client
    settings.update_advanced({"dry_run_default": False})

    resp = client.post(
        "/v1/pipeline/runs",
        json={"name": "GAP-079 run, dry_run omitted"},
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["run"]["dry_run"] is False, (
        "an omitted `dry_run` must defer to the deployment profile's "
        "`dry_run_default` rather than being hardcoded True; "
        f"got {resp.json()['run']['dry_run']!r}"
    )


def test_explicit_dry_run_still_overrides_the_profile_default(operator_client):
    """The field stays three-state: an explicit value wins over the profile default either way."""
    client, _start_async, settings = operator_client
    settings.update_advanced({"dry_run_default": False})

    resp = client.post(
        "/v1/pipeline/runs",
        json={"name": "GAP-079 run, dry_run explicit True", "dry_run": True},
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["run"]["dry_run"] is True, (
        "an explicit `dry_run: true` must not be overridden by the profile default; "
        f"got {resp.json()['run']['dry_run']!r}"
    )

    settings.update_advanced({"dry_run_default": True})

    resp = client.post(
        "/v1/pipeline/runs",
        json={"name": "GAP-079 run, dry_run explicit False", "dry_run": False},
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["run"]["dry_run"] is False, (
        "an explicit `dry_run: false` must not be overridden by the profile default; "
        f"got {resp.json()['run']['dry_run']!r}"
    )
