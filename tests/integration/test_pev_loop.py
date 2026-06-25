"""Integration test for PEV loop (spec 011).

T036: Integration test for executor with guardrails.
Tests the full plan→execute→result flow with guardrail interception.

NOTE: Legacy AgentExecutor/AgentPlanner have been replaced by migration_agent
LangGraph nodes (spec 012). These tests are skipped pending rewrite.
"""
import pytest

pytest.skip("Legacy PEV modules deleted (spec 012) — rewrite for LangGraph nodes", allow_module_level=True)

from ado2gh.agents.migration_agent.guardrails import evaluate_guardrail


class TestExecutorWithGuardrails:
    """T036: Integration tests for executor with guardrail layer."""

    def _make_plan(self):
        planner = AgentPlanner()
        return planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA", "Project/RepoB"],
            dependency_edges=[
                {"from_repo": "Project/RepoA", "to_repo": "Project/RepoB"},
            ],
            dry_run=True,
            discovery_data={
                "repos": [
                    {"project": "Project", "repo_name": "RepoA", "total_score": 2},
                    {"project": "Project", "repo_name": "RepoB", "total_score": 1},
                ],
                "pipeline_inventory": [
                    {"name": "build-ci", "repo": "Project/RepoA"},
                    {"name": "deploy", "repo": "Project/RepoB"},
                ],
                "service_connections": [
                    {"name": "azure-sub", "type": "azurerm"},
                ],
            },
        )

    def test_full_plan_execution_with_guardrails(self):
        """Executor executes plan and guardrails allow valid operations."""
        plan = self._make_plan()
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True, session_id="test-session")

        assert isinstance(result, ExecutionResult)
        assert result.repo_mirror_status == "success"
        assert len(result.workflows_created) == 2
        assert len(result.secrets_provisioned) == 1

    def test_guardrail_blocks_execution_without_plan(self):
        """Guardrail blocks executor invocation without approved plan."""
        executor = AgentExecutor()
        result = executor.invoke(
            "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA"},
            approver_ok=True,
            approved_plan=None,
        )
        assert result["error"] == "guardrail_blocked"
        assert "approved plan" in result["reason"]

    def test_guardrail_blocks_unauthorized_repo(self):
        """Guardrail blocks execution targeting repo not in plan."""
        plan = self._make_plan()
        executor = AgentExecutor()
        result = executor.invoke(
            "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoEVIL"},
            approver_ok=True,
            approved_plan=plan,
        )
        assert result["error"] == "guardrail_blocked"
        assert "not found in approved plan" in result["reason"]

    def test_executor_skips_blocked_work_items(self):
        """Executor skips work items with blocked status."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data={
                "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 9}],
            },
        )
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True)
        assert len(result.skipped) == 1
        assert result.skipped[0]["repo"] == "Project/RepoA"
        assert "risk" in result.skipped[0]["reason"].lower()

    def test_executor_result_has_validator_fields(self):
        """Executor result contains all fields needed by validator."""
        plan = self._make_plan()
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True)
        d = result.to_dict()

        # Validator needs: what was created, what failed, what was skipped
        assert "workflows_created" in d
        assert "secrets_provisioned" in d
        assert "issues_created" in d
        assert "failures" in d
        assert "skipped" in d
        assert "gaps" in d

    def test_guardrail_decision_logged_in_result(self):
        """Guardrail decisions are tracked in execution result."""
        plan = self._make_plan()
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True)
        # guardrail_decisions list exists (may be empty for dry-run)
        assert hasattr(result, "guardrail_decisions")


# ─── T052: Continuous PEV loop with retry ───

class TestContinuousPevLoop:
    """T052: Integration tests for continuous PEV loop with retry."""

    def test_pev_loop_completes_on_success(self):
        """PEV loop completes when validation passes."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        # Cycle 1: plan → execute → validate → pass
        pev.start_cycle()
        pev.enter_planning()
        pev.enter_executing()
        pev.enter_validating()
        summary = pev.complete_cycle(
            repos_processed=1,
            repos_succeeded=1,
            repos_failed=0,
            failures=[],
            next_action="complete",
        )

        assert summary["cycle_number"] == 1
        assert summary["next_action"] == "complete"
        assert sm.state == SessionState.COMPLETED
        assert sm.is_terminal()

    def test_pev_loop_retries_on_validation_failure(self):
        """PEV loop retries when validation fails, up to 3 times."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator, MAX_PEV_RETRIES
        from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        # Cycle 1: fail → replan
        pev.start_cycle()
        pev.enter_planning()
        pev.enter_executing()
        pev.enter_validating()
        pev.complete_cycle(1, 0, 1, [{"scope": "pipelines"}], "replan")
        assert pev.pev_retry_count == 1
        assert sm.state == SessionState.PLANNING

        # Cycle 2: fail → replan
        pev.start_cycle()
        pev.enter_planning()
        pev.enter_executing()
        pev.enter_validating()
        pev.complete_cycle(1, 0, 1, [{"scope": "pipelines"}], "replan")
        assert pev.pev_retry_count == 2
        assert sm.state == SessionState.PLANNING

        # Cycle 3: fail → escalate (max retries reached)
        pev.start_cycle()
        pev.enter_planning()
        pev.enter_executing()
        pev.enter_validating()
        pev.complete_cycle(1, 0, 1, [{"scope": "pipelines"}], "replan")
        assert pev.pev_retry_count == 3
        assert sm.state == SessionState.FAILED
        assert sm.is_terminal()

    def test_pev_loop_max_iterations(self):
        """PEV loop stops after max total iterations."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator, MAX_TOTAL_ITERATIONS
        from ado2gh.agents.session_state_machine import SessionStateMachine

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        # Run continue cycles up to max
        for _ in range(MAX_TOTAL_ITERATIONS):
            pev.start_cycle()
            pev.complete_cycle(1, 1, 0, [], "continue")

        assert not pev.can_continue()

    def test_pev_loop_user_input_pause(self):
        """PEV loop pauses for user input and resumes."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        pev.start_cycle()
        pev.enter_planning()
        pev.request_user_input()
        assert sm.state == SessionState.AWAITING_INPUT

        pev.resume_from_input()
        assert sm.state == SessionState.THINKING

    def test_inter_agent_message_creation(self):
        """T055: Inter-agent messages have required structured fields."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        msg = pev.create_message(
            from_role="validator",
            to_role="planner",
            message_type="feedback",
            payload={"failed_scopes": ["pipelines"]},
            correlation_ids={"plan_id": "plan-123"},
        )

        assert msg["message_type"] == "feedback"
        assert msg["from_role"] == "validator"
        assert msg["to_role"] == "planner"
        assert msg["payload"] == {"failed_scopes": ["pipelines"]}
        assert msg["correlation_ids"] == {"plan_id": "plan-123"}
        assert "message_id" in msg
        assert "timestamp" in msg

    def test_batch_queue_sequential_processing(self):
        """T059: Batch queue processes repos sequentially."""
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("test-session", sm)

        queue = pev.init_batch_queue("plan-1", ["RepoA", "RepoB", "RepoC"])
        assert queue["current_index"] == 0

        # Process RepoA
        assert pev.batch_queue_next(queue) == "RepoA"
        pev.advance_batch_queue(queue, "RepoA", True)
        assert queue["current_index"] == 1
        assert "RepoA" in queue["completed_items"]

        # Process RepoB
        assert pev.batch_queue_next(queue) == "RepoB"
        pev.advance_batch_queue(queue, "RepoB", False)
        assert queue["current_index"] == 2
        assert "RepoB" in queue["failed_items"]

        # Process RepoC
        assert pev.batch_queue_next(queue) == "RepoC"
        pev.advance_batch_queue(queue, "RepoC", True)
        assert queue["current_index"] == 3

        # Queue exhausted
        assert pev.batch_queue_next(queue) is None
        assert pev.batch_queue_is_complete(queue)


