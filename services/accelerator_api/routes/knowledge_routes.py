"""Read-only endpoints for the migration knowledge base under ``/v1/knowledge``.

Four questions, all answered from scans already on disk and none of them
touching Azure DevOps or GitHub: find a thing by name, list what one hop of a
thing's dependencies or consumers looks like, and ask what a change to it would
affect.

Every response repeats the scan coverage and the caveats exactly as the store
reports them. That is the whole point of the endpoints: an empty answer from a
knowledge base that only ever read one project is not the same statement as an
empty answer from one that read the entire organisation, and a reader — human
or language model — can only tell the two apart if the coverage travels with
the answer.
"""
from __future__ import annotations

from typing import Any, Optional, TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ado2gh.api.platform_rbac import require_operate
from ado2gh.knowledge.models import (
    NO_RECORDED_DEPENDENCY_CAVEAT,
    EdgeKind,
    ImpactFinding,
    ImpactReport,
    KnowledgeNode,
    NodeKind,
)
from ado2gh.knowledge.store import (
    DIRECTION_CONSUMERS,
    DIRECTION_DEPENDENCIES,
    KnowledgeStore,
)
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes._shared import _settings

router = APIRouter(tags=["knowledge"])

DEFAULT_IMPACT_DEPTH = 2
"""Depth a caller gets when it asks for none — two hops answers most questions."""

MAX_IMPACT_DEPTH = 4
"""Hard ceiling on traversal depth, so one request cannot walk a whole organisation."""

MAX_SEARCH_LIMIT = 100
"""Most matches one search returns, whatever the caller asks for."""

MAX_NEIGHBOUR_LIMIT = 200
"""Most direct neighbours one request returns, whatever the caller asks for."""


class NodeSummary(BaseModel):
    """One thing in the knowledge base, reduced to what an answer needs."""

    node_id: str = Field(description="Identifier to pass to the dependency, consumer and impact endpoints")
    kind: str = Field(description="What the thing is, such as repository or pipeline")
    name: str = Field(description="Human-readable name")
    identity_key: str = Field(description="Most stable name the source offered, used for matching")
    system: str = Field(default="", description="Platform the thing lives in")
    organization: str = Field(default="", description="Organisation or account it belongs to")
    project: str = Field(default="", description="Project within the organisation, empty above project level")
    url: str = Field(default="", description="Address of the thing in its own system, empty when unknown")


class DependencyLink(BaseModel):
    """One hop away, with the evidence for believing the hop is real."""

    node: NodeSummary = Field(description="The thing at the other end of the dependency")
    edge_kind: str = Field(description="How the dependency reads, such as builds or needs_credential")
    confidence: str = Field(
        description=(
            "How firmly the dependency is believed: declared (the source named it), "
            "resolved (a name matched a known thing) or inferred (a heuristic guessed it)"
        ),
    )
    evidence: dict[str, Any] = Field(default_factory=dict, description="Masked details justifying the dependency")
    extraction_method: str = Field(default="", description="Code path that found it")
    source_locator: str = Field(default="", description="Where the fact was read from")
    status: str = Field(default="active", description="Whether the last scan still saw it")
    last_seen_at: str = Field(default="", description="When a scan last confirmed it")


class ImpactEntry(BaseModel):
    """One thing a change to the subject would affect."""

    node: NodeSummary = Field(description="The affected thing")
    relation: str = Field(description="prerequisite, consumer or shared_dependency")
    distance: int = Field(description="Dependency hops from the subject; one means directly connected")
    path: list[str] = Field(default_factory=list, description="Node identifiers walked to reach it, subject first")
    weakest_confidence: str = Field(description="The least certain edge on that path")
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="Evidence of the edges walked, in order")


class KnowledgeSearchResponse(BaseModel):
    """Answer to ``GET /v1/knowledge/search``."""

    text: str = Field(description="The text that was searched for")
    kinds: list[str] = Field(default_factory=list, description="Kinds the search was restricted to, empty for all")
    count: int = Field(description="How many matches are in this response")
    results: list[NodeSummary] = Field(default_factory=list, description="Matching things, best match first")
    coverage: dict[str, Any] = Field(default_factory=dict, description="What the underlying scans covered")
    caveats: list[str] = Field(default_factory=list, description="What this answer cannot know")


