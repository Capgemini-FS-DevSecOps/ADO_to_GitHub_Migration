"""What ``services/accelerator_api/routes/knowledge_routes.py`` actually answers.

The knowledge store is a double holding two repositories, one pipeline and the
edge between them, so the tests exercise the conversion from stored records to
response models without needing a scan or a database.

The properties worth freezing are the ones that stop a thin answer being read as
a confident one: every response carries the scan coverage and the caveats, an
identifier nobody ever scanned answers politely rather than failing, and the
impact depth is clamped to a ceiling so one request cannot walk an entire
organisation.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import permissions_for
from ado2gh.knowledge.models import (
    NO_RECORDED_DEPENDENCY_CAVEAT,
    RELATION_CONSUMER,
    Confidence,
    EdgeKind,
    EdgeStatus,
    ImpactFinding,
    ImpactReport,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeScan,
    NodeKind,
)
from services.accelerator_api.routes import knowledge_routes

FAKE_PROFILE_ID = "profile-fake-0001"
FAKE_COVERAGE = {"projects_scanned": 2, "pipeline_definitions_read": 7, "unparsed_definitions": 1}
FAKE_SCAN_ID = "scan-fake-0001"
FAKE_ORG_URL = "https://dev.azure.com/fake-org"


def _node(node_id: str, kind: NodeKind, name: str) -> KnowledgeNode:
    """Build one obviously fake stored node."""
    return KnowledgeNode(
        node_id=node_id,
        profile_id=FAKE_PROFILE_ID,
        system="azure_devops",
        organization="fake-org",
        project="FakeProject",
        kind=kind,
        identity_key=name.lower(),
        name=name,
        url=f"https://dev.azure.com/fake-org/FakeProject/{name}",
    )


FAKE_REPO = _node("node-repo-1", NodeKind.REPOSITORY, "payments-api")
FAKE_PIPELINE = _node("node-pipeline-1", NodeKind.PIPELINE, "payments-api-ci")
FAKE_EDGE = KnowledgeEdge(
    edge_id="edge-1",
    profile_id=FAKE_PROFILE_ID,
    source_node_id=FAKE_PIPELINE.node_id,
    target_node_id=FAKE_REPO.node_id,
    kind=EdgeKind.BUILDS,
    confidence=Confidence.INFERRED,
    extraction_method="fake_yaml_reader",
    source_locator="FakeProject/azure-pipelines.yml",
    evidence={"step": "checkout payments-api"},
    status=EdgeStatus.ACTIVE,
    last_seen_at="2026-09-23T00:00:00+00:00",
)


class _Store:
    """A knowledge store double holding one pipeline that builds one repository."""

    def __init__(self) -> None:
        self.depths: list[int] = []
        self.edge_kinds: list[Optional[list[EdgeKind]]] = []
        self.scans: list[tuple[str, str]] = []

    def build(self, store: Any, db: Any, *, profile_id: str, source_scope: str) -> KnowledgeScan:
        """Stand in for the builder, recording what it was asked to read."""
        self.scans.append((profile_id, source_scope))
        return KnowledgeScan(
            scan_id=FAKE_SCAN_ID,
            profile_id=profile_id,
            source_scope=source_scope,
            extractor_version="fake-extractor-1",
            started_at="2026-09-23T00:00:00+00:00",
            completed_at="2026-09-23T00:00:01+00:00",
            status="completed",
            coverage=dict(FAKE_COVERAGE),
        )

    def search_nodes(
        self, *, profile_id: str, text: str, kinds: Optional[list[NodeKind]] = None, limit: int = 20,
    ) -> list[KnowledgeNode]:
        found = [n for n in (FAKE_REPO, FAKE_PIPELINE) if text.lower() in n.name.lower()]
        if kinds:
            found = [n for n in found if n.kind in kinds]
        return found[:limit]

    def neighbours(
        self, *, profile_id: str, node_id: str, direction: str,
        kinds: Optional[list[EdgeKind]] = None, limit: int = 100,
    ) -> list[tuple[KnowledgeEdge, KnowledgeNode]]:
        self.edge_kinds.append(kinds)
        if direction == "dependencies" and node_id == FAKE_PIPELINE.node_id:
            return [(FAKE_EDGE, FAKE_REPO)]
        if direction == "consumers" and node_id == FAKE_REPO.node_id:
            return [(FAKE_EDGE, FAKE_PIPELINE)]
        return []

    def latest_coverage(self, *, profile_id: str) -> dict[str, Any]:
        return dict(FAKE_COVERAGE)

    def impact(
        self, *, profile_id: str, node_id: str, max_depth: int = 3, max_nodes: int = 200,
    ) -> ImpactReport:
        self.depths.append(max_depth)
        if node_id != FAKE_REPO.node_id:
            return ImpactReport(
                subject=None,
                coverage=dict(FAKE_COVERAGE),
                caveats=[NO_RECORDED_DEPENDENCY_CAVEAT],
            )
        return ImpactReport(
            subject=FAKE_REPO,
            consumers=[ImpactFinding(
                node=FAKE_PIPELINE,
                relation=RELATION_CONSUMER,
                distance=1,
                path=[FAKE_REPO.node_id, FAKE_PIPELINE.node_id],
                weakest_confidence=Confidence.INFERRED,
                evidence=[dict(FAKE_EDGE.evidence)],
            )],
            coverage=dict(FAKE_COVERAGE),
            caveats=[NO_RECORDED_DEPENDENCY_CAVEAT, "One pipeline definition could not be parsed."],
            truncated=True,
        )


@pytest.fixture
def store() -> _Store:
    """The store double a test drives the routes with."""
    return _Store()


def _client(monkeypatch, store: _Store, user: PlatformUser) -> TestClient:
    """Client for the knowledge router alone, signed in as one user, with a fake store.

    Every door to a real database or a real settings file is closed: the store,
    the builder, the state database and the settings load are all doubles.
    """
    monkeypatch.setattr(knowledge_routes, "_knowledge_store", lambda: store)
    monkeypatch.setattr(knowledge_routes, "build_knowledge_base", store.build)
    monkeypatch.setattr(knowledge_routes, "create_state_db", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(
        knowledge_routes._settings, "load",
        lambda: SimpleNamespace(advanced=SimpleNamespace(db_path="fake-not-a-real-path.db")),
    )
    monkeypatch.setattr(
        knowledge_routes._settings, "get_active_profile",
        lambda: SimpleNamespace(id=FAKE_PROFILE_ID, ado_org_url=FAKE_ORG_URL + "/"),
    )

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.platform_user = user
        request.state.permissions = permissions_for(user.role)
        return await call_next(request)

    app.include_router(knowledge_routes.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch, store) -> TestClient:
    """Client signed in as a user who may operate."""
    return _client(
        monkeypatch, store,
        PlatformUser("u1", "operator1", PlatformRole.ADMIN, "Operator One"),
    )


def _assert_carries_its_limits(body: dict) -> None:
    """Every answer must say what was scanned and what it cannot know."""
    assert body["coverage"] == FAKE_COVERAGE
    assert body["caveats"]


def test_search_returns_matches_with_kind_name_and_identifier(client):
    """A search answers with each match's kind, name and node identifier."""
    body = client.get("/v1/knowledge/search", params={"text": "payments"}).json()
    assert body["count"] == 2
    first = body["results"][0]
    assert first["kind"] == "repository"
    assert first["name"] == "payments-api"
    assert first["node_id"] == FAKE_REPO.node_id
    _assert_carries_its_limits(body)


