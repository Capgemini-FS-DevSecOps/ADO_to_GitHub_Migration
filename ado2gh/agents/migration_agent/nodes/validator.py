"""validator.py module."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.constants import MAX_PEV_RETRIES
from ado2gh.agents.migration_agent.nodes._common import (
    _transition_session,
)
from ado2gh.agents.migration_agent.nodes.messaging import (
    _make_cycle_summary,
    _make_inter_agent_message,
)
from ado2gh.agents.migration_agent.nodes.planner import (
    _advance_migration_queue,
)
from ado2gh.agents.migration_agent.nodes.validator_investigation import (
    _build_validator_investigation_context,  # noqa: F401 -- re-exported for nodes.validator consumers
    _gather_validator_baseline_probes,
    _gather_validator_dry_run_evidence,  # noqa: F401 -- re-exported for nodes.validator consumers
    _normalize_validator_failure,
    _run_validator_llm_investigation,
    _validator_has_pipeline_scope,  # noqa: F401 -- re-exported for nodes.validator consumers
)
from ado2gh.agents.migration_agent.session.state import SessionState
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
)

# ─── Node: validator ─────────────────────────────────────────────────────────────

async def validator_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator node — verifies migration outcomes using evidence-based checks.

    Binds validator tools, streams validation thoughts, verifies migration
    outcomes via API calls and local validation, produces ValidationResult
    with per-scope pass/fail, sets AgentState.validation_feedback for failures,
    validates no operations outside approved plan.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.VALIDATING)
    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    accel_get = state.get("accel_get")
    session_token = state.get("session_token")
    migration_plan = state.get("migration_plan") or session.get("migration_plan")
    executor_result = state.get("executor_result")
    iteration = state.get("iteration", 0)
    pev_retry_count = state.get("pev_retry_count", 0)

    iteration += 1

    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run_preview = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    ) if executor_result else bool(session.get("dry_run", True))

    _append_and_stream(
        session,
        role="system",
        content=(
            "Validator: starting dry-run validation (executor logs)…"
            if dry_run_preview
            else "Validator: starting live validation (API/tool evidence)…"
        ),
        subagent="validator",
    )

    if not executor_result:
        _append_and_stream(
            session,
            role="system",
            content="Validator: no executor result — nothing to validate.",
            subagent="validator",
        )
        return {
            "validation_result": {"passed": False, "per_scope": {}, "failures": ["no_executor_result"]},
            "validation_feedback": {"failures": ["no_executor_result"]},
            "iteration": iteration,
            "should_return": False,
        }

    per_repo_results = executor_result.get("per_repo_results", [])
    failures = executor_result.get("failures", [])

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    # Per-scope validation
    per_scope: dict[str, dict[str, Any]] = {}
    all_failures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    for repo_result in per_repo_results:
        repo_id = repo_result.get("repo", "")
        scopes = repo_result.get("scopes", {})

        for scope_name, scope_result in scopes.items():
            if not dry_run:
                if scope_result.get("error"):
                    all_failures.append({
                        "repo": repo_id,
                        "scope": scope_name,
                        "specific_failure": scope_result["error"],
                        "error": scope_result["error"],
                        "recommended_remediation": f"Fix error in {scope_name} execution and retry",
                        "source": "executor_error",
                    })
                continue

            scope_validation = _validate_scope(scope_name, scope_result, migration_plan, dry_run)
            per_scope.setdefault(scope_name, {"repos": []})
            per_scope[scope_name]["repos"].append({
                "repo": repo_id,
                "passed": scope_validation["passed"],
                "evidence": scope_validation.get("evidence"),
            })

            if not scope_validation["passed"]:
                failure_entry = {
                    "repo": repo_id,
                    "scope": scope_name,
                    "expected_state": scope_validation.get("expected_state"),
                    "observed_state": scope_validation.get("observed_state"),
                    "specific_failure": scope_validation.get("failure"),
                    "recommended_remediation": scope_validation.get("remediation"),
                    "error": scope_validation.get("failure"),
                }
                from ado2gh.agents.migration_agent.hitl.operator_input import is_fr036_failure

                if is_fr036_failure(failure_entry):
                    failure_entry["error_code"] = "migration_in_progress"
                    failure_entry["operator_input_required"] = True
                all_failures.append(failure_entry)

            if scope_validation.get("evidence"):
                evidence.append(scope_validation["evidence"])

    # Check for plan-vs-execution consistency
    discovery = session.get("discovery_snapshot")
    if isinstance(discovery, str):
        import json
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    from ado2gh.agents.migration_agent.utils import (
        canonical_plan_repo_key,
        plan_repo_key_aliases,
        repo_matches_plan_keys,
    )

    plan_aliases = plan_repo_key_aliases(migration_plan, discovery)
    extra_repos: set[str] = set()
    for repo_result in per_repo_results:
        executed = canonical_plan_repo_key(str(repo_result.get("repo", "") or ""), discovery)
        if not executed:
            continue
        if not repo_matches_plan_keys(executed, plan_aliases, discovery):
            extra_repos.add(executed)
    if extra_repos:
        all_failures.append({
            "scope": "plan_consistency",
            "specific_failure": f"Executor processed repos not in plan: {extra_repos}",
            "recommended_remediation": "Review executor output and ensure only planned repos are processed",
        })

    # Include executor failures in validation (ignore benign dry-run infrastructure skips)
    from ado2gh.agents.migration_agent.hitl.operator_input import is_fr036_failure

    for f in failures:
        if _failure_is_benign(f, dry_run=dry_run):
            continue
        failure_entry = {
            "repo": f.get("repo", ""),
            "scope": f.get("scope", ""),
            "error": f.get("error", ""),
            "specific_failure": f.get("specific_failure") or f.get("error", "Execution error"),
            "error_code": f.get("error_code", "execution_error"),
            "lock_holder_session_id": f.get("lock_holder_session_id"),
            "holder_run_id": f.get("holder_run_id"),
            "operator_input_required": f.get("operator_input_required"),
        }
        if is_fr036_failure(failure_entry):
            failure_entry["error_code"] = "migration_in_progress"
            failure_entry["operator_input_required"] = True
        if is_fr036_failure(failure_entry):
            failure_entry["error_code"] = "migration_in_progress"
            failure_entry["operator_input_required"] = True
        all_failures.append(failure_entry)

    # Deterministic baseline probes before LLM investigation
    baseline_findings: list[dict[str, Any]] = []
    if per_repo_results:
        try:
            baseline_findings = await _gather_validator_baseline_probes(
                session,
                executor_result,
                migration_plan if isinstance(migration_plan, dict) else None,
                accel_get,
                session_token,
            )
        except Exception as exc:
            _append_and_stream(
                session,
                role="system",
                content=f"Validator: baseline probe error — {exc}",
                subagent="validator",
            )
        else:
            from ado2gh.agents.migration_agent.hitl.operator_input import (
                validation_failures_from_baseline_probes,
            )

            probe_failures = validation_failures_from_baseline_probes(baseline_findings)
            if probe_failures:
                all_failures.extend(probe_failures)
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Validator: baseline probes found {len(probe_failures)} issue(s) — "
                        "including in validation report."
                    ),
                    subagent="validator",
                )

    # LLM-driven evidence validation (APIs + local workflow checks)
    llm_analysis: str | None = None
    if llm and not llm_unconfigured and accel_get:
        try:
            llm_report = await _run_validator_llm_investigation(
                state,
                executor_result=executor_result,
                migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
                baseline_failures=list(all_failures),
                baseline_findings=baseline_findings,
            )
        except Exception as exc:
            llm_report = None
            _append_and_stream(
                session,
                role="system",
                content=f"Validator: LLM investigation error — {exc}",
                subagent="validator",
            )
        else:
            if isinstance(llm_report, dict) and llm_report.get("operator_input_request"):
                from ado2gh.agents.migration_agent.hitl.operator_input import (
                    blockers_from_baseline_probes,
                    blockers_from_validator_baseline_probes,
                    operator_input_from_probe_failures,
                    store_operator_input,
                )
                from ado2gh.agents.migration_agent.hitl.schemas import OperatorInputRequest

                op_raw = llm_report["operator_input_request"]
                op_req: OperatorInputRequest | None = None
                try:
                    op_req = OperatorInputRequest.model_validate(op_raw)
                except Exception:
                    blocker_list = (
                        blockers_from_baseline_probes(baseline_findings)
                        + blockers_from_validator_baseline_probes(baseline_findings)
                    )
                    if not blocker_list:
                        blocker_list = [{
                            "repo": str(per_repo_results[0].get("repo") if per_repo_results else ""),
                            "scope": "validation",
                            "blocker": str(op_raw.get("description") or op_raw.get("thinking") or "Validator needs operator decision."),
                            "key": "validation:llm_operator_request",
                        }]
                    op_req = operator_input_from_probe_failures(
                        blocker_list, session, source="validator",
                    )
                if op_req:
                    store_operator_input(session, op_req)
                    all_failures.append({
                        "scope": "validation",
                        "specific_failure": op_req.description[:500],
                        "operator_input_required": True,
                        "source": "validator_llm",
                    })
                    _append_and_stream(
                        session,
                        role="system",
                        content="Validator: operator decision required — escalating to orchestrator.",
                        subagent="validator",
                    )
            elif isinstance(llm_report, dict):
                llm_analysis = str(llm_report.get("analysis") or "").strip() or None
                if llm_report.get("per_scope"):
                    for scope_name, scope_data in llm_report["per_scope"].items():
                        if not isinstance(scope_data, dict):
                            continue
                        per_scope.setdefault(scope_name, {"repos": []})
                        for repo_row in scope_data.get("repos") or []:
                            if isinstance(repo_row, dict):
                                per_scope[scope_name]["repos"].append(repo_row)
                if llm_report.get("passed") is False:
                    added = False
                    for raw_failure in llm_report.get("failures") or []:
                        normalized = _normalize_validator_failure(raw_failure)
                        if normalized:
                            all_failures.append(normalized)
                            added = True
                    if not added and llm_analysis:
                        all_failures.append({
                            "scope": "validation",
                            "specific_failure": llm_analysis[:1000],
                            "recommended_remediation": "Revise the migration plan using validator analysis",
                            "source": "validator_llm",
                            "error": llm_analysis[:500],
                        })
                if llm_analysis:
                    evidence.append({"llm_analysis": llm_analysis, "tool_calls": llm_report.get("tool_calls_total")})
                    _append_and_stream(
                        session,
                        role="system",
                        content=f"Validator: analysis complete — {llm_analysis[:500]}",
                        subagent="validator",
                    )

    passed = len(all_failures) == 0
    validation_result = {
        "passed": passed,
        "per_scope": per_scope,
        "evidence": evidence,
        "failures": all_failures,
        "dry_run": dry_run,
        "llm_analysis": llm_analysis,
    }

    # T100: Advance queue index after executor consumed a queue slot (even if no scopes ran).
    migration_queue = state.get("migration_queue")
    if passed and migration_queue:
        current_index = int(migration_queue.get("current_index", 0) or 0)
        queue_items = migration_queue.get("items", [])
        current_repo_id = str((executor_result or {}).get("current_repo_id", "") or "").strip()
        if not current_repo_id and per_repo_results:
            current_repo_id = str(per_repo_results[0].get("repo", "") or "").strip()

        if current_index < len(queue_items):
            _advance_migration_queue(migration_queue, repo_id=current_repo_id)
            _append_and_stream(
                session,
                role="system",
                content=(
                    f"Validator: validation passed for {current_repo_id or 'repo'} — "
                    f"queue advanced to {migration_queue['current_index']}/{len(queue_items)}"
                ),
                subagent="validator",
            )
        else:
            _append_and_stream(
                session,
                role="system",
                content="Validator: queue already complete — no further repos to validate.",
                subagent="validator",
            )

    # Set validation feedback for failures (triggers planner retry unless non-retryable)
    from ado2gh.agents.migration_agent.hitl.operator_input import failures_require_operator_escalation

    validation_feedback = None
    pending_operator_input_payload: dict[str, Any] | None = None
    pending_clarification: dict[str, Any] | None = None
    operator_escalation = not passed and failures_require_operator_escalation(all_failures)
    if operator_escalation:
        from ado2gh.agents.migration_agent.hitl.operator_input import (
            blockers_from_baseline_probes,
            blockers_from_validator_baseline_probes,
            operator_input_from_probe_failures,
            operator_input_from_validator_failures,
            store_operator_input,
        )

        op_req = operator_input_from_validator_failures(
            all_failures, session, plan=migration_plan if isinstance(migration_plan, dict) else None,
        )
        if op_req is None and baseline_findings:
            probe_blockers = (
                blockers_from_baseline_probes(baseline_findings)
                + blockers_from_validator_baseline_probes(baseline_findings)
            )
            if probe_blockers:
                op_req = operator_input_from_probe_failures(
                    probe_blockers, session, source="validator",
                )
        if op_req is None:
            from ado2gh.agents.migration_agent.hitl.operator_input import pending_operator_input

            op_req = pending_operator_input(session)
        if op_req is not None and hasattr(op_req, "model_dump"):
            store_operator_input(session, op_req)
            pending_operator_input_payload = op_req.model_dump()
            pending_clarification = {
                "message_type": "operator_input_required",
                "payload": pending_operator_input_payload,
            }
        elif isinstance(op_req, dict):
            pending_operator_input_payload = op_req
            pending_clarification = {
                "message_type": "operator_input_required",
                "payload": op_req,
            }

        validation_feedback = {
            "failures": all_failures,
            "retry_recommended": False,
            "escalate": True,
            "retry_count": pev_retry_count,
        }
        session["pev_max_retries_exhausted"] = True
        session.pop("start_execution", None)
        _append_and_stream(
            session,
            role="system",
            content="Validator: non-retryable failure — escalating to orchestrator.",
            subagent="validator",
        )
    elif not passed and pev_retry_count < MAX_PEV_RETRIES:
        validation_feedback = {
            "failures": all_failures,
            "analysis": llm_analysis,
            "retry_recommended": True,
            "retry_count": pev_retry_count + 1,
        }
        pev_retry_count += 1
        _append_and_stream(
            session,
            role="system",
            content=f"Validator: validation failed — sending feedback to planner (retry {pev_retry_count}/{MAX_PEV_RETRIES}).",
            subagent="validator",
        )
    elif not passed:
        validation_feedback = {
            "failures": all_failures,
            "analysis": llm_analysis,
            "retry_recommended": False,
            "escalate": True,
            "retry_count": pev_retry_count,
        }
        session["pev_max_retries_exhausted"] = True
        session.pop("start_execution", None)
        _append_and_stream(
            session,
            role="system",
            content="Validator: validation failed — max retries exhausted, sending to planner.",
            subagent="validator",
        )
    else:
        _append_and_stream(
            session,
            role="system",
            content="Validator: all checks passed — sending results to planner.",
            subagent="validator",
        )

    # Determine next action for cycle summary
    if passed:
        next_action = "complete"
    elif pev_retry_count >= MAX_PEV_RETRIES:
        next_action = "fail_max_retries"
    elif iteration >= state.get("max_iterations", 20):
        next_action = "fail_max_iterations"
    else:
        next_action = "retry_planner"

    # T054: Generate PevCycleSummary
    cycle_number = pev_retry_count + 1 if not passed else pev_retry_count
    cycle_summary = _make_cycle_summary(
        cycle_number=cycle_number,
        executor_result=executor_result,
        validation_result=validation_result,
        next_action=next_action,
    )

    # T052: Create inter-agent message from validator
    inter_agent_msg = _make_inter_agent_message(
        from_role="validator",
        to_role="planner",
        message_type="validation_result",
        payload={
            "passed": passed,
            "failures": all_failures[:5],
            "analysis": (llm_analysis or "")[:2000],
            "retry_count": pev_retry_count,
        },
    )

    return {
        "validation_result": validation_result,
        "validation_feedback": validation_feedback,
        "pev_retry_count": pev_retry_count,
        "iteration": iteration,
        **({"migration_queue": migration_queue} if migration_queue is not None else {}),
        **({"pending_operator_input": pending_operator_input_payload} if pending_operator_input_payload else {}),
        **({"pending_clarification": pending_clarification} if pending_clarification else {}),
        "should_return": False,
        "cycle_summaries": [cycle_summary],
        "inter_agent_messages": [inter_agent_msg],
    }


def _failure_text(failure: Any) -> str:
    if not isinstance(failure, dict):
        return str(failure).lower()
    return " ".join(
        str(failure.get(key, ""))
        for key in (
            "specific_failure",
            "error",
            "failure",
            "recommended_remediation",
            "remediation",
            "message",
        )
    ).lower()


_BENIGN_DRY_RUN_HINTS = (
    "endpoint unavailable",
    "accelerator endpoint unavailable",
    "accelerator_unavailable",
    "not included in migration scopes",
    "no operator secret mappings",
)


_DRY_RUN_SCOPE_OK_STATUSES = frozenset({
    "skipped", "success", "simulated", "pending", "dry_run",
})


def _failure_is_benign(failure: Any, *, dry_run: bool) -> bool:
    if not dry_run:
        return False
    text = _failure_text(failure)
    if "404" in text or "not found" in text:
        return True
    return any(hint in text for hint in _BENIGN_DRY_RUN_HINTS)


def _all_failures_benign(failures: list[Any], *, dry_run: bool) -> bool:
    return bool(failures) and all(_failure_is_benign(f, dry_run=dry_run) for f in failures)


def _scope_result_is_benign(scope_result: dict[str, Any], *, dry_run: bool) -> bool:
    status = scope_result.get("status")
    if status in ("skipped", "pending"):
        return True
    if dry_run:
        message = str(
            scope_result.get("message") or scope_result.get("detail") or ""
        ).lower()
        if any(hint in message for hint in _BENIGN_DRY_RUN_HINTS):
            return True
    return False


def _validate_scope(
    scope: str,
    scope_result: dict[str, Any],
    plan: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Validate a single scope's execution result."""
    if _scope_result_is_benign(scope_result, dry_run=dry_run):
        return {
            "passed": True,
            "evidence": {
                "scope": scope,
                "status": scope_result.get("status", "skipped"),
            },
        }

    if scope_result.get("error"):
        return {
            "passed": False,
            "failure": scope_result["error"],
            "observed_state": "error",
            "expected_state": "success",
            "remediation": f"Fix error in {scope} execution and retry",
        }

    if scope_result.get("status") == "skipped":
        return {
            "passed": True,
            "evidence": {"scope": scope, "status": "skipped"},
        }

    if scope_result.get("status") == "pending":
        return {
            "passed": True,
            "evidence": {"scope": scope, "status": "pending"},
        }

    # Scope-specific validation logic
    if scope in ("repo", "git"):
        status = scope_result.get("status", "unknown")
        if dry_run:
            if status in _DRY_RUN_SCOPE_OK_STATUSES:
                return {"passed": True, "evidence": {"scope": scope, "dry_run": True, "status": status}}
            return {
                "passed": False,
                "failure": f"Unexpected git scope status in dry-run: {status}",
                "observed_state": status,
                "expected_state": "skipped, success, or dry_run",
                "remediation": "Review executor dry-run output for the git scope",
            }
        # For live, check for SHA parity (would use validate_git tool)
        return {"passed": True, "evidence": {"scope": "git", "status": status}}

    if scope == "pipelines":
        status = scope_result.get("status", "unknown")
        if dry_run and status not in _DRY_RUN_SCOPE_OK_STATUSES:
            return {
                "passed": False,
                "failure": f"Unexpected pipelines scope status in dry-run: {status}",
                "observed_state": status,
                "expected_state": "skipped, success, or dry_run",
                "remediation": "Review executor dry-run output for the pipelines scope",
            }
        validation_failed = int(scope_result.get("validation_failed") or 0)
        validation_errors = scope_result.get("validation_errors") or []
        if validation_failed > 0 or validation_errors:
            return {
                "passed": False,
                "failure": validation_errors[0] if validation_errors else "Pipeline YAML validation failed",
                "observed_state": {
                    "validation_failed": validation_failed,
                    "validation_errors": validation_errors[:5],
                },
                "expected_state": "All converted workflows pass local validation",
                "remediation": (
                    "Planner should revise pipeline conversion: fix triggers, runs-on, steps, "
                    "or unsupported ADO tasks before re-execution"
                ),
                "evidence": {
                    "scope": "pipelines",
                    "validation_failed": validation_failed,
                    "validation_errors": validation_errors[:10],
                    "workflow_files": scope_result.get("workflow_files") or [],
                },
            }
        completed = int(scope_result.get("completed") or 0)
        failed = int(scope_result.get("failed") or 0)
        if not dry_run and failed > 0:
            return {
                "passed": False,
                "failure": scope_result.get("message") or f"{failed} pipeline(s) failed conversion",
                "observed_state": {"completed": completed, "failed": failed},
                "expected_state": "All planned pipelines converted",
                "remediation": "Review failed pipeline transforms and update the plan",
            }
        return {
            "passed": True,
            "evidence": {
                "scope": "pipelines",
                "status": status,
                "completed": completed,
                "validation_passed": scope_result.get("validation_passed"),
                "workflow_files": scope_result.get("workflow_files") or [],
            },
        }

    # Default: pass if no error
    return {"passed": True, "evidence": {"scope": scope, "status": scope_result.get("status", "success")}}

