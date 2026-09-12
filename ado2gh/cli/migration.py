"""Commands that execute a migration and report on what it did.

Covers the whole run-and-account-for-it half of the CLI: ``run`` executes the
waves, ``status`` and ``report`` show where they got to, ``validate`` compares
the ADO source against the GitHub target commit by commit, ``rollback`` undoes a
wave or one of its scopes, ``export-failed`` writes the failures out for a
targeted retry, and ``token-status`` shows the GitHub rate-limit headroom the
rest of them depend on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ado2gh.cli.helpers import load_clients, load_repos
from ado2gh.logging_config import console
from ado2gh.output_dirs import output_str


def register(cli: click.Group) -> None:
    """Attach the migration execution and reporting commands to the CLI group.

    Args:
        cli: The root Click group that these commands are registered on.
    """

    @cli.command()
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option("--wave", "-w", type=int, default=None,
                  help="Wave number to run. Omit to run every wave in the config.")
    @click.option("--dry-run", is_flag=True, default=False,
                  help="Report what would be migrated without creating or pushing anything.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    def run(config: str, wave: int | None, db: str, *, dry_run: bool) -> None:
        """Execute migration wave(s). A re-run is not free — read on.

        Nothing is skipped because an earlier run finished it: every scope a
        repo asks for is executed again. What that costs depends on the scope.

        \b
          repo         mirror strategy force-pushes over the GitHub repo again,
                       discarding anything pushed there since; GEI instead
                       skips when the target's HEAD already matches ADO, and
                       stops the repo when it exists with a different HEAD
          pipelines    skips the pipelines this wave already recorded as
                       completed; re-transforms if the workflows are gone
                       from the branch, or if you re-run under a new --wave
          work_items   creates the issues again — one duplicate GitHub issue
                       per ADO work item, every time

        Only the repo scope runs by default. Use --dry-run first if you are
        re-running a wave that partly succeeded.
        """
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.contracts import RunWaveRequest
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db

        global_cfg, waves = ConfigLoader.load(config)
        targets = [w for w in waves if wave is None or w.wave_id == wave]
        if not targets:
            console.print(f"[red]Wave {wave} not found.[/red]")
            sys.exit(1)

        accel = Accelerator(db_path=db)
        for w in targets:
            summary = accel.run_wave(RunWaveRequest(
                config_path=config, wave_id=w.wave_id, dry_run=dry_run, db_path=db,
            ))
            state = create_state_db(db)
            Reporter(state).print_wave_status(w.wave_id)
            Reporter(state).print_pipeline_status(w.wave_id)
            console.print(
                f"\n[bold]Wave {w.wave_id}:[/bold] "
                f"{summary.completed} repos completed, {summary.failed} failed"
            )

    @cli.command()
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option("--wave", "-w", type=int, default=None,
                  help="Wave number to report on. Omit for a summary of every wave.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    def status(config: str, wave: int | None, db: str) -> None:
        """Show repo + pipeline migration status."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db
        ConfigLoader.load(config)
        rep = Reporter(create_state_db(db))
        if wave:
            rep.print_wave_status(wave)
            rep.print_pipeline_status(wave)
        else:
            rep.print_all_status()

    @cli.command()
    # Accepted for consistency with the sibling commands; the report is built
    # entirely from --db, so the value is never used here.
    @click.option("--config", "-c", required=True, expose_value=False,
                  help="Path to the migration config YAML.")
    @click.option("--output", default=lambda: output_str("migration_report.html"),
                  show_default="$ADO2GH_OUTPUT_DIR/migration_report.html",
                  help="Path of the report file to write.")
    @click.option("--format", "fmt", default="html",
                  type=click.Choice(["html", "json", "csv"]), show_default=True,
                  help="Report format to generate.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    def report(output: str, fmt: str, db: str) -> None:
        """Generate HTML, JSON, or CSV migration report."""
        from ado2gh.reporting.csv_exporter import CSVExporter
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.state.factory import create_state_db

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        state = create_state_db(db)
        if fmt == "html":
            Reporter(state).generate_html(output)
        elif fmt == "csv":
            CSVExporter.export_migrations(state, output)
            console.print(f"[green]CSV report -> {output}[/green]")
        else:
            data = {
                "migrations": state.get_all_migrations(),
                "pipeline_inventory": state.get_all_inventory(),
            }
            Path(output).write_text(json.dumps(data, indent=2))
            console.print(f"[green]JSON report -> {output}[/green]")

    @cli.command()
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option("--wave", "-w", type=int, required=True, help="Wave number to roll back.")
    @click.option("--dry-run", is_flag=True, default=False,
                  help="Report what would be undone without deleting or changing anything.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    @click.option("--scopes", "-s", default=None,
                  help="Comma-separated scopes to undo, for example "
                       "branch_policies,pipelines. Omit to roll back the whole wave, "
                       "which deletes the GitHub repositories it created.")
    def rollback(
        config: str, wave: int, db: str, scopes: str | None, *, dry_run: bool,
    ) -> None:
        """Rollback migration artifacts — scope-targeted or full wave."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.rollback import RollbackHandler
        from ado2gh.models import ExecutionMode
        from ado2gh.state.factory import create_state_db

        global_cfg, waves = ConfigLoader.load(config)
        _, gh = load_clients(global_cfg)
        state = create_state_db(db)
        target = next((w for w in waves if w.wave_id == wave), None)
        if not target:
            console.print(f"[red]Wave {wave} not found.[/red]")
            sys.exit(1)
        scope_list = [s.strip() for s in scopes.split(",")] if scopes else None
        if not dry_run:
            msg = (
                f"Rollback scopes {scope_list} for wave {wave}?"
                if scope_list
                else f"DELETE {len(target.repos)} GitHub repos in wave {wave}?"
            )
            click.confirm(msg, abort=True)
        RollbackHandler(gh, state).rollback_wave(
            target,
            mode=ExecutionMode.from_dry_run(dry_run=dry_run),
            scopes=scope_list,
        )

    @cli.command("export-failed")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    @click.option("--phase", "-p", default=None,
                  help="Limit the export to one phase. Omit to export every failure.")
    @click.option("--output", "-o", default=lambda: output_str("failed_repos.txt"),
                  show_default="$ADO2GH_OUTPUT_DIR/failed_repos.txt",
                  help="Path of the text file to write.")
    def export_failed(db: str, phase: str | None, output: str) -> None:
        """Export failed repos as a text file for targeted retries."""
        from ado2gh.reporting.csv_exporter import CSVExporter
        from ado2gh.state.factory import create_state_db
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        CSVExporter.export_failed_repos(create_state_db(db), output, phase=phase)
        console.print(f"[green]Failed repos -> {output}[/green]")

    @cli.command()
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    @click.option("--input", "-i", "input_file", default=None,
                  help="File listing the repos to validate. Defaults to the repos "
                       "in the config waves.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="SQLite state file. Overridden by ADO2GH_SQLITE_PATH; "
                       "ignored when ADO2GH_STORAGE_BACKEND selects postgres.")
    @click.option("--output", "-o", default=lambda: output_str("validation_report.csv"),
                  show_default="$ADO2GH_OUTPUT_DIR/validation_report.csv",
                  help="Path of the CSV validation report to write.")
    def validate(config: str, input_file: str | None, db: str, output: str) -> None:
        """Post-migration validation: compare ADO source vs GitHub target."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.post_migration_validator import PostMigrationValidator
        from ado2gh.state.factory import create_state_db

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        global_cfg, waves = ConfigLoader.load(config)
        ado, gh = load_clients(global_cfg)
        all_repos = load_repos(input_file, global_cfg, waves)
        if not all_repos:
            console.print("[red]No repos to validate. Use --input <file>[/red]")
            sys.exit(1)
        validator = PostMigrationValidator(ado, gh, create_state_db(db))
        results = validator.validate(all_repos, output_path=output)
        validator.print_summary(results)

    @cli.command("token-status")
    @click.option("--config", "-c", required=True, help="Path to the migration config YAML.")
    def token_status(config: str) -> None:
        """Show GitHub token rate limit status."""
        from rich import box
        from rich.table import Table

        from ado2gh.core.config_loader import ConfigLoader

        global_cfg, _ = ConfigLoader.load(config)
        _, gh = load_clients(global_cfg)
        gh.token_manager.check_rate_limits()
        info = gh.token_manager.get_token_status()
        t = Table(title="Token Status", box=box.ROUNDED)
        t.add_column("Type")
        t.add_column("Remaining", justify="right")
        t.add_column("Status")
        for i, tok in enumerate(info["tokens"]):
            t.add_row(
                "App" if tok["is_app"] else f"PAT #{i + 1}",
                str(tok["remaining"]),
                "[green]OK[/green]" if tok["remaining"] > 100 else "[red]LOW[/red]",
            )
        if info["app_configured"]:
            t.add_row("App Auth", "configured", "[green]OK[/green]")
        console.print(t)
