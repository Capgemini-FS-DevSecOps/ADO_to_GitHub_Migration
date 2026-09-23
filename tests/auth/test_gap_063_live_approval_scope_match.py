"""Regression check for register entry GAP-063 (GAP-AUTH-08) — a quoted ``live_approval_id`` was checked by status alone.

Both places that accept a client-supplied approval token read the row and asked one
question of it::

    approved = bool(row and row.get("status") == "approved")   # main.py:431-433
    if not row or row.get("status") != "approved": ...          # accelerator.py:186-193

Neither read ``scope_type`` or ``scope_id``, even though ``main.py`` computes the
correct scope one line earlier and the sibling paths (``migrate_guard.py:118``,
``pipeline_routes.py:255``) go through ``has_approved(scope_type, scope_id)``, which
is keyed on both columns.

Reproduction: an operator is parked awaiting approval for wave 1, an approver grants
it, and the operator replays the granted id against wave 2 — or against a different
config, or from a ``pipeline_run`` approval entirely. The live migration runs on a
confirmation that was never given for it (CA-002). Approvals are never consumed, so
the replay window never closes.

The tests below assert one property at both call sites: an approval releases live
execution only for the scope it was granted for.

Every identifier here is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import RunWaveRequest, RunWaveResult
from ado2gh.api.errors import ConfigurationError
from ado2gh.api.live_approval_store import migrate_scope_id
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

APPROVAL_ID = "lve_gap063fake"
_RESULT = RunWaveResult(wave_id=1, status="completed", completed=1, failed=0, total=1)

_WAVE_CONFIG = (
    "global:\n"
    "  gh_org: fake-org\n"
    "waves:\n"
    "  - wave_id: 1\n"
    "    name: wave-1\n"
    "    description: one repo\n"
    "    repos:\n"
    "      - ado_project: Contoso\n"
    "        ado_repo: payments\n"
    "        gh_org: fake-org\n"
    "        gh_repo: payments\n"
    "  - wave_id: 2\n"
    "    name: wave-2\n"
    "    description: one repo\n"
    "    repos:\n"
    "      - ado_project: Contoso\n"
    "        ado_repo: ledger\n"
    "        gh_org: fake-org\n"
    "        gh_repo: ledger\n"
)


def _seed_approved(db, scope_type: str, scope_id: str) -> None:
    """Write one live-execution approval row for `scope_type`/`scope_id`, already granted."""
    now = datetime.now(timezone.utc).isoformat()
    db.create_live_execution_approval(
        approval_id=APPROVAL_ID,
        requester_user_id="user-1",
        requester_username="operator",
        scope_type=scope_type,
        scope_id=scope_id,
        requested_at=now,
    )
    db.decide_live_execution_approval(
        APPROVAL_ID, "approved",
        PlatformUser(id="user-2", username="approver",
                     role=PlatformRole.APPROVER, display_name="Approver"),
        "granted in test", now,
    )


class _StubExecutor:
    """Stands in for ``BatchExecutor``; records that the wave actually ran."""

    executed: list[int] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def execute_wave(self, wave: object, **kwargs: object) -> dict:
        """Return the summary shape ``run_wave`` builds its result from."""
        wave_id = getattr(wave, "wave_id", 0)
        _StubExecutor.executed.append(wave_id)
        return {
            "wave_id": wave_id, "status": "completed",
            "completed": 1, "failed": 0, "total": 1,
        }


@pytest.fixture
def wave_env(tmp_path, monkeypatch):
    """A two-wave config and state DB with every outbound client stubbed out.

    Nothing here reaches Azure DevOps or GitHub: the clients and the engine are
    inert placeholders and the executor is ``_StubExecutor``, so a wave that
    "runs" only appends its id to ``_StubExecutor.executed``.
    """
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = str(tmp_path / "gap063.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)

    config_path = tmp_path / "migration.yaml"
    config_path.write_text(_WAVE_CONFIG, encoding="utf-8")

    import ado2gh.api.accelerator as accel_mod
    monkeypatch.setattr(accel_mod, "_build_ado_client", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "_build_gh_client", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "MigrationEngine", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "BatchExecutor", _StubExecutor)
    _StubExecutor.executed = []

    return str(config_path), db_path, create_state_db(db_path)


def _request(config_path: str, db_path: str, wave_id: int) -> RunWaveRequest:
    """Build a live run-wave request for one wave, quoting the seeded approval."""
    return RunWaveRequest(
        config_path=config_path, wave_id=wave_id, dry_run=False,
        db_path=db_path, live_approval_id=APPROVAL_ID,
    )


def test_approval_for_another_wave_migrates_nothing(wave_env):
    """An approval granted for wave 1 does not release wave 2."""
    config_path, db_path, db = wave_env
    _seed_approved(db, "migrate_job", migrate_scope_id(None, 1, config_path))

    with pytest.raises(ConfigurationError) as exc:
        Accelerator(db_path=db_path).run_wave(_request(config_path, db_path, 2))

    assert APPROVAL_ID in str(exc.value)
    assert _StubExecutor.executed == [], (
        "wave 2 migrated live on an approval granted for wave 1"
    )


def test_approval_for_another_config_migrates_nothing(wave_env, tmp_path):
    """The same wave number under a different config is a different scope."""
    config_path, db_path, db = wave_env
    other_config = tmp_path / "other-migration.yaml"
    other_config.write_text(_WAVE_CONFIG, encoding="utf-8")
    _seed_approved(db, "migrate_job", migrate_scope_id(None, 1, str(other_config)))

    with pytest.raises(ConfigurationError) as exc:
        Accelerator(db_path=db_path).run_wave(_request(config_path, db_path, 1))

    assert APPROVAL_ID in str(exc.value)
    assert _StubExecutor.executed == [], (
        "a wave migrated live on an approval granted for another config file"
    )


def test_pipeline_run_approval_does_not_release_a_migrate_job(wave_env):
    """A ``pipeline_run`` approval is not a migrate-job approval, whatever its id."""
    config_path, db_path, db = wave_env
    _seed_approved(db, "pipeline_run", migrate_scope_id(None, 1, config_path))

    with pytest.raises(ConfigurationError) as exc:
        Accelerator(db_path=db_path).run_wave(_request(config_path, db_path, 1))

    assert APPROVAL_ID in str(exc.value)
    assert _StubExecutor.executed == [], (
        "a migrate job ran live on an approval granted for a pipeline run"
    )


def test_matching_approval_still_releases_the_wave(wave_env):
    """The refusal is targeted: the scope it was granted for still runs."""
    config_path, db_path, db = wave_env
    _seed_approved(db, "migrate_job", migrate_scope_id(None, 1, config_path))

    result = Accelerator(db_path=db_path).run_wave(_request(config_path, db_path, 1))

    assert result.wave_id == 1
    assert _StubExecutor.executed == [1]


@pytest.fixture
def operator_client(tmp_path, monkeypatch):
    """Authenticated OPERATOR against the accelerator, with an active profile.

    Mirrors the GAP-005 fixture: ``ADO2GH_AUTH_ENABLED=true`` is what makes the
    quoted-token branch reachable at all, since an identity-less live run is
    refused with 401 before any approval is read.
    """
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "gap063_route.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app
    from services.accelerator_api.routes import _shared

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", "AdminPass12345!", "Admin")
    svc.create_user("op1", "OperPass12345!", PlatformRole.OPERATOR, "Operator One")

    monkeypatch.setattr(_shared._settings, "path", tmp_path / "ui_settings.json")
    _shared._settings.setup_profile(
        {
            "name": "GAP-063 profile",
            "ado_org_url": "https://dev.azure.com/fake-org",
            "ado_pat": "fake-ado-pat",
            "gh_org": "fake-gh-org",
            "github_token": "fake-gh-token",
        },
        role="admin",
    )
    profile = _shared._settings.get_active_profile()

    config_path = tmp_path / "migration.yaml"
    config_path.write_text(_WAVE_CONFIG, encoding="utf-8")

    client = TestClient(app)
    login = client.post(
        "/v1/auth/login", json={"username": "op1", "password": "OperPass12345!"},
    )
    assert login.status_code == 200
    client.cookies.set("ado2gh_session", login.cookies.get("ado2gh_session"))

    return client, str(config_path), str(db_path), create_state_db(str(db_path)), profile.id


def _migrate(client: TestClient, config_path: str, db_path: str, wave_id: int):
    """POST a live migrate for one wave, quoting the seeded approval."""
    return client.post(
        "/v1/migrate",
        json={
            "config_path": config_path,
            "db_path": db_path,
            "wave_id": wave_id,
            "dry_run": False,
            "live_approval_id": APPROVAL_ID,
        },
    )


def test_route_refuses_an_approval_granted_for_another_wave(operator_client):
    """``POST /v1/migrate`` must not replay a wave-1 approval against wave 2."""
    client, config_path, db_path, db, profile_id = operator_client
    _seed_approved(db, "migrate_job", migrate_scope_id(profile_id, 1, config_path))

    with patch.object(Accelerator, "run_wave") as run_wave:
        run_wave.return_value = _RESULT
        resp = _migrate(client, config_path, db_path, 2)

    run_wave.assert_not_called()
    assert resp.status_code == 403, (
        f"an operator replayed a wave-1 approval against wave 2 (HTTP "
        f"{resp.status_code}); the quoted token is checked by status alone"
    )
    assert "awaiting_approval" in str(resp.json()), resp.json()


def test_route_refuses_a_pipeline_run_approval(operator_client):
    """A ``pipeline_run`` approval must not release a live migrate job."""
    client, config_path, db_path, db, profile_id = operator_client
    _seed_approved(db, "pipeline_run", migrate_scope_id(profile_id, 1, config_path))

    with patch.object(Accelerator, "run_wave") as run_wave:
        run_wave.return_value = _RESULT
        resp = _migrate(client, config_path, db_path, 1)

    run_wave.assert_not_called()
    assert resp.status_code == 403, (
        f"a pipeline-run approval released a live migrate job (HTTP {resp.status_code})"
    )
    assert "awaiting_approval" in str(resp.json()), resp.json()


def test_route_accepts_the_approval_it_was_granted_for(operator_client):
    """The matching scope still runs — the route refusal is targeted, not blanket."""
    client, config_path, db_path, db, profile_id = operator_client
    _seed_approved(db, "migrate_job", migrate_scope_id(profile_id, 1, config_path))

    with patch.object(Accelerator, "run_wave") as run_wave:
        run_wave.return_value = _RESULT
        resp = _migrate(client, config_path, db_path, 1)

    assert resp.status_code == 200, resp.json()
    run_wave.assert_called_once()
