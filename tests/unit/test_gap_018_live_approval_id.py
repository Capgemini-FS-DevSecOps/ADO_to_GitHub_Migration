"""Regression check for register entry GAP-018 — ``RunWaveRequest.live_approval_id`` was accepted and thrown away.

Reproduction, in plain English:

``ado2gh/api/contracts.py`` declares ``RunWaveRequest.live_approval_id`` and
documents it as the token that "replays the request against the approval that
was granted". ``Accelerator.run_wave`` — the single function every migrate
caller routes through (the CLI, the queue worker, the pipeline runner and both
accelerator routes) — never read the field. A caller could therefore quote an
approval that was pending, denied or entirely invented and the wave would run
anyway, because the only code that ever looked at the token lived in one HTTP
route and ran only for operators the role-based access control (RBAC) layer had
already decided needed an approval.

The fix is a refusal at that choke point: a quoted token must name an
``approved`` row or nothing is migrated. It deliberately does **not** make an
approval mandatory — requiring one, like flipping the ``--dry-run`` default,
changes the frozen public surface and is held for operator sign-off under
FR-024.

No credential value appears in this file; approval ids are opaque (CA-003).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import RunWaveRequest
from ado2gh.api.errors import ConfigurationError
from ado2gh.api.live_approval_store import migrate_scope_id
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.factory import create_state_db

APPROVAL_ID = "approval-0001"


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
    """A one-wave config and state DB, with every outbound client stubbed out.

    Nothing in here reaches Azure DevOps or GitHub: the clients and the engine
    are inert placeholders and the executor is ``_StubExecutor``, so a wave that
    "runs" only appends its id to ``_StubExecutor.executed``.
    """
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = str(tmp_path / "approvals.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)

    config_path = tmp_path / "migration.yaml"
    config_path.write_text(
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
        "        gh_repo: payments\n",
        encoding="utf-8",
    )

    import ado2gh.api.accelerator as accel_mod
    monkeypatch.setattr(accel_mod, "_build_ado_client", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "_build_gh_client", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "MigrationEngine", lambda *a, **k: object())
    monkeypatch.setattr(accel_mod, "BatchExecutor", _StubExecutor)
    _StubExecutor.executed = []

    return str(config_path), db_path, create_state_db(db_path)


def _seed_approval(db, status: str, scope_id: str) -> None:
    """Write one live-execution approval row for `scope_id`, decided to `status`.

    The scope id is the one ``Accelerator.run_wave`` rebuilds for itself, since a
    quoted token has to name the scope it was granted for (GAP-063).
    """
    now = datetime.now(timezone.utc).isoformat()
    db.create_live_execution_approval(
        approval_id=APPROVAL_ID,
        requester_user_id="user-1",
        requester_username="operator",
        scope_type="migrate_job",
        scope_id=scope_id,
        requested_at=now,
    )
    if status != "pending":
        db.decide_live_execution_approval(
            APPROVAL_ID, status,
            PlatformUser(id="user-2", username="approver",
                         role=PlatformRole.APPROVER, display_name="Approver"),
            "decided in test", now,
        )


def _request(config_path: str, db_path: str, approval_id: str | None) -> RunWaveRequest:
    """Build a live run-wave request, optionally quoting an approval token."""
    return RunWaveRequest(
        config_path=config_path, wave_id=1, dry_run=False,
        db_path=db_path, live_approval_id=approval_id,
    )


@pytest.mark.parametrize("status", ["pending", "denied"])
def test_undecided_or_denied_approval_migrates_nothing(wave_env, status):
    """A quoted approval that was never granted stops the wave."""
    config_path, db_path, db = wave_env
    _seed_approval(db, status, migrate_scope_id(None, 1, config_path))

    with pytest.raises(ConfigurationError) as exc:
        Accelerator(db_path=db_path).run_wave(_request(config_path, db_path, APPROVAL_ID))

    assert APPROVAL_ID in str(exc.value)
    assert _StubExecutor.executed == [], "the wave ran despite an ungranted approval"


def test_unknown_approval_id_migrates_nothing(wave_env):
    """An approval id with no row behind it is refused, not ignored."""
    config_path, db_path, _db = wave_env

    with pytest.raises(ConfigurationError) as exc:
        Accelerator(db_path=db_path).run_wave(
            _request(config_path, db_path, "no-such-approval"),
        )

    assert "no-such-approval" in str(exc.value)
    assert _StubExecutor.executed == [], "the wave ran on an invented approval"


def test_approved_token_lets_the_wave_run(wave_env):
    """A granted approval is honoured — the refusal is targeted, not blanket."""
    config_path, db_path, db = wave_env
    _seed_approval(db, "approved", migrate_scope_id(None, 1, config_path))

    result = Accelerator(db_path=db_path).run_wave(
        _request(config_path, db_path, APPROVAL_ID),
    )

    assert result.wave_id == 1
    assert _StubExecutor.executed == [1]


def test_request_without_a_token_is_unaffected(wave_env):
    """Quoting no token keeps the pre-existing behaviour: the wave runs.

    Making an approval mandatory here would change the frozen CLI and HTTP
    contract; that half of GAP-018 is held for operator sign-off (FR-024).
    """
    config_path, db_path, _db = wave_env

    result = Accelerator(db_path=db_path).run_wave(
        _request(config_path, db_path, None),
    )

    assert result.wave_id == 1
    assert _StubExecutor.executed == [1]
