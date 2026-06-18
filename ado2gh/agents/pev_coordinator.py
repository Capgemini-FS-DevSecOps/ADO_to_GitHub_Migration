"""LLM-driven Planner–Executor–Validator reviews with retry loop."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ado2gh.agents.failure_analysis import (
    analyze_pipeline_run,
    apply_failure_analysis,
    extract_pipeline_failures,
)
from ado2gh.agents.llm_provider import LLMProvider

MAX_PEV_RETRIES = 3

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass
class PevReview:
    role: str
    verdict: str  # pass | fail | retry
    summary: str
    retry_recommended: bool = False
    issues: list[str] = field(default_factory=list)
    next_action: str = "proceed"  # proceed | retry_migration | abort
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_review_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _load_skill(role: str) -> str:
    path = _SKILLS_DIR / f"{role}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _review_system(role: str) -> str:
    skill = _load_skill(role)
    return f"""You are the {role} subagent for ADO→GitHub migration.
{skill}

Analyze the structured subagent output below. Respond with JSON only:
{{
  "verdict": "pass|fail|retry",
  "summary": "operator-facing summary (2-4 sentences)",
  "retry_recommended": true|false,
  "issues": ["concrete issue if any"],
  "next_action": "proceed|retry_migration|abort"
}}

Rules:
- pass + proceed when work succeeded or dry-run completed as expected
- retry + retry_migration only when failures look transient (rate limits, timeouts, git push flakes)
- fail + abort when errors are not retryable (target repo already exists, bad credentials, missing config, approval required)
- Always cite concrete failure reasons from logs and repo_details in summary and issues
- Include operator remediation in summary when aborting (what to fix before retrying)
- Never recommend retry beyond attempt {MAX_PEV_RETRIES}
"""


def _build_review(
    role: str,
    data: dict[str, Any] | None,
    *,
    attempt: int,
    fallback: PevReview,
) -> PevReview:
    if not data:
        return fallback
    verdict = str(data.get("verdict", fallback.verdict)).lower()
    if verdict not in ("pass", "fail", "retry"):
        verdict = fallback.verdict
    next_action = str(data.get("next_action", fallback.next_action)).lower()
    if next_action not in ("proceed", "retry_migration", "abort"):
        next_action = fallback.next_action
    retry = bool(data.get("retry_recommended", fallback.retry_recommended))
    if attempt >= MAX_PEV_RETRIES:
        retry = False
        if next_action == "retry_migration":
            next_action = "abort" if verdict != "pass" else "proceed"
    issues = data.get("issues") or fallback.issues
    if not isinstance(issues, list):
        issues = fallback.issues
    summary = str(data.get("summary") or fallback.summary).strip() or fallback.summary
    return PevReview(
        role=role,
        verdict=verdict,
        summary=summary,
        retry_recommended=retry,
        issues=[str(i) for i in issues][:8],
        next_action=next_action,
        attempt=attempt,
    )


def _planner_fallback(plan: dict[str, Any]) -> PevReview:
    if plan.get("blocked"):
        return PevReview(
            role="planner",
            verdict="fail",
            summary=plan.get("block_reason", "Migration plan is blocked."),
            issues=[plan.get("block_reason", "blocked")],
            next_action="abort",
        )
    count = plan.get("repo_count", 0)
    phase = plan.get("phase", "poc")
    mode = "dry-run" if plan.get("dry_run", True) else "live"
    summary = plan.get("narrative") or f"Plan ready: {count} repo(s) in phase {phase} ({mode})."
    if not plan.get("blocked"):
        summary = (
            f"{summary}\n\n"
            "**Please confirm:** Is this plan correct? Describe any changes needed, "
            "then approve before execution starts."
        )
    return PevReview(
        role="planner",
        verdict="pass",
        summary=str(summary),
        next_action="proceed",
    )


def _executor_fallback(pipeline_run: dict[str, Any], attempt: int) -> PevReview:
    status = pipeline_run.get("status", "unknown")
    failed_steps = [
        s for s in pipeline_run.get("steps", [])
        if s.get("status") == "failed"
    ]
    analysis = analyze_pipeline_run(pipeline_run)
    if status in ("completed", "dry_run_complete") and not failed_steps and not analysis.has_failures:
        return PevReview(
            role="executor",
            verdict="pass",
            summary=f"Pipeline {status} on attempt {attempt}.",
            next_action="proceed",
            attempt=attempt,
        )
    issues = analysis.issues[:8] if analysis.issues else [
        s.get("message") or s.get("id", "step") for s in failed_steps[:5]
    ]
    retryable = analysis.should_retry and attempt < MAX_PEV_RETRIES
    summary = analysis.operator_summary or (
        f"Pipeline {status}" + (f" — {len(failed_steps)} step(s) failed" if failed_steps else "")
    )
    if not retryable and analysis.remediation_steps:
        summary = f"{summary}\n\n{analysis.remediation_steps[0]}"
    return PevReview(
        role="executor",
        verdict="retry" if retryable else "fail",
        summary=summary,
        retry_recommended=retryable,
        issues=issues or [f"pipeline status: {status}"],
        next_action="retry_migration" if retryable else "abort",
        attempt=attempt,
    )


def _validator_fallback(
    pipeline_run: dict[str, Any],
    validate_step: dict[str, Any] | None,
    attempt: int,
) -> PevReview:
    if not validate_step:
        status = pipeline_run.get("status", "")
        if status == "dry_run_complete":
            return PevReview(
                role="validator",
                verdict="pass",
                summary="Validation skipped for dry-run (expected).",
                next_action="proceed",
                attempt=attempt,
            )
        return PevReview(
            role="validator",
            verdict="fail",
            summary="No validation step results available.",
            next_action="abort",
            attempt=attempt,
        )
    vstatus = validate_step.get("status", "")
    message = validate_step.get("message") or "Validation finished"
    result = validate_step.get("result") or {}
    repo_details = result.get("repo_details") or []
    failed = [d for d in repo_details if d.get("overall") == "FAIL" or d.get("status") == "failed"]
    if vstatus == "skipped":
        analysis = analyze_pipeline_run(pipeline_run)
        if not analysis.has_failures:
            return PevReview(
                role="validator",
                verdict="pass",
                summary=message,
                next_action="proceed",
                attempt=attempt,
            )
    if vstatus == "completed" and not failed:
        analysis = analyze_pipeline_run(pipeline_run)
        if not analysis.has_failures:
            return PevReview(
                role="validator",
                verdict="pass",
                summary=message,
                next_action="proceed",
                attempt=attempt,
            )
    analysis = analyze_pipeline_run(pipeline_run)
    issues = analysis.issues[:8] if analysis.issues else [
        d.get("primary_reason") or d.get("summary") or d.get("repo", "repo")
        for d in failed[:5]
    ]
    has_failures = bool(failed) or analysis.has_failures
    retryable = analysis.should_retry and has_failures and attempt < MAX_PEV_RETRIES
    summary = analysis.operator_summary or message
    if not retryable and analysis.remediation_steps:
        summary = f"{summary}\n\n{analysis.remediation_steps[0]}"
    return PevReview(
        role="validator",
        verdict="retry" if retryable else "fail",
        summary=summary,
        retry_recommended=retryable,
        issues=issues or [message],
        next_action="retry_migration" if retryable else "abort",
        attempt=attempt,
    )


def _pipeline_review_payload(pipeline_run: dict[str, Any], *, extra: dict | None = None) -> str:
    failures = extract_pipeline_failures(pipeline_run)
    steps_summary = [
        {
            "id": s.get("id"),
            "label": s.get("label"),
            "status": s.get("status"),
            "message": s.get("message"),
        }
        for s in pipeline_run.get("steps", [])
    ]
    body: dict[str, Any] = {
        "pipeline_status": pipeline_run.get("status"),
        "steps": steps_summary,
        "error": pipeline_run.get("error"),
        "recent_logs": (pipeline_run.get("logs") or [])[-80:],
        "extracted_failures": [
            {"message": f.message, "retryable": f.retryable, "remediation": f.remediation}
            for f in failures[:15]
        ],
    }
    if extra:
        body.update(extra)
    return json.dumps(body, default=str)


def review_planner_output(
    llm: LLMProvider,
    plan: dict[str, Any],
    *,
    llm_degraded: bool = False,
) -> PevReview:
    fallback = _planner_fallback(plan)
    if llm_degraded:
        return fallback
    payload = json.dumps({
        "plan": {
            k: plan.get(k)
            for k in (
                "phase", "repo_count", "dry_run", "blocked", "block_reason",
                "work_summary", "pipeline_steps", "narrative",
            )
        },
        "work_items": (plan.get("work_items") or [])[:20],
    }, default=str)
    raw = llm.complete(payload, system=_review_system("planner"))
    return _build_review("planner", _parse_review_json(raw), attempt=1, fallback=fallback)


def review_executor_output(
    llm: LLMProvider,
    pipeline_run: dict[str, Any],
    attempt: int,
    *,
    llm_degraded: bool = False,
) -> PevReview:
    fallback = _executor_fallback(pipeline_run, attempt)
    if llm_degraded:
        return fallback
    payload = _pipeline_review_payload(pipeline_run, extra={
        "attempt": attempt,
        "max_retries": MAX_PEV_RETRIES,
    })
    raw = llm.complete(payload, system=_review_system("executor"))
    review = _build_review(
        "executor", _parse_review_json(raw), attempt=attempt, fallback=fallback,
    )
    return apply_failure_analysis(review, pipeline_run)


def review_validator_output(
    llm: LLMProvider,
    pipeline_run: dict[str, Any],
    validate_step: dict[str, Any] | None,
    attempt: int,
    *,
    llm_degraded: bool = False,
) -> PevReview:
    fallback = _validator_fallback(pipeline_run, validate_step, attempt)
    if llm_degraded:
        return fallback
    result = (validate_step or {}).get("result") or {}
    payload = _pipeline_review_payload(pipeline_run, extra={
        "attempt": attempt,
        "max_retries": MAX_PEV_RETRIES,
        "validate_status": (validate_step or {}).get("status"),
        "validate_message": (validate_step or {}).get("message"),
        "repo_details": (result.get("repo_details") or [])[:15],
    })
    raw = llm.complete(payload, system=_review_system("validator"))
    review = _build_review(
        "validator", _parse_review_json(raw), attempt=attempt, fallback=fallback,
    )
    return apply_failure_analysis(review, pipeline_run)


def should_retry_migration(review: PevReview, attempt: int) -> bool:
    return (
        attempt < MAX_PEV_RETRIES
        and review.retry_recommended
        and review.next_action == "retry_migration"
        and review.verdict == "retry"
    )


def should_retry_pev(
    executor_review: PevReview,
    validator_review: PevReview,
    pipeline_run: dict[str, Any],
    attempt: int,
) -> bool:
    analysis = analyze_pipeline_run(pipeline_run)
    if analysis.has_failures and not analysis.should_retry:
        return False
    return (
        should_retry_migration(executor_review, attempt)
        or should_retry_migration(validator_review, attempt)
    )
