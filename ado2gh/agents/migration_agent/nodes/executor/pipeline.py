"""Execute agent migrations through the accelerator pipeline runner (monitor-visible)."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

AccelGet = Callable[..., Awaitable[dict[str, Any]]]
AccelPost = Callable[..., Awaitable[dict[str, Any]]]
ProgressFn = Callable[[str, str, dict[str, Any]], None]

_PIPELINE_TERMINAL = frozenset({
    "completed",
    "failed",
    "cancelled",
    "dry_run_complete",
})
_STEP_TO_SCOPE = {
    "migrate_repos": "repo",
    "convert_pipelines": "pipelines",
    "convert_metadata": "branch_policies",
}


def agent_live_approved(session: dict[str, Any], *, dry_run: bool) -> bool:
    """True when the agent session already passed the platform live gate."""
    if dry_run:
        return False
    if session.get("live_approval_status") == "approved":
        return True
    from ado2gh.agents.migration_agent.policies import can_execute_live_without_approval

    return can_execute_live_without_approval(session)


def resolve_agent_repository_id(
    session: dict[str, Any],
    migration_plan: dict[str, Any] | None,
) -> str:
    """Canonical project/repo id for pipeline runs."""
    from ado2gh.agents.migration_agent.utils import (
        canonical_plan_repo_key,
        canonical_repo_id,
    )

    candidates: list[str] = []
    if session.get("plan_repository_id"):
        candidates.append(str(session["plan_repository_id"]))
    for rid in session.get("plan_repository_ids") or []:
        candidates.append(str(rid))
    plan = migration_plan or {}
    for repo in plan.get("repos") or []:
        if isinstance(repo, dict):
            candidates.append(canonical_repo_id(repo))
        elif repo:
            candidates.append(str(repo))
    if plan.get("repository_id"):
        candidates.append(str(plan["repository_id"]))

    discovery = session.get("discovery_snapshot") or {}

    for raw in candidates:
        key = canonical_plan_repo_key(raw, discovery if isinstance(discovery, dict) else None)
        if key:
            return key
    return ""


async def ensure_agent_pipeline_run(
    session: dict[str, Any],
    migration_plan: dict[str, Any],
    *,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,
) -> str | None:
    """Create a dashboard-visible pipeline run for this agent migration attempt."""
    existing = str(session.get("run_id") or "").strip()
    if existing:
        return existing
    if not accel_post:
        return None

    from ado2gh.agents.migration_agent.nodes.executor.plan import (
        AGENT_PIPELINE_STEP_IDS,
        resolve_migration_phase,
    )

    repo_id = resolve_agent_repository_id(session, migration_plan)
    seq = int(session.get("agent_run_seq", 0)) + 1
    session["agent_run_seq"] = seq
    mode = "dry-run" if dry_run else "live"
    target = repo_id or "migration"
    run_name = session.get("agent_run_label") or f"Agent: {target} ({mode} #{seq})"
    session["agent_run_label"] = run_name

    run_body: dict[str, Any] = {
        "name": run_name,
        "dry_run": dry_run,
        "wave_id": None,
        "steps": migration_plan.get("pipeline_step_ids") or AGENT_PIPELINE_STEP_IDS,
        "repository_id": repo_id or None,
        "migrate_deps_only": False,
        "agent_live_approved": agent_live_approved(session, dry_run=dry_run),
    }
    phase = resolve_migration_phase(session, migration_plan)
    if phase:
        run_body["phase"] = phase

    run_resp = await accel_post("/v1/pipeline/runs", run_body, session_token=session_token)
    if not isinstance(run_resp, dict):
        return None
    run = run_resp.get("run") if isinstance(run_resp.get("run"), dict) else run_resp
    run_id = str((run or {}).get("id") or run_resp.get("id") or "").strip()
    if not run_id:
        return None
    session["run_id"] = run_id
    session["pipeline_run_id"] = run_id
    return run_id


async def ensure_pipeline_running(
    session: dict[str, Any],
    run_id: str,
    *,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,
) -> None:
    """Start a pipeline run that was created but left awaiting approval."""
    if not accel_post or not run_id:
        return
    try:
        await accel_post(
            f"/v1/pipeline/runs/{run_id}/start",
            {"agent_live_approved": agent_live_approved(session, dry_run=dry_run)},
            session_token=session_token,
        )
    except Exception:
        return


async def poll_pipeline_run(
    run_id: str,
    *,
    accel_get: AccelGet,
    session_token: str | None,
    on_progress: ProgressFn | None = None,
    poll_interval_s: float = 3.0,
) -> dict[str, Any]:
    """Poll accelerator pipeline run until terminal status."""
    last_label = ""
    last_status = ""
    while True:
        resp = await accel_get(f"/v1/pipeline/runs/{run_id}", session_token=session_token)
        run = resp.get("run") if isinstance(resp.get("run"), dict) else resp
        if not isinstance(run, dict):
            raise RuntimeError(f"Pipeline run {run_id} not found")

        status = str(run.get("status") or "pending")
        label = _current_step_label(run)
        if on_progress and (label != last_label or status != last_status):
            on_progress(label, status, run)
        last_label = label
        last_status = status

        if status in _PIPELINE_TERMINAL:
            return run
        await asyncio.sleep(poll_interval_s)


def _current_step_label(run: dict[str, Any]) -> str:
    for step in run.get("steps") or []:
        if isinstance(step, dict) and step.get("status") == "running":
            return str(step.get("label") or step.get("id") or "")
    for step in run.get("steps") or []:
        if isinstance(step, dict) and step.get("status") == "pending":
            return str(step.get("label") or step.get("id") or "")
    return str(run.get("current_step") or "")


def _scope_status_from_step(step_status: str) -> str:
    normalized = str(step_status or "").lower()
    if normalized == "completed":
        return "success"
    if normalized == "warn":
        return "success"
    if normalized in ("failed", "skipped"):
        return normalized
    return normalized or "unknown"


def build_executor_result_from_pipeline(
    run: dict[str, Any],
    repo_id: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Map pipeline step outcomes to the agent executor_result shape."""
    scopes: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []

    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        scope = _STEP_TO_SCOPE.get(step_id)
        if not scope:
            continue
        step_status = str(step.get("status") or "")
        mapped = _scope_status_from_step(step_status)
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        scope_detail = ""
        scope_error = ""
        for detail in result.get("repo_details") or []:
            if not isinstance(detail, dict):
                continue
            if detail.get("repo") not in (repo_id, "", None) and str(detail.get("repo") or "") != repo_id:
                continue
            for row in detail.get("scopes") or []:
                if isinstance(row, dict) and str(row.get("scope") or "") in (scope, "git", "repo"):
                    scope_detail = str(row.get("detail") or row.get("error") or "")
                    scope_error = str(row.get("error") or "")
            for err in detail.get("errors") or []:
                if err and not scope_error:
                    scope_error = str(err)
        scopes[scope] = {
            "status": mapped,
            "pipeline_step": step_id,
            "message": scope_error or step.get("message") or "",
            "detail": scope_detail or scope_error,
            "error": scope_error,
            "result": result,
        }
        if step_status == "failed":
            msg = step.get("message") or f"Pipeline step {step_id} failed"
            failure: dict[str, Any] = {
                "repo": repo_id,
                "scope": scope,
                "error": msg,
                "error_code": f"{scope}_pipeline_error",
            }
            from ado2gh.agents.migration_agent.hitl.operator_input import is_fr036_failure

            if is_fr036_failure(msg) or is_fr036_failure(scope_error):
                failure["error_code"] = "migration_in_progress"
                failure["operator_input_required"] = True
            result_data = step.get("result") if isinstance(step.get("result"), dict) else {}
            holder = result_data.get("holder_run_id")
            if holder:
                failure["holder_run_id"] = holder
            failures.append(failure)

    run_status = str(run.get("status") or "")
    if run_status == "failed" and not failures:
        failures.append({
            "repo": repo_id,
            "scope": "pipeline",
            "error": run.get("error") or "Pipeline run failed",
            "error_code": "pipeline_failed",
        })

    return {
        "per_repo_results": [{"repo": repo_id, "scopes": scopes}],
        "failures": failures,
        "skipped": [],
        "dry_run": dry_run,
        "current_repo_id": repo_id,
        "pipeline_run_id": run.get("id"),
        "pipeline_status": run_status,
    }