class NeighboursResponse(BaseModel):
    """Answer to the dependency and consumer endpoints."""

    node_id: str = Field(description="The thing that was asked about")
    direction: str = Field(description="dependencies (what it needs) or consumers (what needs it)")
    count: int = Field(description="How many direct neighbours are in this response")
    neighbours: list[DependencyLink] = Field(default_factory=list, description="One hop away")
    coverage: dict[str, Any] = Field(default_factory=dict, description="What the underlying scans covered")
    caveats: list[str] = Field(default_factory=list, description="What this answer cannot know")


class ImpactResponse(BaseModel):
    """Answer to ``GET /v1/knowledge/nodes/{node_id}/impact``."""

    subject: Optional[NodeSummary] = Field(
        default=None, description="The thing asked about, null when the knowledge base holds no record of it",
    )
    max_depth: int = Field(description="Depth actually walked, after the ceiling was applied")
    prerequisites: list[ImpactEntry] = Field(default_factory=list, description="Things the subject depends on")
    consumers: list[ImpactEntry] = Field(default_factory=list, description="Things that depend on the subject")
    shared_dependencies: list[ImpactEntry] = Field(
        default_factory=list, description="Things the subject and other consumers both depend on",
    )
    coverage: dict[str, Any] = Field(default_factory=dict, description="What the underlying scans covered")
    caveats: list[str] = Field(default_factory=list, description="What this answer cannot know")
    truncated: bool = Field(default=False, description="Whether the walk stopped at its limit")


def _knowledge_store() -> KnowledgeStore:
    """Open the knowledge base held in the configured state database.

    Returns:
        A store bound to the same database the rest of the accelerator reads.
    """
    return KnowledgeStore(create_state_db(_settings.load().advanced.db_path))


def _active_profile_id() -> str:
    """Resolve the profile whose facts these endpoints answer from.

    Returns:
        The active migration profile's identifier.

    Raises:
        HTTPException: 400 when no profile is active, because facts from two
            profiles must never be mixed into one answer.
    """
    profile = _settings.get_active_profile()
    if not profile:
        raise HTTPException(status_code=400, detail="No active migration profile")
    return str(profile.id)


_KindT = TypeVar("_KindT", NodeKind, EdgeKind)


def _parse_kinds(kinds: Optional[list[str]], enum: type[_KindT]) -> list[_KindT]:
    """Turn requested kind names into members of the kind enumeration asked for.

    Args:
        kinds: Kind names from the query string, or ``None`` for no restriction.
            One value may carry several names separated by commas.
        enum: ``NodeKind`` when the caller is filtering things, ``EdgeKind``
            when it is filtering dependencies.

    Returns:
        The parsed kinds, empty when none were asked for.

    Raises:
        HTTPException: 400 naming the value that is not a kind this platform
            records, rather than quietly returning everything.
    """
    parsed: list[_KindT] = []
    for raw in kinds or []:
        for part in str(raw).split(","):
            name = part.strip()
            if not name:
                continue
            try:
                parsed.append(enum(name))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown {enum.__name__} value: {name}",
                ) from exc
    return parsed


def _summary(node: KnowledgeNode) -> NodeSummary:
    """Reduce a stored node to the fields an answer carries.

    Args:
        node: The stored node.

    Returns:
        The node as an answer model.
    """
    return NodeSummary(
        node_id=node.node_id,
        kind=str(node.kind.value if isinstance(node.kind, NodeKind) else node.kind),
        name=node.name or node.identity_key,
        identity_key=node.identity_key,
        system=node.system,
        organization=node.organization,
        project=node.project,
        url=node.url,
    )


def _enum_text(value: object) -> str:
    """Render an enum member or a plain string as the text an answer shows.

    Args:
        value: A string enum member, a bare string, or anything else.

    Returns:
        The member's value where there is one, otherwise the value as text.
    """
    return str(getattr(value, "value", value) or "")


