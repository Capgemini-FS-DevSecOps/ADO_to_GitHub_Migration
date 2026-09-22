"""Regression check for register entry GAP-133 — the audit `actor` field bypassed redaction.

``AuditWriter.write`` passed `payload` through `redact_payload` before
persisting it, but wrote `actor` and `event_type` to the database exactly as
given. `event_type` is always a fixed machine name chosen by code, so it stays
as written, but `actor` is sometimes free text a caller assembled (a username
embedded in a larger string, for example), and nothing stopped a secret shape
from reaching that column unmasked (CA-003).

The obviously-fake token below is a shape, not a credential.
"""
from __future__ import annotations

from ado2gh.audit import AuditWriter

_FAKE_GH_TOKEN = "ghp_" + "0123456789abcdefghij"


class _CapturingDb:
    """Minimal StateDB stand-in that records what would have been persisted."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs) -> None:
        self.rows.append(kwargs)


def test_a_token_in_the_actor_field_is_masked_before_it_is_persisted():
    """No recognised secret shape reaches the `actor` column."""
    db = _CapturingDb()
    AuditWriter(db).write(
        "migration.started",
        profile_id="lightweight",
        actor=f"service-account using {_FAKE_GH_TOKEN}",
        payload={},
    )

    stored_actor = db.rows[0]["actor"]
    assert _FAKE_GH_TOKEN not in stored_actor
    assert "***" in stored_actor


def test_an_ordinary_actor_name_is_unchanged():
    """A plain username, the common case, must not be altered."""
    db = _CapturingDb()
    AuditWriter(db).write(
        "migration.started", profile_id="lightweight", actor="operator1", payload={},
    )

    assert db.rows[0]["actor"] == "operator1"


def test_an_empty_actor_stays_empty():
    """The default, empty actor, must round-trip unchanged (no crash on falsy input)."""
    db = _CapturingDb()
    AuditWriter(db).write("migration.started", profile_id="lightweight", payload={})

    assert db.rows[0]["actor"] == ""
