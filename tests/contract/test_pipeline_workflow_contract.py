"""Contract smoke for workflow branch strategy."""
from ado2gh.agents.planner import AgentPlanner


def test_planner_includes_workflow_branch():
    plan = AgentPlanner().plan("p1", ["A/r1"], [], workflow_layout="modular")
    assert plan["workflow_branch"] == "ado2gh/migrated-workflows"
