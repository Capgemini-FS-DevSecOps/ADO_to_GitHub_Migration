"""LLM-driven Planner–Executor–Validator reviews with retry loop."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
- retry + retry_migration when transient failures may succeed on retry (git push, rate limits, partial pipeline)
- fail + abort when plan is blocked, approval required, or errors are not retryable
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
    if status in ("completed", "dry_run_complete") and not failed_steps:
        return PevReview(
            role="executor",
            verdict="pass",
            summary=f"Pipeline {status} on attempt {attempt}.",
            next_action="proceed",
            attempt=attempt,
        )
    issues = [s.get("message") or s.get("id", "step") for s in failed_steps[:5]]
    retryable = status == "failed" and attempt < MAX_PEV_RETRIES
    return PevReview(
        role="executor",
        verdict="retry" if retryable else "fail",
        summary=f"Pipeline {status}" + (f" — {len(failed_steps)} step(s) failed" if failed_steps else ""),
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
        return PevReview(
            role="validator",
            verdict="pass",
            summary=message,
            next_action="proceed",
            attempt=attempt,
        )
    if vstatus == "completed" and not failed:
        return PevReview(
            role="validator",
            verdict="pass",
            summary=message,
            next_action="proceed",
            attempt=attempt,
        )
    issues = [
        d.get("primary_reason") or d.get("summary") or d.get("repo", "repo")
        for d in failed[:5]
    ]
    retryable = bool(failed) and attempt < MAX_PEV_RETRIES
    return PevReview(
        role="validator",
        verdict="retry" if retryable else "fail",
        summary=message,
        retry_recommended=retryable,
        issues=issues or [message],
        next_action="retry_migration" if retryable else "abort",
        attempt=attempt,
    )


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
    steps_summary = [
        {
            "id": s.get("id"),
            "label": s.get("label"),
            "status": s.get("status"),
            "message": s.get("message"),
        }
        for s in pipeline_run.get("steps", [])
    ]
    payload = json.dumps({
        "attempt": attempt,
        "max_retries": MAX_PEV_RETRIES,
        "pipeline_status": pipeline_run.get("status"),
        "steps": steps_summary,
        "error": pipeline_run.get("error"),
    }, default=str)
    raw = llm.complete(payload, system=_review_system("executor"))
    return _build_review(
        "executor", _parse_review_json(raw), attempt=attempt, fallback=fallback,
    )


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
    payload = json.dumps({
        "attempt": attempt,
        "max_retries": MAX_PEV_RETRIES,
        "pipeline_status": pipeline_run.get("status"),
        "validate_status": (validate_step or {}).get("status"),
        "validate_message": (validate_step or {}).get("message"),
        "repo_details": (result.get("repo_details") or [])[:15],
    }, default=str)
    raw = llm.complete(payload, system=_review_system("validator"))
    return _build_review(
        "validator", _parse_review_json(raw), attempt=attempt, fallback=fallback,
    )


def should_retry_migration(review: PevReview, attempt: int) -> bool:
    return (
        attempt < MAX_PEV_RETRIES
        and review.retry_recommended
        and review.next_action == "retry_migration"
    )
