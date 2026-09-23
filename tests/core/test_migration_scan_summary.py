"""Scan summary, gap and persistence helpers in ``ado2gh/api/migration_scan.py`` (COV-DRIFT-008).

The module sat at 34 % with 150 statements unexercised while this feature
rewrote 293 of its lines. What it produces is the discovery payload the console
renders and the operator plans a migration from, so a summariser that drops a
service connection or a merge that overwrites a discovery field is a planning
error nobody sees.

Everything here is either a pure function or a file round-trip inside
``tmp_path``. The state DB is a stub that records what it was handed; nothing
reaches Azure DevOps and nothing reads the developer's own ``data/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ado2gh.api.migration_scan import (
    _scan_results_path,
    _stub_pipeline,
    _summarize_service_connections,
    _summarize_variable_groups,
    build_inventory_gaps,
    load_scan_results,
    merge_scan_payload,
    persist_scan_results,
    replace_scan_results,
)
from ado2gh.models import PipelineComplexity, PipelineType
from ado2gh.state.scan_payload import DISCOVERY_DETAIL_FIELDS


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """Point every scan path at a per-test directory, never the repo's data/."""
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# merge_scan_payload
# --------------------------------------------------------------------------


def test_merging_two_empty_payloads_yields_nothing():
    assert merge_scan_payload(None, None) is None
    assert merge_scan_payload({}, None) is None
    assert merge_scan_payload(None, {}) == {}


def test_an_absent_base_is_replaced_by_the_extra_payload():
    extra = {"repos_scanned": 3}
    assert merge_scan_payload(None, extra) is extra
    assert merge_scan_payload({}, extra) is extra


def test_an_absent_extra_leaves_the_base_untouched():
    base = {"repos_scanned": 3}
    assert merge_scan_payload(base, None) is base
    assert merge_scan_payload(base, {}) is base


def test_a_field_the_base_already_holds_is_never_overwritten():
    field = DISCOVERY_DETAIL_FIELDS[0]
    merged = merge_scan_payload({field: ["mine"]}, {field: ["theirs"]})
    assert merged[field] == ["mine"]


def test_a_discovery_field_missing_from_the_base_is_filled_from_the_extra():
    field = DISCOVERY_DETAIL_FIELDS[0]
    merged = merge_scan_payload({"repos_scanned": 1}, {field: ["recovered"]})
    assert merged[field] == ["recovered"]
    assert merged["repos_scanned"] == 1


def test_an_empty_value_in_the_base_counts_as_missing():
    field = DISCOVERY_DETAIL_FIELDS[0]
    merged = merge_scan_payload({field: []}, {field: ["recovered"]})
    assert merged[field] == ["recovered"]


def test_a_field_absent_from_both_payloads_is_not_invented():
    field = DISCOVERY_DETAIL_FIELDS[0]
    merged = merge_scan_payload({"repos_scanned": 1}, {"repos_scanned": 2})
    assert field not in merged


def test_only_discovery_fields_are_merged_across():
    merged = merge_scan_payload({"repos_scanned": 0}, {"repos_scanned": 9, "extra": "x"})
    assert merged["repos_scanned"] == 0, "a non-discovery field was topped up"
    assert "extra" not in merged


def test_the_base_payload_is_not_mutated_in_place():
    field = DISCOVERY_DETAIL_FIELDS[0]
    base = {"repos_scanned": 1}
    merge_scan_payload(base, {field: ["recovered"]})
    assert base == {"repos_scanned": 1}


# --------------------------------------------------------------------------
# _summarize_service_connections
# --------------------------------------------------------------------------


def test_a_service_connection_is_reduced_to_the_four_summary_fields():
    assert _summarize_service_connections([
        {"name": "prod-azure", "type": "azurerm", "id": "sc-1", "isReady": False,
         "authorization": {"parameters": {"serviceprincipalkey": "fake-secret-0001"}}},
    ]) == [
        {"name": "prod-azure", "type": "azurerm", "id": "sc-1", "is_ready": False},
    ]


def test_a_service_connection_is_assumed_ready_when_ado_does_not_say():
    assert _summarize_service_connections([{"name": "x"}])[0]["is_ready"] is True


def test_an_unnamed_service_connection_is_dropped():
    assert _summarize_service_connections([{"id": "sc-1"}, {"name": ""}]) == []


def test_no_credential_material_survives_the_service_connection_summary():
    """ADO never returns a connection's credential, and the summary keeps none."""
    summary = _summarize_service_connections([
        {"name": "prod", "authorization": {"parameters": {"token": "ghp_fake0000001"}}},
    ])
    assert "ghp_fake0000001" not in json.dumps(summary)
    assert set(summary[0]) == {"name", "type", "id", "is_ready"}


def test_an_empty_service_connection_list_summarises_to_an_empty_list():
    assert _summarize_service_connections([]) == []


# --------------------------------------------------------------------------
# _summarize_variable_groups
# --------------------------------------------------------------------------


