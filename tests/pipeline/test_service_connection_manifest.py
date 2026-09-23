"""Tests for the (deprecated) service connection migration manifest generator."""
from __future__ import annotations

from pathlib import Path

from ado2gh.reporting.service_connection_manifest import ServiceConnectionManifest


class _FakeADO:
    """Minimal ADOClient stand-in; raises for a project name to exercise the
    generate() error-handling branch."""

    def __init__(self, by_project):
        self._by_project = by_project

    def list_service_connections(self, project):
        if project == "boom":
            raise RuntimeError("ADO unreachable")
        return self._by_project.get(project, [])


def _manifest(by_project=None):
    return ServiceConnectionManifest(_FakeADO(by_project or {}))


# ── _map_connection ──────────────────────────────────────────────────────────


def test_map_connection_uses_matching_migration_guide():
    m = _manifest()
    sc = {"type": "AzureRM", "name": "azure-conn", "url": "https://azure",
          "isShared": True, "createdBy": {"displayName": "Alice"}}
    mapped = m._map_connection("ProjA", sc)
    assert mapped["gh_secret_names"] == [
        "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID",
    ]
    assert mapped["is_shared"] is True
    assert mapped["created_by"] == "Alice"
    assert mapped["action_required"] == "ops_team_setup"


def test_map_connection_falls_back_for_unknown_type():
    m = _manifest()
    sc = {"type": "SomeCustomType99", "name": "weird-conn"}
    mapped = m._map_connection("ProjA", sc)
    assert mapped["gh_secret_names"] == ["WEIRD_CONN_TOKEN"]
    assert "Review manually" in mapped["recommendation"]
    assert mapped["docs_url"] == ""


# ── _build_summary ───────────────────────────────────────────────────────────


def test_build_summary_counts_types_oidc_and_unique_secrets():
    m = _manifest()
    connections = [
        m._map_connection("ProjA", {"type": "AzureRM", "name": "azure-conn"}),
        m._map_connection("ProjA", {"type": "SomeCustomType99", "name": "weird-conn"}),
    ]
    summary = m._build_summary(connections)
    assert summary["total_connections"] == 2
    assert summary["by_type"] == {"AzureRM": 1, "SomeCustomType99": 1}
    assert summary["oidc_eligible"] == 1
    assert summary["manual_setup_required"] == 2
    assert summary["unique_secret_names"] == 4


def test_build_summary_of_no_connections_is_all_zero():
    summary = _manifest()._build_summary([])
    assert summary == {
        "total_connections": 0,
        "by_type": {},
        "oidc_eligible": 0,
        "manual_setup_required": 0,
        "unique_secret_names": 0,
    }


# ── generate() / _write_csv() ────────────────────────────────────────────────


def test_generate_writes_json_and_csv_and_skips_failing_project(tmp_path):
    svcs = {"ProjA": [{"type": "AzureRM", "name": "azure-conn", "url": "https://azure"}]}
    m = _manifest(svcs)
    out_path = str(tmp_path / "manifest" / "svc.json")

    summary = m.generate(["ProjA", "boom"], output_path=out_path)

    assert summary["total_connections"] == 1
    assert Path(out_path).exists()
    csv_path = Path(out_path).with_suffix(".csv")
    assert csv_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "azure-conn" in csv_text
    assert "AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID" in csv_text


def test_generate_manifest_json_has_no_connections_for_failing_project(tmp_path):
    m = _manifest({})
    out_path = str(tmp_path / "svc.json")
    summary = m.generate(["boom"], output_path=out_path)
    assert summary["total_connections"] == 0


# ── print_summary() ──────────────────────────────────────────────────────────


def test_print_summary_renders_without_error(capsys):
    summary = {
        "total_connections": 2, "oidc_eligible": 1, "unique_secret_names": 4,
        "manual_setup_required": 2, "by_type": {"AzureRM": 1, "SomeCustomType99": 1},
    }
    _manifest().print_summary(summary)
    out = capsys.readouterr().out
    assert "Service Connection Migration" in out
    assert "Total connections" in out
    assert "AzureRM" in out
