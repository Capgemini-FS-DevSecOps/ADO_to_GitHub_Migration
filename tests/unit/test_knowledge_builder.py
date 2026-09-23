"""The knowledge builder derives dependency facts from the pipeline inventory already collected.

Every row here is fabricated: an obviously fake organisation, obviously fake
repositories and obviously fake pipelines. Nothing in this file reaches Azure
DevOps or any network, which is the point of the builder itself — it reads
only what an earlier inventory run already wrote into the state store.

What is checked: each dependency kind is recorded, pointing the right way,
with the confidence its source justifies and evidence that names things
without ever carrying a secret value; a row whose stored pipeline text cannot
be parsed is counted in the coverage rather than stopping the pass; a second
pass over unchanged rows repeats the same identifiers; and a dependency that
goes away is marked as gone rather than quietly kept.
"""
from __future__ import annotations

import json
from typing import Any

from ado2gh.knowledge.builder import _read_rows, build_knowledge_base
from ado2gh.knowledge.store import KnowledgeStore
from ado2gh.models import PipelineEnvironment, PipelineMetadata, PipelineType
from ado2gh.pipelines.inventory import _template_ref_records
from ado2gh.state.factory import create_state_db

PROFILE = "fabrikam-sample-profile"
SCOPE = "fabrikam-sample-org"
PROJECT = "SampleSpace"

WEB_YAML = """
resources:
  pipelines:
    - pipeline: upstream
      source: Sample-Api-CI
steps:
  - task: NuGetCommand@2
    inputs:
      command: restore
      feedsToUse: select
      vstsFeed: sample-internal-feed
  - task: DownloadBuildArtifacts@1
    inputs:
      buildType: specific
      definition: 4242
  - task: PipAuthenticate@1
    inputs:
      artifactFeeds: $(feedFromVariable)
"""

BROKEN_YAML = "steps: [ - task: {{{{ never closed"


def _web_pipeline() -> PipelineMetadata:
    """Return the fabricated web pipeline row, the one carrying every kind of fact."""
    return PipelineMetadata(
        pipeline_id=101,
        pipeline_name="Sample-Web-CI",
        pipeline_type=PipelineType.YAML,
        project=PROJECT,
        repo_id="repo-sample-web-0001",
        repo_name="sample-web",
        repo_branch="main",
        yaml_path="azure-pipelines.yml",
        yaml_content=WEB_YAML,
        variable_groups=[{
            "id": 7,
            "name": "sample-web-settings",
            "type": "Vsts",
            "variables": ["apiUrl", "apiKey"],
            "secret_variables": ["apiKey"],
        }],
        service_connections=[
            {"name": "sample-azure-connection", "type": "azurerm", "id": "sc-0001"},
        ],
        agent_pools=["Sample-Linux-Pool"],
        environments=[PipelineEnvironment(name="sample-staging", id=3)],
        template_refs=[
            {"ref": "build/steps.yml@sample-templates", "repository": "sample-templates"},
            {"ref": "local/steps.yml", "repository": ""},
        ],
    )


def _api_pipeline() -> PipelineMetadata:
    """Return the fabricated upstream pipeline the web pipeline takes artifacts from."""
    return PipelineMetadata(
        pipeline_id=202,
        pipeline_name="Sample-Api-CI",
        pipeline_type=PipelineType.YAML,
        project=PROJECT,
        repo_id="repo-sample-api-0002",
        repo_name="sample-api",
        yaml_content="steps:\n  - script: echo build\n",
    )


def _broken_pipeline() -> PipelineMetadata:
    """Return a fabricated row whose stored pipeline text cannot be parsed."""
    return PipelineMetadata(
        pipeline_id=303,
        pipeline_name="Sample-Broken-CI",
        pipeline_type=PipelineType.YAML,
        project=PROJECT,
        repo_id="repo-sample-broken-0003",
        repo_name="sample-broken",
        yaml_content=BROKEN_YAML,
    )


def _state_db(tmp_path: Any, metas: list[PipelineMetadata]) -> Any:
    """Return a state store holding the fabricated inventory rows."""
    db = create_state_db(str(tmp_path / "knowledge-sample.db"))
    for meta in metas:
        db.upsert_pipeline_inventory(meta)
    return db


