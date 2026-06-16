"""Repository dependency graph — topological sort and cycle detection (FR-045)."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class RepoDependencyEdge:
    """Edge from consumer repo to dependency repo."""

    from_repo: str
    to_repo: str
    edge_type: str = "pipeline_resource"


@dataclass
class DependencyGraphResult:
    """Topological sort output or cycle report."""

    sorted_repos: List[str] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)
    edges: List[RepoDependencyEdge] = field(default_factory=list)

    @property
    def has_cycles(self) -> bool:
        return bool(self.cycles)


def build_graph(edges: List[RepoDependencyEdge]) -> DependencyGraphResult:
    """Build adjacency and run Kahn's algorithm for topological order."""
    result = DependencyGraphResult(edges=edges)
    nodes: set[str] = set()
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = defaultdict(int)

    for e in edges:
        nodes.add(e.from_repo)
        nodes.add(e.to_repo)
        adj[e.to_repo].append(e.from_repo)
        indeg[e.from_repo] += 1
        if e.to_repo not in indeg:
            indeg[e.to_repo] = indeg.get(e.to_repo, 0)

    for n in nodes:
        indeg.setdefault(n, 0)

    q = deque([n for n in nodes if indeg[n] == 0])
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in adj.get(n, []):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)

    if len(order) != len(nodes):
        result.cycles = _find_cycles(nodes, adj)
        result.sorted_repos = []
    else:
        result.sorted_repos = order
    return result


def _find_cycles(nodes: set[str], adj: dict[str, list[str]]) -> List[List[str]]:
    """Return simple cycle hints for operator review."""
    cycles: list[list[str]] = []
    for start in nodes:
        path = [start]
        seen = {start}
        stack = list(adj.get(start, []))
        while stack:
            cur = stack.pop()
            if cur in seen:
                cycles.append(path + [cur])
                break
            seen.add(cur)
            path.append(cur)
            stack.extend(adj.get(cur, []))
    return cycles[:5]


def sort_repo_order(
    repo_keys: List[str],
    edges: List[RepoDependencyEdge],
) -> Tuple[List[str], Optional[List[List[str]]]]:
    """Return topo-sorted subset of repo_keys or cycles if blocked."""
    filtered = [e for e in edges if e.from_repo in repo_keys and e.to_repo in repo_keys]
    res = build_graph(filtered)
    if res.has_cycles:
        return repo_keys, res.cycles
    ordered = [r for r in res.sorted_repos if r in repo_keys]
    for r in repo_keys:
        if r not in ordered:
            ordered.append(r)
    return ordered, None
