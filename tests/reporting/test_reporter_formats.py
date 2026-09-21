"""The three report formats and the reporter's rendering helpers (COV-DRIFT-008).

`ado2gh/reporting/reporter.py` was the lowest-covered module in the package at
15 % — 146 of 171 statements unexercised — and it backs
``ado2gh report --format html|json|csv``, the artefact operators archive and
circulate.

Coverage this thin meant two of the three output formats were never rendered in
the suite at all. Everything here runs against a real SQLite state store in
``tmp_path`` and writes its artefacts there; the Rich console is replaced with
one writing to a string so the status views can be asserted rather than merely
executed.

Note on CA-003: the reporter performs **no** redaction of its own. The tests at
the end pin that as observed behaviour rather than asserting a mask that does
not exist — the gap is carried as a follow-up.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from ado2gh.models import MigrationStatus, PipelineComplexity, PipelineMetadata, PipelineType, RepoConfig
from ado2gh.reporting import reporter as reporter_module
from ado2gh.reporting.reporter import (
    Reporter,
    _colour,
    _count_json_list,
    _fmt_ts,
    _html_complexity_badge,
    _html_pipeline_rows,
    _html_repo_rows,
    _html_status_badge,
)
from ado2gh.state.factory import create_state_db

# Obviously fake, shaped like a real credential so the masking question is real.
FAKE_TOKEN = "ghp_fakereporttokenvalue00000000000001"


def _repo(name: str = "payments") -> RepoConfig:
    return RepoConfig(
        ado_project="Contoso", ado_repo=name, gh_org="fake-gh-org", gh_repo=name,
    )


def _pipeline(name: str = "Payments CI") -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=7,
        pipeline_name=name,
        pipeline_type=PipelineType.YAML,
        project="Contoso",
        repo_name="payments",
        complexity=PipelineComplexity.MEDIUM,
    )


@pytest.fixture
def db(tmp_path):
    """A populated SQLite state store: two repos and one pipeline in wave 1."""
    store = create_state_db(str(tmp_path / "report.db"))
    store.upsert_migration(1, _repo("payments"), "repo", MigrationStatus.COMPLETED)
    store.upsert_migration(
        1, _repo("billing"), "repo", MigrationStatus.FAILED, error="push rejected",
    )
    store.upsert_pipeline_migration(
        1, _pipeline(), _repo(), MigrationStatus.COMPLETED, workflow_file="ci.yml",
    )
    store.upsert_pipeline_inventory(_pipeline())
    return store


@pytest.fixture
def captured(monkeypatch):
    """Replace the module console with one writing to a string."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        reporter_module, "console", Console(file=buffer, width=200, no_color=True),
    )
    return buffer


# --------------------------------------------------------------------------
# _colour
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,colour",
    [
        ("completed", "green"),
        ("in_progress", "yellow"),
        ("pending", "dim"),
        ("failed", "bold red"),
        ("skipped", "cyan"),
        ("rolled_back", "magenta"),
    ],
)
def test_each_known_status_gets_its_own_markup(status, colour):
    assert _colour(status, reporter_module._STATUS_COLOURS) == f"[{colour}]{status}[/{colour}]"


def test_an_unknown_status_falls_back_to_white():
    assert _colour("nonsense", reporter_module._STATUS_COLOURS) == "[white]nonsense[/white]"


@pytest.mark.parametrize(
    "complexity,colour",
    [("simple", "green"), ("medium", "yellow"), ("complex", "bold red")],
)
def test_each_known_complexity_gets_its_own_markup(complexity, colour):
    assert _colour(complexity, reporter_module._COMPLEXITY_COLOURS) == (
        f"[{colour}]{complexity}[/{colour}]"
    )


# --------------------------------------------------------------------------
# _fmt_ts
# --------------------------------------------------------------------------


def test_a_missing_timestamp_renders_as_nothing():
    assert _fmt_ts(None) == ""
    assert _fmt_ts("") == ""


def test_an_iso_timestamp_is_narrowed_to_month_day_and_time():
    assert _fmt_ts("2026-09-13T14:05:09+00:00") == "09-13 14:05"


def test_a_long_unparseable_timestamp_is_truncated_to_sixteen_characters():
    raw = "not-a-timestamp-at-all-really"
    assert _fmt_ts(raw) == raw[:16]
    assert len(_fmt_ts(raw)) == 16


def test_a_short_unparseable_timestamp_is_left_alone():
    assert _fmt_ts("whenever") == "whenever"


# --------------------------------------------------------------------------
# _count_json_list
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "not json", '{"a": 1}', "7", "null"])
def test_anything_that_is_not_a_json_list_counts_zero(raw):
    assert _count_json_list(raw) == 0


def test_a_json_list_is_counted():
    assert _count_json_list(json.dumps(["a", "b", "c"])) == 3
    assert _count_json_list("[]") == 0


