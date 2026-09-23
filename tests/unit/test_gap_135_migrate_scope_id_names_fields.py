"""Regression check for register entry GAP-135 — a live-migrate approval scope did not name its fields.

The safeguard is that an approval is scoped to the exact action it was given
for. ``_live_scope_id`` in ``services/accelerator_api/routes/migrate_guard.py``
joined the allowlisted field *values* with ``:`` and no field names, so a body
naming only ``github_org`` and a body naming only ``github_repo`` produced the
same trailing string whenever the two values matched, and two different
destinations shared one approval. Encoding each field as ``name=value`` makes
the id unambiguous regardless of which fields a body happens to carry.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from services.accelerator_api.routes.migrate_guard import _live_scope_id

_PATH = "/v1/migrate/git-mirror"


def test_org_only_and_repo_only_bodies_get_different_scopes():
    """Critical property: naming the org is not the same grant as naming the repo."""
    org_only = _live_scope_id(_PATH, {"github_org": "acme"}, "prod")
    repo_only = _live_scope_id(_PATH, {"github_repo": "acme"}, "prod")

    assert org_only != repo_only, (
        f"a body naming only github_org ({org_only}) built the same scope as a body "
        f"naming only github_repo ({repo_only}); an approval for one destination "
        f"would release the other"
    )


def test_the_same_body_gets_the_same_scope_twice():
    """A repeat of the same live request must find the approval already granted."""
    body = {"project": "Contoso", "repo_name": "payments", "github_org": "acme"}

    first = _live_scope_id(_PATH, body, "prod")
    second = _live_scope_id(_PATH, dict(body), "prod")

    assert first == second


def test_scope_id_names_the_field_it_encodes():
    """The id must be self-describing, not a bare colon-joined list of values."""
    scope_id = _live_scope_id(_PATH, {"github_org": "acme"}, "prod")

    assert "github_org=" in scope_id, (
        f"scope id {scope_id!r} does not name the github_org field it encodes"
    )
