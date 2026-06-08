"""Miscellaneous CLI commands."""
from __future__ import annotations

import click

from ado2gh.cli.helpers import load_clients, load_repos
from ado2gh.logging_config import console
from ado2gh.output_dirs import output_str


def register(cli):
    @cli.command("pipeline-readiness")
    @click.option("--config", "-c", required=True)
    @click.option("--input", "-i", "input_file", default=None)
    @click.option("--output", "-o", default=lambda: output_str("pipeline_readiness.csv"))
    @click.option("--db", default="migration_state.db", show_default=True)
    def pipeline_readiness(config, input_file, output, db):
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
        from ado2gh.state.factory import create_state_db
        global_cfg, waves = ConfigLoader.load(config)
        repos = load_repos(input_file, global_cfg, waves) or None
        report = PipelineReadinessReport(create_state_db(db)).generate(repos=repos, output_path=output)
        console.print(report)

    @cli.command("service-connections")
    @click.option("--config", "-c", required=True)
    @click.option("--input", "-i", "input_file", default=None)
    @click.option("--output", "-o", default=lambda: output_str("service_connections"))
    def service_connections(config, input_file, output):
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.service_connection_manifest import ServiceConnectionManifest
        global_cfg, waves = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        repos = load_repos(input_file, global_cfg, waves)
        ServiceConnectionManifest(ado).generate(repos, output)

    @cli.command("ado-cleanup")
    @click.option("--config", "-c", required=True)
    @click.option("--input", "-i", "input_file", default=None)
    @click.option("--archive", is_flag=True, default=False)
    @click.option("--dry-run", is_flag=True, default=False)
    def ado_cleanup(config, input_file, archive, dry_run):
        from ado2gh.core.ado_cleanup import ADOCleanup
        from ado2gh.core.config_loader import ConfigLoader
        global_cfg, waves = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        repos = load_repos(input_file, global_cfg, waves)
        ADOCleanup(ado).run(repos, archive=archive, dry_run=dry_run)

    @cli.command("push-workflows")
    @click.option("--config", "-c", required=True)
    @click.option("--input", "-i", "input_file", default=None)
    @click.option(
        "--workflows-dir", "-d",
        default=lambda: output_str("workflows"),
        show_default="$ADO2GH_OUTPUT_DIR/workflows",
    )
    @click.option("--branch", default="ado2gh/migrated-workflows", show_default=True)
    @click.option("--base", default=None, help="Base branch (default: repo default branch)")
    @click.option("--dry-run", is_flag=True, default=False)
    def push_workflows(config, input_file, workflows_dir, branch, base, dry_run):
        """Push locally generated workflow YAML to GitHub via branch + PR."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.tools.push_workflows import push_workflows_for_repos

        global_cfg, waves = ConfigLoader.load(config)
        _, gh = load_clients(global_cfg)
        repos = load_repos(input_file, global_cfg, waves)
        if not repos:
            console.print("[red]No repos. Use --input <file> or configure waves.[/red]")
            return
        count = push_workflows_for_repos(
            gh, repos, workflows_dir, branch=branch, base=base, dry_run=dry_run,
        )
        console.print(f"\n[green]Pushed workflows for {count} repo(s)[/green]")
