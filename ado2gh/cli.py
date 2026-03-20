"""CLI entry point — Click commands for ado2gh v5."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import yaml

from ado2gh.logging_config import console, log
from ado2gh.models import (
    DEFAULT_PHASES, GateStatus, PHASE_ORDER, PhaseType, next_phase,
)


def _load_clients(cfg_global: dict):
    """Initialize ADO + GH clients from env vars or config."""
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.clients.gh_client import GHClient
    from ado2gh.clients.token_manager import TokenManager

    ado_url = os.environ.get("ADO_ORG_URL") or cfg_global.get("ado_org_url", "")
    ado_pat = os.environ.get("ADO_PAT") or cfg_global.get("ado_pat", "")
    if not ado_url or not ado_pat:
        console.print("[red]ADO_ORG_URL + ADO_PAT required[/red]")
        sys.exit(1)

    # Multi-token support: check for GH_TOKEN_1, GH_TOKEN_2, etc.
    token_config = cfg_global.get("gh_token_config", "")
    gh_token_vars = []
    for i in range(1, 20):
        var = f"GH_TOKEN_{i}"
        if os.environ.get(var):
            gh_token_vars.append(var)

    if token_config and Path(token_config).exists():
        tm = TokenManager.from_json_config(token_config)
        log.info(f"Loaded {tm.token_count} tokens from {token_config}")
    elif gh_token_vars:
        tm = TokenManager.from_env(gh_token_vars)
        log.info(f"Loaded {len(gh_token_vars)} GitHub tokens for load balancing")
    else:
        gh_token = os.environ.get("GH_TOKEN") or cfg_global.get("gh_token", "")
        if not gh_token:
            console.print("[red]GH_TOKEN required[/red]")
            sys.exit(1)
        tm = TokenManager.from_single_token(gh_token)

    # Optional: GitHub App auth
    app_id = os.environ.get("GH_APP_ID", "")
    install_id = os.environ.get("GH_APP_INSTALLATION_ID", "")
    key_path = os.environ.get("GH_APP_PRIVATE_KEY_PATH", "")
    if app_id and install_id and key_path:
        tm.configure_app_auth(app_id, install_id, key_path)
        log.info("GitHub App authentication configured")

    return ADOClient(ado_url, ado_pat), GHClient(tm)


def _load_repos(input_path: str, global_cfg: dict,
                waves: list = None) -> list:
    """Load repos from --input file, or fall back to waves in config.

    This is the central helper that all commands use to get the repo list.
    --input takes priority over waves defined in migration_phase.yaml.
    """
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.models import RepoConfig

    if input_path:
        gh_org = global_cfg.get("gh_org", "")
        default_scopes = global_cfg.get("default_scopes", ["repo"])
        repos = ConfigLoader.load_input(input_path, gh_org, default_scopes)
        if not repos:
            console.print(f"[red]No repos found in {input_path}[/red]")
        return repos

    if waves:
        repos = [r for w in waves for r in w.repos]
        if repos:
            return repos

    console.print("[yellow]No repos specified. Use --input <file> or add waves to config.[/yellow]")
    return []


@click.group()
@click.version_option("5.0.0")
def cli():
    """ado2gh v5 — Production-ready ADO to GitHub migration with multi-token,
    risk-based phasing, and post-migration validation."""
    pass


# ── Top-level commands ──────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--output", "-o", default="output/discovery", show_default=True)
def discover(config, output):
    """Scan ADO org and output structured inventory for planning.

    Outputs repos.csv, pipelines.csv, and a repos_template.txt you can
    copy to in/repos.txt and uncomment the repos you want to migrate."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.discovery import DiscoveryScanner
    global_cfg, _ = ConfigLoader.load(config)
    ado, _ = _load_clients(global_cfg)
    DiscoveryScanner(ado).scan(output)


