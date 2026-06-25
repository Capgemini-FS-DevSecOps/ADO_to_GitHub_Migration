"""Topological sort and cycle detection tests."""
from ado2gh.pipelines.dependency_graph import RepoDependencyEdge, build_graph, sort_repo_order


def test_empty_graph():
    res = build_graph([])
    assert res.sorted_repos == []
    assert not res.has_cycles


def test_topo_sort():
    edges = [
        RepoDependencyEdge("Payments/api", "Platform/lib"),
        RepoDependencyEdge("Payments/web", "Payments/api"),
    ]
    res = build_graph(edges)
    assert res.sorted_repos.index("Platform/lib") < res.sorted_repos.index("Payments/api")
    assert res.sorted_repos.index("Payments/api") < res.sorted_repos.index("Payments/web")


def test_cycle_detection():
    edges = [
        RepoDependencyEdge("a", "b"),
        RepoDependencyEdge("b", "a"),
    ]
    res = build_graph(edges)
    assert res.has_cycles
    assert res.sorted_repos == []


def test_sort_repo_order_subset():
    ordered, cycles = sort_repo_order(
        ["Payments/api", "Platform/lib"],
        [RepoDependencyEdge("Payments/api", "Platform/lib")],
    )
    assert cycles is None
    assert ordered[0] == "Platform/lib"