# ─── T060: End-to-end hands-off migration ───

class TestEndToEndMigration:
    """T060: Integration test for end-to-end hands-off single repo migration."""

    def test_full_plan_execute_validate_cycle(self):
        """Full PEV cycle: plan → execute → validate → complete."""
        from ado2gh.agents.planner import AgentPlanner
        from ado2gh.agents.executor import AgentExecutor
        from ado2gh.agents.validator import AgentValidator
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

        # Setup
        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("e2e-session", sm)
        planner = AgentPlanner()
        executor = AgentExecutor()
        validator = AgentValidator()

        # Discovery data for a repo with pipelines and service connections
        discovery_data = {
            "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
            "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
            "service_connections": [{"name": "azure-sub", "type": "azurerm"}],
        }

        # Start PEV cycle
        pev.start_cycle()
        assert sm.state == SessionState.THINKING

        # Plan
        pev.enter_planning()
        assert sm.state == SessionState.PLANNING
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data=discovery_data,
        )
        assert plan["profile_id"] == "lightweight"
        assert len(plan["work_items"]) == 1

        # Execute
        pev.enter_executing()
        assert sm.state == SessionState.EXECUTING
        exec_result = executor.execute_plan(plan, dry_run=True, session_id="e2e-session")
        assert exec_result.repo_mirror_status == "success"
        assert len(exec_result.workflows_created) == 1

        # Validate
        pev.enter_validating()
        assert sm.state == SessionState.VALIDATING
        val_result = validator.validate_execution(plan, exec_result.to_dict(), dry_run=True)
        assert val_result.passed is True

        # Complete
        summary = pev.complete_cycle(
            repos_processed=1,
            repos_succeeded=1,
            repos_failed=0,
            failures=[],
            next_action="complete",
        )
        assert summary["next_action"] == "complete"
        assert sm.state == SessionState.COMPLETED
        assert sm.is_terminal()

    def test_full_cycle_with_validation_retry(self):
        """Full PEV cycle with validation failure → replan → success."""
        from ado2gh.agents.planner import AgentPlanner
        from ado2gh.agents.executor import AgentExecutor
        from ado2gh.agents.validator import AgentValidator
        from ado2gh.agents.pev_cycle import PevCycleOrchestrator
        from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState

        sm = SessionStateMachine()
        pev = PevCycleOrchestrator("retry-session", sm)
        planner = AgentPlanner()
        executor = AgentExecutor()
        validator = AgentValidator()

        discovery_data = {
            "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
            "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
        }

        # Cycle 1: plan → execute → validate → fail → replan
        pev.start_cycle()
        pev.enter_planning()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data=discovery_data,
        )
        pev.enter_executing()
        exec_result = executor.execute_plan(plan, dry_run=True)
        # Simulate a failure by adding an unexpected workflow
        exec_output = exec_result.to_dict()
        exec_output["workflows_created"].append({"github_workflow": "evil.yml"})
        pev.enter_validating()
        val_result = validator.validate_execution(plan, exec_output, dry_run=True)
        assert val_result.passed is False
        pev.complete_cycle(1, 0, 1, val_result.failures, "replan")
        assert pev.pev_retry_count == 1
        assert sm.state == SessionState.PLANNING

        # Cycle 2: replan → execute → validate → pass → complete
        pev.start_cycle()
        pev.enter_planning()
        revised_plan = planner.generate_revised_plan(plan, val_result.feedback_to_planner)
        pev.enter_executing()
        exec_result2 = executor.execute_plan(revised_plan, dry_run=True)
        pev.enter_validating()
        val_result2 = validator.validate_execution(revised_plan, exec_result2.to_dict(), dry_run=True)
        assert val_result2.passed is True
        pev.complete_cycle(1, 1, 0, [], "complete")
        assert sm.state == SessionState.COMPLETED