@cli.command()
@click.option("--config", "-c", required=True)
def plan(config):
    """Print migration plan without running."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.state.db import StateDB
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    global_cfg, waves = ConfigLoader.load(config)
    db = StateDB()
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


@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--wave", "-w", type=int, default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
def run(config, wave, dry_run, db):
    """Execute migration wave(s). Idempotent — skips completed scopes."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.migration_engine import MigrationEngine
    from ado2gh.core.wave_runner import WaveRunner
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    engine = MigrationEngine(global_cfg, ado, gh, state, dry_run=dry_run)
    runner = WaveRunner(engine, state)

    targets = [w for w in waves if wave is None or w.wave_id == wave]
    if not targets:
        console.print(f"[red]Wave {wave} not found.[/red]")
        sys.exit(1)

    for w in targets:
        summary = runner.run_wave(w, dry_run=dry_run)
        Reporter(state).print_wave_status(w.wave_id)
        Reporter(state).print_pipeline_status(w.wave_id)
        console.print(
            f"\n[bold]Wave {w.wave_id}:[/bold] "
            f"{summary['completed']} repos completed, {summary['failed']} failed")


@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--wave", "-w", type=int, default=None)
@click.option("--db", default="migration_state.db", show_default=True)
def status(config, wave, db):
    """Show repo + pipeline migration status."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.state.db import StateDB
    ConfigLoader.load(config)
    state = StateDB(db)
    rep = Reporter(state)
    if wave:
        rep.print_wave_status(wave)
        rep.print_pipeline_status(wave)
    else:
        rep.print_all_status()


@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--output", default="migration_report.html", show_default=True)
@click.option("--format", "fmt", default="html",
              type=click.Choice(["html", "json", "csv"]), show_default=True)
@click.option("--db", default="migration_state.db", show_default=True)
def report(config, output, fmt, db):
    """Generate HTML, JSON, or CSV migration report."""
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.reporting.csv_exporter import CSVExporter
    from ado2gh.state.db import StateDB

    state = StateDB(db)
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
@click.option("--scopes", "-s", default=None,
              help="Comma-separated scopes to rollback (e.g., 'branch_policies,pipelines'). "
                   "Omit to rollback everything including repo deletion.")
def rollback(config, wave, dry_run, db, scopes):
    """Rollback migration artifacts — scope-targeted or full wave.

    Without --scopes: deletes GitHub repos and resets all records.
    With --scopes: only rolls back specified scopes (e.g., branch_policies)."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.rollback import RollbackHandler
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    _, gh = _load_clients(global_cfg)
    state = StateDB(db)
    target = next((w for w in waves if w.wave_id == wave), None)
    if not target:
        console.print(f"[red]Wave {wave} not found.[/red]")
        sys.exit(1)

    scope_list = [s.strip() for s in scopes.split(",")] if scopes else None

    if not dry_run:
        if scope_list:
            click.confirm(
                f"Rollback scopes {scope_list} for wave {wave} "
                f"({len(target.repos)} repos)?", abort=True)
        else:
            click.confirm(
                f"DELETE {len(target.repos)} GitHub repos in wave {wave}?", abort=True)

    RollbackHandler(gh, state).rollback_wave(
        target, dry_run=dry_run, scopes=scope_list)


@cli.command("export-failed")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--phase", "-p", default=None)
@click.option("--output", "-o", default="failed_repos.txt", show_default=True)
def export_failed(db, phase, output):
    """Export failed repos as a text file for targeted retries."""
    from ado2gh.reporting.csv_exporter import CSVExporter
    from ado2gh.state.db import StateDB
    state = StateDB(db)
    CSVExporter.export_failed_repos(state, output, phase=phase)
    console.print(f"[green]Failed repos -> {output}[/green]")


