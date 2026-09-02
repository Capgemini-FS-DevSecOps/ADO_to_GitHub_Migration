"""Discover and plan commands."""
from __future__ import annotations

import click

from ado2gh.cli.helpers import load_clients
from ado2gh.logging_config import console
from ado2gh.output_dirs import output_str


def register(cli):
    @cli.command("discover")
    @click.option("--config", "-c", required=True)
    @click.option("--output", "-o", default=lambda: output_str("discovery"),
                  show_default="$ADO2GH_OUTPUT_DIR/discovery")
    def discover_cmd(config, output):
        """Scan ADO org and output structured inventory for planning."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.discovery import DiscoveryScanner
        global_cfg, _ = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        DiscoveryScanner(ado).scan(output)

    @cli.command()
    @click.option("--config", "-c", required=True)
    def plan(config):
        """Print migration plan without running."""
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.state.factory import create_state_db

        global_cfg, waves = ConfigLoader.load(config)
        db = create_state_db()
        for wave in waves:
            console.print(Panel(
                f"[bold]Wave {wave.wave_id}: {wave.name}[/bold]\n{wave.description}\n"
                f"Repos: {len(wave.repos)} | Repo parallel: {wave.parallel} | "
                f"Pipeline parallel: {wave.pipeline_parallel}",
                border_style="cyan",
            ))
            t = Table(box=box.SIMPLE)
            t.add_column("ADO Project")
            t.add_column("ADO Repo")
            t.add_column("GH Target")
            t.add_column("Scopes")
            t.add_column("Pipelines", justify="right")
            for r in wave.repos:
                count = db.inventory_count_for_repo(r.ado_project, r.ado_repo)
                t.add_row(r.ado_project, r.ado_repo,
                          f"{r.gh_org}/{r.gh_repo}", ", ".join(r.scopes), str(count))
            console.print(t)
