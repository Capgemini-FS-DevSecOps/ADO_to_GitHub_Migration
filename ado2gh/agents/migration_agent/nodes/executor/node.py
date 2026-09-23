"""Executor graph node — deterministic per-repo migration execution."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ado2gh.agents.migration_agent.nodes._common import (
    _executor_result_for_repo_lock,
    _transition_session,
)
from ado2gh.agents.migration_agent.session.state import SessionState
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    canonical_plan_repo_key,
)
from ado2gh.models import ExecutionMode

if TYPE_CHECKING:
    from ado2gh.agents.migration_agent.nodes.executor.pipeline import AccelGet, AccelPost

logger = logging.getLogger(__name__)

# ─── Node: executor ───────────────────────────────────────────────────

_AGENT_WRITE_SCOPES = frozenset({
    "git", "repo", "pipelines", "secrets", "service_connections",
    "boards", "work_items", "branch_policies", "test_plans", "artifacts", "wiki",
})


async def _execute_deterministic_repo_scopes(
    repo_id: str,
    ready_items: list[dict[str, Any]],
    migration_plan: dict[str, Any],
    *,
    session: dict[str, Any],
    deps: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run only the ready plan scopes for one repository.

    Args:
        repo_id: Canonical ``Project/Repo`` id being migrated.
        ready_items: The plan work items whose dependencies are satisfied.
        migration_plan: The approved plan the work items belong to.
        session: Live agent session. The execution mode is read off
            ``session["dry_run"]``, which ``executor_node`` resolves and writes
            before any repository runs; an absent key means dry-run.
        deps: Runtime dependency mapping in the shape returned by
            ``runtime.deps.get_runtime_deps`` — ``accel_get``, ``accel_post``
            and ``session_token`` are the keys read here.

    Returns:
        A ``(repo_result, failures, rollback_records)`` triple. ``repo_result``
        holds one entry per executed scope, ``failures`` the errors that are
        neither skipped nor pending, and ``rollback_records`` one entry per
        write scope that succeeded during a live run (empty in dry-run).
    """
    from ado2gh.agents.migration_agent.nodes.executor.plan import work_item_scopes

    runtime = deps or {}
    mode = ExecutionMode.from_dry_run(dry_run=bool(session.get("dry_run", True)))
    discovery = session.get("discovery_snapshot")
    if isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    repo_id = canonical_plan_repo_key(
        repo_id,
        discovery if isinstance(discovery, dict) else None,
    )
    scope_labels = [str(wi.get("scope") or "") for wi in ready_items if wi.get("scope")]
    if scope_labels:
        _append_and_stream(
            session,
            role="system",
            content=f"Executor: processing {repo_id} (scopes: {', '.join(scope_labels)})…",
            subagent="executor",
        )

    repo_result: dict[str, Any] = {"repo": repo_id, "scopes": {}}
    failures: list[dict[str, Any]] = []
    rollback_records: list[dict[str, Any]] = []

    for wi in ready_items:
        for scope in work_item_scopes(wi):
            scope_result = await _execute_scope(
                scope,
                wi,
                migration_plan,
                runtime.get("accel_get"),
                runtime.get("accel_post"),
                runtime.get("session_token"),
                mode is ExecutionMode.DRY_RUN,
                session,
            )
            repo_result["scopes"][scope] = scope_result
            if scope_result.get("error") and scope_result.get("status") not in (
                "skipped",
                "pending",
            ):
                failures.append({
                    "repo": repo_id,
                    "scope": scope,
                    "error": scope_result["error"],
                    "error_code": scope_result.get("error_code", "execution_error"),
                })
            if (
                scope in _AGENT_WRITE_SCOPES
                and not scope_result.get("error")
                and mode is ExecutionMode.LIVE
            ):
                rollback_records.append({
                    "resource_type": scope,
                    "resource_name": repo_id,
                    "github_org": wi.get("github_org", ""),
                    "correlation_id": f"{repo_id}:{scope}",
                })
    return repo_result, failures, rollback_records


