"""The dry-run warning branches of ``ado2gh/api/pipeline_steps.py`` (COV-DRIFT-006).

`pipeline_steps.py` is the largest uncovered module in the package — 453 of its
618 statements missing, 6.7 % of the whole coverage shortfall in one file — and
this feature rewrote 392 of its lines.

This module is deliberately focused on the part of it an operator reads before
committing to a migration: the dry-run warning builders. They are a dense band
of branches (five repository-size bands, a branch-count threshold, per-project
metadata probes, and a truncating formatter) that decide what an operator is
told about an irreversible run, and each branch is reachable from a plain
``PipelineRun`` plus a faked ADO client.

`PipelineStepsMixin` is exercised through a bare subclass because the warning
builders touch no other part of the runner. Every credential-adjacent value here
is a name only, never a secret (CA-003).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.api.pipeline_models import PipelineRun, PipelineStep
from ado2gh.api.pipeline_steps import PipelineStepsMixin, _phase_config_exists
from ado2gh.models import RepoConfig


class _Steps(PipelineStepsMixin):
    """The mixin on its own — the warning builders need no runner state."""


@pytest.fixture
def steps() -> _Steps:
    return _Steps()


def _run(**overrides) -> PipelineRun:
    base = {"id": "run-1", "name": "Dry run"}
    base.update(overrides)
    return PipelineRun(**base)


def _analyze_step(result: dict) -> PipelineStep:
    return PipelineStep(
        id="analyze_deps", label="Analyze", description="", result=result,
    )


def _repo(project: str = "Contoso", repo: str = "payments") -> RepoConfig:
    return RepoConfig(
        ado_project=project, ado_repo=repo, gh_org="fake-gh-org", gh_repo=repo,
    )


def _ado(size_kb: int = 0, *, branch_count: int = 0, policies: list | None = None) -> MagicMock:
    ado = MagicMock()
    ado.get_repo.return_value = {"id": "repo-guid", "size": size_kb}
    ado.get_repo_stats.return_value = {"branch_count": branch_count}
    ado.list_branch_policies.return_value = policies or []
    ado.list_wiki_pages.return_value = []
    ado.list_artifacts.return_value = []
    ado.list_work_items.return_value = []
    return ado


# --------------------------------------------------------------------------
# _dep_note
# --------------------------------------------------------------------------


def test_a_multi_repo_run_gets_no_dependency_note(steps):
    assert steps._dep_note(_run(), [_repo(), _repo(repo="billing")]) == ""


def test_a_single_repo_run_with_no_extra_repos_says_so(steps):
    note = steps._dep_note(_run(repository_id="Contoso/payments"), [_repo()])
    assert note == " (no repo dependencies)"


def test_a_single_repo_run_counts_the_repos_dragged_in_alongside_it(steps):
    repos = [_repo(), _repo(repo="billing"), _repo(repo="ledger")]
    note = steps._dep_note(_run(repository_id="Contoso/payments"), repos)
    assert note == " (2 dependencies)"


def test_the_dependency_count_never_goes_negative(steps):
    assert steps._dep_note(_run(repository_id="Contoso/payments"), []) == (
        " (no repo dependencies)"
    )


# --------------------------------------------------------------------------
# _format_warnings — the ten-line page and its overflow line
# --------------------------------------------------------------------------


def test_no_warnings_formats_to_an_empty_string(steps):
    assert steps._format_warnings([]) == ""


def test_each_warning_is_rendered_as_its_own_bullet(steps):
    assert steps._format_warnings(["one", "two"]) == "  • one\n  • two"


def test_exactly_ten_warnings_are_shown_without_an_overflow_line(steps):
    out = steps._format_warnings([f"w{i}" for i in range(10)])
    assert len(out.splitlines()) == 10
    assert "more" not in out


def test_the_eleventh_warning_becomes_an_overflow_line(steps):
    out = steps._format_warnings([f"w{i}" for i in range(11)])
    lines = out.splitlines()
    assert len(lines) == 11
    assert lines[-1] == "  … and 1 more (see warnings in result data)"
    assert "w10" not in out, "a warning past the page was rendered as well as counted"


def test_the_overflow_line_counts_every_warning_that_was_dropped(steps):
    out = steps._format_warnings([f"w{i}" for i in range(37)])
    assert out.splitlines()[-1].startswith("  … and 27 more")


# --------------------------------------------------------------------------
# _repo_migration_dry_run_warnings — the migration-order note
# --------------------------------------------------------------------------


def test_a_multi_repo_run_gets_no_migration_order_note(steps):
    warnings = steps._repo_migration_dry_run_warnings(_run(), [])
    assert warnings == []


def test_a_single_repo_run_with_no_upstreams_says_so(steps):
    run = _run(
        repository_id="Contoso/payments",
        steps=[_analyze_step({"migration_order": ["Contoso/payments"]})],
    )
    assert steps._repo_migration_dry_run_warnings(run, []) == [
        "No upstream repo dependencies in migration order",
    ]


def test_a_single_repo_run_names_its_upstreams_in_dependency_order(steps):
    run = _run(
        repository_id="Contoso/payments",
        steps=[_analyze_step({
            "migration_order": ["Contoso/shared", "Contoso/ledger", "Contoso/payments"],
        })],
    )
    warnings = steps._repo_migration_dry_run_warnings(run, [])
    assert warnings == [
        "Upstream repos to migrate first (dependency order): "
        "Contoso/shared, Contoso/ledger",
    ]


def test_a_run_with_no_analyze_step_still_reports_no_upstreams(steps):
    run = _run(repository_id="Contoso/payments")
    assert steps._repo_migration_dry_run_warnings(run, []) == [
        "No upstream repo dependencies in migration order",
    ]


# --------------------------------------------------------------------------
# _repo_migration_dry_run_warnings — the five repository-size bands
# --------------------------------------------------------------------------

ONE_MB = 1024
ONE_GB = 1024 * 1024

SIZE_BANDS = [
    (512, "Contoso/payments: Repository size 512 KB"),
    (ONE_MB, "Contoso/payments: Repository size 1.0 MB"),
    (400 * ONE_MB, "Contoso/payments: Repository size 400.0 MB"),
    (int(0.5 * ONE_GB), "Contoso/payments: Repository size 0.50 GB — large repo"),
    (int(1.5 * ONE_GB), "Contoso/payments: Repository size 1.50 GB — large repo"),
    (2 * ONE_GB, "Contoso/payments: Repository size 2.00 GB — GEI recommended"),
    (5 * ONE_GB, "Contoso/payments: Repository size 5.00 GB — GEI recommended"),
    (10 * ONE_GB, "Contoso/payments: Repository size 10.00 GB — manual migration required"),
    (40 * ONE_GB, "Contoso/payments: Repository size 40.00 GB — manual migration required"),
]


@pytest.mark.parametrize(
    "size_kb,expected", SIZE_BANDS, ids=[str(s) for s, _ in SIZE_BANDS],
)
def test_each_repository_size_band_produces_its_own_note(steps, size_kb, expected):
    warnings = steps._repo_migration_dry_run_warnings(
        _run(), [_repo()], ado=_ado(size_kb),
    )
    assert expected in warnings


def test_a_repository_with_no_reported_size_is_reported_as_zero(steps):
    ado = _ado()
    ado.get_repo.return_value = {"id": "repo-guid"}
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert "Contoso/payments: Repository size 0 KB" in warnings


def test_no_size_note_at_all_when_no_ado_client_is_supplied(steps):
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()])
    assert warnings == []


def test_a_failing_repository_lookup_is_reported_rather_than_raised(steps):
    ado = _ado()
    ado.get_repo.side_effect = RuntimeError("ADO unreachable")
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert warnings == [
        "Contoso/payments: Could not read repository stats (ADO unreachable)",
    ]


# --------------------------------------------------------------------------
# _repo_migration_dry_run_warnings — branches and policies
# --------------------------------------------------------------------------


def test_a_repository_at_the_branch_threshold_gets_no_note(steps):
    warnings = steps._repo_migration_dry_run_warnings(
        _run(), [_repo()], ado=_ado(1, branch_count=500),
    )
    assert not any("branches" in w for w in warnings)


def test_a_repository_past_the_branch_threshold_is_flagged(steps):
    warnings = steps._repo_migration_dry_run_warnings(
        _run(), [_repo()], ado=_ado(1, branch_count=501),
    )
    assert "Contoso/payments: 501 branches — expect longer mirror/GEI runtime" in warnings


def test_branch_policies_are_reported_with_their_own_scope(steps):
    warnings = steps._repo_migration_dry_run_warnings(
        _run(), [_repo()], ado=_ado(1, policies=[{"id": 1}, {"id": 2}]),
    )
    assert (
        "Contoso/payments: 2 branch polic(ies) — migrate via branch_policies scope"
        in warnings
    )


def test_no_policy_note_when_the_repository_has_none(steps):
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=_ado(1))
    assert not any("branch polic" in w for w in warnings)


def test_a_failing_policy_read_does_not_lose_the_size_note(steps):
    ado = _ado(1)
    ado.list_branch_policies.side_effect = RuntimeError("policy API down")
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert any("Repository size" in w for w in warnings)
    assert not any("branch polic" in w for w in warnings)


def test_a_repository_with_no_id_skips_the_stats_lookup(steps):
    ado = _ado(1)
    ado.get_repo.return_value = {"size": 1}
    steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert not ado.get_repo_stats.called


# --------------------------------------------------------------------------
# _repo_migration_dry_run_warnings — per-project metadata, probed once
# --------------------------------------------------------------------------


def test_project_metadata_is_probed_once_per_project_not_once_per_repo(steps):
    ado = _ado(1)
    repos = [_repo(repo="payments"), _repo(repo="billing"), _repo("Other", "ledger")]
    steps._repo_migration_dry_run_warnings(_run(), repos, ado=ado)
    assert ado.list_wiki_pages.call_count == 2, (
        "the wiki probe ran once per repo instead of once per project"
    )


def test_a_detected_wiki_is_reported_with_its_migration_scope(steps):
    ado = _ado(1)
    ado.list_wiki_pages.return_value = [{"wiki": {}}, {"wiki": {}}]
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert (
        "Contoso: Wiki detected (2 wiki root(s)) — "
        "migrate via convert_metadata / wiki scope" in warnings
    )


def test_detected_artifact_feeds_are_reported(steps):
    ado = _ado(1)
    ado.list_artifacts.return_value = [{"name": "feed"}]
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert (
        "Contoso: Artifact feeds detected (1) — "
        "plan GitHub Packages or external feed migration" in warnings
    )


def test_present_work_items_are_reported(steps):
    ado = _ado(1)
    ado.list_work_items.return_value = [{"id": 1}]
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert (
        "Contoso: Work items present — migrate via work_items / boards scope" in warnings
    )


def test_the_work_item_probe_asks_for_one_item_only(steps):
    ado = _ado(1)
    steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert ado.list_work_items.call_args.kwargs == {"top": 1}


@pytest.mark.parametrize("probe", ["list_wiki_pages", "list_artifacts", "list_work_items"])
def test_a_failing_metadata_probe_is_swallowed(steps, probe):
    ado = _ado(1)
    getattr(ado, probe).side_effect = RuntimeError("API down")
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], ado=ado)
    assert any("Repository size" in w for w in warnings)


# --------------------------------------------------------------------------
# _repo_migration_dry_run_warnings — unsupported pipeline tasks from the DB
# --------------------------------------------------------------------------


def _db(*task_lists: list[str]) -> MagicMock:
    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [
        MagicMock(unsupported_tasks=tasks) for tasks in task_lists
    ]
    return db


def test_unsupported_pipeline_tasks_are_named_and_deduplicated(steps):
    db = _db(["Vendor@1", "Vendor@1"], ["Other@2"])
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], db=db)
    assert warnings == [
        "Contoso/payments: ADO pipeline tasks/extensions needing GitHub Actions "
        "equivalents: Other@2, Vendor@1",
    ]


def test_at_most_five_unsupported_tasks_are_shown_and_the_rest_are_counted(steps):
    db = _db([f"Task{i}@1" for i in range(8)])
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], db=db)
    assert warnings[0].endswith(
        "Task0@1, Task1@1, Task2@1, Task3@1, Task4@1 (+3 more)",
    )


def test_exactly_five_unsupported_tasks_get_no_overflow_count(steps):
    db = _db([f"Task{i}@1" for i in range(5)])
    warnings = steps._repo_migration_dry_run_warnings(_run(), [_repo()], db=db)
    assert "more)" not in warnings[0]


def test_no_note_when_every_pipeline_converts_cleanly(steps):
    assert steps._repo_migration_dry_run_warnings(_run(), [_repo()], db=_db([])) == []


def test_a_failing_pipeline_lookup_is_swallowed(steps):
    db = MagicMock()
    db.get_pipelines_for_repo.side_effect = RuntimeError("db down")
    assert steps._repo_migration_dry_run_warnings(_run(), [_repo()], db=db) == []


# --------------------------------------------------------------------------
# _pipeline_conversion_dry_run_warnings
# --------------------------------------------------------------------------


def test_no_conversion_warnings_without_an_analyze_step(steps):
    assert steps._pipeline_conversion_dry_run_warnings(_run(), [_repo()]) == []


def test_no_conversion_warnings_when_the_analyze_step_has_no_result(steps):
    run = _run(steps=[PipelineStep(id="analyze_deps", label="A", description="")])
    assert steps._pipeline_conversion_dry_run_warnings(run, [_repo()]) == []


def test_each_service_connection_names_what_must_be_created_on_github(steps):
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {"service_connections": [{"name": "prod-azure"}]},
        },
    })])
    assert steps._pipeline_conversion_dry_run_warnings(run, [_repo()]) == [
        "Contoso/payments: Service connection 'prod-azure' — "
        "create matching GitHub secret or OIDC login",
    ]


def test_each_variable_group_names_what_must_be_created_on_github(steps):
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {"variable_groups": [{"name": "shared-config"}]},
        },
    })])
    assert steps._pipeline_conversion_dry_run_warnings(run, [_repo()]) == [
        "Contoso/payments: Variable group 'shared-config' — "
        "map to GitHub Actions secrets/variables",
    ]


def test_a_bare_string_dependency_is_accepted_as_its_own_name(steps):
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {
                "service_connections": ["prod-azure"],
                "variable_groups": ["shared-config"],
            },
        },
    })])
    warnings = steps._pipeline_conversion_dry_run_warnings(run, [_repo()])
    assert "prod-azure" in warnings[0]
    assert "shared-config" in warnings[1]


def test_an_unnamed_dependency_is_skipped_rather_than_reported_blank(steps):
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {
                "service_connections": [{"id": 1}, {"name": ""}],
                "variable_groups": [{"name": None}],
            },
        },
    })])
    assert steps._pipeline_conversion_dry_run_warnings(run, [_repo()]) == []


def test_a_run_scoped_to_one_repo_reports_only_that_repo(steps):
    run = _run(
        repository_id="Contoso/payments",
        steps=[_analyze_step({
            "dependencies": {
                "Contoso/payments": {"variable_groups": [{"name": "mine"}]},
                "Contoso/billing": {"variable_groups": [{"name": "theirs"}]},
            },
        })],
    )
    warnings = steps._pipeline_conversion_dry_run_warnings(
        run, [_repo(), _repo(repo="billing")],
    )
    assert len(warnings) == 1
    assert "mine" in warnings[0]


def test_an_unscoped_run_reports_every_repo_in_sorted_order(steps):
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {"variable_groups": [{"name": "p"}]},
            "Contoso/billing": {"variable_groups": [{"name": "b"}]},
        },
    })])
    warnings = steps._pipeline_conversion_dry_run_warnings(
        run, [_repo(), _repo(repo="billing")],
    )
    assert [w.split(":")[0] for w in warnings] == ["Contoso/billing", "Contoso/payments"]


def test_a_repo_with_no_recorded_dependencies_contributes_nothing(steps):
    run = _run(steps=[_analyze_step({"dependencies": {}})])
    assert steps._pipeline_conversion_dry_run_warnings(run, [_repo()]) == []


def test_no_credential_value_can_reach_a_conversion_warning(steps):
    """Only the dependency's name is reported, never anything stored with it."""
    run = _run(steps=[_analyze_step({
        "dependencies": {
            "Contoso/payments": {
                "service_connections": [
                    {"name": "prod-azure", "token": "ghp_fakeleakedtoken00000000000001"},
                ],
            },
        },
    })])
    warnings = steps._pipeline_conversion_dry_run_warnings(run, [_repo()])
    assert "ghp_fakeleakedtoken00000000000001" not in "".join(warnings)


# --------------------------------------------------------------------------
# _phase_config_exists — the by-convention sibling file
# --------------------------------------------------------------------------


def test_the_phase_file_is_found_beside_the_migration_config(tmp_path):
    config = tmp_path / "migration.yaml"
    config.write_text("waves: []")
    assert _phase_config_exists(str(config)) is False
    (tmp_path / "migration_phase.yaml").write_text("phases: []")
    assert _phase_config_exists(str(config)) is True


def test_a_config_path_with_no_yaml_suffix_falls_back_to_the_config_itself(tmp_path):
    """``.yml`` has no ``.yaml`` substring, so the substitution is a no-op.

    The lookup then tests the migration config's own path, which exists — so a
    ``.yml`` config is reported as having a phase file when it has none. Pinned
    as observed behaviour; the divergence is carried as a follow-up rather than
    fixed here.
    """
    config = tmp_path / "migration.yml"
    config.write_text("waves: []")
    assert not (tmp_path / "migration_phase.yaml").exists()
    assert _phase_config_exists(str(config)) is True
    assert _phase_config_exists(str(tmp_path / "absent.yml")) is False