@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to validate")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--output", "-o", default="validation_report.csv", show_default=True)
def validate(config, input_file, db, output):
    """Post-migration validation: compare ADO source vs GitHub target.

    Checks commit SHAs, branch counts, workflow presence per repo."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.reporting.post_migration_validator import PostMigrationValidator
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    all_repos = _load_repos(input_file, global_cfg, waves)
    if not all_repos:
        console.print("[red]No repos to validate. Use --input <file>[/red]")
        sys.exit(1)
    validator = PostMigrationValidator(ado, gh, state)
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
    _, gh = _load_clients(global_cfg)
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


# ── `pipelines` subcommand group ───────────────────────────────────────────

@cli.group("pipelines")
def pipelines_group():
    """Pipeline-specific commands (inventory, plan, status, retry-failed)."""
    pass


@pipelines_group.command("inventory")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to scan (text or CSV). If omitted, scans all projects.")
@click.option("--projects", "-p", multiple=True,
              help="Specific ADO projects to scan (alternative to --input)")
@click.option("--no-releases", is_flag=True, default=False)
@click.option("--parallel", default=12, show_default=True)
@click.option("--clear", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
def pipelines_inventory(config, input_file, projects, no_releases, parallel, clear, dry_run, db):
    """Scan ADO pipelines and store metadata in the state DB.

    Use --input to scan only pipelines for specific repos.
    Use --projects to scan entire ADO projects.
    Omit both to scan all projects found in config waves."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.pipelines.inventory import PipelineInventoryBuilder
    from ado2gh.state.db import StateDB
    from rich.table import Table
    from rich import box

    global_cfg, waves = ConfigLoader.load(config)
    ado, _ = _load_clients(global_cfg)
    state = StateDB(db)

    if input_file:
        repos = _load_repos(input_file, global_cfg)
        projects = list({r.ado_project for r in repos})
    elif not projects:
        repos = _load_repos(None, global_cfg, waves)
        projects = list({r.ado_project for r in repos}) if repos else []

    if clear:
        for proj in projects:
            state.clear_inventory(proj)

    builder = PipelineInventoryBuilder(ado, state, parallel=parallel, dry_run=dry_run)
    summary = builder.build_for_projects(list(projects),
                                          include_releases=not no_releases)
    t = Table(title="Pipeline Inventory Summary", box=box.ROUNDED)
    t.add_column("Project", style="cyan")
    t.add_column("Build", justify="right", style="green")
    t.add_column("Release", justify="right", style="blue")
    t.add_column("Total", justify="right", style="bold")
    total = 0
    for proj, counts in summary.items():
        t.add_row(proj, str(counts["build"]), str(counts["release"]),
                  str(counts["total"]))
        total += counts["total"]
    t.add_row("[bold]TOTAL[/bold]", "", "", f"[bold]{total}[/bold]")
    console.print(t)


@pipelines_group.command("plan")
@click.option("--config", "-c", required=True)
@click.option("--wave", "-w", type=int, default=None)
@click.option("--db", default="migration_state.db", show_default=True)
def pipelines_plan(config, wave, db):
    """Show pipeline breakdown per wave (complexity, types, envs)."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.models import PipelineComplexity, PipelineType
    from ado2gh.state.db import StateDB
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    global_cfg, waves = ConfigLoader.load(config)
    state = StateDB(db)
    targets = [w for w in waves if wave is None or w.wave_id == wave]
    for w in targets:
        console.print(Panel(f"[bold]Wave {w.wave_id}: {w.name}[/bold]",
                            border_style="cyan"))
        t = Table(box=box.SIMPLE)
        t.add_column("Repo", style="cyan")
        t.add_column("Total", justify="right")
        t.add_column("Simple", justify="right", style="green")
        t.add_column("Medium", justify="right", style="yellow")
        t.add_column("Complex", justify="right", style="red")
        t.add_column("YAML", justify="right")
        t.add_column("Classic", justify="right")
        t.add_column("Release", justify="right")
        for repo in w.repos:
            pipes = state.get_pipelines_for_repo(repo.ado_project, repo.ado_repo)
            if not pipes:
                t.add_row(repo.ado_repo, "[dim]0[/dim]", *[""] * 6)
                continue
            t.add_row(
                repo.ado_repo, str(len(pipes)),
                str(sum(1 for p in pipes if p.complexity == PipelineComplexity.SIMPLE)),
                str(sum(1 for p in pipes if p.complexity == PipelineComplexity.MEDIUM)),
                str(sum(1 for p in pipes if p.complexity == PipelineComplexity.COMPLEX)),
                str(sum(1 for p in pipes if p.pipeline_type == PipelineType.YAML)),
                str(sum(1 for p in pipes if p.pipeline_type == PipelineType.CLASSIC)),
                str(sum(1 for p in pipes if p.pipeline_type == PipelineType.RELEASE)),
            )
        console.print(t)


@pipelines_group.command("status")
@click.option("--config", "-c", required=True)
@click.option("--wave", "-w", type=int, required=True)
@click.option("--db", default="migration_state.db", show_default=True)
def pipelines_status(config, wave, db):
    """Show per-pipeline migration status for a wave."""
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.state.db import StateDB
    Reporter(StateDB(db)).print_pipeline_status(wave)


@pipelines_group.command("retry-failed")
@click.option("--config", "-c", required=True)
@click.option("--wave", "-w", type=int, required=True)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
def pipelines_retry_failed(config, wave, dry_run, db):
    """Reset failed pipeline migrations in a wave and re-run them."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.migration_engine import MigrationEngine
    from ado2gh.core.wave_runner import WaveRunner
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    target = next((w for w in waves if w.wave_id == wave), None)
    if not target:
        console.print(f"[red]Wave {wave} not found.[/red]")
        sys.exit(1)
    failed = state.get_failed_pipeline_migrations(wave)
    console.print(f"[yellow]{len(failed)} failed pipeline migrations to retry.[/yellow]")
    if not failed:
        return
    if not dry_run:
        state.reset_failed_pipeline_migrations(wave)
    engine = MigrationEngine(global_cfg, ado, gh, state, dry_run=dry_run)
    runner = WaveRunner(engine, state)
    runner.run_wave(target, dry_run=dry_run)
    Reporter(state).print_pipeline_status(wave)


