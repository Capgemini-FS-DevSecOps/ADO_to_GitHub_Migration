"""Pipeline inventory and migration commands."""
from __future__ import annotations

import click

from ado2gh.cli.helpers import load_clients
from ado2gh.logging_config import console


def register(cli: click.Group) -> None:
    """Attach the ``pipelines`` command group to the top-level CLI group.

    Args:
        cli: The root Click group that the ``pipelines`` group is registered on.
    """

    @cli.group("pipelines")
    def pipelines_group() -> None:
        """Pipeline inventory, plan, status, retry."""
        pass

    @pipelines_group.command("inventory")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option(
        "--project", "-p", multiple=True,
        help="ADO project to scan. Repeat to scan several; omit to scan every project.",
    )
    @click.option(
        "--parallel", default=12, show_default=True,
        help="How many pipelines to read from ADO at once.",
    )
    @click.option(
        "--db", default="migration_state.db", show_default=True,
        help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
             "ignored when ADO2GH_STORAGE_BACKEND selects postgres.",
    )
    def pipelines_inventory(
        config: str, project: tuple[str, ...], parallel: int, db: str,
    ) -> None:
        """Scan ADO projects and record every pipeline found in the state database.

        Without --project every project in the organisation is scanned. The
        inventory this writes is what `pipeline-readiness` and the pipeline
        migration steps read.
        """
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.core.config_loader import ConfigLoader
        global_cfg, _ = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        projects = list(project) or [p["name"] for p in ado.list_projects()]
        accel = Accelerator(db_path=db)
        summary = accel.inventory(config, projects=projects, parallel=parallel)
        console.print(summary)

    @pipelines_group.command("status")
    # Accepted for consistency with the sibling pipeline commands; the status
    # report is read entirely from --db, so the value is never used here.
    @click.option(
        "--config", "-c", required=True, expose_value=False,
        help="Path to the migration config YAML.",
    )
    @click.option("--wave", "-w", type=int, required=True, help="Wave number to report on.")
    @click.option(
        "--db", default="migration_state.db", show_default=True,
        help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
             "ignored when ADO2GH_STORAGE_BACKEND selects postgres.",
    )
    def pipelines_status(wave: int, db: str) -> None:
        """Print the pipeline migration status for one wave."""
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db
        Reporter(create_state_db(db)).print_pipeline_status(wave)

    @pipelines_group.command("retry-failed")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option("--wave", "-w", type=int, required=True, help="Wave number to retry.")
    @click.option(
        "--dry-run", is_flag=True, default=False,
        help="Report which pipelines would be retried without changing anything.",
    )
    @click.option(
        "--db", default="migration_state.db", show_default=True,
        help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
             "ignored when ADO2GH_STORAGE_BACKEND selects postgres.",
    )
    def pipelines_retry_failed(config: str, wave: int, db: str, *, dry_run: bool) -> None:
        """Re-attempt the pipelines that failed in one wave.

        Their previous failure is cleared before the wave is run again, unless
        this is a dry run, in which case nothing is reset and nothing is pushed.
        """
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.migration_engine import MigrationEngine
        from ado2gh.core.wave_runner import WaveRunner
        from ado2gh.models import ExecutionMode
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
        mode = ExecutionMode.from_dry_run(dry_run=dry_run)
        engine = MigrationEngine(global_cfg, ado, gh, state, mode=mode)
        WaveRunner(engine, state).run_wave(target, mode=mode)
        Reporter(state).print_pipeline_status(wave)