# --------------------------------------------------------------------------
# HTML badges
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,colour",
    [
        ("completed", "#22c55e"),
        ("in_progress", "#eab308"),
        ("pending", "#6b7280"),
        ("failed", "#ef4444"),
        ("skipped", "#06b6d4"),
        ("rolled_back", "#a855f7"),
    ],
)
def test_each_status_badge_carries_its_colour_and_its_text(status, colour):
    badge = _html_status_badge(status)
    assert f"background:{colour}" in badge
    assert f">{status}</span>" in badge
    assert badge.startswith("<span")


def test_an_unknown_status_badge_is_grey():
    assert "background:#6b7280" in _html_status_badge("nonsense")


@pytest.mark.parametrize(
    "complexity,colour",
    [("simple", "#22c55e"), ("medium", "#eab308"), ("complex", "#ef4444")],
)
def test_each_complexity_badge_carries_its_colour(complexity, colour):
    assert f"background:{colour}" in _html_complexity_badge(complexity)


def test_an_unknown_complexity_badge_is_grey():
    assert "background:#6b7280" in _html_complexity_badge("epic")


# --------------------------------------------------------------------------
# HTML rows
# --------------------------------------------------------------------------


def test_no_migrations_render_no_rows():
    assert _html_repo_rows([]) == ""
    assert _html_pipeline_rows([]) == ""


def test_one_row_is_rendered_per_migration():
    rows = _html_repo_rows([
        {"wave_id": 1, "ado_project": "Contoso", "ado_repo": "payments",
         "gh_org": "fake-gh-org", "gh_repo": "payments", "scope": "repo",
         "status": "completed"},
        {"wave_id": 2, "ado_project": "Contoso", "ado_repo": "billing",
         "gh_org": "fake-gh-org", "gh_repo": "billing", "scope": "repo",
         "status": "failed"},
    ])
    assert rows.count("<tr>") == 2
    assert "<td>fake-gh-org/payments</td>" in rows
    assert ">completed</span>" in rows


def test_a_long_repo_error_message_is_truncated_to_sixty_characters():
    rows = _html_repo_rows([{
        "wave_id": 1, "ado_project": "C", "ado_repo": "r", "gh_org": "o",
        "gh_repo": "r", "scope": "repo", "status": "failed",
        "error_message": "E" * 200,
    }])
    assert "E" * 60 in rows
    assert "E" * 61 not in rows


def test_a_missing_repo_error_message_renders_as_an_empty_cell():
    rows = _html_repo_rows([{
        "wave_id": 1, "ado_project": "C", "ado_repo": "r", "gh_org": "o",
        "gh_repo": "r", "scope": "repo", "status": "completed",
    }])
    assert "<td class='err'></td>" in rows


def test_one_row_is_rendered_per_pipeline_migration():
    rows = _html_pipeline_rows([{
        "wave_id": 1, "pipeline_name": "Payments CI", "project": "Contoso",
        "repo_name": "payments", "gh_org": "fake-gh-org", "gh_repo": "payments",
        "complexity": "medium", "status": "completed", "workflow_file": "ci.yml",
    }])
    assert rows.count("<tr>") == 1
    assert "<td>Payments CI</td>" in rows
    assert "<td>ci.yml</td>" in rows
    assert ">medium</span>" in rows


def test_a_pipeline_with_no_recorded_complexity_is_shown_as_simple():
    rows = _html_pipeline_rows([{
        "wave_id": 1, "pipeline_name": "CI", "project": "C", "repo_name": "r",
        "gh_org": "o", "gh_repo": "r", "status": "completed",
    }])
    assert ">simple</span>" in rows
    assert "<td></td>" in rows, "a missing workflow file did not render an empty cell"


def test_a_long_pipeline_error_message_is_truncated_to_sixty_characters():
    rows = _html_pipeline_rows([{
        "wave_id": 1, "pipeline_name": "CI", "project": "C", "repo_name": "r",
        "gh_org": "o", "gh_repo": "r", "status": "failed",
        "error_message": "E" * 200,
    }])
    assert "E" * 60 in rows
    assert "E" * 61 not in rows


# --------------------------------------------------------------------------
# generate_html
# --------------------------------------------------------------------------


def test_the_html_report_is_written_and_its_resolved_path_returned(db, tmp_path):
    target = tmp_path / "out" / "migration_report.html"
    returned = Reporter(db).generate_html(str(target))
    assert Path(returned) == target.resolve()
    assert target.is_file()


def test_the_output_directory_is_created_when_it_does_not_exist(db, tmp_path):
    target = tmp_path / "deep" / "nested" / "report.html"
    Reporter(db).generate_html(str(target))
    assert target.is_file()


def test_the_html_report_is_a_complete_document(db, tmp_path):
    target = tmp_path / "report.html"
    Reporter(db).generate_html(str(target))
    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "ADO2GH Migration Report" in html
    assert html.rstrip().endswith("</html>")


def test_the_html_report_counts_the_repos_and_the_pipelines_it_rendered(db, tmp_path):
    target = tmp_path / "report.html"
    Reporter(db).generate_html(str(target))
    html = target.read_text(encoding="utf-8")
    # Two repo migrations, one completed and one failed; one completed pipeline.
    # Every data row carries exactly one error cell; the template's header rows
    # do not, so this counts data rows rather than markup.
    assert "payments" in html
    assert "billing" in html
    assert "Payments CI" in html
    assert html.count("class='err'") == 3


