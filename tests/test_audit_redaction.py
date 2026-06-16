"""Audit payload redaction tests."""
from ado2gh.assignments.audit import redact_payload


def test_redact_github_token():
    out = redact_payload({"token": "ghp_abcdefghijklmnopqrst"})
    assert "ghp_" in out["token"]
    assert "***" in out["token"]


def test_redact_nested():
    out = redact_payload({"nested": {"pat": "pat-abc123xyz"}})
    assert "***" in str(out["nested"]["pat"])
