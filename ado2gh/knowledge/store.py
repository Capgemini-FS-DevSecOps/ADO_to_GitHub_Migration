"""Persistence for the migration knowledge base.

This module is the only place that knows how a thing, a dependency or a scan
turns into stored rows. It keeps one row per real thing by deriving the row
identifier from what makes that thing itself, so a second scan of the same
organisation refreshes the same rows instead of piling up near-duplicates, and
the date a thing was first seen survives every later scan.

Reading goes both ways: forwards along the dependencies to answer "what does
this need", backwards to answer "what would notice if this moved". A
dependency that stops being seen is marked as disappeared rather than deleted,
so the record shows that it went away instead of quietly forgetting it ever
existed. Every answer carries the coverage of the scans behind it and a plain
sentence saying that nothing recorded is not the same as nothing existing.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from typing import Any

from ado2gh.knowledge.models import (
    NO_RECORDED_DEPENDENCY_CAVEAT,
    RELATION_CONSUMER,
    RELATION_PREREQUISITE,
    RELATION_SHARED_DEPENDENCY,
    Confidence,
    EdgeKind,
    EdgeStatus,
    ImpactFinding,
    ImpactReport,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeScan,
    NodeKind,
    utc_now_text,
)
from ado2gh.state.base import StateDBBase

DIRECTION_DEPENDENCIES = "dependencies"
"""Walk forwards, from a consumer to the things it depends on."""

DIRECTION_CONSUMERS = "consumers"
"""Walk backwards, from a thing to whatever depends on it."""

_RELATION_BY_DIRECTION = {
    DIRECTION_DEPENDENCIES: RELATION_PREREQUISITE,
    DIRECTION_CONSUMERS: RELATION_CONSUMER,
}

# Most believed first; the walk keeps the least believed link it crossed.
_CONFIDENCE_ORDER = {
    Confidence.INFERRED: 0,
    Confidence.RESOLVED: 1,
    Confidence.DECLARED: 2,
}

# Separator that cannot appear in a name, so two different identities can never
# be joined into the same text.
_IDENTITY_SEPARATOR = "\x1f"

_NODE_PREFIX = "kn_"
_EDGE_PREFIX = "ke_"
_SCAN_PREFIX = "ks_"


def _identity(prefix: str, parts: list[str]) -> str:
    """Return a stable identifier for the parts that make a record itself.

    Args:
        prefix: Short marker saying what kind of record the identifier names.
        parts: The values that decide identity, in a fixed order.

    Returns:
        The prefix followed by a hash of the parts, the same in every process
        and on every run, so the same thing keeps the same identifier.
    """
    joined = _IDENTITY_SEPARATOR.join(parts).encode("utf-8")
    return f"{prefix}{hashlib.sha256(joined).hexdigest()[:24]}"


def _read_json(text: str) -> dict[str, Any]:
    """Return a stored JSON column as a mapping, empty when it cannot be read."""
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _node_from_row(row: dict[str, Any]) -> KnowledgeNode:
    """Build a node record from one stored row."""
    return KnowledgeNode(
        node_id=str(row.get("id", "")),
        profile_id=str(row.get("profile_id", "")),
        system=str(row.get("system", "")),
        organization=str(row.get("organization", "")),
        project=str(row.get("project", "")),
        kind=NodeKind(str(row.get("kind", NodeKind.UNKNOWN.value))),
        identity_key=str(row.get("identity_key", "")),
        external_id=str(row.get("external_id", "")),
        name=str(row.get("name", "")),
        url=str(row.get("url", "")),
        source_locator=str(row.get("source_locator", "")),
        first_seen_at=str(row.get("first_seen_at", "")),
        last_seen_at=str(row.get("last_seen_at", "")),
        last_scan_id=str(row.get("last_scan_id", "")),
    )


def _edge_from_row(row: dict[str, Any]) -> KnowledgeEdge:
    """Build a dependency record from one stored row."""
    return KnowledgeEdge(
        edge_id=str(row.get("id", "")),
        profile_id=str(row.get("profile_id", "")),
        source_node_id=str(row.get("source_node_id", "")),
        target_node_id=str(row.get("target_node_id", "")),
        kind=EdgeKind(str(row.get("kind", ""))),
        confidence=Confidence(str(row.get("confidence", Confidence.DECLARED.value))),
        extraction_method=str(row.get("extraction_method", "")),
        source_locator=str(row.get("source_locator", "")),
        source_revision=str(row.get("source_revision", "")),
        evidence=_read_json(str(row.get("evidence_json", ""))),
        status=EdgeStatus(str(row.get("status", EdgeStatus.ACTIVE.value))),
        first_seen_at=str(row.get("first_seen_at", "")),
        last_seen_at=str(row.get("last_seen_at", "")),
        last_scan_id=str(row.get("last_scan_id", "")),
    )


def _weaker(left: Confidence, right: Confidence) -> Confidence:
    """Return the less believed of two confidences."""
    return left if _CONFIDENCE_ORDER[left] <= _CONFIDENCE_ORDER[right] else right


def _ordered(findings: list[ImpactFinding]) -> list[ImpactFinding]:
    """Return findings in a fixed order so two runs give the same answer."""
    return sorted(
        findings,
        key=lambda f: (f.distance, f.node.kind.value, f.node.identity_key),
    )


class KnowledgeStore:
    """Reads and writes the knowledge base on top of a state store.

    The store owns identity, the first-seen and last-seen stamps, and the
    walk that turns recorded dependencies into an answer about impact. It
    holds no connection of its own; every backend the platform supports works
    the same way through the state store passed in.
    """

    def __init__(self, db: StateDBBase) -> None:
        """Hold the state store that keeps the rows.

        Args:
            db: A state store from ``create_state_db``; either backend works.
        """
        self._db = db

    # ── Scans ────────────────────────────────────────────────────────────────

    def start_scan(
        self, *, profile_id: str, source_scope: str, extractor_version: str,
    ) -> KnowledgeScan:
        """Record that a pass over a source has begun.

        Args:
            profile_id: Migration profile the scan belongs to.
            source_scope: What is being read, such as an organisation or one
                project.
            extractor_version: Version of the extraction code that is running.

        Returns:
            The scan record, whose identifier every node and edge found by
            this pass should carry.
        """
        scan = KnowledgeScan(
            scan_id=f"{_SCAN_PREFIX}{uuid.uuid4().hex[:24]}",
            profile_id=profile_id,
            source_scope=source_scope,
            extractor_version=extractor_version,
            started_at=utc_now_text(),
        )
        self._db.insert_knowledge_scan({
            "id": scan.scan_id,
            "profile_id": scan.profile_id,
            "source_scope": scan.source_scope,
            "extractor_version": scan.extractor_version,
            "started_at": scan.started_at,
            "completed_at": "",
            "status": scan.status,
            "coverage_json": "{}",
            "error_summary": "",
        })
        return scan

    def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        coverage: dict[str, Any],
        error_summary: str = "",
    ) -> None:
        """Close a scan and record what it managed to cover.

        Args:
            scan_id: The scan being closed.
            status: How it ended, such as ``"completed"`` or ``"failed"``.
            coverage: What was looked at and what could not be, quoted later
                so a reader can tell an empty answer from an unexamined one.
            error_summary: Short, already-masked description of what went
                wrong, empty on success.
        """
        self._db.update_knowledge_scan(scan_id, {
            "completed_at": utc_now_text(),
            "status": status,
            "coverage_json": json.dumps(coverage, sort_keys=True),
            "error_summary": error_summary,
        })

    def latest_coverage(self, *, profile_id: str) -> dict[str, Any]:
        """Return what the profile's most recent finished scan covered.

        Args:
            profile_id: Migration profile to report on.

        Returns:
            The recorded coverage with the scan's identifier and timestamps
            added, or an empty mapping when no scan has finished.
        """
        row = self._db.latest_knowledge_scan(profile_id)
        if not row:
            return {}
        coverage = _read_json(str(row.get("coverage_json", "")))
        coverage["scan_id"] = str(row.get("id", ""))
        coverage["started_at"] = str(row.get("started_at", ""))
        coverage["completed_at"] = str(row.get("completed_at", ""))
        return coverage

    # ── Writing facts ────────────────────────────────────────────────────────

    def record_node(self, node: KnowledgeNode) -> str:
        """Store a thing, updating the row already held for it.

        Identity is the profile, system, organisation, project, kind and
        identity key together, so the same thing keeps one row and one
        identifier across scans. The date it was first seen is kept from the
        existing row; the last-seen stamp and the scan that confirmed it are
        moved forward.

        Args:
            node: The thing to record; its ``node_id`` is ignored and derived.

        Returns:
            The identifier of the stored row.
        """
        node_id = _identity(_NODE_PREFIX, [
            node.profile_id, node.system, node.organization, node.project,
            node.kind.value, node.identity_key,
        ])
        seen_at = node.last_seen_at or utc_now_text()
        held = self._db.get_knowledge_nodes(node.profile_id, node_ids=[node_id])
        first_seen = str(held[0]["first_seen_at"]) if held else (node.first_seen_at or seen_at)
        self._db.upsert_knowledge_node({
            "id": node_id,
            "profile_id": node.profile_id,
            "system": node.system,
            "organization": node.organization,
            "project": node.project,
            "kind": node.kind.value,
            "identity_key": node.identity_key,
            "external_id": node.external_id,
            "name": node.name,
            "url": node.url,
            "source_locator": node.source_locator,
            "first_seen_at": first_seen,
            "last_seen_at": seen_at,
            "last_scan_id": node.last_scan_id,
        })
        return node_id

    def record_edge(self, edge: KnowledgeEdge) -> str:
        """Store a dependency, updating the row already held for it.

        Identity is the profile, the two ends, the kind of dependency and the
        code path that found it, so re-reading the same source refreshes one
        row. The dependency is marked active because this scan has just seen
        it, and the date it was first seen is kept.

        Args:
            edge: The dependency to record; its ``edge_id`` is ignored and
                derived.

        Returns:
            The identifier of the stored row.
        """
        edge_id = _identity(_EDGE_PREFIX, [
            edge.profile_id, edge.source_node_id, edge.target_node_id,
            edge.kind.value, edge.extraction_method,
        ])
        seen_at = edge.last_seen_at or utc_now_text()
        held = [
            row for row in self._db.get_knowledge_edges(
                edge.profile_id, source_ids=[edge.source_node_id],
            )
            if str(row.get("id", "")) == edge_id
        ]
        first_seen = str(held[0]["first_seen_at"]) if held else (edge.first_seen_at or seen_at)
        self._db.upsert_knowledge_edge({
            "id": edge_id,
            "profile_id": edge.profile_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "kind": edge.kind.value,
            "confidence": edge.confidence.value,
            "extraction_method": edge.extraction_method,
            "source_locator": edge.source_locator,
            "source_revision": edge.source_revision,
            "evidence_json": json.dumps(edge.evidence, sort_keys=True),
            "status": EdgeStatus.ACTIVE.value,
            "first_seen_at": first_seen,
            "last_seen_at": seen_at,
            "last_scan_id": edge.last_scan_id,
        })
        return edge_id

    def mark_unseen_edges_disappeared(self, *, profile_id: str, scan_id: str) -> int:
        """Mark every dependency this scan did not see again as disappeared.

        A dependency that was removed at the source has to become visible as
        gone, otherwise it keeps being answered as though it were current.

        Args:
            profile_id: Migration profile the scan covered.
            scan_id: The scan that has just finished recording facts.

        Returns:
            How many dependencies were marked.
        """
        return self._db.mark_knowledge_edges_disappeared(profile_id, scan_id)

    # ── Reading facts ────────────────────────────────────────────────────────

    def find_node(
        self, *, profile_id: str, kind: NodeKind, identity_key: str,
    ) -> KnowledgeNode | None:
        """Return the one thing of this kind with this identity key, or ``None``.

        Args:
            profile_id: Migration profile to look in.
            kind: What the thing is.
            identity_key: The most stable name the source offers for it.

        Returns:
            The thing, or ``None`` when the knowledge base holds no record.
        """
        rows = self._db.get_knowledge_nodes(
            profile_id, kind=kind.value, identity_key=identity_key,
        )
        return _node_from_row(rows[0]) if rows else None

    def search_nodes(
        self,
        *,
        profile_id: str,
        text: str,
        kinds: list[NodeKind] | None = None,
        limit: int = 20,
    ) -> list[KnowledgeNode]:
        """Return things whose identity key or name contains the text.

        Args:
            profile_id: Migration profile to look in.
            text: What to look for; upper and lower case are treated alike.
            kinds: Restrict to these kinds of thing, or every kind when not
                given.
            limit: Most rows to return.

        Returns:
            The matching things, ordered by identity key.
        """
        rows = self._db.search_knowledge_nodes(
            profile_id,
            text,
            kinds=[k.value for k in kinds] if kinds else None,
            limit=limit,
        )
        return [_node_from_row(row) for row in rows]

    def neighbours(
        self,
        *,
        profile_id: str,
        node_id: str,
        direction: str,
        kinds: list[EdgeKind] | None = None,
        limit: int = 100,
    ) -> list[tuple[KnowledgeEdge, KnowledgeNode]]:
        """Return one step out from a thing, with the dependency that led there.

        Args:
            profile_id: Migration profile to look in.
            node_id: The thing to step out from.
            direction: ``DIRECTION_DEPENDENCIES`` to follow what this thing
                needs, ``DIRECTION_CONSUMERS`` to follow what needs it.
            kinds: Restrict to these kinds of dependency, or every kind when
                not given.
            limit: Most steps to return.

        Returns:
            Pairs of the dependency walked and the thing at its other end,
            skipping dependencies that have disappeared and ends the knowledge
            base holds no record of.
        """
        forwards = direction == DIRECTION_DEPENDENCIES
        rows = self._db.get_knowledge_edges(
            profile_id,
            source_ids=[node_id] if forwards else None,
            target_ids=None if forwards else [node_id],
            status=EdgeStatus.ACTIVE.value,
        )
        wanted = {k.value for k in kinds} if kinds else None
        edges = [
            _edge_from_row(row) for row in rows
            if wanted is None or str(row.get("kind", "")) in wanted
        ]
        far_end = {
            edge.edge_id: edge.target_node_id if forwards else edge.source_node_id
            for edge in edges
        }
        nodes = {
            str(row["id"]): _node_from_row(row)
            for row in self._db.get_knowledge_nodes(
                profile_id, node_ids=sorted(set(far_end.values())),
            )
        }
        pairs = [
            (edge, nodes[far_end[edge.edge_id]])
            for edge in edges if far_end[edge.edge_id] in nodes
        ]
        pairs.sort(key=lambda pair: (pair[0].kind.value, pair[1].identity_key))
        return pairs[:limit]

    # ── Impact ───────────────────────────────────────────────────────────────

    def impact(
        self,
        *,
        profile_id: str,
        node_id: str,
        max_depth: int = 3,
        max_nodes: int = 200,
    ) -> ImpactReport:
        """Answer what a change to one thing would reach.

        The walk goes both ways from the subject: forwards for the things it
        needs, backwards for the things that need it, stopping at the depth
        and the number of things asked for and never visiting the same thing
        twice, so a loop in the recorded dependencies still ends.

        Args:
            profile_id: Migration profile to look in.
            node_id: The thing being asked about.
            max_depth: How many dependency steps to follow.
            max_nodes: Most things to visit in each direction.

        Returns:
            The report, always carrying the coverage of the scans behind it
            and the sentence saying that an absence of recorded dependencies
            is not proof that none exist.
        """
        coverage = self.latest_coverage(profile_id=profile_id)
        caveats = [NO_RECORDED_DEPENDENCY_CAVEAT]
        held = self._db.get_knowledge_nodes(profile_id, node_ids=[node_id])
        if not held:
            return ImpactReport(subject=None, coverage=coverage, caveats=caveats)

        prerequisites, cut_forwards, guessed_forwards = self._walk(
            profile_id, node_id, DIRECTION_DEPENDENCIES, max_depth, max_nodes,
        )
        consumers, cut_backwards, guessed_backwards = self._walk(
            profile_id, node_id, DIRECTION_CONSUMERS, max_depth, max_nodes,
        )
        caveats.extend(sorted(set(guessed_forwards) | set(guessed_backwards)))
        return ImpactReport(
            subject=_node_from_row(held[0]),
            prerequisites=_ordered(prerequisites),
            consumers=_ordered(consumers),
            shared_dependencies=_ordered(
                self._shared(profile_id, node_id, prerequisites),
            ),
            coverage=coverage,
            caveats=caveats,
            truncated=cut_forwards or cut_backwards,
        )

    def _walk(
        self,
        profile_id: str,
        start_id: str,
        direction: str,
        max_depth: int,
        max_nodes: int,
    ) -> tuple[list[ImpactFinding], bool, list[str]]:
        """Walk one direction, nearest things first.

        Args:
            profile_id: Migration profile to look in.
            start_id: The thing to walk out from.
            direction: Which way to follow the dependencies.
            max_depth: How many steps to take.
            max_nodes: Most things to visit before stopping.

        Returns:
            The things found, whether the limit stopped the walk, and a plain
            sentence for every guessed dependency the findings rest on.
        """
        relation = _RELATION_BY_DIRECTION[direction]
        findings: list[ImpactFinding] = []
        guessed: list[str] = []
        visited = {start_id}
        truncated = False
        queue: deque[ImpactFinding] = deque()
        queue.append(ImpactFinding(
            node=KnowledgeNode(
                node_id=start_id, profile_id=profile_id, system="", organization="",
                project="", kind=NodeKind.UNKNOWN, identity_key="",
            ),
            relation=relation,
            distance=0,
            path=[start_id],
        ))
        while queue and not truncated:
            current = queue.popleft()
            if current.distance >= max_depth:
                continue
            for edge, node in self.neighbours(
                profile_id=profile_id, node_id=current.node.node_id,
                direction=direction, limit=max_nodes + 1,
            ):
                if node.node_id in visited:
                    continue
                if len(findings) >= max_nodes:
                    truncated = True
                    break
                visited.add(node.node_id)
                if edge.confidence is Confidence.INFERRED:
                    guessed.append(
                        f"The {edge.kind.value} link to {node.identity_key} was "
                        f"inferred by {edge.extraction_method or 'a heuristic'} "
                        "rather than declared by the source, so it may be wrong.",
                    )
                found = ImpactFinding(
                    node=node,
                    relation=relation,
                    distance=current.distance + 1,
                    path=[*current.path, node.node_id],
                    weakest_confidence=_weaker(current.weakest_confidence, edge.confidence),
                    evidence=[*current.evidence, edge.evidence],
                )
                findings.append(found)
                queue.append(found)
        return findings, truncated, guessed

    def _shared(
        self, profile_id: str, subject_id: str, prerequisites: list[ImpactFinding],
    ) -> list[ImpactFinding]:
        """Return the subject's direct dependencies that another thing also needs.

        Args:
            profile_id: Migration profile to look in.
            subject_id: The thing being asked about.
            prerequisites: What the forwards walk found.

        Returns:
            One finding per shared dependency, carrying the same path and
            evidence as the prerequisite it came from.
        """
        shared: list[ImpactFinding] = []
        for found in prerequisites:
            if found.distance != 1:
                continue
            others = self.neighbours(
                profile_id=profile_id, node_id=found.node.node_id,
                direction=DIRECTION_CONSUMERS,
            )
            if any(other.node_id != subject_id for _edge, other in others):
                shared.append(ImpactFinding(
                    node=found.node,
                    relation=RELATION_SHARED_DEPENDENCY,
                    distance=found.distance,
                    path=found.path,
                    weakest_confidence=found.weakest_confidence,
                    evidence=found.evidence,
                ))
        return shared
