"""Unified live execution approval queue (Agent PEV, migrate, pipeline)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from ado2gh.api.profile_governance import write_profile_audit
from ado2gh.auth.models import PlatformUser
from ado2gh.state.factory import create_state_db

ScopeType = str
ExecuteCallback = Callable[[dict], Any]

_migrate_executor: ExecuteCallback | None = None
_pipeline_executor: ExecuteCallback | None = None


def _internal_headers() -> dict[str, str]:
    """Shared secret for the agent's /v1/internal/ routes (see services/agent/main.py).

    Read per call rather than at import so tests and redeploys can change it without
    reimporting the module. Empty when unset, which the agent treats as open access.
    """
    token = os.environ.get("ADO2GH_INTERNAL_TOKEN", "")
    return {"x-ado2gh-internal-token": token} if token else {}


def register_migrate_executor(fn: ExecuteCallback) -> None:
    global _migrate_executor
    _migrate_executor = fn


def register_pipeline_executor(fn: ExecuteCallback) -> None:
    global _pipeline_executor
    _pipeline_executor = fn


def _db_path() -> str:
    return os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")


def _public_row(row: dict) -> dict:
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
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _db_path()
        self.db = create_state_db(self.db_path)

    def create_or_get_pending(
        self,
        requester: PlatformUser,
        scope_type: ScopeType,
        scope_id: str,
        *,
        profile_id: str | None = None,
        reason_request: str | None = None,
        context: dict | None = None,
    ) -> dict:
        existing = self.db.find_pending_live_execution_approval(scope_type, scope_id)
        if existing:
            return _public_row(existing)
        approval_id = f"lve_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        row = self.db.create_live_execution_approval(
            approval_id=approval_id,
            requester_user_id=requester.id,
            requester_username=requester.username,
            scope_type=scope_type,
            scope_id=scope_id,
            requested_at=now,
            profile_id=profile_id,
            reason_request=reason_request,
            context_json=json.dumps(context or {}),
        )
        write_profile_audit(
            "platform.live_execution.requested",
            profile_id=profile_id or "_platform",
            actor=requester.username,
            payload={
                "approval_id": approval_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "role": requester.role.value,
            },
            db_path=self.db_path,
        )
        return _public_row(row)

    def list_approvals(self, status: str = "pending", limit: int = 100) -> list[dict]:
        rows = self.db.list_live_execution_approvals(status=status, limit=limit)
        return [_public_row(r) for r in rows]

    def get_approval(
        self, approval_id: str, requester: PlatformUser | None = None,
    ) -> dict:
        row = self.db.get_live_execution_approval(approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval not found")
        if requester and requester.role.value not in ("admin", "approver"):
            if row["requester_user_id"] != requester.id:
                raise HTTPException(status_code=403, detail="Forbidden")
        return _public_row(row)

    def has_approved(self, scope_type: ScopeType, scope_id: str) -> bool:
        return self.db.find_approved_live_execution_approval(scope_type, scope_id) is not None

    def approve(
        self,
        approval_id: str,
        approver: PlatformUser,
        reason: str,
    ) -> dict:
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
            approval_id,
            "approved",
            approver.id,
            approver.username,
            reason,
            now,
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
            approval_id,
            "denied",
            approver.id,
            approver.username,
            reason,
            now,
        )
        assert updated is not None
        if updated["scope_type"] == "agent_session":
            self._notify_agent_denied(updated["scope_id"], reason)
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
        scope_type = row["scope_type"]
        if scope_type == "agent_session":
            self._notify_agent_resume(row["scope_id"])
        elif scope_type == "migrate_job":
            self._execute_migrate(row)
        elif scope_type == "pipeline_run":
            self._execute_pipeline(row)

    def _context(self, row: dict) -> dict:
        raw = row.get("context_json") or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _execute_migrate(self, row: dict) -> None:
        if not _migrate_executor:
            return
        ctx = self._context(row)
        _migrate_executor(ctx)

    def _execute_pipeline(self, row: dict) -> None:
        if not _pipeline_executor:
            return
        ctx = self._context(row)
        _pipeline_executor(ctx)

    def _notify_agent_resume(self, session_id: str) -> None:
        agent_url = os.environ.get("AGENT_URL", "http://agent:8090")
        try:
            httpx.post(
                f"{agent_url}/v1/internal/sessions/{session_id}/resume-live",
                headers=_internal_headers(),
                timeout=30.0,
            )
        except httpx.HTTPError:
            pass

    def _notify_agent_denied(self, session_id: str, reason: str) -> None:
        agent_url = os.environ.get("AGENT_URL", "http://agent:8090")
        try:
            httpx.post(
                f"{agent_url}/v1/internal/sessions/{session_id}/deny-live",
                json={"reason": reason},
                headers=_internal_headers(),
                timeout=30.0,
            )
        except httpx.HTTPError:
            pass

    def _mark_pipeline_denied(self, run_id: str, reason: str) -> None:
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
        from ado2gh.api.pipeline_runner import PipelineRunStore

        run = PipelineRunStore.get(row.get("scope_id", ""))
        if not run:
            return
        run.live_approval_status = status
        run.approved_by_username = approver.username
        run.approved_by_display_name = approver.display_name or approver.username
        run.updated_at = datetime.now(timezone.utc).isoformat()


def migrate_scope_id(profile_id: str | None, wave_id: int | None, config_path: str) -> str:
    return f"migrate:{profile_id or 'default'}:{wave_id or 'all'}:{config_path}"
