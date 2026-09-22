"""Regression check for register entry GAP-141 — the audit `profile_id` field and secret-shaped payload keys bypassed redaction.

``AuditWriter.write`` inserted `profile_id` exactly as given, unlike `actor`
and the payload, which already went through masking. Separately,
`redact_payload`'s dict branch only masked a secret-looking key's *value*; a
raw token used as the key itself (rather than the more usual named field,
e.g. ``{"ghp_...": "seen"}``) reached the stored payload untouched, because
nothing ran the key string through the same value-shape scan.

The obviously-fake token below is a shape, not a credential.
"""
from __future__ import annotations

from ado2gh.audit import AuditWriter
from ado2gh.audit.redaction import redact_payload

_FAKE_GH_TOKEN = "ghp_" + "FAKEFAKE1234"


class _CapturingDb:
    """Minimal StateDB stand-in that records what would have been persisted."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs) -> None:
        self.rows.append(kwargs)


def test_a_token_in_profile_id_is_masked_before_it_is_persisted() -> None:
    """No recognised secret shape reaches the `profile_id` column."""
    db = _CapturingDb()
    AuditWriter(db).write(
        "migration.started",
        profile_id=f"profile-{_FAKE_GH_TOKEN}",
        actor="operator1",
        payload={},
    )

    stored_profile_id = db.rows[0]["profile_id"]
    assert _FAKE_GH_TOKEN not in stored_profile_id
    assert "***" in stored_profile_id


def test_an_ordinary_profile_id_is_unchanged() -> None:
    """A plain profile name, the common case, must not be altered."""
    db = _CapturingDb()
    AuditWriter(db).write(
        "migration.started", profile_id="lightweight", actor="operator1", payload={},
    )

    assert db.rows[0]["profile_id"] == "lightweight"


def test_an_empty_profile_id_stays_empty() -> None:
    """A falsy profile_id must round-trip unchanged (no crash on empty input)."""
    db = _CapturingDb()
    AuditWriter(db).write("migration.started", profile_id="", actor="", payload={})

    assert db.rows[0]["profile_id"] == ""


def test_a_secret_shaped_dict_key_is_masked() -> None:
    """A raw token used as a dict key, not just as a value, must be masked."""
    result = redact_payload({_FAKE_GH_TOKEN: "seen"})

    assert _FAKE_GH_TOKEN not in result
    assert any("***" in key for key in result)


def test_an_ordinary_dict_key_is_unchanged() -> None:
    """A plain field name, the common case, must not be altered."""
    result = redact_payload({"status": "seen"})

    assert result == {"status": "seen"}
