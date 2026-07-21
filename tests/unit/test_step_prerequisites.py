"""Unit tests for StepPrerequisiteChecker (feature 009, T013 + T024)."""
from __future__ import annotations

from ado2gh.api.pipeline_runner import PipelineRun, PipelineStep, StepStatus
from ado2gh.api.step_prerequisites import (
    STEP_PREREQUISITES,
    StepPrerequisiteChecker,
)


def _run(step_states: list[tuple[str, StepStatus]]) -> PipelineRun:
    """Build a PipelineRun whose steps have the given (id, status) states."""
    steps = []
    for step_id, status in step_states:
        steps.append(
            PipelineStep(
                id=step_id,
                label=step_id.replace("_", " ").title(),
                description="",
                status=status,
            )
        )
    return PipelineRun(id="run-1", name="test", steps=steps)


class TestCheck:
    def test_no_prerequisites_passes(self):
        checker = StepPrerequisiteChecker()
        run = _run([("connect", StepStatus.PENDING)])
        ok, missing = checker.check(run, "connect")
        assert ok is True
        assert missing == []

    def test_passes_when_prerequisite_completed(self):
        checker = StepPrerequisiteChecker()
        run = _run([
            ("analyze_deps", StepStatus.COMPLETED),
            ("convert_pipelines", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "convert_pipelines")
        assert ok is True
        assert missing == []

    def test_passes_when_prerequisite_warn(self):
        # WARN is an acceptable completion state for a prerequisite.
        checker = StepPrerequisiteChecker()
        run = _run([
            ("analyze_deps", StepStatus.WARN),
            ("migrate_repos", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "migrate_repos")
        assert ok is True

    def test_fails_when_prerequisite_pending(self):
        checker = StepPrerequisiteChecker()
        run = _run([
            ("analyze_deps", StepStatus.PENDING),
            ("convert_pipelines", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "convert_pipelines")
        assert ok is False
        assert missing == ["Analyze Deps"]

    def test_fails_when_prerequisite_failed(self):
        checker = StepPrerequisiteChecker()
        run = _run([
            ("analyze_deps", StepStatus.FAILED),
            ("migrate_repos", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "migrate_repos")
        assert ok is False
        assert missing == ["Analyze Deps"]

    def test_absent_prerequisite_is_out_of_scope(self):
        # inventory is not part of this run's pipeline (MIGRATE_UI flow), so the
        # analyze_deps -> inventory prerequisite is not enforced.
        checker = StepPrerequisiteChecker()
        run = _run([
            ("connect", StepStatus.COMPLETED),
            ("analyze_deps", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "analyze_deps")
        assert ok is True
        assert missing == []

    def test_multiple_missing_prerequisites(self):
        # validate requires both migrate_repos and convert_pipelines.
        checker = StepPrerequisiteChecker()
        run = _run([
            ("migrate_repos", StepStatus.PENDING),
            ("convert_pipelines", StepStatus.PENDING),
            ("validate", StepStatus.PENDING),
        ])
        ok, missing = checker.check(run, "validate")
        assert ok is False
        assert set(missing) == {"Migrate Repos", "Convert Pipelines"}

    def test_uses_run_step_label(self):
        checker = StepPrerequisiteChecker()
        steps = [
            PipelineStep(id="analyze_deps", label="Analyze dependencies",
                         description="", status=StepStatus.PENDING),
            PipelineStep(id="convert_pipelines", label="Convert workflows",
                         description="", status=StepStatus.PENDING),
        ]
        run = PipelineRun(id="r", name="n", steps=steps)
        ok, missing = checker.check(run, "convert_pipelines")
        assert ok is False
        assert missing == ["Analyze dependencies"]  # run-provided label preferred


class TestFailureMessage:
    def test_single_prerequisite_message(self):
        checker = StepPrerequisiteChecker()
        msg = checker.failure_message(["Analyze dependencies"], "Convert workflows")
        assert msg == (
            "Analyze dependencies must be completed before Convert workflows can proceed."
        )

    def test_multiple_prerequisite_message(self):
        checker = StepPrerequisiteChecker()
        msg = checker.failure_message(
            ["Migrate repositories", "Convert workflows"], "Validate"
        )
        assert "Migrate repositories, Convert workflows" in msg
        assert msg.endswith("before Validate can proceed.")


class TestCustomPrerequisites:
    def test_custom_prerequisite_map(self):
        checker = StepPrerequisiteChecker({"b": ["a"]})
        run = _run([("a", StepStatus.PENDING), ("b", StepStatus.PENDING)])
        ok, missing = checker.check(run, "b")
        assert ok is False
        assert missing == ["A"]

    def test_default_map_is_complete(self):
        # Every pipeline step id has an entry in the prerequisite map.
        for step_id in (
            "connect", "discover", "inventory", "readiness", "assign",
            "analyze_deps", "migrate_repos", "convert_pipelines",
            "convert_metadata", "migrate", "validate",
        ):
            assert step_id in STEP_PREREQUISITES
