"""In-memory agent session and run caches, and their bounds.

Split out of ``_helpers`` (docs/STRUCTURAL_CHANGELOG.md) to keep that module
under the project's file-size cap. This module owns the two process-global
caches and the eviction policy that bounds them; ``_helpers`` re-exports every
name here so no other module's import line needed to change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ado2gh.agents.migration_agent.constants import (
    MAX_IN_MEMORY_SESSIONS,
    SESSION_IDLE_TTL_SECONDS,
)

# ponytail: process-global, so the agent is single-replica only and loses these on
# restart — MigrationSessionStore recovery in services/agent/main.py only partially
# compensates. Deploy the agent at replicas: 1 (see deploy/kubernetes/agent-deployment.yaml).
# DEPRECATED: this in-memory session store is being replaced by the persistent
# MigrationSessionStore (ado2gh.agents.migration_agent.session.store) in Spec 011.
# Do not add new consumers; existing routes will be migrated incrementally.
_runs: dict[str, dict] = {}
_sessions: dict[str, dict] = {}


def _session_activity_epoch(session: dict[str, Any]) -> float:
    """Read a session's last-activity timestamp as an epoch, newest-wins on failure.

    Args:
        session: The in-memory session dict.

    Returns:
        The parsed ``updated_at``/``created_at`` epoch, or *now* when neither parses —
        an entry with no usable timestamp is treated as fresh so it is never evicted
        by age, only ever by the size cap.
    """
    raw = session.get("updated_at") or session.get("created_at")
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).timestamp()


def _forget_session(session_id: str) -> None:
    """Drop one session and its run record from the in-memory caches."""
    session = _sessions.pop(session_id, None)
    run_id = (session or {}).get("run_id")
    if run_id:
        _runs.pop(str(run_id), None)


# State that only exists in the in-memory dict: hydration rebuilds a session through
# `new_isolated_agent_session`, which clears the LangGraph thread, so an interrupted
# form, a live-approval handshake, a pending clarification and the remediation counter
# do not survive a round trip. A session holding any of them is left alone by the TTL
# sweep — the size cap below is still absolute. A *busy* run is already excluded above
# by `is_session_busy`; `run_id` alone is deliberately not in this tuple, or a session
# that ran PEV once and went idle would carry it forever and never age out.
_NON_RECONSTRUCTIBLE_KEYS = (
    "pending_form", "live_approval_id", "live_approval_status",
    "remediation_attempts", "pending_clarification",
)


def _is_reconstructible(session: dict[str, Any]) -> bool:
    """Report whether dropping a session from memory loses nothing but a reload.

    Args:
        session: The in-memory session dict.

    Returns:
        ``True`` when the session is idle and carries no in-memory-only state, so
        :func:`_try_hydrate_session` can rebuild an equivalent one from the store.
    """
    from ado2gh.agents.migration_agent.session.state import is_session_busy

    if is_session_busy(session.get("status")):
        return False
    return not any(session.get(key) for key in _NON_RECONSTRUCTIBLE_KEYS)


def _evict_stale_state() -> None:
    """Bound the in-memory session and run caches by age, then by size.

    Both dicts are process-global and were only ever emptied by an explicit ``DELETE /v1/sessions/{id}``, so any
    caller able to create sessions could grow them without limit. Every in-memory record the server keeps on a
    caller's behalf needs both a time limit and a count limit, or a caller can exhaust server memory simply by
    making enough requests (register item THR-10-001).

    The age sweep only drops sessions :func:`_is_reconstructible` vouches for — ``persist_session_snapshot``
    writes those to ``MigrationSessionStore`` and :func:`_try_hydrate_session` reads them back, so the eviction
    costs a reload and nothing else.

    The size cap is deliberately unconditional: an unbounded map is worse than a dropped run, and refusing to
    evict "interesting" sessions would let a caller defeat the cap by making every session interesting. Oldest
    first.
    """
    now = datetime.now(timezone.utc).timestamp()
    for session_id, session in list(_sessions.items()):
        if (
            now - _session_activity_epoch(session) > SESSION_IDLE_TTL_SECONDS
            and _is_reconstructible(session)
        ):
            _forget_session(session_id)
    overflow = len(_sessions) - MAX_IN_MEMORY_SESSIONS
    if overflow > 0:
        by_age = sorted(_sessions.items(), key=lambda kv: _session_activity_epoch(kv[1]))
        for session_id, _ in by_age[:overflow]:
            _forget_session(session_id)
    run_overflow = len(_runs) - MAX_IN_MEMORY_SESSIONS
    if run_overflow > 0:
        for run_id in list(_runs)[:run_overflow]:
            _runs.pop(run_id, None)


def _remember_session(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    """Put a session into the in-memory cache, evicting stale entries around it.

    Args:
        session_id: The session's id.
        session: The session dict to cache.

    Returns:
        The same session dict, for use at the call site.
    """
    _sessions[session_id] = session
    _evict_stale_state()
    return session


def _remember_run(run_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Put a run record into the in-memory cache, under the same bound as sessions.

    Args:
        run_id: The run's id.
        record: The run record to cache.

    Returns:
        The same record, for use at the call site.
    """
    _runs[run_id] = record
    _evict_stale_state()
    return record


__all__ = [
    "_NON_RECONSTRUCTIBLE_KEYS",
    "_evict_stale_state",
    "_forget_session",
    "_is_reconstructible",
    "_remember_run",
    "_remember_session",
    "_runs",
    "_session_activity_epoch",
    "_sessions",
]