def _edges(db: Any) -> list[dict]:
    """Return every recorded dependency; each one leaves a pipeline by design."""
    pipeline_ids = [row["id"] for row in db.get_knowledge_nodes(PROFILE, kind="pipeline")]
    return db.get_knowledge_edges(PROFILE, source_ids=pipeline_ids)


def _by_kind(edges: list[dict], kind: str) -> list[dict]:
    """Return the recorded dependencies of one kind."""
    return [e for e in edges if e["kind"] == kind]


def _node(db: Any, node_id: str) -> dict:
    """Return one recorded node row."""
    return db.get_knowledge_nodes(PROFILE, node_ids=[node_id])[0]


def test_every_edge_kind_is_recorded_with_its_direction_confidence_and_evidence(
    tmp_path: Any,
) -> None:
    """Each dependency kind lands, pointing from the pipeline at what it needs."""
    db = _state_db(tmp_path, [_web_pipeline(), _api_pipeline()])
    scan = build_knowledge_base(
        KnowledgeStore(db), db, profile_id=PROFILE, source_scope=SCOPE,
    )
    assert scan.status == "completed"
    edges = _edges(db)

    web_node = db.get_knowledge_nodes(PROFILE, kind="pipeline", identity_key="101")[0]
    assert {e["source_node_id"] for e in edges} <= {
        row["id"] for row in db.get_knowledge_nodes(PROFILE, kind="pipeline")
    }

    builds = _by_kind(edges, "builds")
    assert len(builds) == 2
    web_builds = [e for e in builds if e["source_node_id"] == web_node["id"]][0]
    assert web_builds["confidence"] == "declared"
    assert _node(db, web_builds["target_node_id"])["kind"] == "repository"
    assert json.loads(web_builds["evidence_json"])["repository_name"] == "sample-web"

    credential = _by_kind(edges, "needs_credential")[0]
    assert credential["confidence"] == "inferred"
    assert json.loads(credential["evidence_json"]) == {
        "connection_name": "sample-azure-connection",
        "connection_type": "azurerm",
    }

    group = _by_kind(edges, "needs_variable_group")[0]
    assert group["confidence"] == "declared"
    group_evidence = json.loads(group["evidence_json"])
    assert group_evidence["group_name"] == "sample-web-settings"
    assert group_evidence["secret_variable_names"] == ["apiKey"]
    assert "value" not in group["evidence_json"]

    environment = _by_kind(edges, "deploys_to")[0]
    assert environment["confidence"] == "declared"
    assert _node(db, environment["target_node_id"])["name"] == "sample-staging"

    pool = _by_kind(edges, "runs_on_pool")[0]
    assert pool["confidence"] == "declared"
    assert _node(db, pool["target_node_id"])["kind"] == "agent_pool"

    templates = _by_kind(edges, "extends_template_in")
    by_confidence = {e["confidence"]: json.loads(e["evidence_json"]) for e in templates}
    assert by_confidence["declared"]["repository_alias"] == "sample-templates"
    assert by_confidence["inferred"]["template_ref"] == "local/steps.yml"

    feed = _by_kind(edges, "consumes_feed")
    assert [json.loads(e["evidence_json"])["feed"] for e in feed] == ["sample-internal-feed"]
    assert feed[0]["confidence"] == "declared"

    artifacts = _by_kind(edges, "consumes_artifact_of")
    references = {
        json.loads(e["evidence_json"])["pipeline_reference"]: e for e in artifacts
    }
    assert references["Sample-Api-CI"]["confidence"] == "resolved"
    api_node = db.get_knowledge_nodes(PROFILE, kind="pipeline", identity_key="202")[0]
    assert references["Sample-Api-CI"]["target_node_id"] == api_node["id"]
    assert references["4242"]["confidence"] == "declared"


def test_coverage_names_what_was_read_and_what_was_not_attempted(tmp_path: Any) -> None:
    """The scan says how much it read and which dependency kinds it never looks for."""
    db = _state_db(tmp_path, [_web_pipeline(), _api_pipeline()])
    scan = build_knowledge_base(
        KnowledgeStore(db), db, profile_id=PROFILE, source_scope=SCOPE,
    )
    assert scan.coverage["rows_read"] == 2
    assert scan.coverage["source"] == "pipeline_inventory"
    assert scan.coverage["edges_by_kind"]["builds"] == 2
    assert "checks_out" in scan.coverage["edge_kinds_not_attempted"]
    assert "triggered_by" in scan.coverage["edge_kinds_not_attempted"]


