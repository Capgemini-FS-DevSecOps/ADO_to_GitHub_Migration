"""Regression check for register entry GAP-010 (GAP-TOKEN-01): audit redaction misses non-GitHub secret shapes.

Reproduction from the gap register's evidence. ``ado2gh/audit/redaction.py``
declares seven ``_SECRET_PATTERNS`` that all match GitHub token prefixes
(``ghp_``, ``gho_``, ``ghu_``, ``ghs_``, ``github_pat_``, ``pat-``) plus one
JSON-key pattern, and a ``_SECRET_KEY_NAMES`` set matched by exact equality
(``k.lower() in _SECRET_KEY_NAMES``). Neither path recognises:

* a bare Azure DevOps personal access token (PAT), a 52-character opaque string
  with no prefix;
* a generic ``Authorization: Bearer <token>`` value in free text;
* the platform's own environment-variable key names -- ``ado_pat``,
  ``access_token``, ``github_token``, ``gh_token`` (CLAUDE.md documents all of
  these), which are prefixed/suffixed rather than exactly ``token`` or ``pat``.

``AuditWriter.write()`` calls ``redact_payload()`` as the sole redaction step
before ``json.dumps(safe)`` is handed to ``insert_audit_event``, so any shape
missed above is persisted verbatim into the highest-retention artefact in the
platform (audit events are exported through ``GET /v1/history/sessions/export``).

Violates Principle V (CA-003: secret values masked in all messages, logs and
audit records).

These tests assert the security property at the sink -- the serialised audit
payload must not contain the raw secret -- not that any particular helper was
called, so they stay valid once T039 consolidates the three redaction
implementations onto ``redact_payload``.
"""
from __future__ import annotations

import json

from ado2gh.audit import AuditWriter, redact_payload

# Obviously-fake literals shaped like the real thing (CA-003 -- never a real credential).
FAKE_ADO_PAT = "a7x2k9q4m1p8s3v6y0b5n2h7j4l1d8f3g6t9w2z5c0r7e4u1i8o5"  # 52 chars, opaque
FAKE_BEARER = "eyJhbGciOiJIUzI1NiJ9.ZmFrZS1wYXlsb2Fk.ZmFrZS1zaWduYXR1cmU"


class _CapturingDb:
    """Minimal StateDB stand-in that records what would have been persisted."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs) -> None:
        self.rows.append(kwargs)


def _persisted(payload: dict) -> str:
    db = _CapturingDb()
    AuditWriter(db).write("migration.started", profile_id="lightweight", payload=payload)
    return db.rows[0]["payload_json"]


def test_bare_ado_pat_under_realistic_key_is_not_persisted():
    """`ado_pat` is not in _SECRET_KEY_NAMES and the PAT matches no pattern."""
    stored = _persisted({"ado_pat": FAKE_ADO_PAT})
    assert FAKE_ADO_PAT not in stored


def test_access_token_key_name_is_not_persisted():
    stored = _persisted({"access_token": FAKE_BEARER})
    assert FAKE_BEARER not in stored


def test_github_token_env_var_key_name_is_not_persisted():
    """Redaction must key off the *name*, not a recognised value prefix.

    A GitHub App installation token has no ``gh*_`` prefix, so these values are
    invisible to the pattern list and the key names are not exact matches.
    """
    stored = _persisted({"github_token": "z" * 40, "GH_TOKEN": "y" * 40})
    assert "z" * 40 not in stored
    assert "y" * 40 not in stored


def test_bearer_header_value_in_free_text_is_not_persisted():
    """A copied Authorization header reaches audit as an unstructured string."""
    stored = _persisted({"detail": f"curl -H 'Authorization: Bearer {FAKE_BEARER}' ..."})
    assert FAKE_BEARER not in stored


def test_bare_ado_pat_in_free_text_is_not_persisted():
    stored = _persisted({"detail": f"clone failed using pat {FAKE_ADO_PAT}"})
    assert FAKE_ADO_PAT not in stored


def test_secret_key_names_are_matched_case_and_affix_insensitively():
    """Nested realistic key names must all redact, at any depth."""
    out = redact_payload(
        {
            "credentials": {
                "ADO_PAT": FAKE_ADO_PAT,
                "gh_token": "ghs_" + "q" * 36,
                "clientSecret": "fake-client-secret-value-0001",
                "apiKey": "sk-ant-fake-key-0002",
            }
        }
    )
    blob = json.dumps(out)
    assert FAKE_ADO_PAT not in blob
    assert "q" * 36 not in blob
    assert "fake-client-secret-value-0001" not in blob
    assert "sk-ant-fake-key-0002" not in blob


def test_secret_in_list_of_strings_is_not_persisted():
    stored = _persisted({"args": ["--pat", FAKE_ADO_PAT, "--org", "contoso"]})
    assert FAKE_ADO_PAT not in stored
    assert "contoso" in stored  # non-secret context is preserved