def _link(row: tuple[Any, Any]) -> DependencyLink:
    """Turn one ``(edge, node)`` pair from the store into an answer model.

    Args:
        row: The dependency walked and the thing at its other end, in the order
            :meth:`KnowledgeStore.neighbours` returns them.

    Returns:
        The neighbour with the dependency kind, the confidence and the evidence.
    """
    edge, node = row
    return DependencyLink(
        node=_summary(node),
        edge_kind=_enum_text(edge.kind),
        confidence=_enum_text(edge.confidence),
        evidence=dict(edge.evidence or {}),
        extraction_method=edge.extraction_method,
        source_locator=edge.source_locator,
        status=_enum_text(edge.status),
        last_seen_at=edge.last_seen_at,
    )


def _entry(finding: ImpactFinding) -> ImpactEntry:
    """Turn one impact finding into an answer model.

    Args:
        finding: The finding from the store's impact report.

    Returns:
        The finding as an answer model.
    """
    return ImpactEntry(
        node=_summary(finding.node),
        relation=finding.relation,
        distance=finding.distance,
        path=list(finding.path),
        weakest_confidence=_enum_text(finding.weakest_confidence),
        evidence=[dict(e) for e in finding.evidence],
    )


def _caveats(reported: Optional[list[str]] = None) -> list[str]:
    """Return the caveats an answer carries, never an empty list.

    An answer with no caveats reads as a confident one. The knowledge base is
    never that confident: it knows only what a scan happened to read.

    Args:
        reported: Caveats the store supplied, if any.

    Returns:
        The store's caveats, or the standing one when it supplied none.
    """
    return list(reported) if reported else [NO_RECORDED_DEPENDENCY_CAVEAT]


def _neighbours(
    request: Request,
    node_id: str,
    direction: str,
    kinds: Optional[list[str]],
    limit: int,
) -> NeighboursResponse:
    """Answer one direction of the one-hop neighbour question.

    Args:
        request: The incoming request, carrying the caller's identity.
        node_id: The thing being asked about.
        direction: ``dependencies`` or ``consumers``.
        kinds: Restrict to these dependency kinds, or ``None`` for all.
        limit: Maximum neighbours to return, capped at ``MAX_NEIGHBOUR_LIMIT``.

    Returns:
        The direct neighbours with their dependency kind, confidence and evidence,
        plus the scan coverage and the caveats.
    """
    require_operate(request)
    profile_id = _active_profile_id()
    store = _knowledge_store()
    parsed = _parse_kinds(kinds, EdgeKind)
    rows = store.neighbours(
        profile_id=profile_id,
        node_id=node_id,
        direction=direction,
        kinds=parsed or None,
        limit=max(1, min(limit, MAX_NEIGHBOUR_LIMIT)),
    )
    links = [_link(row) for row in rows or []]
    return NeighboursResponse(
        node_id=node_id,
        direction=direction,
        count=len(links),
        neighbours=links,
        coverage=store.latest_coverage(profile_id=profile_id) or {},
        caveats=_caveats(),
    )


@router.get("/v1/knowledge/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    request: Request,
    text: str = Query(default="", description="Text to match against a thing's name"),
    kinds: Optional[list[str]] = Query(default=None, description="Restrict to these node kinds"),
    limit: int = Query(default=20, description="Maximum matches to return"),
) -> KnowledgeSearchResponse:
    """Find things in the knowledge base whose name matches some text.

    Requires the ``can_operate`` capability, the same as the other inventory
    reads. Nothing here contacts Azure DevOps or GitHub — it reads what a scan
    already recorded.

    Args:
        request: The incoming request, carrying the caller's identity.
        text: Text to match against a thing's name or identity key.
        kinds: Restrict the search to these kinds. Repeat the parameter or give
            one comma-separated value. Omit for every kind.
        limit: Maximum matches to return, capped at ``MAX_SEARCH_LIMIT``.

    Returns:
        The matching things with their kind, name and identifier, plus the scan
        coverage and the caveats.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``,
            400 for an unknown kind or with no active profile.
    """
    require_operate(request)
    profile_id = _active_profile_id()
    store = _knowledge_store()
    parsed = _parse_kinds(kinds, NodeKind)
    nodes = store.search_nodes(
        profile_id=profile_id,
        text=text,
        kinds=parsed or None,
        limit=max(1, min(limit, MAX_SEARCH_LIMIT)),
    )
    results = [_summary(n) for n in nodes or []]
    return KnowledgeSearchResponse(
        text=text,
        kinds=[k.value for k in parsed],
        count=len(results),
        results=results,
        coverage=store.latest_coverage(profile_id=profile_id) or {},
        caveats=_caveats(),
    )