async def executor_node(state: dict[str, Any]) -> dict[str, Any]:
    """Executor node — performs migration operations deterministically.

    Binds executor tools, streams execution thoughts, executes migration
    operations, produces ExecutorResult, tracks RollbackRecord entries,
    handles idempotency, routes via accelerator API for existing ops and
    direct API for new resource types.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.EXECUTING)
    accel_get = state.get("accel_get")
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")
    migration_plan = state.get("migration_plan") or session.get("migration_plan")
    iteration = state.get("iteration", 0)

    iteration += 1

    # GAP-006: one resolution for both the banner and the run — the banner must
    # announce the mode the run actually executes in.
    from ado2gh.agents.migration_agent.policies import resolve_execution_dry_run
    dry_run = resolve_execution_dry_run(session, migration_plan)

    _append_and_stream(
        session,
        role="system",
        content=(
            "Executor: starting migration operations in dry-run (simulated) mode…"
            if dry_run
            else "Executor: starting migration operations in live mode…"
        ),
        subagent="executor",
    )

    if not migration_plan:
        _append_and_stream(
            session,
            role="system",
            content="Executor: no migration plan — sending failure to validator.",
            subagent="executor",
        )
        return {
            "executor_result": {
                "per_repo_results": [],
                "failures": [{
                    "error": "No migration plan available for execution.",
                    "error_code": "no_plan",
                }],
                "skipped": [],
                "dry_run": session.get("dry_run", True),
            },
            "iteration": iteration,
        }

    work_items = migration_plan.get("work_items", [])
    session["dry_run"] = dry_run

    from ado2gh.agents.migration_agent.nodes.executor.pipeline import execute_repo_via_pipeline

    def _pipeline_progress(label: str, status: str, run: dict[str, Any]) -> None:
        run_id = str(run.get("id") or session.get("run_id") or "")
        if label == "Monitoring run created" and run_id:
            _append_and_stream(
                session,
                role="system",
                content=f"Executor: created pipeline run {run_id} for monitoring.",
                subagent="executor",
            )
            return
        if label:
            _append_and_stream(
                session,
                role="system",
                content=f"Pipeline: {label} ({status})…",
                subagent="executor",
            )

    async def _run_repo(
        repo_id: str,
        ready_items: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Migrate one repo via the accelerator pipeline, else scope by scope.

        Returns:
            The ``(repo_result, failures, rollback_records)`` triple produced by
            whichever execution path was available.
        """
        if accel_get and accel_post:
            executor_result = await execute_repo_via_pipeline(
                repo_id,
                migration_plan,
                session,
                deps={
                    "accel_get": accel_get,
                    "accel_post": accel_post,
                    "session_token": session_token,
                    "on_progress": _pipeline_progress,
                },
                dry_run=dry_run,
            )
            per_repo = (executor_result.get("per_repo_results") or [{}])[0]
            return per_repo, list(executor_result.get("failures") or []), []
        return await _execute_deterministic_repo_scopes(
            repo_id,
            ready_items,
            migration_plan,
            session=session,
            deps={
                "accel_get": accel_get,
                "accel_post": accel_post,
                "session_token": session_token,
            },
        )

    # Use language-model-driven execution when available
    # Disabled: language-model tool-calling protocol not fully integrated — deterministic
    # execution is reliable and calls _execute_scope directly.
    use_llm_execution = False

    # Check for migration queue for sequential processing
    migration_queue = state.get("migration_queue")
    if migration_queue:
        queue_items = migration_queue.get("items", [])
        current_index = migration_queue.get("current_index", 0)

        # Process one repository at a time
        if current_index < len(queue_items):
            queue_item = queue_items[current_index]
            discovery = session.get("discovery_snapshot")
            if isinstance(discovery, str):
                try:
                    discovery = json.loads(discovery)
                except Exception:
                    discovery = {}
            if not isinstance(discovery, dict):
                discovery = {}
            repo_id = canonical_plan_repo_key(
                str(queue_item.get("repo_id", "") or "").strip(),
                discovery,
            )
            item_work_items = queue_item.get("work_items", [])
            if not repo_id and item_work_items:
                repo_id = canonical_plan_repo_key(
                    str(item_work_items[0].get("repo") or "").strip(),
                    discovery,
                )

            per_repo_results = []
            failures = []
            skipped = []
            rollback_records = []

            from ado2gh.api.migration_work_plan import filter_executable_work_items

            for wi in item_work_items:
                if wi.get("status") == "blocked":
                    skipped.append({
                        "repo": repo_id,
                        "reason": "blocked",
                        "details": [wi.get("blocker") or "Work item status: blocked"],
                    })

            ready_items = filter_executable_work_items(item_work_items)
            session_id = session.get("session_id", "")
            if ready_items:
                lock_acquired = False
                if session_id:
                    try:
                        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
                        store = MigrationSessionStore()
                        if not store.acquire_repo_lock(session_id, repo_id):
                            holder = store.repo_lock_holder(repo_id)
                            return _executor_result_for_repo_lock(
                                session,
                                repo_id=repo_id,
                                lock_holder_session_id=holder,
                                iteration=iteration,
                                migration_queue=migration_queue,
                            )
                        lock_acquired = True
                    except Exception:
                        logger.warning("repo lock acquire failed for %s; proceeding without lock", repo_id, exc_info=True)

                try:
                    if use_llm_execution:
                        _append_and_stream(
                            session,
                            role="system",
                            content=(
                                f"Executor: LLM execution not fully integrated — "
                                f"using deterministic path for {repo_id}."
                            ),
                            subagent="executor",
                        )

                    repo_result, wi_failures, wi_rollbacks = await _run_repo(
                        repo_id,
                        ready_items,
                    )
                    per_repo_results.append(repo_result)
                    failures.extend(wi_failures)
                    rollback_records.extend(wi_rollbacks)
                finally:
                    if lock_acquired:
                        try:
                            from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
                            store = MigrationSessionStore()
                            store.release_repo_lock(session_id, repo_id)
                        except Exception:
                            logger.warning("repo lock release failed for %s", repo_id, exc_info=True)
            else:
                for wi in item_work_items:
                    status = wi.get("status", "ready")
                    if status in ("blocked", "skipped"):
                        skipped.append({
                            "repo": repo_id or wi.get("repo", ""),
                            "scope": wi.get("scope", ""),
                            "reason": status,
                            "details": [wi.get("blocker") or f"Work item status: {status}"],
                        })
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Executor: no ready scopes for {repo_id or 'repository'} "
                        f"— skipping execution ({len(skipped)} item(s) not ready)."
                    ),
                    subagent="executor",
                )

            # Don't advance queue index yet - let validator run first
            # Queue index will be advanced after validation passes
            executor_result = {
                "per_repo_results": per_repo_results,
                "failures": failures,
                "skipped": skipped,
                "dry_run": dry_run,
                "current_repo_id": repo_id,  # Track which repo was processed
            }

            _append_event(
                session,
                role="system",
                content=f"Executor: processed {repo_id} — awaiting validation (queue progress: {current_index + 1}/{len(queue_items)})",
                kind="thinking",
                subagent="executor",
            )

            return {
                "executor_result": executor_result,
                "rollback_records": rollback_records,
                "migration_queue": migration_queue,
                "iteration": iteration,
                "pending_clarification": None,
                "should_return": False,
            }
        else:
            # Queue already exhausted — do not re-run validation loop
            _append_and_stream(
                session,
                role="system",
                content="Executor: all queue items already processed.",
                subagent="executor",
            )
            return {
                "migration_queue": migration_queue,
                "iteration": iteration,
                "pending_clarification": None,
                "should_return": True,
            }

    # Fallback: process all work items (non-queue mode)
    from ado2gh.api.migration_work_plan import (
        filter_executable_work_items,
        group_work_items_by_repo,
    )

    per_repo_results = []
    failures = []
    skipped = []
    rollback_records = []

    for wi in work_items:
        if wi.get("status") == "blocked":
            skipped.append({
                "repo": wi.get("repo", ""),
                "reason": "blocked",
                "details": [wi.get("blocker") or "Work item status: blocked"],
            })

    for repo_id, repo_ready_items in group_work_items_by_repo(
        filter_executable_work_items(work_items)
    ).items():
        session_id = session.get("session_id", "")
        lock_acquired = False
        if session_id:
            try:
                from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
                store = MigrationSessionStore()
                if not store.acquire_repo_lock(session_id, repo_id):
                    holder = store.repo_lock_holder(repo_id)
                    lock_result = _executor_result_for_repo_lock(
                        session,
                        repo_id=repo_id,
                        lock_holder_session_id=holder,
                        iteration=iteration,
                    )
                    failures.extend(lock_result["executor_result"]["failures"])
                    skipped.extend(lock_result["executor_result"]["skipped"])
                    continue
                lock_acquired = True
            except Exception:
                logger.warning("repo lock acquire failed for %s; proceeding without lock", repo_id, exc_info=True)

        try:
            repo_result, repo_failures, repo_rollbacks = await _run_repo(
                repo_id,
                repo_ready_items,
            )
            per_repo_results.append(repo_result)
            failures.extend(repo_failures)
            rollback_records.extend(repo_rollbacks)
        finally:
            if lock_acquired:
                try:
                    from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
                    store = MigrationSessionStore()
                    store.release_repo_lock(session_id, repo_id)
                except Exception:
                    logger.warning("repo lock release failed for %s", repo_id, exc_info=True)

    executor_result = {
        "per_repo_results": per_repo_results,
        "failures": failures,
        "skipped": skipped,
        "dry_run": dry_run,
    }

    _append_and_stream(
        session,
        role="system",
        content=f"Executor: complete — {len(per_repo_results)} repo(s) processed, {len(failures)} failure(s), {len(skipped)} skipped.",
        subagent="executor",
    )

    return {
        "executor_result": executor_result,
        "rollback_records": rollback_records,
        "iteration": iteration,
        "pending_clarification": None,
        "should_return": False,
    }


async def _execute_scope(  # noqa: PLR0913 - exception-register.md: positional shape pinned by tests
    scope: str,
    work_item: dict[str, Any],
    plan: dict[str, Any],
    _accel_get: AccelGet | None,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,  # noqa: FBT001 - exception-register.md: positional shape pinned by tests
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single migration scope for a work item.

    The positional shape of this signature is pinned by callers that pass all
    seven leading arguments positionally, so ``_accel_get`` stays even though
    scope execution only ever posts — the leading underscore marks it as accepted
    and unused. ``dry_run`` keeps its boolean form (the reviewer classified it as
    data, not a mode switch) and is converted to an
    :class:`~ado2gh.models.ExecutionMode` here.

    Returns:
        The accelerator response for the scope, or a dict carrying ``status``
        and ``error``/``message`` when the scope was skipped or failed.
    """
    from ado2gh.agents.migration_agent.nodes.executor.scope import execute_migration_scope

    return await execute_migration_scope(
        scope,
        work_item,
        plan,
        accel_post=accel_post,
        session_token=session_token,
        mode=ExecutionMode.from_dry_run(dry_run=dry_run),
        session=session,
    )

