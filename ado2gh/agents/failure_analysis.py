"""Extract and classify pipeline failures for agent feedback and retry decisions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MIGRATE_STEP_IDS = frozenset({
    "migrate", "migrate_repos", "migrate_pipelines", "migrate_policies",
})

# (regex, short remediation for operators)
_NON_RETRYABLE: list[tuple[str, str]] = [
    (
        r"target repo already exists",
        "Delete the empty GitHub target repo or change github_repo in your migration config, then re-run.",
    ),
    (
        r"no repos assigned to phase",
        "Assign repos to this phase on the Discovery tab before migrating.",
    ),
    (
        r"live_approval_required|approval required|awaiting.*approval",
        "Request live execution approval from an approver before retrying.",
    ),
    (
        r"invalid credentials|authentication failed|401 unauthorized|403 forbidden",
        "Fix ADO or GitHub credentials in Settings → Profiles, then retry.",
    ),
    (
        r"repo not found|repository .* not found",
        "Verify the ADO repo name and that your PAT can access it.",
    ),
    (
        r"blocked|plan is blocked",
        "Resolve the plan blocker (missing config, inventory gaps, etc.) before retrying.",
    ),
    (
        r"usage help|gh-ado2gh extension",
        "Install the gh-ado2gh extension (`gh extension install github/gh-ado2gh`) and retry.",
    ),
]

_RETRYABLE: list[tuple[str, str]] = [
    (
        r"rate limit|429|too many requests",
        "GitHub or ADO rate-limited the request — wait a few minutes and retry.",
    ),
    (
        r"timeout|timed out|deadline exceeded",
        "The operation timed out — retry may succeed on a less busy run.",
    ),
    (
        r"connection (?:reset|refused|error)|network|temporarily unavailable|503|502|504",
        "Transient network or service error — safe to retry shortly.",
    ),
    (
        r"git push failed|push rejected|remote end hung up",
        "Git push failed — retry after confirming network and GitHub availability.",
    ),
]


@dataclass
class FailureRecord:
    message: str
    source: str
    retryable: bool
    remediation: str = ""


@dataclass
class PipelineFailureAnalysis:
    has_failures: bool = False
    should_retry: bool = False
    issues: list[str] = field(default_factory=list)
    remediation_steps: list[str] = field(default_factory=list)
    operator_summary: str = ""

    @property
    def remediation_hint(self) -> str:
        return self.remediation_steps[0] if self.remediation_steps else ""


def _classify_message(message: str) -> tuple[bool, str]:
    text = (message or "").strip()
    lower = text.lower()
    if not lower:
        return False, ""

    for pattern, remediation in _NON_RETRYABLE:
        if re.search(pattern, lower):
            return False, remediation

    for pattern, remediation in _RETRYABLE:
        if re.search(pattern, lower):
            return True, remediation

    if "failed" in lower or "error" in lower:
        return False, "Fix the reported issue before retrying — this failure is unlikely to resolve on its own."

    return False, ""


def _dedupe_messages(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for msg in messages:
        key = msg.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(msg.strip())
    return out


def extract_pipeline_failures(pipeline_run: dict[str, Any]) -> list[FailureRecord]:
    """Collect failure lines from logs, step messages, and per-repo results."""
    records: list[FailureRecord] = []
    seen: set[str] = set()

    def add(message: str, source: str) -> None:
        msg = (message or "").strip()
        if not msg:
            return
        key = msg.lower()
        if key in seen:
            return
        seen.add(key)
        retryable, remediation = _classify_message(msg)
        records.append(FailureRecord(
            message=msg,
            source=source,
            retryable=retryable,
            remediation=remediation,
        ))

    for line in pipeline_run.get("logs") or []:
        text = str(line).strip()
        if not text:
            continue
        lower = text.lower()
        if ": failed" in lower or lower.startswith("failed "):
            add(text, "log")

    if pipeline_run.get("error"):
        add(str(pipeline_run["error"]), "pipeline")

    for step in pipeline_run.get("steps") or []:
        if step.get("status") == "failed" and step.get("message"):
            add(f"{step.get('label') or step.get('id')}: {step['message']}", "step")

        result = step.get("result") or {}
        for detail in result.get("repo_details") or []:
            summary = detail.get("summary") or detail.get("message") or ""
            status = str(detail.get("status") or detail.get("overall") or "").lower()
            errors = detail.get("errors") or []
            primary = detail.get("primary_reason") or detail.get("detail") or ""

            if summary and ("fail" in status or "fail" in summary.lower()):
                add(summary, "repo")
            elif primary and status in ("fail", "failed"):
                repo = detail.get("repo") or f"{detail.get('project', '')}/{detail.get('repo', '')}"
                add(f"{repo}: {primary}", "repo")
            for err in errors:
                repo = detail.get("repo") or detail.get("gh_target") or "repo"
                add(f"{repo}: {err}", "repo")

            for scope in detail.get("scopes") or []:
                if scope.get("status") != "completed":
                    repo = detail.get("repo") or "repo"
                    label = scope.get("label") or scope.get("scope") or "scope"
                    err = scope.get("error") or scope.get("detail") or "failed"
                    add(f"{repo}: {label}={err}", "scope")

    return records


def analyze_pipeline_run(pipeline_run: dict[str, Any]) -> PipelineFailureAnalysis:
    records = extract_pipeline_failures(pipeline_run)
    if not records:
        return PipelineFailureAnalysis(has_failures=False)

    issues = _dedupe_messages([r.message for r in records])
    remediations = _dedupe_messages([r.remediation for r in records if r.remediation])

    has_non_retryable = any(not r.retryable for r in records)
    has_retryable = any(r.retryable for r in records)
    should_retry = has_retryable and not has_non_retryable

    if len(issues) == 1:
        summary = issues[0]
    else:
        summary = f"{len(issues)} failure(s): " + "; ".join(issues[:3])
        if len(issues) > 3:
            summary += f" (+{len(issues) - 3} more)"

    return PipelineFailureAnalysis(
        has_failures=True,
        should_retry=should_retry,
        issues=issues,
        remediation_steps=remediations,
        operator_summary=summary,
    )


def apply_failure_analysis(
    review: Any,
    pipeline_run: dict[str, Any],
) -> Any:
    """Override retry recommendations when log analysis shows a non-retryable root cause."""
    from ado2gh.agents.pev_coordinator import PevReview

    analysis = analyze_pipeline_run(pipeline_run)
    if not analysis.has_failures:
        if analysis.issues and not review.issues:
            return PevReview(
                role=review.role,
                verdict=review.verdict,
                summary=review.summary,
                retry_recommended=review.retry_recommended,
                issues=list(analysis.issues),
                next_action=review.next_action,
                attempt=review.attempt,
            )
        return review

    issues = analysis.issues or list(review.issues)
    summary = review.summary
    if analysis.operator_summary and (
        not summary or summary.startswith("Pipeline failed")
        or "step(s) failed" in summary
    ):
        summary = analysis.operator_summary

    if not analysis.should_retry:
        if analysis.remediation_steps and analysis.remediation_steps[0] not in summary:
            summary = f"{summary}\n\n{analysis.remediation_steps[0]}"
        return PevReview(
            role=review.role,
            verdict="fail",
            summary=summary,
            retry_recommended=False,
            issues=issues,
            next_action="abort",
            attempt=review.attempt,
        )

    return PevReview(
        role=review.role,
        verdict=review.verdict if review.verdict != "fail" else "retry",
        summary=summary,
        retry_recommended=True,
        issues=issues or list(review.issues),
        next_action="retry_migration",
        attempt=review.attempt,
    )


def format_retry_message(
    review: Any,
    pipeline_run: dict[str, Any],
    attempt: int,
    *,
    max_retries: int,
) -> str:
    analysis = analyze_pipeline_run(pipeline_run)
    reasons = review.issues or analysis.issues
    reason_text = "; ".join(reasons[:3]) if reasons else (review.summary or "transient failure")
    lines = [
        f"Retry recommended (attempt {attempt + 1}/{max_retries}): {reason_text}",
    ]
    remediation = analysis.remediation_hint or (
        review.issues[0] if review.issues else ""
    )
    if analysis.remediation_steps:
        lines.append("")
        lines.append("Before retrying:")
        for step in analysis.remediation_steps[:3]:
            lines.append(f"• {step}")
    elif remediation and remediation not in reason_text:
        lines.append("")
        lines.append(f"Before retrying: {remediation}")
    return "\n".join(lines)


def format_failure_feedback(
    pipeline_run: dict[str, Any],
    *,
    fallback_summary: str = "",
) -> str:
    analysis = analyze_pipeline_run(pipeline_run)
    if not analysis.has_failures:
        return fallback_summary or "Migration failed — check pipeline logs for details."

    lines = [analysis.operator_summary or fallback_summary or "Migration failed."]
    if analysis.remediation_steps:
        lines.append("")
        lines.append("What to do:")
        for step in analysis.remediation_steps[:5]:
            lines.append(f"• {step}")
    return "\n".join(line for line in lines if line)
