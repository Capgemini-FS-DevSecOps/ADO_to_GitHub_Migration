"""A deployment profile's configured dry-run default could never reach a session.

``services/agent/routes/session_routes.py:59`` read
``req.dry_run if req.dry_run is not None else profile.dry_run_default``, but
``SessionRequest.dry_run`` was declared ``bool = True`` at
``services/agent/routes/_helpers.py:75``, so the field was never ``None`` and the
``else`` branch could not execute. A request that omitted ``dry_run`` got the model's
``True``, not the deployment's configured default (register item GAP-079).

The defect failed *safe* — the effective value was a preview run, which is what the
default-to-preview safeguard requires — so what these tests protect is the setting,
plus the safe reading of a malformed flag that ``coerce_dry_run`` gives: only a real
boolean decides, and anything else falls back to the configured default rather than
being coerced to a real, live run.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from services.agent.main import _sessions, app
from services.agent.profiles import get_profile


@pytest.fixture
def live_default_client(tmp_path, monkeypatch):
    """Agent client whose active profile is configured ``dry_run_default: false``."""
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap079.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    live_profile = replace(get_profile("lightweight"), dry_run_default=False)
    monkeypatch.setattr(
        "services.agent.routes.session_routes._profile", lambda _pid=None: live_profile,
    )
    yield TestClient(app)
    _sessions.clear()


def test_omitted_dry_run_takes_the_profile_default(live_default_client):
    created = live_default_client.post("/v1/sessions", json={"profile_id": "lightweight"})

    assert created.status_code == 200, created.text
    assert created.json()["dry_run"] is False, (
        "the profile's dry_run_default was ignored; the request model's own default "
        "won instead"
    )


def test_an_explicit_dry_run_still_wins_over_the_profile(live_default_client):
    created = live_default_client.post(
        "/v1/sessions", json={"profile_id": "lightweight", "dry_run": True},
    )

    assert created.status_code == 200, created.text
    assert created.json()["dry_run"] is True


def test_an_explicit_live_request_is_still_honoured(live_default_client):
    created = live_default_client.post(
        "/v1/sessions", json={"profile_id": "lightweight", "dry_run": False},
    )

    assert created.status_code == 200, created.text
    assert created.json()["dry_run"] is False


def test_a_dry_run_profile_still_defaults_to_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap079_safe.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    created = client.post("/v1/sessions", json={"profile_id": "lightweight"})

    assert created.json()["dry_run"] is True
    _sessions.clear()


def test_a_malformed_dry_run_is_rejected_not_coerced(live_default_client):
    """``"false"`` must never be read as a decision — the boundary rejects it."""
    created = live_default_client.post(
        "/v1/sessions", json={"profile_id": "lightweight", "dry_run": "not-a-bool"},
    )

    assert created.status_code == 422