# ── `phase` subgroup ───────────────────────────────────────────────────────

@cli.group("phase")
def phase_group():
    """v5 Phase orchestration: assign / plan / run / gate-check / dashboard."""
    pass


@phase_group.command("assign")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to score/assign (text or CSV). "
                   "This is the primary way to specify which repos to migrate.")
@click.option("--gh-org", default="")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--output", default="migration_phase.yaml", show_default=True)
@click.option("--db", default="migration_state.db", show_default=True)
def phase_assign(config, input_file, gh_org, dry_run, output, db):
    """Score repos and auto-assign to phases by risk score.

    Reads repos from --input file (recommended) or from waves in config.
    Scores each repo on 9 signals, assigns to POC/Pilot/Wave1-3.
    Outputs migration_phase.yaml with risk scores and phase assignments."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.phase.risk_scorer import RiskScorer
    from ado2gh.phase.wave_assigner import WaveAssigner
    from ado2gh.state.db import StateDB
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TimeElapsedColumn)
    from rich.table import Table
    from rich import box

    global_cfg, waves = ConfigLoader.load(config)
    ado, _ = _load_clients(global_cfg)
    state = StateDB(db)
    gh_org = gh_org or global_cfg.get("gh_org", "your-github-org")
    scorer = RiskScorer()
    assigner = WaveAssigner()
    scopes = global_cfg.get("default_scopes", ["repo", "pipelines"])

    # Load repos from input file or config waves
    repos = _load_repos(input_file, global_cfg, waves)
    if not repos:
        console.print("[red]No repos to score. Use --input <file>[/red]")
        sys.exit(1)

    all_repo_keys: dict = {}
    for r in repos:
        key = (r.ado_project, r.ado_repo)
        if key not in all_repo_keys:
            all_repo_keys[key] = {"project": r.ado_project, "repo": r.ado_repo}

    console.print(f"Scoring {len(all_repo_keys)} repos...")
    from ado2gh.models import RiskScore
    all_scores: list[RiskScore] = []

    with Progress(SpinnerColumn(), "[progress.description]{task.description}",
                  MofNCompleteColumn(), BarColumn(), TimeElapsedColumn(),
                  console=console, transient=True) as progress:
        task = progress.add_task("Scoring", total=len(all_repo_keys))
        for (project, repo_name), _ in all_repo_keys.items():
            try:
                ado_repo = ado.get_repo(project, repo_name)
                repo_stats = ado.get_repo_stats(project, ado_repo.get("id", ""))
                commits = ado.get_repo_commits(project, ado_repo.get("id", ""))
                vgs = ado.list_variable_groups(project)
                svc = ado.list_service_connections(project)
                pipelines = state.get_pipelines_for_repo(project, repo_name)
                rs = scorer.score(
                    project=project,
                    repo_meta={"name": repo_name, "size": ado_repo.get("size", 0)},
                    pipelines=pipelines, repo_stats=repo_stats,
                    commits=commits, var_groups=vgs, svc_conns=svc, gh_org=gh_org,
                )
                all_scores.append(rs)
                if not dry_run:
                    state.upsert_risk_score(rs)
            except Exception as e:
                log.warning(f"  Score failed [{repo_name}]: {e}")
                fb = RiskScore(project=project, repo_name=repo_name,
                               gh_org=gh_org, gh_repo=repo_name, total_score=50.0)
                all_scores.append(fb)
                if not dry_run:
                    state.upsert_risk_score(fb)
            finally:
                progress.advance(task)

    assigned = assigner.assign(all_scores, gh_org=gh_org)
    if not dry_run:
        for phase_type, phase_scores in assigned.items():
            for rs in phase_scores:
                rs.assigned_phase = phase_type
                state.upsert_risk_score(rs)

    wave_configs = assigner.to_wave_configs(assigned, scopes, global_cfg)
    out_yaml: dict = {
        "global": {
            "ado_org_url": global_cfg.get("ado_org_url", ""),
            "gh_org": gh_org, "parallel": 4, "pipeline_parallel": 12,
            "default_scopes": scopes,
        },
        "phases": {
            "poc":   {"repo_cap": 10,     "risk_max": 25,  "gate_repo_pct": 0.90, "gate_pipe_pct": 0.80},
            "pilot": {"repo_cap": 100,    "risk_max": 45,  "gate_repo_pct": 0.95, "gate_pipe_pct": 0.90},
            "wave1": {"repo_cap": 500,    "risk_max": 65,  "gate_repo_pct": 0.97, "gate_pipe_pct": 0.95},
            "wave2": {"repo_cap": 1000,   "risk_max": 80,  "gate_repo_pct": 0.98, "gate_pipe_pct": 0.97},
            "wave3": {"repo_cap": 999999, "risk_max": 100, "gate_repo_pct": 0.98, "gate_pipe_pct": 0.97},
        },
        "waves": [],
    }
    for wc in wave_configs:
        out_yaml["waves"].append({
            "wave_id": wc.wave_id, "name": wc.name, "description": wc.description,
            "phase": wc.phase, "parallel": wc.parallel,
            "pipeline_parallel": wc.pipeline_parallel,
            "repos": [
                {"ado_project": r.ado_project, "ado_repo": r.ado_repo,
                 "gh_org": r.gh_org, "gh_repo": r.gh_repo,
                 "risk_score": round(r.risk_score, 2), "scopes": r.scopes}
                for r in wc.repos
            ],
        })

    if not dry_run:
        Path(output).write_text(yaml.dump(out_yaml, default_flow_style=False))
        console.print(f"[green]Phase config -> {output}[/green]")

    t = Table(title="Phase Assignment", box=box.ROUNDED)
    t.add_column("Phase", style="bold")
    t.add_column("Repos", justify="right")
    t.add_column("Risk Min", justify="right")
    t.add_column("Risk Max", justify="right")
    t.add_column("Risk Avg", justify="right")
    for phase_type in PHASE_ORDER:
        ph = assigned.get(phase_type, [])
        if not ph:
            continue
        vals = [s.total_score for s in ph]
        t.add_row(phase_type.value.upper(), str(len(ph)),
                  f"{min(vals):.1f}", f"{max(vals):.1f}",
                  f"{sum(vals) / len(vals):.1f}")
    console.print(t)


@phase_group.command("plan")
@click.option("--config", "-c", required=True)
@click.option("--phase", "-p", default=None,
              type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
@click.option("--db", default="migration_state.db", show_default=True)
def phase_plan(config, phase, db):
    """Show per-phase breakdown: repos, risk bands, gates."""
    from ado2gh.state.db import StateDB
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    state = StateDB(db)
    filter_p = PhaseType(phase) if phase else None
    for phase_type in (PHASE_ORDER if not filter_p else [filter_p]):
        scores = state.get_risk_scores_for_phase(phase_type)
        if not scores:
            continue
        cfg = DEFAULT_PHASES[phase_type]
        vals = [s["total_score"] for s in scores]
        n_pipes = sum(state.inventory_count_for_repo(s["project"], s["repo_name"])
                      for s in scores)
        console.print(Panel(
            f"[bold]{phase_type.value.upper()}[/bold]  "
            f"{len(scores)} repos | cap={cfg.repo_cap} | risk_max={cfg.risk_max}\n"
            f"Risk: min={min(vals):.1f}  max={max(vals):.1f}  "
            f"avg={sum(vals) / len(vals):.1f}\n"
            f"Pipelines: {n_pipes} | batch_size={cfg.batch_size}\n"
            f"Gate:  repo >={cfg.gate_repo_success_pct:.0%}  "
            f"pipeline >={cfg.gate_pipeline_success_pct:.0%}",
            border_style="cyan",
        ))
        t = Table(box=box.SIMPLE)
        t.add_column("Repo", style="cyan", max_width=35)
        t.add_column("Risk", justify="right")
        t.add_column("Pipes", justify="right")
        t.add_column("GH Target", style="green")
        for s in scores[:20]:
            d = json.loads(s.get("score_json", "{}"))
            t.add_row(s["repo_name"][:35], f"{s['total_score']:.1f}",
                      str(d.get("pipeline_count", 0)),
                      f"{s['gh_org']}/{s['gh_repo']}")
        if len(scores) > 20:
            t.add_row(f"... {len(scores) - 20} more", "", "", "")
        console.print(t)


@phase_group.command("run")
@click.option("--config", "-c", required=True)
@click.option("--phase", "-p", required=True,
              type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--force", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
def phase_run(config, phase, dry_run, force, db):
    """Execute a phase with sub-batch checkpointing and gate enforcement."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.migration_engine import MigrationEngine
    from ado2gh.phase.batch_executor import BatchExecutor
    from ado2gh.phase.gate_checker import PhaseGateChecker
    from ado2gh.phase.progress_tracker import ProgressTracker
    from ado2gh.state.db import StateDB
    from rich.panel import Panel

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    phase_t = PhaseType(phase)
    checker = PhaseGateChecker(state)

    prev_idx = PHASE_ORDER.index(phase_t) - 1
    if prev_idx >= 0 and not force:
        prev = PHASE_ORDER[prev_idx]
        if not checker.can_advance(prev):
            console.print(Panel(
                f"[bold red]Gate BLOCKED[/bold red]\n"
                f"Phase '{prev.value}' gate has not passed yet.\n"
                f"Run: [bold]phase gate-check --phase {prev.value}[/bold]\n"
                f"Or: [bold]phase run --phase {phase} --force[/bold]",
                border_style="red",
            ))
            sys.exit(1)

    phase_scores = state.get_risk_scores_for_phase(phase_t)
    total_repos = len(phase_scores)
    total_pipes = sum(state.inventory_count_for_repo(s["project"], s["repo_name"])
                      for s in phase_scores)
    tracker = ProgressTracker(total_repos=max(1, total_repos),
                               total_pipelines=max(1, total_pipes))
    engine = MigrationEngine(global_cfg, ado, gh, state, dry_run=dry_run)
    executor = BatchExecutor(engine, state, tracker)

    summary = executor.execute_phase(phase_t, waves, dry_run=dry_run)

    console.print(Panel(
        f"[bold]Phase {phase.upper()} complete[/bold]\n"
        f"Repos done: {summary['completed']} | Failed: {summary['failed']}\n"
        f"Batches: {summary['batches_run']} run, "
        f"{summary['batches_skipped']} skipped (resume)",
        border_style="green" if summary["failed"] == 0 else "yellow",
    ))

    # Auto-generate failed repos list
    from ado2gh.reporting.csv_exporter import CSVExporter
    CSVExporter.export_failed_repos(state, f"failed_repos_{phase}.txt", phase=phase)

    gate = checker.check(phase_t)
    _print_gate_result(gate, phase)
    if gate.status == GateStatus.PASS:
        np = next_phase(phase_t)
        if np:
            console.print(
                f"\n[green]Gate PASS[/green] Ready for [bold]{np.value.upper()}[/bold]")