def test_an_empty_state_store_still_produces_a_report(tmp_path):
    empty = create_state_db(str(tmp_path / "empty.db"))
    target = tmp_path / "empty.html"
    Reporter(empty).generate_html(str(target))
    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "<tr>" not in html.split("<tbody>")[-1].split("</tbody>")[0]


def test_the_report_names_when_it_was_generated(db, tmp_path):
    target = tmp_path / "report.html"
    Reporter(db).generate_html(str(target))
    assert "UTC" in target.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The console status views
# --------------------------------------------------------------------------


def test_the_wave_status_view_names_every_repo_of_that_wave(db, captured):
    Reporter(db).print_wave_status(1)
    out = captured.getvalue()
    assert "payments" in out
    assert "billing" in out


def test_the_wave_status_view_of_an_empty_wave_does_not_raise(db, captured):
    Reporter(db).print_wave_status(99)
    assert captured.getvalue() != "", "an empty wave printed nothing at all"


def test_the_pipeline_status_view_names_the_pipeline(db, captured):
    Reporter(db).print_pipeline_status(1)
    assert "Payments CI" in captured.getvalue()


def test_the_pipeline_status_view_of_an_empty_wave_does_not_raise(db, captured):
    Reporter(db).print_pipeline_status(99)


def test_the_all_status_view_summarises_every_wave(db, captured):
    """The cross-wave view is a per-wave summary, not a per-repo listing."""
    Reporter(db).print_all_status()
    out = captured.getvalue()
    assert "All Waves" in out
    assert "50.0%" in out, "one of two repos completed; the success rate is not shown"


def test_the_all_status_view_of_an_empty_store_does_not_raise(tmp_path, captured):
    Reporter(create_state_db(str(tmp_path / "empty2.db"))).print_all_status()


# --------------------------------------------------------------------------
# The three formats the CLI dispatches
# --------------------------------------------------------------------------


@pytest.fixture
def cli_report(tmp_path, monkeypatch):
    """Invoke ``ado2gh report`` against a populated store in ``tmp_path``."""
    from click.testing import CliRunner

    from ado2gh.cli.main import cli

    db_path = tmp_path / "cli_report.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    store = create_state_db(str(db_path))
    store.upsert_migration(1, _repo("payments"), "repo", MigrationStatus.COMPLETED)
    store.upsert_pipeline_inventory(_pipeline())

    config = tmp_path / "migration.yaml"
    config.write_text("global:\n  ado_org_url: https://dev.azure.com/fake\nwaves: []\n")

    def _run(fmt: str, suffix: str):
        output = tmp_path / f"report.{suffix}"
        result = CliRunner().invoke(
            cli,
            ["report", "--config", str(config), "--format", fmt,
             "--output", str(output), "--db", str(db_path)],
        )
        return result, output

    return _run


def test_the_html_format_writes_a_document(cli_report):
    result, output = cli_report("html", "html")
    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_the_json_format_writes_a_parseable_document_with_both_sections(cli_report):
    result, output = cli_report("json", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert set(data) == {"migrations", "pipeline_inventory"}
    assert len(data["migrations"]) == 1
    assert data["migrations"][0]["ado_repo"] == "payments"
    assert len(data["pipeline_inventory"]) == 1


def test_the_csv_format_writes_a_header_and_one_row_per_migration(cli_report):
    import csv

    result, output = cli_report("csv", "csv")
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 1
    assert rows[0]["ado_repo"] == "payments"


def test_an_unknown_format_is_refused_by_the_cli(cli_report):
    result, _ = cli_report("pdf", "pdf")
    assert result.exit_code != 0


# --------------------------------------------------------------------------
# The reporter renders whatever the database holds, with no redaction step of
# its own — observed behaviour, carried here as a tracked gap (CA-003)
# --------------------------------------------------------------------------


def test_a_token_shaped_error_message_reaches_the_html_report_unmasked(tmp_path):
    """`reporter.py` has no redaction step, so whatever the DB holds is rendered.

    The drift report's point is that a report writer never executed cannot
    demonstrate a redaction path runs — and here there is none to run. Pinned as
    observed behaviour so a later fix has a test to flip; recorded as a CA-003
    follow-up rather than changed from a test module.
    """
    store = create_state_db(str(tmp_path / "leak.db"))
    store.upsert_migration(
        1, _repo(), "repo", MigrationStatus.FAILED, error=f"auth failed {FAKE_TOKEN}",
    )
    target = tmp_path / "leak.html"
    Reporter(store).generate_html(str(target))
    html = target.read_text(encoding="utf-8")
    assert FAKE_TOKEN[:40] in html, (
        "the reporter gained a redaction step — flip this test and close the gap"
    )


def test_the_shared_redaction_helper_would_mask_that_same_message():
    """The mask exists and works; the reporter simply never calls it."""
    from ado2gh.audit import redact_text

    masked = redact_text(f"auth failed {FAKE_TOKEN}")
    assert FAKE_TOKEN not in masked
    assert "ghp_***" in masked
