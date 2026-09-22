"""Scope identifiers and context validation for the live execution approval queue.

Split out of ``live_approval_store`` (docs/STRUCTURAL_CHANGELOG.md) to keep that
module under the project's file-size cap. This module owns the question "does
this context name the same work as this scope id" for every scope type the
queue accepts; ``LiveApprovalStore`` owns storage, decisions and dispatch.
"""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import ValidationError

from ado2gh.api.contracts import LiveApprovalCreateRequest, RunWaveRequest

ScopeType = str

# The only context keys an approved ``migrate_job`` may carry into the run-wave
# request. An allowlist, not a filter: the approver sees a scope id and nothing
# else, so anything the scope does not encode must not reach the executor
# (GAP-071, CA-002). ``dry_run`` is absent deliberately — it is set from the
# scope at execution, because a queued migrate job is live by definition.
# ``db_path`` is kept because it names the state database the run records itself
# in, which every caller of the unguarded ``POST /v1/plan`` already chooses; it
# selects no migration target and so grants nothing the scope withholds.
_MIGRATE_CONTEXT_KEYS = ("config_path", "wave_id", "db_path")

PIPELINE_RUN_SCOPE_TYPE: ScopeType = "pipeline_run"
"""Approval scope type for one persisted pipeline run."""

AGENT_SESSION_SCOPE_TYPE: ScopeType = "agent_session"
"""Approval scope type for one agent chat session's live-execution request.

The scope id for this type is the session's own identifier: an agent session
has no wave or run to fold in, and ``_notify_agent`` reads ``row["scope_id"]``
back as the session id when it calls the session's own service (GAP-110).
"""

PIPELINE_RUN_CONTEXT_RUN_ID = "run_id"
"""Context key whose value must equal the pipeline-run scope id (GAP-108)."""


def pipeline_run_scope_id(run_id: str) -> str:
    """Build the approval scope id for a persisted pipeline run.

    A pipeline run has no wave or config to fold in the way a migrate job does
    (``migrate_scope_id``), so the scope id is the run's own identifier. Kept
    as a function, not a literal, so every caller that derives or compares a
    pipeline-run scope reads the same statement of what it means (GAP-108).

    Args:
        run_id: Identifier of the persisted pipeline run.

    Returns:
        The run identifier unchanged.
    """
    return run_id


def migrate_scope_id(profile_id: str | None, wave_id: int | None, config_path: str) -> str:
    """Build the approval scope id for a dashboard migrate run.

    Args:
        profile_id: Active migration profile, or ``None`` for the default profile.
        wave_id: Wave being migrated, or ``None`` when every wave is.
        config_path: Migration config the run was started from.

    Returns:
        A stable ``migrate:<profile>:<wave>:<config>`` identifier, so repeated
        requests for the same profile, wave and config resolve to one approval row
        instead of queueing duplicates.

        Absence is tested, not falsiness. ``wave_id or 'all'`` made wave ``0`` — a
        wave a config may legitimately declare — indistinguishable from "every
        wave", so an approval for the narrowest live migration released the widest
        one; ``profile_id or 'default'`` did the same to a profile named ``""``
        (GAP-072, CA-002).
    """
    wave = "all" if wave_id is None else wave_id
    profile = "default" if profile_id is None else profile_id
    return f"migrate:{profile}:{wave}:{config_path}"


def _migrate_job_params(context: dict) -> dict | None:
    """Canonicalise a ``migrate_job`` context into the run-wave it would execute.

    Validation happens here rather than at the executor so that the scope derived
    from a context and the request built from it are the same reading of the same
    values. Deriving from the raw dict instead let the two disagree: ``" 1"``,
    ``"01"``, ``1.0`` and ``True`` all render distinct scope ids and all arrive at
    the executor as wave ``1`` (GAP-071).

    Args:
        context: The approval's executor context, as the client supplied it.

    Returns:
        The parameters this context executes — ``config_path``, ``wave_id``,
        ``db_path`` and a ``dry_run`` forced live, because a queued migrate job is
        live by definition and never takes that flag from the client. ``None``
        when the context names a ``/v1/migrate/*`` feature route, which executes
        nothing here, or is not a run-wave request at all; ``None`` runs nothing,
        so an unreadable context fails closed.
    """
    if context.get("route"):
        return None
    try:
        req = RunWaveRequest(
            **{k: context[k] for k in _MIGRATE_CONTEXT_KEYS if k in context},
            dry_run=False,
        )
    except ValidationError:
        return None
    return req.model_dump(include={*_MIGRATE_CONTEXT_KEYS, "dry_run"})


def _pipeline_run_params(context: dict) -> dict[str, str] | None:
    """Canonicalise a ``pipeline_run`` context into what it may execute.

    Args:
        context: The approval's executor context, as the client supplied it.

    Returns:
        The allowlisted run identifier, keyed by
        ``PIPELINE_RUN_CONTEXT_RUN_ID``. ``None`` when the context does not
        name a run id, which fails closed the same way ``_migrate_job_params``
        does.
    """
    run_id = context.get(PIPELINE_RUN_CONTEXT_RUN_ID)
    if not isinstance(run_id, str):
        return None
    return {PIPELINE_RUN_CONTEXT_RUN_ID: run_id}


def _assert_migrate_context_matches(
    request: LiveApprovalCreateRequest, context: dict,
) -> None:
    """Refuse a ``migrate_job`` whose context names work its scope id does not.

    The approver is shown a scope id and never the context, so the two have to be
    the same statement about the same work.

    Args:
        request: The approval being opened.
        context: The context as it will be stored — redacted already, so the
            check runs on the bytes the executor will later read back rather than
            on a copy that masking may still change (GAP-073).

    Raises:
        HTTPException: 422 when the context re-derives to a different scope than
            the one the approver will be shown, or asks for a dry run — a queued
            approval exists to release a live run, so ``dry_run`` true is a
            request that contradicts itself.
    """
    if request.scope_type != "migrate_job":
        return
    params = _migrate_job_params(context)
    if params is None:
        return
    derived = migrate_scope_id(
        request.profile_id, params["wave_id"], params["config_path"],
    )
    if derived == request.scope_id and not context.get("dry_run"):
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "scope_context_mismatch",
            "scope_id": request.scope_id,
            "derived_scope_id": derived,
        },
    )


def _assert_pipeline_context_matches(
    request: LiveApprovalCreateRequest, context: dict,
) -> None:
    """Refuse a ``pipeline_run`` whose context names a run its scope id does not.

    Mirrors ``_assert_migrate_context_matches`` for the pipeline-run scope
    (GAP-108): the approver is shown a scope id and never the context, so the
    two have to name the same run.

    Args:
        request: The approval being opened.
        context: The context as it will be stored — redacted already, so the
            check runs on the bytes the executor will later read back.

    Raises:
        HTTPException: 422 when the context re-derives to a different scope
            than the one the approver will be shown.
    """
    if request.scope_type != PIPELINE_RUN_SCOPE_TYPE:
        return
    params = _pipeline_run_params(context)
    if params is None:
        return
    derived = pipeline_run_scope_id(params[PIPELINE_RUN_CONTEXT_RUN_ID])
    if derived == request.scope_id:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "scope_context_mismatch",
            "scope_id": request.scope_id,
            "derived_scope_id": derived,
        },
    )


__all__ = [
    "AGENT_SESSION_SCOPE_TYPE",
    "PIPELINE_RUN_CONTEXT_RUN_ID",
    "PIPELINE_RUN_SCOPE_TYPE",
    "ScopeType",
    "migrate_scope_id",
    "pipeline_run_scope_id",
]