def test_a_variable_group_reports_its_variable_count_not_its_values():
    assert _summarize_variable_groups([
        {"name": "shared", "id": 7, "isShared": True,
         "variables": {"API_KEY": {"value": "fake-key-0002"}, "REGION": {"value": "eu"}}},
    ]) == [
        {"name": "shared", "id": 7, "variable_count": 2, "is_shared": True},
    ]


def test_a_variable_group_with_no_variables_counts_zero():
    assert _summarize_variable_groups([{"name": "empty"}])[0]["variable_count"] == 0


def test_the_shared_flag_is_normalised_to_a_boolean():
    assert _summarize_variable_groups([{"name": "a"}])[0]["is_shared"] is False
    assert _summarize_variable_groups([{"name": "a", "isShared": 1}])[0]["is_shared"] is True


def test_an_unnamed_variable_group_is_dropped():
    assert _summarize_variable_groups([{"id": 7}]) == []


def test_no_variable_value_survives_the_variable_group_summary():
    summary = _summarize_variable_groups([
        {"name": "shared", "variables": {"API_KEY": {"value": "fake-key-0002"}}},
    ])
    assert "fake-key-0002" not in json.dumps(summary)


# --------------------------------------------------------------------------
# build_inventory_gaps
# --------------------------------------------------------------------------


def test_a_service_connection_becomes_a_gap_naming_its_operator_field():
    gaps = build_inventory_gaps([
        {"project": "Contoso", "service_connections": [{"name": "prod", "type": "azurerm"}]},
    ])
    assert gaps == [{
        "type": "service_connection",
        "project": "Contoso",
        "name": "prod",
        "connection_type": "azurerm",
        "field": "secret_mapping__Contoso__prod",
        "hint": "GitHub secret name or OIDC federated credential to use",
    }]


def test_a_variable_group_becomes_a_gap_naming_its_operator_field():
    gaps = build_inventory_gaps([
        {"project": "Contoso", "variable_groups": [{"name": "shared"}]},
    ])
    assert gaps[0]["type"] == "variable_group"
    assert gaps[0]["field"] == "variable_group__Contoso__shared"
    assert "values are not readable from ADO" in gaps[0]["hint"]


def test_a_project_with_neither_asset_contributes_no_gap():
    assert build_inventory_gaps([{"project": "Contoso"}]) == []
    assert build_inventory_gaps([]) == []


def test_gaps_are_produced_for_every_project_in_order():
    gaps = build_inventory_gaps([
        {"project": "A", "service_connections": [{"name": "a1"}]},
        {"project": "B", "variable_groups": [{"name": "b1"}]},
    ])
    assert [g["project"] for g in gaps] == ["A", "B"]


def test_a_null_asset_list_is_treated_as_empty():
    assert build_inventory_gaps([
        {"project": "Contoso", "service_connections": None, "variable_groups": None},
    ]) == []


# --------------------------------------------------------------------------
# _stub_pipeline
# --------------------------------------------------------------------------


def test_a_process_type_of_two_is_a_yaml_pipeline():
    meta = _stub_pipeline(
        {"id": 7, "name": "CI", "process": {"type": 2},
         "repository": {"id": "repo-guid", "name": "payments"}},
        "Contoso",
    )
    assert meta.pipeline_type is PipelineType.YAML
    assert meta.pipeline_id == 7
    assert meta.pipeline_name == "CI"
    assert meta.project == "Contoso"
    assert meta.repo_id == "repo-guid"
    assert meta.repo_name == "payments"
    assert meta.complexity is PipelineComplexity.SIMPLE


@pytest.mark.parametrize("process_type", [1, 3, 0])
def test_any_other_process_type_is_a_classic_pipeline(process_type):
    meta = _stub_pipeline({"process": {"type": process_type}}, "Contoso")
    assert meta.pipeline_type is PipelineType.CLASSIC


def test_a_definition_with_no_process_block_defaults_to_classic():
    meta = _stub_pipeline({}, "Contoso")
    assert meta.pipeline_type is PipelineType.CLASSIC
    assert meta.pipeline_id == 0
    assert meta.pipeline_name == ""
    assert meta.repo_name == ""


# --------------------------------------------------------------------------
# _scan_results_path
# --------------------------------------------------------------------------


def test_a_profile_scan_is_stored_under_its_own_name(_data_dir):
    assert _scan_results_path("prof-1") == _data_dir / "scan_prof-1.json"


def test_a_scan_with_no_profile_is_stored_as_the_preview(_data_dir):
    assert _scan_results_path() == _data_dir / "scan_preview.json"
    assert _scan_results_path("") == _data_dir / "scan_preview.json"


# --------------------------------------------------------------------------
# persist / replace / load
# --------------------------------------------------------------------------


class _StubDb:
    """State DB stand-in recording which persistence path was taken."""

    def __init__(self, payload: dict | None = None) -> None:
        self.saved: list[tuple[str, dict]] = []
        self.replaced: list[tuple[str, dict]] = []
        self._payload = payload

    def save_profile_scan(self, profile_id: str, results: dict) -> None:
        self.saved.append((profile_id, results))

    def replace_profile_scan(self, profile_id: str, results: dict) -> None:
        self.replaced.append((profile_id, results))

    def build_profile_scan_payload(self, _profile_id: str) -> dict | None:
        return self._payload


