"""Pipeline inventory and migration commands."""
from __future__ import annotations

import click

from ado2gh.cli.helpers import load_clients
from ado2gh.logging_config import console


def register(cli):
    @cli.group("pipelines")
    def pipelines_group():
        """Pipeline inventory, plan, status, retry."""
        pass

    @pipelines_group.command("inventory")
    @click.option("--config", "-c", required=True)
    @click.option("--project", "-p", multiple=True)
    @click.option("--parallel", default=12, show_default=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def pipelines_inventory(config, project, parallel, db):
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.core.config_loader import ConfigLoader
        global_cfg, _ = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        projects = list(project) or [p["name"] for p in ado.list_projects()]
        accel = Accelerator(db_path=db)
        summary = accel.inventory(config, projects=projects, parallel=parallel)
        console.print(summary)

    @pipelines_group.command("status")
    @click.option("--config", "-c", required=True)
    @click.option("--wave", "-w", type=int, required=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def pipelines_status(config, wave, db):
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db
        Reporter(create_state_db(db)).print_pipeline_status(wave)

    @pipelines_group.command("retry-failed")
    @click.option("--config", "-c", required=True)
    @click.option("--wave", "-w", type=int, required=True)
    @click.option("--dry-run", is_flag=True, default=False)
    @click.option("--db", default="migration_state.db", show_default=True)
    def pipelines_retry_failed(config, wave, dry_run, db):
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.migration_engine import MigrationEngine
        from ado2gh.core.wave_runner import WaveRunner
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db
        global_cfg, waves = ConfigLoader.load(config)
        ado, gh = load_clients(global_cfg)
        state = create_state_db(db)
        target = next((w for w in waves if w.wave_id == wave), None)
        if not target:
            console.print(f"[red]Wave {wave} not found.[/red]")
            return
        failed = state.get_failed_pipeline_migrations(wave)
        if not failed:
            console.print("[green]No failed pipelines.[/green]")
            return
        if not dry_run:
            state.reset_failed_pipeline_migrations(wave)
        engine = MigrationEngine(global_cfg, ado, gh, state, dry_run=dry_run)
        WaveRunner(engine, state).run_wave(target, dry_run=dry_run)
        Reporter(state).print_pipeline_status(wave)