def test_unparsable_stored_text_is_counted_rather_than_raised(tmp_path: Any) -> None:
    """A row whose pipeline text will not parse is counted, and the pass carries on."""
    db = _state_db(tmp_path, [_web_pipeline(), _broken_pipeline()])
    scan = build_knowledge_base(
        KnowledgeStore(db), db, profile_id=PROFILE, source_scope=SCOPE,
    )
    assert scan.status == "completed"
    assert scan.coverage["rows_read"] == 2
    assert scan.coverage["pipelines_with_unreadable_text"] == 1
    # The broken row still contributes the facts its columns declare.
    broken = db.get_knowledge_nodes(PROFILE, kind="pipeline", identity_key="303")[0]
    assert _by_kind(db.get_knowledge_edges(PROFILE, source_ids=[broken["id"]]), "builds")


def test_unreadable_inventory_row_is_counted_rather_than_raised() -> None:
    """A row whose stored record is not readable JSON is counted, not raised."""
    rows, unreadable = _read_rows([
        {"project": PROJECT, "pipeline_id": 909, "metadata_json": "{not json"},
        {"project": PROJECT, "pipeline_id": 101,
         "metadata_json": json.dumps(_web_pipeline().to_dict())},
    ])
    assert unreadable == 1
    assert [row.meta.pipeline_id for row in rows] == [101]


def test_second_pass_over_unchanged_rows_repeats_the_same_identifiers(
    tmp_path: Any,
) -> None:
    """Running twice adds no rows and changes no identifier."""
    db = _state_db(tmp_path, [_web_pipeline(), _api_pipeline()])
    store = KnowledgeStore(db)
    build_knowledge_base(store, db, profile_id=PROFILE, source_scope=SCOPE)
    first_nodes = {row["id"] for row in db.get_knowledge_nodes(PROFILE)}
    first_edges = {row["id"] for row in _edges(db)}

    second = build_knowledge_base(store, db, profile_id=PROFILE, source_scope=SCOPE)
    assert {row["id"] for row in db.get_knowledge_nodes(PROFILE)} == first_nodes
    assert {row["id"] for row in _edges(db)} == first_edges
    assert second.coverage["edges_marked_disappeared"] == 0
    assert all(row["status"] == "active" for row in _edges(db))


def test_dependency_absent_on_the_second_pass_is_marked_disappeared(
    tmp_path: Any,
) -> None:
    """A variable group dropped from the source stops being answered as current."""
    db = _state_db(tmp_path, [_web_pipeline()])
    store = KnowledgeStore(db)
    build_knowledge_base(store, db, profile_id=PROFILE, source_scope=SCOPE)
    assert _by_kind(_edges(db), "needs_variable_group")[0]["status"] == "active"

    without_group = _web_pipeline()
    without_group.variable_groups = []
    db.upsert_pipeline_inventory(without_group)
    second = build_knowledge_base(store, db, profile_id=PROFILE, source_scope=SCOPE)

    gone = _by_kind(_edges(db), "needs_variable_group")[0]
    assert gone["status"] == "disappeared"
    assert second.coverage["edges_marked_disappeared"] == 1
    assert all(e["status"] == "active" for e in _by_kind(_edges(db), "builds"))


def test_inventory_keeps_the_template_references_it_already_computed(
    tmp_path: Any,
) -> None:
    """Template references survive the inventory write, with the repository they named."""
    assert _template_ref_records([
        "build/steps.yml@sample-templates", "local/steps.yml",
    ]) == [
        {"ref": "build/steps.yml@sample-templates", "repository": "sample-templates"},
        {"ref": "local/steps.yml", "repository": ""},
    ]

    meta = _web_pipeline()
    assert PipelineMetadata.from_dict(meta.to_dict()).template_refs == meta.template_refs

    db = _state_db(tmp_path, [meta])
    stored = db.get_pipelines_for_repo(PROJECT, "sample-web")[0]
    assert stored.template_refs == meta.template_refs
