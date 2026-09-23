"""The knowledge store keeps one row per thing and answers impact questions.

Everything here runs against an in-memory SQLite state store with obviously
fake names; no network call, no data directory and no real organisation.
"""
from __future__ import annotations

import pytest

from ado2gh.knowledge.models import (
    NO_RECORDED_DEPENDENCY_CAVEAT,
    RELATION_SHARED_DEPENDENCY,
    Confidence,
    EdgeKind,
    EdgeStatus,
    KnowledgeEdge,
    KnowledgeNode,
    NodeKind,
)
from ado2gh.knowledge.store import (
    DIRECTION_CONSUMERS,
    DIRECTION_DEPENDENCIES,
    KnowledgeStore,
)
from ado2gh.state.sqlite_db import SQLiteStateDB

PROFILE = "profile-fake-001"
EARLIER = "2026-01-01T00:00:00+00:00"
LATER = "2026-02-02T00:00:00+00:00"


@pytest.fixture()
def db():
    return SQLiteStateDB(":memory:")


@pytest.fixture()
def store(db):
    return KnowledgeStore(db)


def _node(identity_key, kind=NodeKind.PIPELINE, name="", seen_at=EARLIER):
    return KnowledgeNode(
        node_id="",
        profile_id=PROFILE,
        system="azure_devops",
        organization="fabrikam-fake",
        project="Payments-fake",
        kind=kind,
        identity_key=identity_key,
        name=name or identity_key,
        last_seen_at=seen_at,
    )


def _edge(source_id, target_id, kind=EdgeKind.BUILDS, confidence=Confidence.DECLARED, scan_id=""):
    return KnowledgeEdge(
        edge_id="",
        profile_id=PROFILE,
        source_node_id=source_id,
        target_node_id=target_id,
        kind=kind,
        confidence=confidence,
        extraction_method="fake_extractor",
        evidence={"step": "checkout"},
        last_seen_at=EARLIER,
        last_scan_id=scan_id,
    )


def test_same_node_recorded_twice_keeps_one_row_and_one_identifier(store, db):
    first = store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))
    second = store.record_node(
        _node("repo-alpha", NodeKind.REPOSITORY, name="Repo Alpha renamed", seen_at=LATER),
    )

    assert first == second
    assert first.startswith("kn_")
    assert len(db.get_knowledge_nodes(PROFILE)) == 1


def test_first_seen_survives_an_update_and_last_seen_moves_forward(store):
    store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))
    store.record_node(_node("repo-alpha", NodeKind.REPOSITORY, seen_at=LATER))

    found = store.find_node(
        profile_id=PROFILE, kind=NodeKind.REPOSITORY, identity_key="repo-alpha",
    )
    assert found is not None
    assert found.first_seen_at == EARLIER
    assert found.last_seen_at == LATER


def test_an_edge_that_stops_being_seen_becomes_disappeared(store, db):
    first_scan = store.start_scan(
        profile_id=PROFILE, source_scope="Payments-fake", extractor_version="1.0",
    )
    pipeline = store.record_node(_node("pipeline-build"))
    repo = store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))
    feed = store.record_node(_node("feed-shared", NodeKind.ARTIFACT_FEED))
    store.record_edge(_edge(pipeline, repo, scan_id=first_scan.scan_id))
    store.record_edge(
        _edge(pipeline, feed, kind=EdgeKind.CONSUMES_FEED, scan_id=first_scan.scan_id),
    )

    second_scan = store.start_scan(
        profile_id=PROFILE, source_scope="Payments-fake", extractor_version="1.0",
    )
    store.record_edge(_edge(pipeline, repo, scan_id=second_scan.scan_id))
    marked = store.mark_unseen_edges_disappeared(
        profile_id=PROFILE, scan_id=second_scan.scan_id,
    )

    assert marked == 1
    still_there = store.neighbours(
        profile_id=PROFILE, node_id=pipeline, direction=DIRECTION_DEPENDENCIES,
    )
    assert [node.identity_key for _edge_row, node in still_there] == ["repo-alpha"]
    gone = [
        row for row in db.get_knowledge_edges(PROFILE, source_ids=[pipeline])
        if row["status"] == EdgeStatus.DISAPPEARED.value
    ]
    assert len(gone) == 1


def test_re_recording_an_edge_keeps_its_identifier_and_first_seen(store, db):
    pipeline = store.record_node(_node("pipeline-build"))
    repo = store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))

    first = store.record_edge(_edge(pipeline, repo, scan_id="ks_one"))
    second = store.record_edge(_edge(pipeline, repo, scan_id="ks_two"))

    assert first == second
    assert first.startswith("ke_")
    rows = db.get_knowledge_edges(PROFILE, source_ids=[pipeline])
    assert len(rows) == 1
    assert rows[0]["first_seen_at"] == EARLIER
    assert rows[0]["last_scan_id"] == "ks_two"


def test_neighbours_walk_both_directions_and_respect_kind_and_limit(store):
    pipeline = store.record_node(_node("pipeline-build"))
    repo = store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))
    feed = store.record_node(_node("feed-shared", NodeKind.ARTIFACT_FEED))
    store.record_edge(_edge(pipeline, repo))
    store.record_edge(_edge(pipeline, feed, kind=EdgeKind.CONSUMES_FEED))

    forwards = store.neighbours(
        profile_id=PROFILE, node_id=pipeline, direction=DIRECTION_DEPENDENCIES,
    )
    backwards = store.neighbours(
        profile_id=PROFILE, node_id=repo, direction=DIRECTION_CONSUMERS,
    )
    only_feeds = store.neighbours(
        profile_id=PROFILE,
        node_id=pipeline,
        direction=DIRECTION_DEPENDENCIES,
        kinds=[EdgeKind.CONSUMES_FEED],
    )
    capped = store.neighbours(
        profile_id=PROFILE, node_id=pipeline, direction=DIRECTION_DEPENDENCIES, limit=1,
    )

    assert sorted(node.identity_key for _e, node in forwards) == ["feed-shared", "repo-alpha"]
    assert [node.identity_key for _e, node in backwards] == ["pipeline-build"]
    assert [node.identity_key for _e, node in only_feeds] == ["feed-shared"]
    assert len(capped) == 1


