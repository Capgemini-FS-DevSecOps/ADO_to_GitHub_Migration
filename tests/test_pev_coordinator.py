"""Tests for LLM-driven PEV coordinator reviews."""
from __future__ import annotations

import json

from ado2gh.agents.llm_provider import StubLLMProvider
from ado2gh.agents.pev_coordinator import (
    MAX_PEV_RETRIES,
    review_executor_output,
    review_planner_output,
    review_validator_output,
    should_retry_migration,
)


class JsonStubLLM(StubLLMProvider):
    def __init__(self, payload: dict):
        self.payload = payload

    def complete(self, prompt: str, system: str | None = None) -> str:
        return json.dumps(self.payload)


def test_planner_fallback_when_degraded():
    plan = {"phase": "poc", "repo_count": 2, "dry_run": True, "narrative": "Ready."}
    review = review_planner_output(StubLLMProvider(), plan, llm_degraded=True)
    assert review.verdict == "pass"
    assert review.next_action == "proceed"
    assert "Ready" in review.summary


def test_planner_blocked_abort():
    plan = {"blocked": True, "block_reason": "No repos assigned"}
    review = review_planner_output(StubLLMProvider(), plan, llm_degraded=True)
    assert review.verdict == "fail"
    assert review.next_action == "abort"


def test_executor_retry_on_failure():
    pipeline = {
        "status": "failed",
        "steps": [{"id": "migrate_repos", "status": "failed", "message": "git push failed"}],
    }
    review = review_executor_output(StubLLMProvider(), pipeline, attempt=1, llm_degraded=True)
    assert review.retry_recommended is True
    assert review.next_action == "retry_migration"


def test_validator_pass_on_dry_run_skip():
    pipeline = {"status": "dry_run_complete", "steps": []}
    validate_step = {"status": "skipped", "message": "Skipped — dry run"}
    review = review_validator_output(
        StubLLMProvider(), pipeline, validate_step, attempt=1, llm_degraded=True,
    )
    assert review.verdict == "pass"
    assert review.next_action == "proceed"


def test_should_retry_migration_respects_max():
    from ado2gh.agents.pev_coordinator import PevReview

    review = PevReview(
        role="validator",
        verdict="retry",
        summary="retry",
        retry_recommended=True,
        next_action="retry_migration",
        attempt=MAX_PEV_RETRIES,
    )
    assert should_retry_migration(review, MAX_PEV_RETRIES) is False


def test_llm_json_review_used_when_valid():
    plan = {"phase": "poc", "repo_count": 1, "dry_run": True}
    llm = JsonStubLLM({
        "verdict": "pass",
        "summary": "LLM approved plan.",
        "retry_recommended": False,
        "issues": [],
        "next_action": "proceed",
    })
    review = review_planner_output(llm, plan, llm_degraded=False)
    assert review.summary == "LLM approved plan."
    assert review.verdict == "pass"
