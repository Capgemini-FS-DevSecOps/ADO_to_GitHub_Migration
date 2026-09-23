"""Regression check for register entry GAP-072 (GAP-AUTH-10) — falsy identifiers collapsed into their own defaults.

Both approval-scope builders dropped a value for being falsy rather than for being
absent::

    f"migrate:{profile_id or 'default'}:{wave_id or 'all'}:{config_path}"   # store
    ... for f in _SCOPE_FIELDS if body.get(f)                                # guard

So wave ``0`` produced the same scope as "every wave", and profile ``""`` the same
scope as the default profile. An approval granted for wave 0 — a wave a config may
legitimately declare — released every wave in that config, which is the widest live
migration the platform can run, on a confirmation given for the narrowest (CA-002).
The guard has the same shape: a body that names a target as an empty string built
the scope of a body that did not name it at all.

No approval rows are persisted in this repository, so nothing has to be migrated;
the ids these builders produce for ``None`` and for ``1`` are unchanged, and only
the previously-colliding falsy spellings move.

Every identifier here is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from ado2gh.api.live_approval_store import migrate_scope_id
from services.accelerator_api.routes.migrate_guard import _live_scope_id

_CONFIG = "migration.yaml"


def test_wave_zero_is_not_every_wave():
    """Critical property: wave 0, every wave and wave 1 are three different grants."""
    zero = migrate_scope_id(None, 0, _CONFIG)
    every = migrate_scope_id(None, None, _CONFIG)
    one = migrate_scope_id(None, 1, _CONFIG)

    assert len({zero, every, one}) == 3, (
        f"wave 0 ({zero}), every wave ({every}) and wave 1 ({one}) do not all have "
        f"their own approval scope; an approval for one releases another"
    )


def test_empty_profile_is_not_the_default_profile():
    """An unnamed profile and a profile named '' are different tenants."""
    assert migrate_scope_id("", 1, _CONFIG) != migrate_scope_id(None, 1, _CONFIG)


def test_unchanged_spellings_keep_their_scope_ids():
    """The ids that were already correct must not move — they are what is granted today."""
    assert migrate_scope_id(None, None, _CONFIG) == f"migrate:default:all:{_CONFIG}"
    assert migrate_scope_id("prod", 1, _CONFIG) == f"migrate:prod:1:{_CONFIG}"


def test_guard_scope_distinguishes_an_empty_target_from_an_absent_one():
    """A feature-route body that names a target as '' is not a body that omits it."""
    path = "/v1/migrate/git-mirror"
    named_empty = _live_scope_id(path, {"project": "", "repo_name": "payments"}, "prod")
    omitted = _live_scope_id(path, {"repo_name": "payments"}, "prod")

    assert named_empty != omitted, (
        f"a body naming project='' built the scope of a body that omits project "
        f"({named_empty}); an approval for one admits the other"
    )


def test_guard_scope_treats_an_explicit_null_as_absent():
    """JSON ``null`` and an omitted key mean the same thing to the request model."""
    path = "/v1/migrate/git-mirror"
    assert _live_scope_id(path, {"project": None, "repo_name": "payments"}, "prod") == (
        _live_scope_id(path, {"repo_name": "payments"}, "prod")
    )
