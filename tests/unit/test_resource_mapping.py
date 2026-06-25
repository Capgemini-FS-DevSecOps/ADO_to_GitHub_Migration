"""Unit tests for resource mapping (spec 011).
NOTE: Legacy planner deleted (spec 012) — resource_mapping tests need migration_agent rewrite.
"""
import pytest

pytest.skip("Legacy planner deleted (spec 012) — rewrite for migration_agent", allow_module_level=True)
from ado2gh.agents.resource_mapping import (
    get_mapping, list_mappings, is_supported_artifact_type,
    BOARDS_MAPPING, TEST_CASE_MAPPING, ARTIFACT_FEED_MAPPING,
)
from ado2gh.agents.planner import AgentPlanner


class TestResourceMapping:
    def test_get_mapping_boards(self):
        m = get_mapping("boards")
        assert m is not None
        assert m.ado_type == "work_item"
        assert m.github_target == "issue"

    def test_get_mapping_unknown(self):
        assert get_mapping("nonexistent") is None

    def test_list_mappings(self):
        mappings = list_mappings()
        assert len(mappings) == 5
        types = {m["ado_type"] for m in mappings}
        assert "work_item" in types
        assert "test_case" in types
        assert "artifact_feed" in types

    def test_boards_override_allowed(self):
        assert BOARDS_MAPPING.override_allowed is True

    def test_test_case_override_not_allowed(self):
        assert TEST_CASE_MAPPING.override_allowed is False

    def test_is_supported_artifact_type(self):
        assert is_supported_artifact_type("npm") is True
        assert is_supported_artifact_type("NuGet") is True
        assert is_supported_artifact_type("Docker") is True
        assert is_supported_artifact_type("Python") is False

    def test_artifact_feed_supported_types(self):
        assert "npm" in ARTIFACT_FEED_MAPPING.supported_types
        assert "PyPI" in ARTIFACT_FEED_MAPPING.supported_types


class TestPlanGeneration:
    """T022: Unit tests for plan generation with all resource types."""

    def test_plan_includes_all_resource_types(self):
        """Plan includes mappings for boards, test_case, test_suite, artifact_feed, wiki_page."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
        )
        rm = plan["resource_mappings"]
        assert "boards" in rm
        assert "test_case" in rm
        assert "test_suite" in rm
        assert "artifact_feed" in rm
        assert "wiki_page" in rm

    def test_plan_includes_work_items_with_scopes(self):
        """Plan work items include all ADO resource type scopes."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
        )
        wi = plan["work_items"]
        assert len(wi) == 1
        scopes = wi[0]["scopes"]
        assert "repo" in scopes
        assert "pipelines" in scopes
        assert "secrets" in scopes
        assert "boards" in scopes
        assert "test_plans" in scopes
        assert "artifacts" in scopes
        assert "wiki" in scopes

    def test_plan_includes_topological_order(self):
        """Plan repo_order respects topological sort with dependencies."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA", "Project/RepoB", "Project/RepoC"],
            dependency_edges=[
                {"from_repo": "Project/RepoA", "to_repo": "Project/RepoB"},
                {"from_repo": "Project/RepoA", "to_repo": "Project/RepoC"},
            ],
            dry_run=True,
        )
        order = plan["repo_order"]
        # RepoB and RepoC (dependencies) must come before RepoA (consumer)
        assert order.index("Project/RepoB") < order.index("Project/RepoA")
        assert order.index("Project/RepoC") < order.index("Project/RepoA")

    def test_plan_includes_dry_run_flag(self):
        """Plan includes dry_run flag."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=False,
        )
        assert plan["dry_run"] is False

    def test_plan_includes_revision_number(self):
        """Plan includes revision number (default 1)."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
        )
        assert plan["revision"] == 1

    def test_plan_includes_assumptions(self):
        """Plan includes assumptions when discovery data is incomplete."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
        )
        assert len(plan["assumptions"]) >= 1
        assert plan["assumptions"][0]["requires_confirmation"] is True

    def test_plan_with_discovery_data(self):
        """Plan with discovery data populates pipeline and secret mappings."""
        planner = AgentPlanner()
        discovery = {
            "repos": [
                {"project": "Project", "repo_name": "RepoA", "total_score": 3},
            ],
            "pipeline_inventory": [
                {"name": "build-ci", "repo": "Project/RepoA"},
            ],
            "service_connections": [
                {"name": "azure-sub", "type": "azurerm"},
            ],
        }
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data=discovery,
        )
        assert len(plan["pipeline_mappings"]) == 1
        assert plan["pipeline_mappings"][0]["ado_pipeline"] == "build-ci"
        assert len(plan["secret_mappings"]) == 1
        assert plan["secret_mappings"][0]["ado_service_connection"] == "azure-sub"

    def test_plan_work_item_blocked_for_high_risk(self):
        """Work item is blocked when repo risk score >= 8."""
        planner = AgentPlanner()
        discovery = {
            "repos": [
                {"project": "Project", "repo_name": "RepoA", "total_score": 9},
            ],
        }
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data=discovery,
        )
        wi = plan["work_items"][0]
        assert wi["status"] == "blocked"
        assert any("risk" in r.lower() for r in wi["blocked_reasons"])

    def test_revised_plan_increments_revision(self):
        """Revised plan increments revision number (T026)."""
        planner = AgentPlanner()
        original = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
        )
        revised = planner.generate_revised_plan(
            original,
            {"failed_scopes": ["pipelines"], "summary": "Pipeline conversion failed"},
        )
        assert revised["revision"] == 2
        assert revised["revised"] is True
        assert "pipelines" in revised["failed_scopes"]

    def test_override_resource_mapping_allowed(self):
        """Boards mapping can be overridden (override_allowed=True) (T028)."""
        planner = AgentPlanner()
        result = planner.override_resource_mapping(
            "boards",
            {"github_target": "project"},
            "Custom project board structure requires project-level issues",
        )
        assert result is not None
        assert result["override_allowed"] is True
        assert result["justification"] != ""

    def test_override_resource_mapping_not_allowed(self):
        """Test case mapping cannot be overridden (override_allowed=False) (T028)."""
        planner = AgentPlanner()
        result = planner.override_resource_mapping(
            "test_case",
            {"github_target": "project"},
            "Attempted override",
        )
        assert result is None

    def test_request_clarification(self):
        """Planner can request clarification from orchestrator (T025)."""
        planner = AgentPlanner()
        req = planner.request_clarification("secret_value", {"repo": "Project/RepoA"})
        assert req["type"] == "clarification_request"
        assert req["missing_info"] == "secret_value"
        assert len(req["fields"]) == 1