@phase_group.command("gate-check")
@click.option("--config", "-c", required=True)
@click.option("--phase", "-p", required=True,
              type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
@click.option("--override", is_flag=True, default=False)
@click.option("--reason", default="")
@click.option("--db", default="migration_state.db", show_default=True)
def phase_gate_check(config, phase, override, reason, db):
    """Check if a phase has met its success thresholds."""
    from ado2gh.phase.gate_checker import PhaseGateChecker
    from ado2gh.state.db import StateDB

    state = StateDB(db)
    phase_t = PhaseType(phase)
    checker = PhaseGateChecker(state)
    if override:
        if not reason:
            console.print("[red]--reason required with --override[/red]")
            sys.exit(1)
        result = checker.override(phase_t, reason)
    else:
        result = checker.check(phase_t)
    _print_gate_result(result, phase)


@phase_group.command("dashboard")
@click.option("--config", "-c", required=True)
@click.option("--db", default="migration_state.db", show_default=True)
def phase_dashboard(config, db):
    """Live dashboard: all phases, gates, batch checkpoints, velocity, ETA."""
    from ado2gh.phase.progress_tracker import ProgressTracker
    from ado2gh.state.db import StateDB
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    state = StateDB(db)
    all_scores = state.get_all_risk_scores()
    total = len(all_scores)

    t = Table(title="Phase Dashboard", box=box.ROUNDED)
    t.add_column("Phase", style="bold", width=8)
    t.add_column("Repos", justify="right", width=7)
    t.add_column("Risk Band", width=12)
    t.add_column("Done", justify="right", style="green", width=6)
    t.add_column("Failed", justify="right", style="red", width=7)
    t.add_column("Gate", width=14)
    t.add_column("Repo %", justify="right", width=8)

    gates = {g["phase"]: g for g in state.get_all_phase_gates()}
    for phase_type in PHASE_ORDER:
        cfg = DEFAULT_PHASES[phase_type]
        ph_sc = [s for s in all_scores if s.get("assigned_phase") == phase_type.value]
        n_r = len(ph_sc)
        if n_r == 0:
            t.add_row(phase_type.value.upper(), "0", f"<={cfg.risk_max:.0f}",
                      "-", "-", "[dim]not assigned[/dim]", "-")
            continue
        vals = [s["total_score"] for s in ph_sc]
        rnames = [s["repo_name"] for s in ph_sc]
        with state._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(DISTINCT ado_repo) cnt FROM migrations "
                "WHERE ado_repo IN ({}) GROUP BY status".format(
                    ",".join("?" * len(rnames))),
                rnames,
            ).fetchall() if rnames else []
        c = sum(r["cnt"] for r in rows if r["status"] == "completed")
        f = sum(r["cnt"] for r in rows if r["status"] == "failed")
        gate = gates.get(phase_type.value)
        if gate:
            gs = gate["status"]
            gcol = "green" if gs == "pass" else "yellow" if gs == "override" else "red"
            gs_s = f"[{gcol}]{gs.upper()}[/{gcol}]"
            rp = f"{gate['repo_success_pct']:.0%}"
        else:
            gs_s = "[dim]unchecked[/dim]"
            rp = "-"
        t.add_row(phase_type.value.upper(), str(n_r),
                  f"{min(vals):.0f}-{max(vals):.0f}", str(c), str(f), gs_s, rp)
    console.print(t)

    # Batch checkpoints
    cps = []
    for pt in PHASE_ORDER:
        cps.extend(state.get_batch_checkpoints(pt))
    if cps:
        bt = Table(title="Batch Checkpoints", box=box.SIMPLE)
        bt.add_column("Phase")
        bt.add_column("Batch", justify="right")
        bt.add_column("Repos", justify="right")
        bt.add_column("Status")
        for cp in cps:
            scol = ("[green]completed[/green]" if cp["status"] == "completed"
                    else "[yellow]running[/yellow]" if cp["status"] == "running"
                    else "[dim]pending[/dim]")
            bt.add_row(cp["phase"], f"{cp['batch_num'] + 1}/{cp['total_batches']}",
                       f"{cp['repos_done']}/{cp['repos_total']}", scol)
        console.print(bt)

    if total > 0:
        tracker = ProgressTracker(total_repos=total, total_pipelines=1)
        snap = tracker.snapshot(state)
        console.print(Panel(
            f"Total: {snap['total_repos']} repos | "
            f"Done: {snap['done_repos']} ({snap['pct_complete']}%) | "
            f"Failed: {snap['failed_repos']} | "
            f"Remaining: {snap['remaining_repos']}",
            title="Progress", border_style="blue",
        ))


