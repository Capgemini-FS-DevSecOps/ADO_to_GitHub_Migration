"""Agent session creation, cancellation, and migration-state cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ado2gh.agents.migration_agent.session.state import set_session_idle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_isolated_agent_session(
    session_id: str,
    *,
    profile_id: str,
    assignment_id: str | None = None,
    session_token: str | None = None,
    selected_model_id: str | None = None,
    llm_degraded: bool = False,
    llm_unconfigured: bool = False,
    dry_run: bool = True,
    user_username: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    """Build a fresh in-memory agent session with no migration context carried over."""
    from ado2gh.agents.migration_agent.graph import clear_langgraph_thread_sync

    clear_langgraph_thread_sync(session_id)
    return {
        "session_id": session_id,
        "profile_id": profile_id,
        "assignment_id": assignment_id,
        "session_token": session_token,
        "selected_model_id": selected_model_id,
        "llm_degraded": llm_degraded,
        "llm_unconfigured": llm_unconfigured,
        "status": "idle",
        "subagent": None,
        "dry_run": dry_run,
        "messages": [],
        "plan_id": None,
        "approval": None,
        "live_approval_id": None,
        "live_approval_status": None,
        "llm_available": not llm_unconfigured,
        "plan_approved": False,
        "tasks": [],
        "pev_retry_count": 0,
        "rollback_requested": False,
        "cancelled_at": None,
        "created_at": _now(),
        "updated_at": _now(),
        "user_username": user_username,
        "user_display_name": user_display_name,
    }


def collect_session_repo_ids(session: dict[str, Any]) -> list[str]:
    """Repository ids referenced by this session's migration state."""
    from ado2gh.agents.migration_agent.utils import canonical_repo_id, normalize_repo_key

    ids: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        text = normalize_repo_key(str(raw or "").strip())
        if text and text not in seen:
            seen.add(text)
            ids.append(text)

    _add(session.get("plan_repository_id"))
    for rid in session.get("plan_repository_ids") or []:
        _add(rid)
    plan = session.get("migration_plan") or {}
    if isinstance(plan, dict):
        _add(plan.get("repository_id"))
        for repo in plan.get("repos") or []:
            if isinstance(repo, dict):
                _add(canonical_repo_id(repo))
            else:
                _add(repo)
        for wi in plan.get("work_items") or []:
            if isinstance(wi, dict):
                _add(wi.get("repo"))
    return ids


def _repo_parts(repo_id: str) -> tuple[str, str]:
    if "/" in repo_id:
        project, repo = repo_id.split("/", 1)
        return project.strip(), repo.strip()
    return "", repo_id.strip()


def clear_session_migration_state(
    session: dict[str, Any],
    *,
    current_run_id: str | None = None,
) -> int:
    """Clear FR-036 in_progress rows for repos tied to this session."""
    from ado2gh.core.migration_fr036 import clear_stale_in_progress_migrations
    from ado2gh.state.factory import create_state_db

    db = create_state_db()
    cleared = 0
    for repo_id in collect_session_repo_ids(session):
        project, repo = _repo_parts(repo_id)
        if not repo:
            continue
        if not project:
            continue
        cleared += clear_stale_in_progress_migrations(
            db,
            project,
            repo,
            current_run_id=current_run_id,
        )
    return cleared


def release_session_repo_locks(session_id: str) -> None:
    try:
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

        MigrationSessionStore().release_all_locks(session_id)
    except Exception:
        pass


def persist_session_snapshot(session: dict[str, Any]) -> None:
    """Persist session metadata to the agent session store."""
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        return
    try:
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

        store = MigrationSessionStore()
        store.register_http_session(session)
        store.update_session_status(session_id, session.get("status", "idle"))
        store.update_session(
            session_id,
            pending_form=session.get("pending_form"),
            migration_plan=session.get("migration_plan"),
            iteration_count=int(session.get("iteration", 0) or 0),
            pev_retry_count=int(session.get("pev_retry_count", 0) or 0),
            dry_run=bool(session.get("dry_run", True)),
        )
    except Exception:
        pass


async def cancel_linked_pipeline_run(
    session: dict[str, Any],
    accel_post: Any | None,
    session_token: str | None,
) -> bool:
    """Request accelerator cancellation for the session's pipeline run."""
    run_id = str(session.get("run_id") or session.get("pipeline_run_id") or "").strip()
    if not run_id:
        return False
    if accel_post:
        try:
            await accel_post(
                f"/v1/pipeline/runs/{run_id}/cancel",
                {},
                session_token=session_token,
            )
            return True
        except Exception:
            pass
    try:
        from ado2gh.api.pipeline_runner import PipelineRunStore

        return PipelineRunStore.request_cancel(run_id)
    except Exception:
        return False


async def cancel_agent_session(
    session: dict[str, Any],
    *,
    action: str = "stop",
    accel_post: Any | None = None,
    session_token: str | None = None,
) -> dict[str, Any]:
    """Cancel an agent session and clean linked migration state."""
    from ado2gh.agents.migration_agent.session.state import reset_session_for_new_migration

    run_id = str(session.get("run_id") or session.get("pipeline_run_id") or "").strip()
    await cancel_linked_pipeline_run(session, accel_post, session_token)
    clear_session_migration_state(session, current_run_id=run_id or None)
    release_session_repo_locks(str(session.get("session_id") or ""))

    rollback = action == "rollback"
    session["cancelled_at"] = _now()
    session.pop("start_execution", None)
    session.pop("start_pev", None)
    session.pop("pending_clarification", None)
    session["pending_form"] = None
    if rollback:
        session["rollback_requested"] = True
    else:
        session.pop("rollback_requested", None)

    reset_session_for_new_migration(session)
    set_session_idle(session)
    session["updated_at"] = _now()
    persist_session_snapshot(session)

    try:
        from ado2gh.agents.migration_agent.graph import clear_langgraph_thread

        await clear_langgraph_thread(str(session.get("session_id") or ""))
    except Exception:
        pass

    if rollback:
        return {
            "status": "idle",
            "rollback": "initiated",
            "message": "Session cancelled. Rollback of session-created resources initiated.",
            "session_id": session.get("session_id"),
        }
    return {
        "status": "idle",
        "rollback": "not_requested",
        "message": "Session cancelled. Migration state for this session was cleared.",
        "session_id": session.get("session_id"),
    }


def clear_pipeline_run_migration_state(run: dict[str, Any] | Any) -> int:
    """Clear stale in_progress rows when a pipeline run ends or is cancelled."""
    from ado2gh.core.migration_fr036 import clear_stale_in_progress_migrations
    from ado2gh.state.factory import create_state_db

    if hasattr(run, "to_dict"):
        run_data = run.to_dict()
        run_id = getattr(run, "id", None)
    else:
        run_data = run if isinstance(run, dict) else {}
        run_id = run_data.get("id")
    repo_id = str(run_data.get("repository_id") or "").strip()
    if not repo_id or "/" not in repo_id:
        return 0
    project, repo = _repo_parts(repo_id)
    if not project or not repo:
        return 0
    return clear_stale_in_progress_migrations(
        create_state_db(),
        project,
        repo,
        current_run_id=str(run_id or "") or None,
    )
