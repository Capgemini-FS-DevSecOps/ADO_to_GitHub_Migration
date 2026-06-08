"""Run, status, report, rollback, validate commands."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ado2gh.cli.helpers import load_clients, load_repos
from ado2gh.logging_config import console
from ado2gh.output_dirs import output_str


def register(cli):
    @cli.command()
    @click.option("--config", "-c", required=True)
    @click.option("--wave", "-w", type=int, default=None)
    @click.option("--dry-run", is_flag=True, default=False)
    @click.option("--db", default="migration_state.db", show_default=True)
    def run(config, wave, dry_run, db):
        """Execute migration wave(s). Idempotent — skips completed scopes."""
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
    @click.option("--config", "-c", required=True)
    @click.option("--wave", "-w", type=int, default=None)
    @click.option("--db", default="migration_state.db", show_default=True)
    def status(config, wave, db):
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
    @click.option("--config", "-c", required=True)
    @click.option("--output", default=lambda: output_str("migration_report.html"),
                  show_default="$ADO2GH_OUTPUT_DIR/migration_report.html")
    @click.option("--format", "fmt", default="html",
                  type=click.Choice(["html", "json", "csv"]), show_default=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def report(config, output, fmt, db):
        """Generate HTML, JSON, or CSV migration report."""
        from ado2gh.reporting.reporter import Reporter
        from ado2gh.reporting.csv_exporter import CSVExporter
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
    @click.option("--config", "-c", required=True)
    @click.option("--wave", "-w", type=int, required=True)
    @click.option("--dry-run", is_flag=True, default=False)
    @click.option("--db", default="migration_state.db", show_default=True)
    @click.option("--scopes", "-s", default=None)
    def rollback(config, wave, dry_run, db, scopes):
        """Rollback migration artifacts — scope-targeted or full wave."""
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.rollback import RollbackHandler
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
        RollbackHandler(gh, state).rollback_wave(target, dry_run=dry_run, scopes=scope_list)

    @cli.command("export-failed")
    @click.option("--db", default="migration_state.db", show_default=True)
    @click.option("--phase", "-p", default=None)
    @click.option("--output", "-o", default=lambda: output_str("failed_repos.txt"),
                  show_default="$ADO2GH_OUTPUT_DIR/failed_repos.txt")
    def export_failed(db, phase, output):
        """Export failed repos as a text file for targeted retries."""
        from ado2gh.reporting.csv_exporter import CSVExporter
        from ado2gh.state.factory import create_state_db
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        CSVExporter.export_failed_repos(create_state_db(db), output, phase=phase)
        console.print(f"[green]Failed repos -> {output}[/green]")

    @cli.command()
    @click.option("--config", "-c", required=True)
    @click.option("--input", "-i", "input_file", default=None)
    @click.option("--db", default="migration_state.db", show_default=True)
    @click.option("--output", "-o", default=lambda: output_str("validation_report.csv"),
                  show_default="$ADO2GH_OUTPUT_DIR/validation_report.csv")
    def validate(config, input_file, db, output):
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
    @click.option("--config", "-c", required=True)
    def token_status(config):
        """Show GitHub token rate limit status."""
        from ado2gh.core.config_loader import ConfigLoader
        from rich.table import Table
        from rich import box

        global_cfg, _ = ConfigLoader.load(config)
        _, gh = load_clients(global_cfg)
        gh.token_manager.check_rate_limits()
        info = gh.token_manager.summary()
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
