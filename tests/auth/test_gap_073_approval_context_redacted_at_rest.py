"""GAP-073 (GAP-TOKEN-02) — approval contexts were persisted without masking.

`LiveApprovalStore.create_or_get_pending` serialised the caller's `context` dict
straight into the `context_json` column::

    context_json=json.dumps(request.context or {})

Every other persistence path in the platform goes through
`ado2gh.audit.redaction.redact_payload` — the audit writer, the agent's session
store, the root log handler — because CA-003 is about what reaches a persisted
artefact, not only about what reaches a screen. This column did not, and the
post-fix security review put a `ghp_`-shaped probe through
`POST /v1/platform/approvals` and read it back out of the database verbatim.

The context is client-supplied and the approver never sees it, so nothing warns
anyone that a credential went into the queue. It is also long-lived: approvals are
never consumed or expired, so the row outlives the run it released.

The executed fields have to survive masking untouched, or an approval would stop
matching the scope it was granted for. They do: `config_path`, `db_path` and
`route` are paths, `wave_id` is an integer, and none of them is a shape
`redact_text` recognises.

Every token below is an obvious fake; it is a shape, not a credential (CA-003).
"""
from __future__ import annotations

import json

import pytest

from ado2gh.api import live_approval_store as store_mod
from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_store import LiveApprovalStore, migrate_scope_id
from ado2gh.auth.models import PlatformRole, PlatformUser

_OPERATOR = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")

# Shapes `redact_text` recognises. Neither is a credential; both are 20 fake
# characters chosen to match the GitHub token and ADO PAT patterns.
_FAKE_GH_TOKEN = "ghp_" + "0123456789abcdefghij"
_FAKE_PAT = "pat-" + "0123456789abcdefghij"


@pytest.fixture
def store(tmp_path, monkeypatch) -> LiveApprovalStore:
    """A live-approval store on a throwaway state database."""
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    return LiveApprovalStore(str(db_path))


def _stored_context(store: LiveApprovalStore, approval_id: str) -> str:
    """Read the serialised context column back exactly as it was written."""
    row = store.db.get_live_execution_approval(approval_id)
    return row["context_json"] or ""


def test_a_token_in_the_context_is_masked_before_it_is_persisted(store):
    """Critical property: no recognised secret shape reaches the column (CA-003)."""
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="agent_session",
            scope_id="sess_gap073",
            reason_request="Ready for live",
            context={"gh_token": _FAKE_GH_TOKEN, "note": f"use {_FAKE_PAT} to retry"},
        ),
    )

    stored = _stored_context(store, row["id"])

    assert _FAKE_GH_TOKEN not in stored, (
        "the approval context kept a GitHub token verbatim in context_json; the "
        "queue never expires a row, so it outlives the run it released (CA-003)"
    )
    assert _FAKE_PAT not in stored, (
        "a token inside free text survived into context_json"
    )
    assert "***" in stored


def test_a_secret_key_name_is_masked_even_when_its_value_has_no_shape(store):
    """Key-name matching has to apply here too, as it does in the audit writer."""
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="agent_session",
            scope_id="sess_gap073_key",
            context={"ado_pat": "not-a-recognisable-shape-at-all"},
        ),
    )

    assert "not-a-recognisable-shape-at-all" not in _stored_context(store, row["id"])


def test_an_ordinary_migrate_context_round_trips_unchanged(store):
    """Masking must not touch the fields the scope is derived from."""
    scope_id = migrate_scope_id("prod", 1, "configs/migration.yaml")
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="migrate_job",
            scope_id=scope_id,
            profile_id="prod",
            reason_request="Release wave 1",
            context={
                "config_path": "configs/migration.yaml",
                "wave_id": 1,
                "dry_run": False,
                "db_path": "migration_state.db",
            },
        ),
    )

    assert json.loads(_stored_context(store, row["id"])) == {
        "config_path": "configs/migration.yaml",
        "wave_id": 1,
        "dry_run": False,
        "db_path": "migration_state.db",
    }


def test_a_feature_route_context_round_trips_unchanged(store):
    """`migrate_guard`'s context is a route and a scope id; neither is secret-shaped."""
    scope_id = "/v1/migrate/git-mirror:Contoso:payments"
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="migrate_job",
            scope_id=scope_id,
            profile_id="prod",
            context={"route": "/v1/migrate/git-mirror", "scope_id": scope_id},
        ),
    )

    assert json.loads(_stored_context(store, row["id"])) == {
        "route": "/v1/migrate/git-mirror", "scope_id": scope_id,
    }


def test_a_masked_context_still_executes_the_approved_scope(store, monkeypatch):
    """Redaction happens before the scope check, so the two read the same bytes."""
    executed: list[dict] = []
    monkeypatch.setattr(store_mod, "_migrate_executor", executed.append)
    scope_id = migrate_scope_id("prod", 1, "migration.yaml")
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="migrate_job",
            scope_id=scope_id,
            profile_id="prod",
            context={
                "config_path": "migration.yaml", "wave_id": 1,
                "gh_token": _FAKE_GH_TOKEN,
            },
        ),
    )

    store.approve(row["id"], _APPROVER, "Release wave 1")

    assert executed == [{
        "config_path": "migration.yaml", "wave_id": 1,
        "db_path": "migration_state.db", "dry_run": False,
    }]
    assert _FAKE_GH_TOKEN not in _stored_context(store, row["id"])