@router.get("/v1/knowledge/nodes/{node_id}/dependencies", response_model=NeighboursResponse)
def node_dependencies(
    request: Request,
    node_id: str,
    kinds: Optional[list[str]] = Query(default=None, description="Restrict to these dependency kinds"),
    limit: int = Query(default=100, description="Maximum dependencies to return"),
) -> NeighboursResponse:
    """List what this thing depends on, one hop out.

    An identifier the knowledge base has never seen answers with an empty list
    rather than an error: not knowing about a thing is an ordinary state for a
    knowledge base built from partial scans.

    Args:
        request: The incoming request, carrying the caller's identity.
        node_id: The thing being asked about.
        kinds: Restrict to these dependency kinds, such as ``builds``. Omit for
            every kind.
        limit: Maximum dependencies to return, capped at ``MAX_NEIGHBOUR_LIMIT``.

    Returns:
        Each dependency with its kind, how firmly it is believed and the
        evidence, plus the scan coverage and the caveats.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``,
            400 for an unknown kind or with no active profile.
    """
    return _neighbours(request, node_id, DIRECTION_DEPENDENCIES, kinds, limit)


@router.get("/v1/knowledge/nodes/{node_id}/consumers", response_model=NeighboursResponse)
def node_consumers(
    request: Request,
    node_id: str,
    kinds: Optional[list[str]] = Query(default=None, description="Restrict to these dependency kinds"),
    limit: int = Query(default=100, description="Maximum consumers to return"),
) -> NeighboursResponse:
    """List what depends on this thing, one hop out.

    An identifier the knowledge base has never seen answers with an empty list
    rather than an error, for the same reason the dependency endpoint does.

    Args:
        request: The incoming request, carrying the caller's identity.
        node_id: The thing being asked about.
        kinds: Restrict to these dependency kinds, such as ``builds``. Omit for
            every kind.
        limit: Maximum consumers to return, capped at ``MAX_NEIGHBOUR_LIMIT``.

    Returns:
        Each consumer with the dependency kind, how firmly it is believed and
        the evidence, plus the scan coverage and the caveats.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``,
            400 for an unknown kind or with no active profile.
    """
    return _neighbours(request, node_id, DIRECTION_CONSUMERS, kinds, limit)


@router.get("/v1/knowledge/nodes/{node_id}/impact", response_model=ImpactResponse)
def node_impact(
    request: Request,
    node_id: str,
    max_depth: int = Query(
        default=DEFAULT_IMPACT_DEPTH,
        description=f"Dependency hops to walk, 1 to {MAX_IMPACT_DEPTH}; anything higher is lowered to the ceiling",
    ),
) -> ImpactResponse:
    """Report what a change to this thing would affect.

    The depth is clamped to ``MAX_IMPACT_DEPTH`` rather than refused, and the
    depth actually walked comes back in the response, so one request can never
    be talked into traversing an entire organisation.

    Args:
        request: The incoming request, carrying the caller's identity.
        node_id: The thing being asked about.
        max_depth: Dependency hops to walk.

    Returns:
        The prerequisites, the consumers, the shared dependencies, the scan
        coverage, the caveats and whether the walk was truncated. An identifier
        the knowledge base has never seen answers with a null subject and empty
        lists rather than an error.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``,
            400 with no active profile.
    """
    require_operate(request)
    profile_id = _active_profile_id()
    depth = max(1, min(max_depth, MAX_IMPACT_DEPTH))
    report: ImpactReport = _knowledge_store().impact(
        profile_id=profile_id,
        node_id=node_id,
        max_depth=depth,
    )
    return ImpactResponse(
        subject=_summary(report.subject) if report.subject else None,
        max_depth=depth,
        prerequisites=[_entry(f) for f in report.prerequisites],
        consumers=[_entry(f) for f in report.consumers],
        shared_dependencies=[_entry(f) for f in report.shared_dependencies],
        coverage=report.coverage or {},
        caveats=_caveats(report.caveats),
        truncated=report.truncated,
    )
