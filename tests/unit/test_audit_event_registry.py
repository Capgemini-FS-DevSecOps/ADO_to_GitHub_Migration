"""Drift guard for the audit event name registry (``ado2gh.audit.events.AuditEvent``).

Scans every ``.py`` file under ``ado2gh/`` and ``services/`` for a literal string
passed as the event name to ``write_profile_audit(...)`` or an ``AuditWriter``/
``AuditWriter``-alike ``.write(...)`` call, and asserts each one is a value the
registry knows about. A new call site that types a fresh literal instead of
importing ``AuditEvent`` either fails here because the string is not a
registry value, or — if it happens to reuse an existing event name — is only
let through when its file is one of the acknowledged ``_PENDING_FILES`` below,
so a genuinely new file sneaking a raw literal past review still fails.
"""
from __future__ import annotations

import re
from pathlib import Path

from ado2gh.audit.events import AuditEvent

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = ("ado2gh", "services")

# Matches the literal first argument of `write_profile_audit(...)` or of an
# AuditWriter `.write(...)` call, whether passed positionally or as
# `event_type="...".` `\s` spans the newline between the call and its first
# argument, so a literal on the line after the opening paren is still caught.
_EVENT_LITERAL = re.compile(r'(?:write_profile_audit|\.write)\(\s*(?:event_type\s*=\s*)?"([^"]+)"')

# Files this change could not edit (another agent owns them, or they were out
# of scope for this task) that still type an audit event name as a bare
# literal instead of the registry member. Every literal they hold is already
# a registry value — this only records that they have not been migrated to
# `AuditEvent.<member>.value` yet, so a future pass can pick them up.
#
# Empty: every file that used to hold a bare literal has been migrated onto
# `AuditEvent.<member>.value`. Left as a set (not deleted) so a future bare
# literal has somewhere to be acknowledged instead of loosening the check above.
_PENDING_FILES: set[str] = set()


def _literal_events_by_file() -> dict[str, list[str]]:
    """Map every scanned file (as a repo-relative posix path) to its literal event names."""
    found: dict[str, list[str]] = {}
    for scan_dir in _SCAN_DIRS:
        for path in (_REPO_ROOT / scan_dir).rglob("*.py"):
            matches = _EVENT_LITERAL.findall(path.read_text(encoding="utf-8"))
            if matches:
                found[path.relative_to(_REPO_ROOT).as_posix()] = matches
    return found


def test_every_literal_audit_event_is_a_registry_value() -> None:
    """Every literal event name found is a value ``AuditEvent`` declares."""
    known_values = {member.value for member in AuditEvent}
    for file_path, literals in _literal_events_by_file().items():
        for literal in literals:
            assert literal in known_values, (
                f"{file_path} writes audit event {literal!r}, which is not in "
                "AuditEvent — add it to ado2gh/audit/events.py."
            )


def test_only_acknowledged_pending_files_still_use_bare_literals() -> None:
    """A file with an unmigrated literal must be one of the acknowledged ``_PENDING_FILES``.

    Catches the case a bare-literal-equals-a-registry-value check alone
    misses: a *new* file reusing an existing event name as a fresh literal
    instead of importing ``AuditEvent``.
    """
    found_files = set(_literal_events_by_file())
    unexpected = found_files - _PENDING_FILES
    assert not unexpected, (
        f"These files write an audit event as a bare literal instead of "
        f"AuditEvent.<member>.value, and are not in the acknowledged pending "
        f"list: {sorted(unexpected)}"
    )


def test_live_approval_scope_type_literal_matches_the_scope_type_constants() -> None:
    """``LiveApprovalCreateRequest.scope_type`` must name the same three scopes as the registry.

    Pydantic needs an actual ``Literal`` for that field, not a variable, so the
    three scope-type strings are typed a second time in ``ado2gh/api/contracts.py``.
    This asserts the two never drift apart.
    """
    from typing import get_args

    from ado2gh.api.contracts import LiveApprovalCreateRequest
    from ado2gh.api.live_approval_scopes import (
        AGENT_SESSION_SCOPE_TYPE,
        MIGRATE_JOB_SCOPE_TYPE,
        PIPELINE_RUN_SCOPE_TYPE,
    )

    annotation = LiveApprovalCreateRequest.model_fields["scope_type"].annotation
    assert set(get_args(annotation)) == {
        AGENT_SESSION_SCOPE_TYPE,
        MIGRATE_JOB_SCOPE_TYPE,
        PIPELINE_RUN_SCOPE_TYPE,
    }
