"""Tests for pipeline failure extraction and retry classification."""
from ado2gh.agents.failure_analysis import (
    analyze_pipeline_run,
    extract_pipeline_failures,
    format_failure_feedback,
    format_retry_message,
)
from ado2gh.agents.pev_coordinator import (
    PevReview,
    review_executor_output,
    should_retry_pev,
)
from ado2gh.agents.llm_provider import StubLLMProvider


def test_extract_failure_from_pipeline_logs():
    pipeline = {
        "status": "failed",
        "logs": [
            "Starting: Migrate repository contents",
            (
                "infrastructure-as-code/iac-infra-az-storage-account: failed — "
                "Migrate repository (git / GEI)=gh ado2gh skipped migration: "
                "target repo already exists on GitHub. Delete the empty target repo "
                "or choose a different github_repo name."
            ),
        ],
        "steps": [
            {
                "id": "migrate_repos",
                "status": "failed",
                "message": "Migrate repository contents: 0 completed, 1 failed",
            }
        ],
    }
    failures = extract_pipeline_failures(pipeline)
    assert failures
    assert any("target repo already exists" in f.message for f in failures)
    assert all(not f.retryable for f in failures)


def test_target_repo_exists_not_retryable():
    pipeline = {
        "status": "failed",
        "logs": [
            "repo-a: failed — target repo already exists on GitHub",
        ],
        "steps": [],
    }
    analysis = analyze_pipeline_run(pipeline)
    assert analysis.has_failures
    assert analysis.should_retry is False
    assert "Delete the empty GitHub target repo" in analysis.remediation_steps[0]


def test_rate_limit_is_retryable():
    pipeline = {
        "status": "failed",
        "logs": ["repo-a: failed — GitHub API rate limit exceeded (429)"],
        "steps": [],
    }
    analysis = analyze_pipeline_run(pipeline)
    assert analysis.should_retry is True


def test_executor_fallback_aborts_on_existing_target_repo():
    pipeline = {
        "status": "failed",
        "logs": [
            "proj/repo: failed — gh ado2gh skipped migration: target repo already exists on GitHub",
        ],
        "steps": [{"id": "migrate_repos", "status": "failed", "message": "1 failed"}],
    }
    review = review_executor_output(StubLLMProvider(), pipeline, attempt=1, llm_degraded=True)
    assert review.retry_recommended is False
    assert review.next_action == "abort"
    assert "target repo already exists" in review.summary.lower()


def test_format_retry_message_includes_reason_and_remediation():
    pipeline = {
        "status": "failed",
        "logs": ["repo-a: failed — connection timed out during git push"],
        "steps": [],
    }
    review = PevReview(
        role="validator",
        verdict="retry",
        summary="retry",
        retry_recommended=True,
        issues=["repo-a: connection timed out during git push"],
        next_action="retry_migration",
        attempt=1,
    )
    msg = format_retry_message(review, pipeline, attempt=1, max_retries=3)
    assert "Retry recommended" in msg
    assert "connection timed out" in msg
    assert "retry" in msg.lower()


def test_format_failure_feedback_includes_remediation():
    pipeline = {
        "status": "failed",
        "logs": [
            "iac/repo: failed — target repo already exists on GitHub",
        ],
        "steps": [],
    }
    msg = format_failure_feedback(pipeline)
    assert "target repo already exists" in msg
    assert "What to do:" in msg
    assert "Delete the empty GitHub target repo" in msg


def test_should_retry_pev_blocks_non_retryable_failures():
    pipeline = {
        "status": "failed",
        "logs": ["repo: failed — target repo already exists on GitHub"],
        "steps": [],
    }
    executor = PevReview(
        role="executor",
        verdict="retry",
        summary="retry",
        retry_recommended=True,
        issues=["repo exists"],
        next_action="retry_migration",
        attempt=1,
    )
    validator = PevReview(
        role="validator",
        verdict="retry",
        summary="retry",
        retry_recommended=True,
        issues=["repo exists"],
        next_action="retry_migration",
        attempt=1,
    )
    assert should_retry_pev(executor, validator, pipeline, attempt=1) is False
