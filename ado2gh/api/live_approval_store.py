"""Unified live execution approval queue (Agent PEV, migrate, pipeline)."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.profile_governance import write_profile_audit
from ado2gh.auth.models import PlatformUser
from ado2gh.state.factory import create_state_db

logger = logging.getLogger(__name__)

ScopeType = str
ExecuteCallback = Callable[[dict], Any]

_migrate_executor: ExecuteCallback | None = None
_pipeline_executor: ExecuteCallback | None = None


def _internal_headers() -> dict[str, str]:
    """Shared secret for the agent's /v1/internal/ routes (see services/agent/main.py).

    Read per call rather than at import so tests and redeploys can change it without
    reimporting the module. When it is unset we send no header at all, and the agent
    answers 401 — the range fails closed on both sides, so an accelerator that is
    missing the token can no longer resume or deny anything.
    """
    token = os.environ.get("ADO2GH_INTERNAL_TOKEN", "")
    return {"x-ado2gh-internal-token": token} if token else {}


def register_migrate_executor(fn: ExecuteCallback) -> None:
    """Register the callback that runs an approved ``migrate_job``.

    Args:
        fn: Called with the approval's stored context dict once an approver approves
            a migrate job. Replaces any callback registered before it.
    """
    global _migrate_executor
    _migrate_executor = fn


def register_pipeline_executor(fn: ExecuteCallback) -> None:
    """Register the callback that runs an approved ``pipeline_run``.

    Args:
        fn: Called with the approval's stored context dict once an approver approves
            a pipeline run. Replaces any callback registered before it.
    """
    global _pipeline_executor
    _pipeline_executor = fn


def _db_path() -> str:
    """Locate the state database holding the approval queue.

    Returns:
        The path from ``ADO2GH_SQLITE_PATH``, or ``migration_state.db`` in the
        working directory when that variable is unset.
    """
    return os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")


def _public_row(row: dict) -> dict:
    """Reduce a stored approval row to the fields the API may hand back.

    Args:
        row: Raw approval row as read from the state database.

    Returns:
        The approval id, requester username, scope type and id, profile, status,
        the request and decision reasons, both timestamps and the approver
        username. Internal columns — the requester's user id and the serialised
        context — are deliberately left out.
    """
    return {
        "id": row["id"],
        "requester_username": row["requester_username"],
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "profile_id": row.get("profile_id"),
        "status": row["status"],
        "reason_request": row.get("reason_request"),
        "reason_decision": row.get("reason_decision"),
        "requested_at": row["requested_at"],
        "decided_at": row.get("decided_at"),
        "approver_username": row.get("approver_username"),
    }


class LiveApprovalStore:
    """Queue of live-execution approvals shared by the agent, migrate and pipeline routes."""

    def __init__(self, db_path: str | None = None) -> None:
        """Open the approval queue against a state database.

        Args:
            db_path: State database holding the approval rows. Defaults to the path
                in ``ADO2GH_SQLITE_PATH``, or ``migration_state.db``.
        """
        self.db_path = db_path or _db_path()
        self.db = create_state_db(self.db_path)

    def create_or_get_pending(
        self,
        requester: PlatformUser,
        request: LiveApprovalCreateRequest,
    ) -> dict:
        """Open an approval request for a live run, or return the pending one.

        Idempotent per scope: a second request for a scope that is already awaiting
        a decision returns the existing row instead of queueing a duplicate. A newly
        created row is recorded as a ``platform.live_execution.requested`` audit
        event naming the requester (CA-004).

        Args:
            requester: The signed-in user asking to execute live. Comes from the
                server-side session, never from a request body.
            request: The scope to approve — ``scope_type`` and ``scope_id`` — plus
                the optional profile the run belongs to, the reason shown to the
                approver, and the context dict handed to the registered executor if
                the request is approved.

        Returns:
            The public view of the approval row: its id, requester, scope, profile,
            ``pending`` status and request timestamp.
        """
        existing = self.db.find_pending_live_execution_approval(
            request.scope_type, request.scope_id,
        )
        if existing:
            return _public_row(existing)
        approval_id = f"lve_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        row = self.db.create_live_execution_approval(
            approval_id=approval_id,
            requester_user_id=requester.id,
            requester_username=requester.username,
            scope_type=request.scope_type,
            scope_id=request.scope_id,
            requested_at=now,
            profile_id=request.profile_id,
            reason_request=request.reason_request,
            context_json=json.dumps(request.context or {}),
        )
        write_profile_audit(
            "platform.live_execution.requested",
            profile_id=request.profile_id or "_platform",
            actor=requester.username,
            payload={
                "approval_id": approval_id,
                "scope_type": request.scope_type,
                "scope_id": request.scope_id,
                "role": requester.role.value,
            },
            db_path=self.db_path,
        )
        return _public_row(row)

    def list_approvals(self, status: str = "pending", limit: int = 100) -> list[dict]:
        """List queued approvals in one status.

        Args:
            status: Approval status to list, one of ``pending``, ``approved`` or
                ``denied``.
            limit: Maximum number of approvals to return.

        Returns:
            The public view of each matching approval — id, requester, scope,
            status, reasons and timestamps.
        """
        rows = self.db.list_live_execution_approvals(status=status, limit=limit)
        return [_public_row(r) for r in rows]

    def get_approval(
        self, approval_id: str, requester: PlatformUser | None = None,
    ) -> dict:
        """Fetch one approval, optionally restricted to the user who requested it.

        Args:
            approval_id: Identifier of the approval to read.
            requester: When given, a caller who is neither an admin nor an approver
                may only read an approval they opened themselves.

        Returns:
            The public view of the approval: id, requester, scope, status, reasons
            and timestamps.

        Raises:
            HTTPException: 404 when no such approval exists, 403 when ``requester``
                may not see this one.
        """
        row = self.db.get_live_execution_approval(approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval not found")
        if requester and requester.role.value not in ("admin", "approver"):
            if row["requester_user_id"] != requester.id:
                raise HTTPException(status_code=403, detail="Forbidden")
        return _public_row(row)

    def has_approved(self, scope_type: ScopeType, scope_id: str) -> bool:
        """Report whether a scope already carries an approved decision.

        Args:
            scope_type: Kind of scope: ``agent_session``, ``migrate_job`` or
                ``pipeline_run``.
            scope_id: Identifier of the scope within that kind.

        Returns:
            ``True`` when an approver has approved this scope and it may execute
            live, ``False`` when it is unknown, still pending or denied.
        """
        return self.db.find_approved_live_execution_approval(scope_type, scope_id) is not None

    def approve(
        self,
        approval_id: str,
        approver: PlatformUser,
        reason: str,
    ) -> dict:
        """Approve a queued live run and release it for execution.

        Records the decision, stamps a pipeline run with the approver, resumes the
        scope — notifying the agent, or invoking the registered migrate or pipeline
        executor — and writes a ``platform.live_execution.approved`` audit event.

        Args:
            approval_id: Identifier of the approval to decide.
            approver: The user making the decision; recorded as the actor.
            reason: The approver's justification, kept on the row and in the audit
                event.

        Returns:
            The public view of the decided approval, now ``approved``, carrying the
            approver username, decision reason and decision timestamp.

        Raises:
            HTTPException: 404 when no such approval exists, 409 when it has already
                been approved or denied.
        """
        row = self.db.get_live_execution_approval(approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval not found")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail={"message": "Already decided", "status": row["status"]},
            )
        now = datetime.now(timezone.utc).isoformat()
        updated = self.db.decide_live_execution_approval(
            approval_id, "approved", approver, reason, now,
        )
        assert updated is not None
        if updated["scope_type"] == "pipeline_run":
            self._stamp_pipeline_approval(updated, "approved", approver)
        self._resume_scope(updated)
        write_profile_audit(
            "platform.live_execution.approved",
            profile_id=updated.get("profile_id") or "_platform",
            actor=approver.username,
            payload={
                "approval_id": approval_id,
                "scope_type": updated["scope_type"],
                "scope_id": updated["scope_id"],
                "role": approver.role.value,
                "reason": reason,
            },
            db_path=self.db_path,
        )
        return _public_row(updated)

    def deny(
        self,
        approval_id: str,
        approver: PlatformUser,
        reason: str,
    ) -> dict:
        """Deny a queued live run so it never executes.

        Records the decision, tells the agent that a session was denied or marks a
        pipeline run denied with the reason, and writes a
        ``platform.live_execution.denied`` audit event.

        Args:
            approval_id: Identifier of the approval to decide.
            approver: The user making the decision; recorded as the actor.
            reason: The approver's justification, shown to the requester and kept in
                the audit event.

        Returns:
            The public view of the decided approval, now ``denied``, carrying the
            approver username, decision reason and decision timestamp.

        Raises:
            HTTPException: 404 when no such approval exists, 409 when it has already
                been approved or denied.
        """
        row = self.db.get_live_execution_approval(approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval not found")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail={"message": "Already decided", "status": row["status"]},
            )
        now = datetime.now(timezone.utc).isoformat()
        updated = self.db.decide_live_execution_approval(
            approval_id, "denied", approver, reason, now,
        )
        assert updated is not None
        if updated["scope_type"] == "agent_session":
            self._notify_agent(updated, "deny-live", {"reason": reason})
        elif updated["scope_type"] == "pipeline_run":
            self._stamp_pipeline_approval(updated, "denied", approver)
            self._mark_pipeline_denied(updated["scope_id"], reason)
        write_profile_audit(
            "platform.live_execution.denied",
            profile_id=updated.get("profile_id") or "_platform",
            actor=approver.username,
            payload={
                "approval_id": approval_id,
                "scope_type": updated["scope_type"],
                "reason": reason,
            },
            db_path=self.db_path,
        )
        return _public_row(updated)

    def _resume_scope(self, row: dict) -> None:
        """Hand an approved row to whatever executes its kind of scope.

        Args:
            row: The decided approval row. An agent session is notified over HTTP; a
                migrate job or pipeline run is passed to its registered executor.
        """
        scope_type = row["scope_type"]
        if scope_type == "agent_session":
            self._notify_agent(row, "resume-live")
        elif scope_type == "migrate_job":
            self._execute_migrate(row)
        elif scope_type == "pipeline_run":
            self._execute_pipeline(row)

    def _context(self, row: dict) -> dict:
        """Decode the executor context stored with an approval.

        Args:
            row: The approval row holding the serialised context.

        Returns:
            The stored context dict, or an empty dict when the row has none or the
            stored value is not valid JSON.
        """
        raw = row.get("context_json") or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _execute_migrate(self, row: dict) -> None:
        """Run an approved migrate job, if a migrate executor was registered.

        Args:
            row: The approved approval row carrying the migrate context.
        """
        if not _migrate_executor:
            return
        ctx = self._context(row)
        _migrate_executor(ctx)

    def _execute_pipeline(self, row: dict) -> None:
        """Run an approved pipeline run, if a pipeline executor was registered.

        Args:
            row: The approved approval row carrying the pipeline-run context.
        """
        if not _pipeline_executor:
            return
        ctx = self._context(row)
        _pipeline_executor(ctx)

    def _notify_agent(
        self, row: dict, route: str, payload: dict | None = None,
    ) -> None:
        """Tell the agent about a decision that has already been written.

        The approval row is committed before we get here, so a failed notify must not
        raise: rolling the caller back would leave the decision recorded while the
        approver sees a 500. It must not be silent either. Since the /v1/internal/
        range started failing closed, a missing or mismatched ADO2GH_INTERNAL_TOKEN
        answers 401, and the session then waits forever with the approval showing as
        decided. So record the failure where an operator already looks — an audit
        event under the approver's own name, plus an ERROR log.

        Nothing derived from the request or its headers may be logged: they carry the
        internal token (CA-003). The status code and the exception type are enough.
        """
        session_id = row["scope_id"]
        agent_url = os.environ.get("AGENT_URL", "http://agent:8090")
        try:
            resp = httpx.post(
                f"{agent_url}/v1/internal/sessions/{session_id}/{route}",
                json=payload,
                headers=_internal_headers(),
                timeout=30.0,
            )
            if resp.is_success:
                return
            error = f"agent returned HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            error = f"agent unreachable ({type(exc).__name__})"
        logger.error(
            "Live-execution %s notify failed for agent session %s: %s. The decision is "
            "recorded, but the session will not move until the agent is told — check "
            "that ADO2GH_INTERNAL_TOKEN is set to the same value on both services.",
            route,
            session_id,
            error,
        )
        try:
            write_profile_audit(
                "platform.live_execution.notify_failed",
                profile_id=row.get("profile_id") or "_platform",
                actor=row.get("approver_username") or "_system",
                payload={
                    "approval_id": row["id"],
                    "scope_type": row["scope_type"],
                    "scope_id": session_id,
                    "route": route,
                    "error": error,
                },
                db_path=self.db_path,
            )
        except Exception:  # noqa: BLE001 - reporting a failure must not become one
            logger.exception("Could not record the live-execution notify failure")

    def _mark_pipeline_denied(self, run_id: str, reason: str) -> None:
        """Mark an in-memory pipeline run as denied so the console stops waiting.

        Args:
            run_id: Identifier of the pipeline run that was denied.
            reason: The approver's justification, surfaced as the run's error.
        """
        from ado2gh.api.pipeline_runner import PipelineRunStore

        run = PipelineRunStore.get(run_id)
        if run:
            run.status = "denied"
            run.live_approval_status = "denied"
            run.error = reason
            run.updated_at = datetime.now(timezone.utc).isoformat()

    def _stamp_pipeline_approval(
        self,
        row: dict,
        status: str,
        approver: PlatformUser,
    ) -> None:
        """Record an approval decision on the pipeline run it belongs to.

        Args:
            row: The decided approval row naming the pipeline run.
            status: Decision to stamp, ``approved`` or ``denied``.
            approver: The deciding user, shown against the run in the console.
        """
        from ado2gh.api.pipeline_runner import PipelineRunStore

        run = PipelineRunStore.get(row.get("scope_id", ""))
        if not run:
            return
        run.live_approval_status = status
        run.approved_by_username = approver.username
        run.approved_by_display_name = approver.display_name or approver.username
        run.updated_at = datetime.now(timezone.utc).isoformat()


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
    """
    return f"migrate:{profile_id or 'default'}:{wave_id or 'all'}:{config_path}"
