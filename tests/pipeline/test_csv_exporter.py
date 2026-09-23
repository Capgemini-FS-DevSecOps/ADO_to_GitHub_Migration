"""Tests for CSVExporter's migration and failed-repo CSV/text output."""
from __future__ import annotations

import csv
from pathlib import Path

from ado2gh.reporting.csv_exporter import CSVExporter


class _FakeDB:
    """Minimal StateDB stand-in exposing only the query methods read here."""

    def __init__(self, all_rows=None, wave_rows=None, failed_rows=None):
        self._all = all_rows or []
        self._wave = wave_rows or []
        self._failed = failed_rows or []

    def get_all_migrations(self):
        return self._all

    def get_wave_migrations(self, wave_id):  # noqa: ARG002 - fixed fake response
        return self._wave

    def get_failed_migrations(self):
        return self._failed


_ROW = {
    "id": 1, "wave_id": 1, "ado_project": "proj", "ado_repo": "repoA",
    "gh_org": "acme", "gh_repo": "repoA", "scope": "repo", "status": "completed",
    "started_at": "t1", "completed_at": "t2", "error_message": None,
    "gh_migration_id": "m1",
}


def test_export_migrations_writes_all_rows_and_creates_parent_dirs(tmp_path):
    out = tmp_path / "sub" / "migrations.csv"
    result_path = CSVExporter.export_migrations(_FakeDB(all_rows=[_ROW]), str(out))

    assert result_path == str(out)
    assert out.exists()
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{
        "id": "1", "wave_id": "1", "ado_project": "proj", "ado_repo": "repoA",
        "gh_org": "acme", "gh_repo": "repoA", "scope": "repo", "status": "completed",
        "started_at": "t1", "completed_at": "t2", "error_message": "",
        "gh_migration_id": "m1",
    }]


def test_export_migrations_filters_by_wave_id(tmp_path):
    out = tmp_path / "wave.csv"
    CSVExporter.export_migrations(_FakeDB(wave_rows=[_ROW]), str(out), wave_id=1)
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ado_repo"] == "repoA"


def test_export_failed_repos_writes_text_list_and_csv_details(tmp_path):
    failed_rows = [
        {"wave_id": 1, "ado_project": "proj", "ado_repo": "repoB", "gh_org": "acme",
         "gh_repo": "repoB", "scope": "repo", "status": "failed", "error_message": "boom"},
        {"wave_id": 1, "ado_project": "proj", "ado_repo": "repoC", "gh_org": "acme",
         "gh_repo": "repoC", "scope": "pipelines", "status": "failed", "error_message": "oops"},
    ]
    out = tmp_path / "failed" / "failed_repos.txt"
    result_path = CSVExporter.export_failed_repos(_FakeDB(failed_rows=failed_rows), str(out))

    assert result_path == str(out)
    assert out.read_text(encoding="utf-8") == "proj/repoB\nproj/repoC\n"

    csv_path = out.with_suffix(".csv")
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["ado_repo"] for r in rows] == ["repoB", "repoC"]
    assert rows[0]["error_message"] == "boom"


def test_export_failed_repos_filters_by_phase(tmp_path):
    failed_rows = [
        {"wave_id": 1, "ado_project": "proj", "ado_repo": "repoB", "gh_org": "acme",
         "gh_repo": "repoB", "scope": "repo", "status": "failed", "error_message": "boom"},
        {"wave_id": 1, "ado_project": "proj", "ado_repo": "repoC", "gh_org": "acme",
         "gh_repo": "repoC", "scope": "pipelines", "status": "failed", "error_message": "oops"},
    ]
    out = tmp_path / "failed_repos.txt"
    CSVExporter.export_failed_repos(_FakeDB(failed_rows=failed_rows), str(out), phase="repo")

    assert out.read_text(encoding="utf-8") == "proj/repoB\n"


def test_export_path_helper_creates_missing_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "c.csv"
    CSVExporter.export_migrations(_FakeDB(all_rows=[]), str(nested))
    assert nested.parent.is_dir()
    assert Path(nested).exists()