def test_search_can_be_restricted_to_one_kind(client):
    """A kind filter narrows the answer and is echoed back."""
    body = client.get("/v1/knowledge/search", params={"text": "payments", "kinds": "pipeline"}).json()
    assert [r["kind"] for r in body["results"]] == ["pipeline"]
    assert body["kinds"] == ["pipeline"]


def test_search_refuses_a_kind_this_platform_does_not_record(client):
    """A misspelt kind is named in the refusal rather than silently ignored."""
    response = client.get("/v1/knowledge/search", params={"text": "x", "kinds": "not-a-kind"})
    assert response.status_code == 400
    assert "not-a-kind" in response.json()["detail"]


def test_dependencies_report_the_kind_confidence_and_evidence(client):
    """One hop out, with enough to judge whether the hop is believable."""
    body = client.get(f"/v1/knowledge/nodes/{FAKE_PIPELINE.node_id}/dependencies").json()
    assert body["direction"] == "dependencies"
    link = body["neighbours"][0]
    assert link["node"]["node_id"] == FAKE_REPO.node_id
    assert link["edge_kind"] == "builds"
    assert link["confidence"] == "inferred"
    assert link["evidence"] == {"step": "checkout payments-api"}
    assert link["source_locator"] == "FakeProject/azure-pipelines.yml"
    _assert_carries_its_limits(body)


def test_dependency_kinds_filter_the_neighbours(client, store):
    """A dependency-kind filter reaches the store as the kind enumeration, not free text."""
    response = client.get(
        f"/v1/knowledge/nodes/{FAKE_PIPELINE.node_id}/dependencies", params={"kinds": "builds"},
    )
    assert response.status_code == 200
    assert store.edge_kinds == [[EdgeKind.BUILDS]]


def test_a_dependency_kind_that_does_not_exist_is_refused(client):
    """``repository`` is a node kind, not a dependency kind, and the refusal says so."""
    response = client.get(
        f"/v1/knowledge/nodes/{FAKE_PIPELINE.node_id}/dependencies", params={"kinds": "repository"},
    )
    assert response.status_code == 400
    assert "repository" in response.json()["detail"]