@pytest.fixture
def stub_db(monkeypatch):
    """Install a stub state DB and hand it back for inspection."""
    def _install(payload: dict | None = None) -> _StubDb:
        db = _StubDb(payload)
        monkeypatch.setattr("ado2gh.api.state_db.get_state_db", lambda: db)
        return db

    return _install


def test_persisting_a_scan_keeps_manual_phase_assignments(stub_db):
    db = stub_db()
    results = {"repos_scanned": 2}
    persist_scan_results("prof-1", results)
    assert db.saved == [("prof-1", results)]
    assert db.replaced == [], "the routine rescan path discarded phase assignments"


def test_replacing_a_scan_discards_manual_phase_assignments(stub_db):
    db = stub_db()
    results = {"repos_scanned": 2}
    replace_scan_results("prof-1", results)
    assert db.replaced == [("prof-1", results)]
    assert db.saved == []


def test_persisting_writes_a_portable_json_copy_carrying_the_profile_id(stub_db, _data_dir):
    stub_db()
    path = persist_scan_results("prof-1", {"repos_scanned": 2})
    assert path == _data_dir / "scan_prof-1.json"
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    assert stored["profile_id"] == "prof-1"
    assert stored["repos_scanned"] == 2


def test_the_backup_prefers_the_payload_the_database_would_serve(stub_db):
    stub_db({"repos_scanned": 99})
    path = persist_scan_results("prof-1", {"repos_scanned": 2})
    assert json.loads(Path(path).read_text(encoding="utf-8"))["repos_scanned"] == 99


def test_the_backup_falls_back_to_the_supplied_results(stub_db):
    stub_db(None)
    path = persist_scan_results("prof-1", {"repos_scanned": 2})
    assert json.loads(Path(path).read_text(encoding="utf-8"))["repos_scanned"] == 2


def test_a_scan_round_trips_through_persist_and_load(stub_db):
    stub_db(None)
    persist_scan_results("prof-1", {"repos_scanned": 2, "recommendations": ["go"]})
    loaded = load_scan_results("prof-1")
    assert loaded["repos_scanned"] == 2
    assert loaded["recommendations"] == ["go"]


def test_loading_a_profile_with_no_scan_anywhere_returns_none(stub_db):
    stub_db(None)
    assert load_scan_results("never-scanned") is None


def test_loading_tops_the_database_payload_up_from_the_on_disk_backup(stub_db, _data_dir):
    field = DISCOVERY_DETAIL_FIELDS[0]
    (_data_dir / "scan_prof-1.json").write_text(
        json.dumps({"profile_id": "prof-1", field: ["only-on-disk"]}), encoding="utf-8",
    )
    stub_db({"repos_scanned": 5})
    loaded = load_scan_results("prof-1")
    assert loaded["repos_scanned"] == 5
    assert loaded[field] == ["only-on-disk"]


def test_an_unreadable_backup_is_ignored_rather_than_raised(stub_db, _data_dir):
    (_data_dir / "scan_prof-1.json").write_text("{not json", encoding="utf-8")
    stub_db({"repos_scanned": 5})
    assert load_scan_results("prof-1")["repos_scanned"] == 5


def test_an_unreadable_backup_with_no_database_payload_returns_none(stub_db, _data_dir):
    (_data_dir / "scan_prof-1.json").write_text("{not json", encoding="utf-8")
    stub_db(None)
    assert load_scan_results("prof-1") is None


def test_an_empty_database_payload_still_serves_a_backup_that_scanned_repos(
    stub_db, _data_dir,
):
    (_data_dir / "scan_prof-1.json").write_text(
        json.dumps({"profile_id": "prof-1", "repos_scanned": 4}), encoding="utf-8",
    )
    stub_db(None)
    assert load_scan_results("prof-1")["repos_scanned"] == 4


def test_any_non_empty_backup_is_served_even_when_it_scanned_nothing(stub_db, _data_dir):
    """The merge returns the backup itself, so the later emptiness test never fires.

    ``merge_scan_payload(None, file_data)`` hands back ``file_data`` unchanged,
    and a payload with any key at all is truthy — so the guard on
    ``recommendations`` / ``repos_scanned > 0`` below it is unreachable for a
    backup that parsed. Pinned as observed behaviour; the dead branch is carried
    as a follow-up rather than removed here.
    """
    (_data_dir / "scan_prof-1.json").write_text(
        json.dumps({"profile_id": "prof-1", "repos_scanned": 0}), encoding="utf-8",
    )
    stub_db(None)
    assert load_scan_results("prof-1") == {"profile_id": "prof-1", "repos_scanned": 0}


def test_an_empty_backup_object_with_no_database_payload_returns_none(stub_db, _data_dir):
    (_data_dir / "scan_prof-1.json").write_text("{}", encoding="utf-8")
    stub_db(None)
    assert load_scan_results("prof-1") is None
