"""Dependency graph analysis with topological sorting (feature 008).

Builds a directed graph from DependencyEdge records, detects cycles,
and produces a topological ordering (Kahn's algorithm) for migration
sequencing.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from ado2gh.api.models import DependencyEdge
from ado2gh.api.discovery_store import DiscoveryStore


class CircularDependencyError(Exception):
    """Raised when a circular dependency is detected in the graph."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        cycle_str = " -> ".join(cycle + [cycle[0]])
        super().__init__(f"Circular dependency detected: {cycle_str}")


class DependencyGraph:
    """Directed graph of repository dependencies with topological sorting.

    Uses Kahn's algorithm for topological sort, which naturally detects
    cycles (remaining nodes after sort = cycle participants).
    """

    def __init__(self, edges: Optional[list[DependencyEdge]] = None):
        self._adj: dict[str, list[str]] = defaultdict(list)
        self._in_degree: dict[str, int] = defaultdict(int)
        self._nodes: set[str] = set()
        if edges:
            for edge in edges:
                self.add_edge(edge)

    @classmethod
    def from_store(cls, store: DiscoveryStore, repository_id: Optional[str] = None) -> DependencyGraph:
        """Build a graph from persisted dependency edges."""
        edges = store.get_dependency_edges(repository_id=repository_id)
        return cls(edges=edges)

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add a directed edge: source depends on target (source -> target)."""
        self._nodes.add(edge.source_repository_id)
        self._nodes.add(edge.target_repository_id)
        self._adj[edge.source_repository_id].append(edge.target_repository_id)
        self._in_degree[edge.target_repository_id] += 1
        if edge.source_repository_id not in self._in_degree:
            self._in_degree[edge.source_repository_id] = 0

    def add_node(self, node: str) -> None:
        """Add an isolated node (no edges)."""
        self._nodes.add(node)
        if node not in self._in_degree:
            self._in_degree[node] = 0

    @property
    def nodes(self) -> set[str]:
        return set(self._nodes)

    @property
    def edges(self) -> list[tuple[str, str]]:
        return [(s, t) for s, targets in self._adj.items() for t in targets]

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order (dependencies first).

        Raises CircularDependencyError if a cycle is detected.
        """
        in_deg = dict(self._in_degree)
        for n in self._nodes:
            if n not in in_deg:
                in_deg[n] = 0

        queue: deque[str] = deque(
            sorted(n for n in self._nodes if in_deg.get(n, 0) == 0)
        )
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in sorted(self._adj.get(node, [])):
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self._nodes):
            remaining = self._nodes - set(result)
            cycle = self._find_cycle(remaining)
            raise CircularDependencyError(cycle)

        return result

    def _find_cycle(self, remaining: set[str]) -> list[str]:
        """Find a cycle among the remaining nodes after topological sort."""
        visited: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> Optional[list[str]]:
            if node in path:
                idx = path.index(node)
                return path[idx:]
            if node in visited:
                return None
            visited.add(node)
            path.append(node)
            for neighbor in self._adj.get(node, []):
                if neighbor in remaining:
                    result = dfs(neighbor)
                    if result:
                        return result
            path.pop()
            return None

        for node in sorted(remaining):
            cycle = dfs(node)
            if cycle:
                return cycle
        return list(remaining)

    def get_transitive_dependencies(self, repository_id: str) -> list[str]:
        """Return all transitive dependencies of a repository in dependency order.

        Raises CircularDependencyError if a cycle is detected.
        """
        visited: set[str] = set()
        order: list[str] = []

        def visit(node: str):
            if node in visited:
                return
            visited.add(node)
            for dep in self._adj.get(node, []):
                visit(dep)
            order.append(node)

        visit(repository_id)
        # Remove the root repo itself, keep only dependencies
        if repository_id in order:
            order.remove(repository_id)
        return order

    def get_full_migration_order(self, repository_id: str) -> list[str]:
        """Return the full migration order including the root repo.

        Dependencies are listed first, then the root repo.
        Raises CircularDependencyError if a cycle is detected.
        """
        deps = self.get_transitive_dependencies(repository_id)
        return deps + [repository_id]

    def has_cycle(self) -> bool:
        """Check if the graph contains a cycle without raising."""
        try:
            self.topological_sort()
            return False
        except CircularDependencyError:
            return True

    def to_dict(self) -> dict:
        """Serialize graph to a dict suitable for JSON responses."""
        return {
            "nodes": sorted(self._nodes),
            "edges": [
                {"source": s, "target": t}
                for s, targets in sorted(self._adj.items())
                for t in sorted(targets)
            ],
        }