def test_impact_finds_a_consumer_two_steps_away_with_the_weakest_confidence(store):
    repo = store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))
    pipeline = store.record_node(_node("pipeline-build"))
    release = store.record_node(_node("release-nightly", NodeKind.RELEASE_PIPELINE))
    store.record_edge(_edge(pipeline, repo))
    store.record_edge(
        _edge(
            release,
            pipeline,
            kind=EdgeKind.CONSUMES_ARTIFACT_OF,
            confidence=Confidence.INFERRED,
        ),
    )

    report = store.impact(profile_id=PROFILE, node_id=repo)

    assert report.subject is not None
    assert report.subject.identity_key == "repo-alpha"
    assert [f.node.identity_key for f in report.consumers] == [
        "pipeline-build", "release-nightly",
    ]
    near, far = report.consumers
    assert near.distance == 1
    assert near.weakest_confidence is Confidence.DECLARED
    assert far.distance == 2
    assert far.path == [repo, pipeline, release]
    assert far.weakest_confidence is Confidence.INFERRED
    assert far.evidence == [{"step": "checkout"}, {"step": "checkout"}]
    assert any("inferred" in caveat for caveat in report.caveats)
    assert not report.truncated


def test_impact_reports_prerequisites_shared_with_another_consumer(store):
    pipeline = store.record_node(_node("pipeline-build"))
    other = store.record_node(_node("pipeline-nightly"))
    feed = store.record_node(_node("feed-shared", NodeKind.ARTIFACT_FEED))
    store.record_edge(_edge(pipeline, feed, kind=EdgeKind.CONSUMES_FEED))
    store.record_edge(_edge(other, feed, kind=EdgeKind.CONSUMES_FEED))

    report = store.impact(profile_id=PROFILE, node_id=pipeline)

    assert [f.node.identity_key for f in report.prerequisites] == ["feed-shared"]
    assert [f.node.identity_key for f in report.shared_dependencies] == ["feed-shared"]
    assert report.shared_dependencies[0].relation == RELATION_SHARED_DEPENDENCY


def test_impact_terminates_when_the_recorded_dependencies_form_a_cycle(store):
    left = store.record_node(_node("pipeline-left"))
    right = store.record_node(_node("pipeline-right"))
    store.record_edge(_edge(left, right, kind=EdgeKind.TRIGGERED_BY))
    store.record_edge(_edge(right, left, kind=EdgeKind.TRIGGERED_BY))

    report = store.impact(profile_id=PROFILE, node_id=left, max_depth=10)

    assert [f.node.identity_key for f in report.prerequisites] == ["pipeline-right"]
    assert [f.node.identity_key for f in report.consumers] == ["pipeline-right"]


def test_impact_stops_at_the_node_limit_and_says_so(store):
    root = store.record_node(_node("pipeline-root"))
    for index in range(4):
        leaf = store.record_node(_node(f"feed-{index}", NodeKind.ARTIFACT_FEED))
        store.record_edge(_edge(root, leaf, kind=EdgeKind.CONSUMES_FEED))

    report = store.impact(profile_id=PROFILE, node_id=root, max_nodes=2)

    assert report.truncated
    assert len(report.prerequisites) == 2


def test_impact_always_carries_the_caveat_and_the_coverage(store):
    scan = store.start_scan(
        profile_id=PROFILE, source_scope="Payments-fake", extractor_version="1.0",
    )
    store.finish_scan(
        scan.scan_id,
        status="completed",
        coverage={"pipelines_read": 7, "pipelines_unparsed": 1},
    )
    lonely = store.record_node(_node("repo-lonely", NodeKind.REPOSITORY))

    report = store.impact(profile_id=PROFILE, node_id=lonely)

    assert report.prerequisites == []
    assert report.consumers == []
    assert NO_RECORDED_DEPENDENCY_CAVEAT in report.caveats
    assert report.coverage["pipelines_read"] == 7
    assert report.coverage["scan_id"] == scan.scan_id
    assert report.coverage["completed_at"]


def test_impact_on_an_unknown_thing_still_carries_the_caveat(store):
    report = store.impact(profile_id=PROFILE, node_id="kn_not_recorded")

    assert report.subject is None
    assert NO_RECORDED_DEPENDENCY_CAVEAT in report.caveats


def test_latest_coverage_is_empty_before_any_scan_finishes(store):
    store.start_scan(
        profile_id=PROFILE, source_scope="Payments-fake", extractor_version="1.0",
    )

    assert store.latest_coverage(profile_id=PROFILE) == {}


def test_searching_ignores_upper_and_lower_case(store):
    store.record_node(_node("pipeline-BUILD", name="Nightly Build"))
    store.record_node(_node("repo-alpha", NodeKind.REPOSITORY))

    by_key = store.search_nodes(profile_id=PROFILE, text="pipeline-build")
    by_name = store.search_nodes(profile_id=PROFILE, text="NIGHTLY")
    by_kind = store.search_nodes(
        profile_id=PROFILE, text="a", kinds=[NodeKind.REPOSITORY],
    )

    assert [n.identity_key for n in by_key] == ["pipeline-BUILD"]
    assert [n.identity_key for n in by_name] == ["pipeline-BUILD"]
    assert [n.identity_key for n in by_kind] == ["repo-alpha"]
