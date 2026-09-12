"""Miscellaneous CLI commands."""
from __future__ import annotations

import click

from ado2gh.api.repo_input import load_repos
from ado2gh.cli.helpers import load_clients
from ado2gh.logging_config import console
from ado2gh.output_dirs import output_str


def register(cli: click.Group) -> None:
    """Attach the miscellaneous commands to the top-level CLI group.

    Args:
        cli: The root Click group that these commands are registered on.
    """

    @cli.command("pipeline-readiness")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option(
        "--input", "-i", "input_file", default=None,
        help="File listing the repos to assess. Defaults to the repos in the config waves.",
    )
    @click.option(
        "--output", "-o", default=lambda: output_str("pipeline_readiness.csv"),
        help="Path of the CSV report to write.",
    )
    @click.option(
        "--db", default="migration_state.db", show_default=True,
        help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
             "ignored when ADO2GH_STORAGE_BACKEND selects postgres.",
    )
    def pipeline_readiness(config: str, input_file: str | None, output: str, db: str) -> None:
        """Classify every inventoried pipeline as an automatic, assisted or manual conversion.

        Each pipeline gets an effort estimate so teams can plan the conversion
        work before committing to a migration. The full assessment is written to
        a CSV report and a summary is printed.
        """
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
        from ado2gh.state.factory import create_state_db
        global_cfg, waves = ConfigLoader.load(config)
        repos = load_repos(input_file or "", global_cfg, waves) or None
        report = PipelineReadinessReport(create_state_db(db)).generate(repos=repos, output_path=output)
        console.print(report)

    @cli.command("service-connections")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option(
        "--input", "-i", "input_file", default=None,
        help="File listing the repos to cover. Defaults to the repos in the config waves.",
    )
    @click.option(
        "--output", "-o", default=lambda: output_str("service_connections"),
        help="Directory the generated manifest is written to.",
    )
    def service_connections(config: str, input_file: str | None, output: str) -> None:
        """Generate the ops manifest for the ADO service connections of the selected repos.

        Only the names of a service connection are readable through the ADO API,
        never its credentials, so the connections themselves cannot be migrated.
        The manifest lists the GitHub secrets and the OIDC setup an ops team has
        to create by hand on the target side.
        """
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.service_connection_manifest import ServiceConnectionManifest
        global_cfg, waves = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        repos = load_repos(input_file or "", global_cfg, waves)
        # generate() scans ADO projects, not repos: list_service_connections()
        # takes a project name (previously passed RepoConfig objects here,
        # which would fail encoding into the ADO API URL).
        projects = sorted({r.ado_project for r in repos})
        ServiceConnectionManifest(ado).generate(projects, output)

    @cli.command("ado-cleanup")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option(
        "--input", "-i", "input_file", default=None,
        help="File listing the repos to clean up. Defaults to the repos in the config waves.",
    )
    @click.option(
        "--archive", is_flag=True, default=False,
        help="Also archive the ADO repository once its pipelines are disabled.",
    )
    @click.option(
        "--dry-run", is_flag=True, default=False,
        help="Report what would happen on the ADO side without changing anything.",
    )
    def ado_cleanup(
        config: str, input_file: str | None, *, archive: bool, dry_run: bool,
    ) -> None:
        """Tidy up the ADO side once a migration has landed.

        Disables the ADO pipelines for each repo and pushes a MIGRATION_NOTICE.md
        redirecting readers to the GitHub repository, optionally archiving the ADO
        repository afterwards. Use --dry-run first: it reports every action it
        would take and changes nothing.
        """
        from ado2gh.core.ado_cleanup import ADOCleanup
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.models import ExecutionMode
        global_cfg, waves = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        repos = load_repos(input_file or "", global_cfg, waves)
        ADOCleanup(
            ado, mode=ExecutionMode.from_dry_run(dry_run=dry_run)
        ).cleanup_repos(repos, archive_repo=archive)

    @cli.command("push-workflows")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option(
        "--input", "-i", "input_file", default=None,
        help="File listing the repos to push to. Defaults to the repos in the config waves.",
    )
    @click.option(
        "--workflows-dir", "-d",
        default=lambda: output_str("workflows"),
        show_default="$ADO2GH_OUTPUT_DIR/workflows",
        help="Directory holding the generated workflow YAML to push.",
    )
    @click.option(
        "--branch", default="ado2gh/migrated-workflows", show_default=True,
        help="Branch the workflows are committed to before the pull request is opened.",
    )
    @click.option("--base", default=None, help="Base branch (default: repo default branch)")
    @click.option(
        "--dry-run", is_flag=True, default=False,
        help="Report what would be pushed without creating a branch or pull request.",
    )
    def push_workflows(  # noqa: PLR0913 - six frozen CLI options; no existing config object groups them (exception-register.md)
        config: str,
        input_file: str | None,
        workflows_dir: str,
        branch: str,
        base: str | None,
        *,
        dry_run: bool,
    ) -> None:
        """Push locally generated workflow YAML to GitHub via branch + PR."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.models import ExecutionMode
        from ado2gh.pipelines.push_workflows import push_workflows_for_repos
        from ado2gh.state.factory import create_state_db

        global_cfg, waves = ConfigLoader.load(config)
        _, gh = load_clients(global_cfg)
        repos = load_repos(input_file or "", global_cfg, waves)
        if not repos:
            console.print("[red]No repos. Use --input <file> or configure waves.[/red]")
            return
        # GAP-016: the readiness assessment gates each live push; the state store
        # comes from the configured backend, same as `pipeline-readiness`.
        count = push_workflows_for_repos(
            gh, repos, workflows_dir, branch=branch, base=base,
            mode=ExecutionMode.from_dry_run(dry_run=dry_run),
            db=None if dry_run else create_state_db(),
        )
        console.print(f"\n[green]Pushed workflows for {count} repo(s)[/green]")