def test_consumers_read_the_same_edge_backwards(client):
    """What depends on the repository is the pipeline that builds it."""
    body = client.get(f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/consumers").json()
    assert body["direction"] == "consumers"
    assert body["neighbours"][0]["node"]["node_id"] == FAKE_PIPELINE.node_id
    _assert_carries_its_limits(body)


def test_impact_returns_the_whole_report_including_its_truncation(client):
    """The coverage, the caveats and the truncation flag all survive the route."""
    body = client.get(f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/impact").json()
    assert body["subject"]["node_id"] == FAKE_REPO.node_id
    consumer = body["consumers"][0]
    assert consumer["relation"] == "consumer"
    assert consumer["distance"] == 1
    assert consumer["weakest_confidence"] == "inferred"
    assert consumer["path"] == [FAKE_REPO.node_id, FAKE_PIPELINE.node_id]
    assert body["truncated"] is True
    assert len(body["caveats"]) == 2
    _assert_carries_its_limits(body)


def test_impact_uses_a_small_default_depth(client, store):
    """A caller who asks for no depth gets the small default, not the store's."""
    client.get(f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/impact")
    assert store.depths == [knowledge_routes.DEFAULT_IMPACT_DEPTH]


@pytest.mark.parametrize("asked,walked", [(1, 1), (3, 3), (99, knowledge_routes.MAX_IMPACT_DEPTH), (0, 1), (-5, 1)])
def test_impact_depth_is_clamped_to_the_ceiling(client, store, asked, walked):
    """No request walks deeper than the ceiling, and the answer says how deep it went."""
    body = client.get(
        f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/impact", params={"max_depth": asked},
    ).json()
    assert store.depths == [walked]
    assert body["max_depth"] == walked


def test_an_unknown_identifier_answers_politely(client):
    """Never having scanned a thing is an ordinary state, not an error."""
    impact = client.get("/v1/knowledge/nodes/never-scanned/impact")
    assert impact.status_code == 200
    body = impact.json()
    assert body["subject"] is None
    assert body["consumers"] == []
    _assert_carries_its_limits(body)

    for direction in ("dependencies", "consumers"):
        response = client.get(f"/v1/knowledge/nodes/never-scanned/{direction}")
        assert response.status_code == 200
        assert response.json()["neighbours"] == []
        _assert_carries_its_limits(response.json())


def test_every_route_needs_the_operate_capability(monkeypatch, store):
    """An approver, who may sign off a run but not drive one, is refused every knowledge read."""
    monkeypatch.setattr(knowledge_routes, "_knowledge_store", lambda: store)
    monkeypatch.setattr(
        knowledge_routes._settings, "get_active_profile",
        lambda: SimpleNamespace(id=FAKE_PROFILE_ID),
    )
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    app = FastAPI()
    approver = PlatformUser("u2", "approver1", PlatformRole.APPROVER, "Approver One")

    @app.middleware("http")
    async def inject_approver(request, call_next):
        request.state.platform_user = approver
        request.state.permissions = permissions_for(approver.role)
        return await call_next(request)

    app.include_router(knowledge_routes.router)
    approver_client = TestClient(app, raise_server_exceptions=False)
    for path in (
        "/v1/knowledge/search?text=x",
        f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/dependencies",
        f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/consumers",
        f"/v1/knowledge/nodes/{FAKE_REPO.node_id}/impact",
    ):
        assert approver_client.get(path).status_code == 403, path


def test_no_active_profile_is_refused_rather_than_mixed(client, monkeypatch):
    """Two profiles' facts must never land in one answer."""
    monkeypatch.setattr(knowledge_routes._settings, "get_active_profile", lambda: None)
    response = client.get("/v1/knowledge/search", params={"text": "x"})
    assert response.status_code == 400
    assert "profile" in response.json()["detail"].lower()


def test_scan_answers_with_the_scan_identifier_and_the_coverage_it_recorded(client, store):
    """A rebuild says which scan ran and what that scan managed to cover."""
    body = client.post("/v1/knowledge/scan").json()
    assert body["scan_id"] == FAKE_SCAN_ID
    assert body["status"] == "completed"
    assert body["coverage"] == FAKE_COVERAGE
    assert body["caveats"]
    # The organisation must read the same as it does when the inventory scan
    # builds this, trailing slash and all, or one thing is recorded twice.
    assert store.scans == [(FAKE_PROFILE_ID, FAKE_ORG_URL)]


def test_scanning_twice_is_harmless(client, store):
    """An operator unsure whether it ran may simply run it again."""
    first = client.post("/v1/knowledge/scan")
    second = client.post("/v1/knowledge/scan")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(store.scans) == 2


def test_scan_refuses_without_an_active_profile(client, monkeypatch, store):
    """Facts from two profiles must never mix, so no profile means no scan."""
    monkeypatch.setattr(knowledge_routes._settings, "get_active_profile", lambda: None)
    response = client.post("/v1/knowledge/scan")
    assert response.status_code == 400
    assert store.scans == []


def test_scan_refuses_a_caller_without_the_capability_the_reads_require(monkeypatch, store):
    """Filling the knowledge base is gated exactly like reading it."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    approver = _client(
        monkeypatch, store,
        PlatformUser("u2", "approver1", PlatformRole.APPROVER, "Approver One"),
    )
    assert approver.post("/v1/knowledge/scan").status_code == 403
    assert approver.get("/v1/knowledge/search", params={"text": "x"}).status_code == 403
    assert store.scans == []
