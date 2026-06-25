"""Unit tests for DependencyGraph (feature 008, T077, T088)."""
from __future__ import annotations

import pytest
from ado2gh.api.dependency_graph import DependencyGraph, CircularDependencyError
from ado2gh.api.models import DependencyEdge


def _edge(src: str, tgt: str) -> DependencyEdge:
    return DependencyEdge(source_repository_id=src, target_repository_id=tgt)


class TestDependencyGraphBasic:
    def test_empty_graph(self):
        graph = DependencyGraph()
        assert graph.topological_sort() == []
        assert not graph.has_cycle()

    def test_single_node(self):
        graph = DependencyGraph()
        graph.add_node("repo-a")
        assert "repo-a" in graph.topological_sort()

    def test_add_edge(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("repo-a", "repo-b"))
        deps = graph.get_transitive_dependencies("repo-a")
        assert "repo-b" in deps


class TestTransitiveDependencies:
    def test_chain_of_three(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "c"))

        deps = graph.get_transitive_dependencies("a")
        assert set(deps) == {"b", "c"}

    def test_diamond_dependency(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("a", "c"))
        graph.add_edge(_edge("b", "d"))
        graph.add_edge(_edge("c", "d"))

        deps = graph.get_transitive_dependencies("a")
        assert set(deps) == {"b", "c", "d"}

    def test_no_dependencies(self):
        graph = DependencyGraph()
        graph.add_node("isolated-repo")
        deps = graph.get_transitive_dependencies("isolated-repo")
        assert deps == []


class TestTopologicalSort:
    def test_linear_order(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "c"))

        order = graph.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_independent_nodes_any_order(self):
        graph = DependencyGraph()
        graph.add_node("x")
        graph.add_node("y")
        order = graph.topological_sort()
        assert set(order) == {"x", "y"}


class TestCycleDetection:
    """FR-015: Detect and report circular dependencies."""

    def test_simple_cycle(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "a"))
        assert graph.has_cycle()

    def test_three_node_cycle(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "c"))
        graph.add_edge(_edge("c", "a"))
        assert graph.has_cycle()

    def test_no_cycle_in_dag(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "c"))
        graph.add_edge(_edge("a", "c"))
        assert not graph.has_cycle()

    def test_two_node_cycle_detected(self):
        graph = DependencyGraph()
        graph.add_edge(_edge("x", "y"))
        graph.add_edge(_edge("y", "x"))
        assert graph.has_cycle()

    def test_cycle_error_message(self):
        """Cycle detection should provide clear error message."""
        graph = DependencyGraph()
        graph.add_edge(_edge("a", "b"))
        graph.add_edge(_edge("b", "c"))
        graph.add_edge(_edge("c", "a"))

        has_cycle = graph.has_cycle()
        assert has_cycle, "Should detect cycle a → b → c → a"
