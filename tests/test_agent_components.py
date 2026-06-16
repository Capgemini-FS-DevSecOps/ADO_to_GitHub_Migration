"""Agent planner and boards gaps."""
from ado2gh.agents.planner import AgentPlanner
from ado2gh.agents.validator import AgentValidator
from ado2gh.reporting.boards_gaps import generate_boards_gaps


def test_planner_topo():
    plan = AgentPlanner().plan(
        "p1",
        ["B/r2", "A/r1"],
        [{"from_repo": "B/r2", "to_repo": "A/r1"}],
    )
    assert plan["repo_order"][0] == "A/r1"


def test_boards_gaps():
    report = generate_boards_gaps(10, 8, ["custom_field"])
    assert report["gap_count"] >= 2
