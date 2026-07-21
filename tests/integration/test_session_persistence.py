"""Integration test for session persistence and resume (spec 011).

T053: Tests that session state persists across server restarts and can be resumed.
NOTE: Legacy modules deleted (spec 012) — rewrite for migration_agent.session_store.
"""
import pytest

pytest.skip("Legacy PEV modules deleted (spec 012) — rewrite for migration_agent", allow_module_level=True)

import os
import tempfile

from ado2gh.agents.session_store import SessionStore
from ado2gh.agents.session_state_machine import SessionStateMachine, SessionState
from ado2gh.agents.pev_cycle import PevCycleOrchestrator


class TestSessionPersistence:
    """T053: Session persistence and resume after restart."""

    def _make_store(self, tmpdir):
        db_path = os.path.join(tmpdir, "test_sessions.db")
        os.environ["ADO2GH_STORAGE_BACKEND"] = "sqlite"
        os.environ["ADO2GH_DB_PATH"] = db_path
        return SessionStore()

    def test_session_persists_and_reloads(self, tmp_path):
        """Session state survives store recreation (simulates restart)."""
        store = self._make_store(str(tmp_path))

        # Create a session
        record = store.create_session(profile_id="lightweight", dry_run=True)
        sid = record["session_id"]
        assert record["status"] == "idle"

        # Update session state
        store.update_session_status(sid, "planning")
        store.update_session(
            sid,
            iteration_count=2,
            pev_retry_count=1,
            migration_plan={"repos": ["Project/RepoA"]},
        )

        # Simulate restart by creating a new store pointing at same DB
        store2 = self._make_store(str(tmp_path))
        loaded = store2.get_session(sid)

        assert loaded is not None
        assert loaded["session_id"] == sid
        assert loaded["status"] == "planning"
        assert loaded["iteration_count"] == 2
        assert loaded["pev_retry_count"] == 1
        assert loaded["migration_plan"] == {"repos": ["Project/RepoA"]}

    def test_messages_persist_across_restart(self, tmp_path):
        """Inter-agent messages persist across restart."""
        store = self._make_store(str(tmp_path))
        record = store.create_session(profile_id="lightweight")
        sid = record["session_id"]

        store.add_message(
            session_id=sid,
            from_role="planner",
            to_role="executor",
            message_type="instruction",
            payload={"action": "migrate_repo"},
            correlation_ids={"plan_id": "plan-1"},
        )

        # Simulate restart
        store2 = self._make_store(str(tmp_path))
        messages = store2.get_messages(sid)

        assert len(messages) == 1
        assert messages[0]["from_role"] == "planner"
        assert messages[0]["to_role"] == "executor"
        assert messages[0]["message_type"] == "instruction"

    def test_pev_cycle_summary_persists(self, tmp_path):
        """PEV cycle summaries persist across restart."""
        store = self._make_store(str(tmp_path))
        record = store.create_session(profile_id="lightweight")
        sid = record["session_id"]

        store.save_pev_cycle_summary(
            session_id=sid,
            cycle_number=1,
            repos_processed=3,
            repos_succeeded=2,
            repos_failed=1,
            failures=[{"scope": "pipelines"}],
            next_action="replan",
        )

        # Simulate restart
        store2 = self._make_store(str(tmp_path))
        summaries = store2.get_pev_cycle_summaries(sid)

        assert len(summaries) == 1
        assert summaries[0]["cycle_number"] == 1
        assert summaries[0]["repos_processed"] == 3
        assert summaries[0]["next_action"] == "replan"

    def test_resume_pev_loop_after_restart(self, tmp_path):
        """PEV loop can resume from persisted state after restart."""
        store = self._make_store(str(tmp_path))
        record = store.create_session(profile_id="lightweight")
        sid = record["session_id"]

        # Simulate a PEV cycle in progress
        sm = SessionStateMachine()
        sm.transition(SessionState.THINKING)
        sm.transition(SessionState.PLANNING)
        store.update_session_status(sid, "planning")
        store.update_session(sid, iteration_count=1, pev_retry_count=0)

        # Simulate restart — reload state and resume
        store2 = self._make_store(str(tmp_path))
        loaded = store2.get_session(sid)
        assert loaded["status"] == "planning"
        assert loaded["iteration_count"] == 1

        # Resume PEV loop
        sm2 = SessionStateMachine()
        sm2._state = SessionState.PLANNING
        pev = PevCycleOrchestrator(sid, sm2, session_store=store2)
        pev.start_cycle()
        pev.enter_executing()
        pev.enter_validating()
        summary = pev.complete_cycle(1, 1, 0, [], "complete")

        assert summary["next_action"] == "complete"
        assert sm2.state == SessionState.COMPLETED

    def test_migration_plan_persists(self, tmp_path):
        """Migration plans persist across restart."""
        from ado2gh.agents.planner import AgentPlanner

        store = self._make_store(str(tmp_path))
        record = store.create_session(profile_id="lightweight")
        sid = record["session_id"]

        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data={
                "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
            },
        )
        plan_id = store.save_plan(
            session_id=sid,
            repos=plan.get("repo_order", []),
            work_items=plan.get("work_items", []),
            dry_run=plan.get("dry_run", True),
            assumptions=plan.get("assumptions", []),
            blocked_items=plan.get("blocked_items", []),
            revision=plan.get("revision", 1),
        )

        # Simulate restart
        store2 = self._make_store(str(tmp_path))
        loaded_plan = store2.get_plan(plan_id)

        assert loaded_plan is not None
        assert "work_items" in loaded_plan
        assert "repos" in loaded_plan