# ── ADO-specific commands ──────────────────────────────────────────────────

@cli.command("pipeline-readiness")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to assess")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--output", "-o", default="output/pipeline_readiness.csv", show_default=True)
def pipeline_readiness(config, input_file, db, output):
    """Assess which pipelines can auto-convert vs need manual work.

    Reads repos from --input file or config. Requires pipelines inventory to be built first."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    state = StateDB(db)
    all_repos = _load_repos(input_file, global_cfg, waves) or None
    report = PipelineReadinessReport(state)
    summary = report.generate(repos=all_repos, output_path=output)
    report.print_summary(summary)


@cli.command("service-connections")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos — scans their projects for service connections")
@click.option("--output", "-o", default="output/service_connection_manifest.json",
              show_default=True)
def service_connections(config, input_file, output):
    """Generate service connection migration manifest.

    Scans ADO projects for service connections and maps them to GitHub secrets/OIDC."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.reporting.service_connection_manifest import ServiceConnectionManifest

    global_cfg, waves = ConfigLoader.load(config)
    ado, _ = _load_clients(global_cfg)
    repos = _load_repos(input_file, global_cfg, waves)
    projects = list({r.ado_project for r in repos}) if repos else []
    if not projects:
        console.print("[red]No projects to scan. Use --input <file>[/red]")
        sys.exit(1)
    manifest = ServiceConnectionManifest(ado)
    summary = manifest.generate(projects, output_path=output)
    manifest.print_summary(summary)