async def execute_repo_migration(
    repo_id: str,
    ready_items: list[dict[str, Any]],
    migration_plan: dict[str, Any],
    session: dict[str, Any],
    *,
    accel_get: AccelGet | None,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,
    on_progress: ProgressFn | None = None,
    scope_executor: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run via accelerator pipeline when available; otherwise fall back to scope endpoints."""
    if accel_get and accel_post:
        executor_result = await execute_repo_via_pipeline(
            repo_id,
            migration_plan,
            session,
            accel_get=accel_get,
            accel_post=accel_post,
            session_token=session_token,
            dry_run=dry_run,
            on_progress=on_progress,
        )
        per_repo = (executor_result.get("per_repo_results") or [{}])[0]
        return per_repo, list(executor_result.get("failures") or []), []

    if scope_executor is None:
        raise RuntimeError("accelerator_unavailable")
    return await scope_executor(
        repo_id,
        ready_items,
        migration_plan,
        accel_get=accel_get,
        accel_post=accel_post,
        session_token=session_token,
        dry_run=dry_run,
        session=session,
    )


async def execute_repo_via_pipeline(
    repo_id: str,
    migration_plan: dict[str, Any],
    session: dict[str, Any],
    *,
    accel_get: AccelGet | None,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run migration through the accelerator pipeline and return executor_result."""
    if not accel_get or not accel_post:
        raise RuntimeError("accelerator_unavailable")

    run_id = await ensure_agent_pipeline_run(
        session,
        migration_plan,
        accel_post=accel_post,
        session_token=session_token,
        dry_run=dry_run,
    )
    if not run_id:
        raise RuntimeError("pipeline_run_create_failed")

    if on_progress:
        on_progress("Monitoring run created", "pending", {"id": run_id})

    await ensure_pipeline_running(
        session,
        run_id,
        accel_post=accel_post,
        session_token=session_token,
        dry_run=dry_run,
    )

    run = await poll_pipeline_run(
        run_id,
        accel_get=accel_get,
        session_token=session_token,
        on_progress=on_progress,
    )
    session["run_status"] = run.get("status")
    session["pipeline_run_snapshot"] = run
    return build_executor_result_from_pipeline(run, repo_id, dry_run=dry_run)
