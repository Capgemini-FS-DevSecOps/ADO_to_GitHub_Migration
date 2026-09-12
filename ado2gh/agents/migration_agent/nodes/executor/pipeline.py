"""Execute agent migrations through the accelerator pipeline runner (monitor-visible)."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ado2gh.models import ExecutionMode

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
    mode: ExecutionMode,
) -> str | None:
    """Create a dashboard-visible pipeline run for this agent migration attempt.

    Reuses the session's existing run when there is one, so a retried attempt does not
    open a second row on the dashboard.

    Args:
        session: The live agent session dict; the new run id and label are written back
            onto it.
        migration_plan: The plan being executed, read for its step ids and phase.
        accel_post: Accelerator POST callable, or ``None`` when none was injected.
        session_token: Bearer token forwarded to the accelerator.
        mode: Whether this attempt previews or writes; sent to the accelerator as the
            wire-level ``dry_run`` boolean and used in the run's display label.

    Returns:
        The pipeline run id, or ``None`` when no accelerator is available or the
        accelerator's response carried no id.
    """
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
    dry_run = mode is ExecutionMode.DRY_RUN
    label = "dry-run" if dry_run else "live"
    target = repo_id or "migration"
    run_name = session.get("agent_run_label") or f"Agent: {target} ({label} #{seq})"
    session["agent_run_label"] = run_name

    run_body: dict[str, Any] = {
        "name": run_name,
        "dry_run": dry_run,
        "wave_id": None,
        "steps": migration_plan.get("pipeline_step_ids") or AGENT_PIPELINE_STEP_IDS,
        "repository_id": repo_id or None,
        "migrate_deps_only": False,
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
    run_id: str,
    *,
    accel_post: AccelPost | None,
    session_token: str | None,
) -> None:
    """Start a pipeline run that was created but left awaiting approval.

    The accelerator decides whether the run may go live; this call carries no
    approval claim of its own.
    """
    if not accel_post or not run_id:
        return
    try:
        await accel_post(
            f"/v1/pipeline/runs/{run_id}/start", {}, session_token=session_token,
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
    """Label of the step a run is on.

    Returns:
        The label of the first running step, else the first pending one, else
        the run's ``current_step``; an empty string when none is set.
    """
    for step in run.get("steps") or []:
        if isinstance(step, dict) and step.get("status") == "running":
            return str(step.get("label") or step.get("id") or "")
    for step in run.get("steps") or []:
        if isinstance(step, dict) and step.get("status") == "pending":
            return str(step.get("label") or step.get("id") or "")
    return str(run.get("current_step") or "")


def _scope_status_from_step(step_status: str) -> str:
    """Translate a pipeline step status into an agent scope status.

    Returns:
        ``"success"`` for completed and warned steps, the status itself for
        failed and skipped ones, and ``"unknown"`` when the step has no status.
    """
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


async def execute_repo_via_pipeline(
    repo_id: str,
    migration_plan: dict[str, Any],
    session: dict[str, Any],
    *,
    deps: dict[str, Any] | None = None,
    dry_run: bool,
) -> dict[str, Any]:
    """Run migration through the accelerator pipeline and return executor_result.

    Args:
        repo_id: Canonical ``Project/Repo`` id being migrated.
        migration_plan: The plan whose steps and phase drive the pipeline run.
        session: The live agent session dict; the run id and status are written onto it.
        deps: Per-invocation runtime dependencies, using the same key names as
            ``runtime/deps.py``: ``accel_get`` and ``accel_post`` (both required here),
            ``session_token``, and ``on_progress`` — a callable the executor node uses
            to stream step progress into the chat.
        dry_run: Whether this run previews or writes. Reviewer-classified as data
            rather than a mode switch, so it keeps its boolean form and is passed
            straight through to the accelerator's ``dry_run`` field.

    Returns:
        The executor result mapped from the terminal pipeline run — per-repo
        scope outcomes, failures, and the pipeline run id and status.

    Raises:
        RuntimeError: When no accelerator callables were supplied, or the
            pipeline run could not be created.
    """
    deps = deps or {}
    accel_get: AccelGet | None = deps.get("accel_get")
    accel_post: AccelPost | None = deps.get("accel_post")
    session_token: str | None = deps.get("session_token")
    on_progress: ProgressFn | None = deps.get("on_progress")
    if not accel_get or not accel_post:
        raise RuntimeError("accelerator_unavailable")

    run_id = await ensure_agent_pipeline_run(
        session,
        migration_plan,
        accel_post=accel_post,
        session_token=session_token,
        mode=ExecutionMode.from_dry_run(dry_run=dry_run),
    )
    if not run_id:
        raise RuntimeError("pipeline_run_create_failed")

    if on_progress:
        on_progress("Monitoring run created", "pending", {"id": run_id})

    await ensure_pipeline_running(
        run_id,
        accel_post=accel_post,
        session_token=session_token,
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