@cli.command("ado-cleanup")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to clean up")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--disable-pipelines/--no-disable-pipelines", default=True,
              help="Disable ADO build pipelines for migrated repos")
@click.option("--add-redirect/--no-redirect", default=True,
              help="Push MIGRATION_NOTICE.md to ADO repo")
@click.option("--archive/--no-archive", default=False,
              help="Disable (archive) the ADO repo to prevent further pushes")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--phase", "-p", default=None,
              type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
def ado_cleanup(config, input_file, db, disable_pipelines, add_redirect, archive, dry_run, phase):
    """Post-migration ADO cleanup: disable pipelines, add redirect, archive repos.

    Reads repos from --input file, --phase filter, or config waves.
    Only run AFTER successful migration and validation."""
    from ado2gh.core.ado_cleanup import ADOCleanup
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, _ = _load_clients(global_cfg)
    state = StateDB(db)

    if input_file:
        all_repos = _load_repos(input_file, global_cfg)
    elif phase:
        scores = state.get_risk_scores_for_phase(PhaseType(phase))
        repo_names = {s["repo_name"] for s in scores}
        all_repos = _load_repos(None, global_cfg, waves)
        all_repos = [r for r in all_repos if r.ado_repo in repo_names]
    else:
        all_repos = _load_repos(None, global_cfg, waves)

    if not all_repos:
        console.print("[red]No repos for cleanup. Use --input <file>[/red]")
        sys.exit(1)

    if not dry_run:
        click.confirm(
            f"Run ADO cleanup on {len(all_repos)} repos "
            f"(disable_pipelines={disable_pipelines}, "
            f"redirect={add_redirect}, archive={archive})?",
            abort=True,
        )

    cleanup = ADOCleanup(ado, state, dry_run=dry_run)
    cleanup.cleanup_repos(
        all_repos,
        disable_pipelines=disable_pipelines,
        add_redirect=add_redirect,
        archive_repo=archive,
    )


# ── Helper ─────────────────────────────────────────────────────────────────

def _print_gate_result(result, phase: str):
    from rich.panel import Panel
    cfg = DEFAULT_PHASES[result.phase]
    color = {"pass": "green", "fail": "red", "override": "yellow"}.get(
        result.status.value, "white")
    lines = [
        f"Phase:     {phase.upper()}",
        f"Status:    [{color}]{result.status.value.upper()}[/{color}]",
        f"Repos:     {result.repos_completed}/{result.repos_total} "
        f"({result.repo_success_pct:.1%}) need >={cfg.gate_repo_success_pct:.0%}",
        f"Pipelines: {result.pipelines_completed}/{result.pipelines_total} "
        f"({result.pipeline_success_pct:.1%}) need >={cfg.gate_pipeline_success_pct:.0%}",
    ]
    if result.failures:
        lines += ["", "Failures:"]
        for f in result.failures:
            lines.append(f"  - {f}")
    if result.override_reason:
        lines.append(f"\nOverride: {result.override_reason}")
    console.print(Panel("\n".join(lines), title=f"Gate Check — {phase.upper()}",
                        border_style=color))
