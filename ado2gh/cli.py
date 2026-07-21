"""CLI entry point — Click commands for ado2gh v6."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from ado2gh.logging_config import console, log
from ado2gh.models import (
    DEFAULT_PHASES, GateStatus, PHASE_ORDER, PhaseType, next_phase,
)
from ado2gh.output_dirs import output_base, output_str


def _block_legacy_live_mutation(command: str, dry_run: bool) -> None:
    """Keep compatibility previews without bypassing the v6 PEV authority."""
    if not dry_run:
        raise click.ClickException(
            f"Live '{command}' execution is disabled in v6 because it bypasses "
            "immutable plan approval and integrated validation. Use "
            "'ado2gh agent plan' followed by 'ado2gh agent run --approve-plan'. "
            "The legacy command remains available with --dry-run for assessment."
        )


def _verify_approved_plan_context(plan, global_cfg: dict, ado, gh) -> None:
    """Bind privileged follow-up commands to the plan's exact runtime context."""
    from ado2gh.pev.contracts import content_digest
    from ado2gh.pev.planner import (
        _non_secret_config,
        _safe_org_url,
        verify_plan_runtime_context,
    )

    try:
        verify_plan_runtime_context(plan, global_cfg, ado, gh)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    current_source = _safe_org_url(
        str(getattr(ado, "org_url", "") or global_cfg.get("ado_org_url", ""))
    )
    if current_source.casefold() != plan.source_org_url.casefold():
        raise click.ClickException(
            f"Approved plan source {plan.source_org_url} does not match the "
            f"active ADO client {current_source}"
        )
    if content_digest(_non_secret_config(global_cfg)) != plan.config_digest:
        raise click.ClickException(
            "Configuration changed after plan approval; use the exact approved "
            "configuration or generate a new plan"
        )


def _load_clients(cfg_global: dict):
    """Initialize ADO + GH clients from env vars or config."""
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.clients.gh_client import GHClient
    from ado2gh.clients.token_manager import TokenManager

    ado_url = os.environ.get("ADO_ORG_URL") or cfg_global.get("ado_org_url", "")
    allow_inline = bool(cfg_global.get("allow_inline_secrets", False))
    ado_pat = os.environ.get("ADO_PAT", "")
    if not ado_pat and allow_inline:
        ado_pat = cfg_global.get("ado_pat", "")
        if ado_pat:
            log.warning("Using inline ADO credential; environment/secret injection is recommended")
    if not ado_url or not ado_pat:
        console.print(
            "[red]ADO_ORG_URL + ADO_PAT required. Inline credentials are "
            "disabled unless global.allow_inline_secrets=true.[/red]"
        )
        sys.exit(1)

    # Multi-token support: check for GH_TOKEN_1, GH_TOKEN_2, etc.
    token_config = cfg_global.get("gh_token_config", "")
    gh_token_vars = []
    for i in range(1, 20):
        var = f"GH_TOKEN_{i}"
        if os.environ.get(var):
            gh_token_vars.append(var)

    app_id = os.environ.get("GH_APP_ID", "")
    install_id = os.environ.get("GH_APP_INSTALLATION_ID", "")
    key_path = os.environ.get("GH_APP_PRIVATE_KEY_PATH", "")

    if token_config and Path(token_config).exists():
        tm = TokenManager.from_json_config(token_config)
        log.info(f"Loaded {tm.token_count} tokens from {token_config}")
    elif gh_token_vars:
        tm = TokenManager.from_env(gh_token_vars)
        log.info(f"Loaded {len(gh_token_vars)} GitHub tokens for load balancing")
    else:
        gh_token = os.environ.get("GH_TOKEN", "")
        if not gh_token and allow_inline:
            gh_token = cfg_global.get("gh_token", "")
            if gh_token:
                log.warning("Using inline GitHub credential; secret injection is recommended")
        if gh_token:
            tm = TokenManager.from_single_token(gh_token)
        elif app_id and install_id and key_path:
            tm = TokenManager()
        else:
            console.print(
                "[red]GH_TOKEN or complete GitHub App credentials required. "
                "Inline credentials are disabled by default.[/red]"
            )
            sys.exit(1)

    # Optional: GitHub App auth
    if app_id and install_id and key_path:
        tm.configure_app_auth(
            app_id, install_id, key_path,
            api_base=cfg_global.get("gh_api_url", "https://api.github.com"),
        )
        log.info("GitHub App authentication configured")

    return ADOClient(ado_url, ado_pat), GHClient(
        tm,
        base_url=cfg_global.get("gh_api_url", "https://api.github.com"),
        enterprise_slug=cfg_global.get("gh_enterprise_slug", ""),
        api_version=cfg_global.get("gh_api_version", "2022-11-28"),
    )


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
        repos = ConfigLoader.load_input(
            input_path,
            gh_org,
            default_scopes,
            mapping=global_cfg.get("mapping", {}),
            strict=True,
        )
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
@click.version_option("6.0.0")
def cli():
    """ado2gh v6 — Planner–Executor–Validator ADO to GitHub migration with multi-token,
    risk-based phasing, and post-migration validation."""
    pass


# ── Top-level commands ──────────────────────────────────────────────────────

@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--output", "-o", default=lambda: output_str("discovery"),
              show_default="$ADO2GH_OUTPUT_DIR/discovery (default: output/discovery)")
def discover(config, output):
    """Scan ADO org and output structured inventory for planning.

    Outputs repos.csv, pipelines.csv, and a repos_template.txt you can
    copy to in/repos.txt and uncomment the repos you want to migrate."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.discovery import DiscoveryScanner
    global_cfg, _ = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
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
    """Preview legacy migration wave(s); live writes require ``agent run``."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.wave_runner import WaveRunner
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.state.db import StateDB

    _block_legacy_live_mutation("run", dry_run)
    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    runner = WaveRunner(global_cfg, ado, gh, state)

    targets = [w for w in waves if wave is None or w.wave_id == wave]
    if not targets:
        console.print(f"[red]Wave {wave} not found.[/red]")
        sys.exit(1)

    failed_waves = []
    for w in targets:
        summary = runner.run_wave(w, dry_run=dry_run)
        Reporter(state).print_wave_status(w.wave_id)
        Reporter(state).print_pipeline_status(w.wave_id)
        console.print(
            f"\n[bold]Wave {w.wave_id}:[/bold] "
            f"{summary['completed']} repos completed, {summary['failed']} failed")
        if not dry_run and summary["status"] != "completed":
            failed_waves.append(w.wave_id)
    if failed_waves:
        raise click.ClickException(
            f"Migration did not complete for wave(s): {failed_waves}"
        )


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
@click.option("--output", default=lambda: output_str("migration_report.html"),
              show_default="$ADO2GH_OUTPUT_DIR/migration_report.html")
@click.option("--format", "fmt", default="html",
              type=click.Choice(["html", "json", "csv"]), show_default=True)
@click.option("--db", default="migration_state.db", show_default=True)
def report(config, output, fmt, db):
    """Generate HTML, JSON, or CSV migration report."""
    from ado2gh.reporting.reporter import Reporter
    from ado2gh.reporting.csv_exporter import CSVExporter
    from ado2gh.state.db import StateDB

    Path(output).parent.mkdir(parents=True, exist_ok=True)
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
@click.option(
    "--wave", "-w", type=int, default=None,
    help="Legacy dry-run wave; live PEV rollback derives the plan wave id",
)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--scopes", "-s", default=None,
              help="Comma-separated scopes to rollback (e.g., 'branch_policies,pipelines'). "
                   "Omit to rollback everything including repo deletion.")
@click.option("--plan", "plan_path", default="", type=click.Path(exists=True),
              help="Approved PEV plan; required for live rollback")
@click.option("--run-id", default="",
              help="Terminal PEV run bound to the approved plan")
@click.option("--approval-ticket", default="",
              help="External change/incident ticket; required for live rollback")
def rollback(config, wave, dry_run, db, scopes, plan_path, run_id,
             approval_ticket):
    """Rollback migration artifacts — scope-targeted or full wave.

    Without --scopes: deletes GitHub repos and resets all records.
    With --scopes: only rolls back specified scopes (e.g., branch_policies)."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.rollback import RollbackHandler
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)
    target = None
    rollback_handler = None
    destructive_capability_id = ""

    scope_list = [s.strip() for s in scopes.split(",")] if scopes else None
    if scope_list:
        from ado2gh.models import MigrationScope
        allowed_scopes = {item.value for item in MigrationScope}
        unknown_scopes = set(scope_list) - allowed_scopes
        if unknown_scopes:
            raise click.ClickException(
                f"Unknown rollback scope(s): {', '.join(sorted(unknown_scopes))}"
            )

    if not dry_run:
        if not plan_path or not run_id or not approval_ticket.strip():
            raise click.ClickException(
                "Live rollback requires --plan, --run-id, and --approval-ticket"
            )
        from ado2gh.pev.contracts import MigrationPlan
        from ado2gh.pev.executor import plan_wave_id
        from ado2gh.models import WaveConfig
        plan = MigrationPlan.from_dict(
            json.loads(Path(plan_path).read_text(encoding="utf-8"))
        )
        _verify_approved_plan_context(plan, global_cfg, ado, gh)
        approved_wave_id = plan_wave_id(plan)
        if wave is not None and wave != approved_wave_id:
            raise click.ClickException(
                f"Live rollback wave must be the approved plan wave id "
                f"{approved_wave_id}, not {wave}"
            )
        wave = approved_wave_id
        pev_run = state.get_pev_run(run_id)
        if not pev_run or pev_run.get("plan_id") != plan.plan_id:
            raise click.ClickException("Rollback run and approved plan do not match")
        if pev_run.get("config_digest") != plan.config_digest:
            raise click.ClickException(
                "Rollback run configuration does not match the approved plan"
            )
        if pev_run.get("status") not in {
            "completed", "failed", "needs_review", "executed"
        }:
            raise click.ClickException(
                f"Rollback requires a terminal PEV run; observed {pev_run.get('status')}"
            )
        if scope_list:
            approved_scopes = {
                scope for repo in plan.repositories for scope in repo.scopes
            }
            outside = set(scope_list) - approved_scopes
            if outside:
                rendered = ", ".join(sorted(outside))
                raise click.ClickException(
                    f"Rollback scopes are outside the approved plan: {rendered}"
                )
        target = WaveConfig(
            wave_id=approved_wave_id,
            name=f"PEV rollback {plan.plan_id}",
            description="Targets derived exclusively from the approved plan",
            repos=[repo.to_repo_config() for repo in plan.repositories],
        )
        if not target.repos:
            raise click.ClickException(
                "Approved plan contains no rollback targets"
            )
        if scope_list:
            click.confirm(
                f"Rollback scopes {scope_list} for wave {wave} "
                f"({len(target.repos)} repos)?", abort=True)
        else:
            click.confirm(
                f"DELETE {len(target.repos)} GitHub repos in wave {wave}?", abort=True)
        rollback_handler = RollbackHandler(
            gh,
            state,
            authorized_plan_id=plan.plan_id,
            authorized_run_id=run_id,
        )
        destructive_request = rollback_handler.build_capability_request(
            target, scope_list
        )
        destructive_capability_id = state.authorize_pev_destructive_capability(
            plan.plan_id,
            run_id,
            RollbackHandler.DESTRUCTIVE_OPERATION_KIND,
            destructive_request,
            approval={
                "approval_ticket": approval_ticket.strip(),
                "actor": os.environ.get("USERNAME")
                or os.environ.get("USER", "unknown"),
                "confirmed": True,
            },
        )
        rollback_handler.destructive_capability_id = destructive_capability_id
        state.record_validation_evidence(
            run_id,
            "rollback_approval",
            "pass",
            evidence={
                "approval_ticket": approval_ticket.strip(),
                "plan_id": plan.plan_id,
                "wave_id": wave,
                "scopes": scope_list or ["all"],
                "targets": sorted(f"{r.gh_org}/{r.gh_repo}" for r in target.repos),
                "destructive_capability_id": destructive_capability_id,
            },
        )
    else:
        if wave is None:
            raise click.ClickException("Legacy rollback dry-run requires --wave")
        target = next((w for w in waves if w.wave_id == wave), None)
        if not target:
            console.print(f"[red]Wave {wave} not found.[/red]")
            sys.exit(1)

    rollback_handler = rollback_handler or RollbackHandler(
        gh,
        state,
        authorized_plan_id=plan.plan_id if not dry_run else "",
        authorized_run_id=run_id if not dry_run else "",
        destructive_capability_id=destructive_capability_id,
    )
    rollback_result = rollback_handler.rollback_wave(
        target, dry_run=dry_run, scopes=scope_list)
    if rollback_result.get("errors"):
        raise click.ClickException(
            f"Rollback finished with {rollback_result['errors']} error(s)"
        )
    if not dry_run:
        state.record_validation_evidence(
            run_id,
            "rollback_result",
            "pass",
            evidence={**rollback_result, "approval_ticket": approval_ticket.strip()},
        )
        state.upsert_pev_run(
            run_id,
            plan.plan_id,
            status="needs_review",
            config_digest=plan.config_digest,
            summary={
                "rollback": rollback_result,
                "approval_ticket": approval_ticket.strip(),
            },
        )


@cli.command("export-failed")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--phase", "-p", default=None)
@click.option("--output", "-o", default=lambda: output_str("failed_repos.txt"),
              show_default="$ADO2GH_OUTPUT_DIR/failed_repos.txt")
def export_failed(db, phase, output):
    """Export failed repos as a text file for targeted retries."""
    from ado2gh.reporting.csv_exporter import CSVExporter
    from ado2gh.state.db import StateDB
    state = StateDB(db)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    CSVExporter.export_failed_repos(state, output, phase=phase)
    console.print(f"[green]Failed repos -> {output}[/green]")


@cli.command()
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to validate")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--output", "-o", default=lambda: output_str("validation_report.csv"),
              show_default="$ADO2GH_OUTPUT_DIR/validation_report.csv")
def validate(config, input_file, db, output):
    """Post-migration validation: compare ADO source vs GitHub target.

    Checks commit SHAs, branch counts, workflow presence per repo."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.reporting.post_migration_validator import PostMigrationValidator
    from ado2gh.state.db import StateDB

    Path(output).parent.mkdir(parents=True, exist_ok=True)
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
    if any(result.get("overall") == "FAIL" for result in results):
        raise click.ClickException("Post-migration validation failed")


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


# ── Planner–Executor–Validator agent ───────────────────────────────────────

@cli.group("agent")
def agent_group():
    """Immutable-plan organisation migration with integrated validation."""
    pass


@agent_group.command("plan")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Optional repo selection; omit to plan the full ADO organisation")
@click.option("--scopes", default="",
              help="Optional comma-separated scope override")
@click.option("--output", "-o", default=lambda: output_str("pev_plan.json"),
              show_default="$ADO2GH_OUTPUT_DIR/pev_plan.json")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--planner-approval-envelope", default="", type=click.Path(exists=True),
              help="Externally signed Ed25519 planner approval (strict governance)")
def agent_plan(config, input_file, scopes, output, db,
               planner_approval_envelope):
    """Discover sources and create a content-addressed plan for approval."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.pev.planner import MigrationPlanner
    from ado2gh.state.db import StateDB
    from ado2gh.governance import load_approval_envelope

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    repos = (
        ConfigLoader.load_input(
            input_file,
            global_cfg.get("gh_org", ""),
            global_cfg.get("default_scopes", ["repo"]),
            mapping=global_cfg.get("mapping", {}),
            strict=True,
        )
        if input_file else (
            [repo for wave in waves for repo in wave.repos]
            if waves else None
        )
    )
    scope_list = [item.strip() for item in scopes.split(",") if item.strip()] or None
    state = StateDB(db)
    plan = MigrationPlanner(ado, global_cfg, gh=gh, db=state).create_plan(
        repos=repos,
        scopes=scope_list,
        planner_approval_envelope=(
            load_approval_envelope(planner_approval_envelope)
            if planner_approval_envelope else None
        ),
    )
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(out.suffix + ".tmp")
    temp.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    temp.replace(out)
    console.print(
        f"[green]Plan written:[/green] {out}\n"
        f"[bold]plan_id:[/bold] {plan.plan_id}\n"
        f"Repositories: {len(plan.repositories)} | Tasks: {len(plan.tasks)}"
    )
    console.print(
        "Review the plan, then execute with "
        f"[bold]ado2gh agent run --plan {out} --approve-plan {plan.plan_id}[/bold]"
    )


@agent_group.command("run")
@click.option("--config", "-c", required=True)
@click.option("--plan", "plan_path", required=True, type=click.Path(exists=True))
@click.option("--approve-plan", default="",
              help="Exact plan_id reviewed by the operator; required for live writes")
@click.option("--approval-envelope", default="", type=click.Path(exists=True),
              help="Externally signed Ed25519 plan-execution approval (strict governance)")
@click.option("--resume", "run_id", default="",
              help="Resume an existing PEV run id")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--output", "-o", default=lambda: output_str("pev_validation.csv"),
              show_default="$ADO2GH_OUTPUT_DIR/pev_validation.csv")
def agent_run(config, plan_path, approve_plan, approval_envelope, run_id,
              dry_run, db, output):
    """Execute exactly an approved plan, validate, and apply bounded repairs."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.pev.contracts import MigrationPlan
    from ado2gh.pev.orchestrator import MigrationOrchestrator
    from ado2gh.state.db import StateDB
    from ado2gh.governance import load_approval_envelope

    global_cfg, _ = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    plan = MigrationPlan.from_dict(
        json.loads(Path(plan_path).read_text(encoding="utf-8"))
    )
    _verify_approved_plan_context(plan, global_cfg, ado, gh)
    outcome = MigrationOrchestrator(
        global_cfg, ado, gh, StateDB(db)
    ).run(
        plan,
        approved_plan_id=approve_plan,
        dry_run=dry_run,
        run_id=run_id or None,
        output_path=None if dry_run else output,
        approval_envelope=(
            load_approval_envelope(approval_envelope)
            if approval_envelope else None
        ),
    )
    console.print(
        f"[bold]PEV run:[/bold] {outcome.run_id}\n"
        f"[bold]Plan:[/bold] {outcome.plan_id}\n"
        f"[bold]Status:[/bold] {outcome.status}\n"
        f"Repair attempts: {outcome.repair_attempts}"
    )
    if outcome.status not in {"completed", "dry_run_passed"}:
        raise click.ClickException(
            f"PEV run requires attention (status={outcome.status})"
        )


@agent_group.command("validate")
@click.option("--config", "-c", required=True)
@click.option("--plan", "plan_path", required=True, type=click.Path(exists=True))
@click.option("--run-id", required=True)
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--output", "-o", default=lambda: output_str("pev_validation.csv"),
              show_default="$ADO2GH_OUTPUT_DIR/pev_validation.csv")
def agent_validate(config, plan_path, run_id, db, output):
    """Re-run validators for a previously executed immutable plan."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.pev.contracts import MigrationPlan
    from ado2gh.pev.validator import PEVValidator
    from ado2gh.state.db import StateDB

    global_cfg, _ = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    plan = MigrationPlan.from_dict(
        json.loads(Path(plan_path).read_text(encoding="utf-8"))
    )
    _verify_approved_plan_context(plan, global_cfg, ado, gh)
    report = PEVValidator(ado, gh, StateDB(db), global_cfg).validate(
        plan, run_id, output_path=output
    )
    console.print(
        f"Validation: [bold]{report.status}[/bold] | "
        f"failures={len(report.failures)} warnings={len(report.warnings)}"
    )
    if not report.passed:
        raise click.ClickException(
            f"PEV validation requires attention (status={report.status})"
        )


@agent_group.command("status")
@click.option("--run-id", default="")
@click.option("--db", default="migration_state.db", show_default=True)
def agent_status(run_id, db):
    """Show durable PEV runs and task state."""
    from ado2gh.state.db import StateDB
    from rich.table import Table

    state = StateDB(db)
    runs = [state.get_pev_run(run_id)] if run_id else state.list_pev_runs(limit=50)
    runs = [item for item in runs if item]
    table = Table(title="PEV Migration Runs")
    table.add_column("Run")
    table.add_column("Plan")
    table.add_column("Status")
    table.add_column("Updated")
    for item in runs:
        table.add_row(
            item.get("run_id", ""), item.get("plan_id", ""),
            item.get("status", ""),
            item.get("updated_at", item.get("completed_at", "")),
        )
    console.print(table)
    if run_id and runs:
        tasks = state.list_pev_tasks(run_id)
        counts = {}
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        console.print("Tasks: " + ", ".join(
            f"{status}={count}" for status, count in sorted(counts.items())
        ))


@agent_group.command("release-quarantine")
@click.option("--config", "-c", required=True)
@click.option("--plan", "plan_path", required=True, type=click.Path(exists=True))
@click.option("--run-id", required=True)
@click.option("--target-org", required=True)
@click.option("--target-repo", required=True)
@click.option("--approval-ticket", required=True)
@click.option("--db", default="migration_state.db", show_default=True)
def agent_release_quarantine(
    config,
    plan_path,
    run_id,
    target_org,
    target_repo,
    approval_ticket,
    db,
):
    """Release a target fence after an operator reconciles an uncertain write."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.pev.contracts import MigrationPlan
    from ado2gh.state.db import StateDB

    global_cfg, _ = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    plan = MigrationPlan.from_dict(
        json.loads(Path(plan_path).read_text(encoding="utf-8"))
    )
    _verify_approved_plan_context(plan, global_cfg, ado, gh)
    target_key = f"{target_org}/{target_repo}".casefold()
    if target_key not in {
        repo.target_key.casefold() for repo in plan.repositories
    }:
        raise click.ClickException(
            "Requested quarantine target is not present in the approved plan"
        )
    state = StateDB(db)
    run = state.get_pev_run(run_id)
    if not run or run.get("plan_id") != plan.plan_id:
        raise click.ClickException("Run and approved plan do not match")
    if run.get("config_digest") != plan.config_digest:
        raise click.ClickException(
            "Run configuration does not match the approved plan"
        )
    observed_repo_id = ""
    if gh.repo_exists(target_org, target_repo):
        metadata = gh.get_repo(target_org, target_repo)
        observed_repo_id = str(
            metadata.get("node_id") or metadata.get("id") or ""
        ).strip()
        if not observed_repo_id:
            raise click.ClickException(
                "Live target has no immutable repository identity"
            )
    click.confirm(
        f"Release quarantine for {target_org}/{target_repo} after external "
        f"reconciliation under {approval_ticket.strip()}?",
        abort=True,
    )
    try:
        evidence = state.release_pev_target_quarantine(
            target_org,
            target_repo,
            plan_id=plan.plan_id,
            run_id=run_id,
            approval_ticket=approval_ticket.strip(),
            observed_target_repo_id=observed_repo_id,
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(
        f"[green]Released target quarantine:[/green] "
        f"{evidence['target']} (fencing token {evidence['fencing_token']})"
    )


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
    ado, gh = _load_clients(global_cfg)
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

    _block_legacy_live_mutation("pipelines retry-failed", dry_run)
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
    runner = WaveRunner(global_cfg, ado, gh, state)
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

    # Preserve per-repo gh_org/gh_repo overrides (e.g. from the
    # project/repo::gh_org/gh_repo text input syntax) by keying the first
    # RepoConfig per (project, repo) and applying its target back onto the
    # RiskScore after scoring.
    all_repo_cfgs: dict = {}
    for r in repos:
        key = (r.ado_project, r.ado_repo)
        if key not in all_repo_cfgs:
            all_repo_cfgs[key] = r

    console.print(f"Scoring {len(all_repo_cfgs)} repos...")
    from ado2gh.models import RiskScore
    all_scores: list[RiskScore] = []

    with Progress(SpinnerColumn(), "[progress.description]{task.description}",
                  MofNCompleteColumn(), BarColumn(), TimeElapsedColumn(),
                  console=console, transient=True) as progress:
        task = progress.add_task("Scoring", total=len(all_repo_cfgs))
        for (project, repo_name), repo_cfg in all_repo_cfgs.items():
            target_org = repo_cfg.gh_org or gh_org
            target_repo = repo_cfg.gh_repo or repo_name
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
                    commits=commits, var_groups=vgs, svc_conns=svc, gh_org=target_org,
                )
                rs.gh_org = target_org
                rs.gh_repo = target_repo
                all_scores.append(rs)
                if not dry_run:
                    state.upsert_risk_score(rs)
            except Exception as e:
                log.warning(f"  Score failed [{repo_name}]: {e}")
                fb = RiskScore(project=project, repo_name=repo_name,
                               gh_org=target_org, gh_repo=target_repo, total_score=50.0)
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
    """Preview a legacy phase; live writes require the PEV agent commands."""
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.core.migration_engine import MigrationEngine
    from ado2gh.phase.batch_executor import BatchExecutor
    from ado2gh.phase.gate_checker import PhaseGateChecker
    from ado2gh.phase.progress_tracker import ProgressTracker
    from ado2gh.state.db import StateDB
    from rich.panel import Panel

    _block_legacy_live_mutation("phase run", dry_run)
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

    # Auto-generate failed repos list under the configured output base so
    # the project root stays clean across runs.
    from ado2gh.reporting.csv_exporter import CSVExporter
    failed_path = output_base() / f"failed_repos_{phase}.txt"
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    CSVExporter.export_failed_repos(state, str(failed_path), phase=phase)

    gate = checker.check(phase_t)
    _print_gate_result(gate, phase)
    if gate.status == GateStatus.PASS:
        np = next_phase(phase_t)
        if np:
            console.print(
                f"\n[green]Gate PASS[/green] Ready for [bold]{np.value.upper()}[/bold]")
    if summary["failed"] or gate.status == GateStatus.FAIL:
        raise click.ClickException(
            f"Phase {phase} did not satisfy its production gate"
        )


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
    if result.status == GateStatus.FAIL:
        raise click.ClickException(f"Phase {phase} gate failed")


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
@click.option("--output", "-o", default=lambda: output_str("pipeline_readiness.csv"),
              show_default="$ADO2GH_OUTPUT_DIR/pipeline_readiness.csv")
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
@click.option("--output", "-o",
              default=lambda: output_str("service_connection_manifest.json"),
              show_default="$ADO2GH_OUTPUT_DIR/service_connection_manifest.json")
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


@cli.command("push-workflows")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos. The local YAML at "
                   "output/workflows/{gh_org}/{gh_repo}/.github/workflows/ "
                   "is committed to the destination GitHub repo on a new branch.")
@click.option("--branch", default="ado2gh/migrated-workflows", show_default=True,
              help="Branch name to create on the destination repo")
@click.option("--base", default=None,
              help="Base branch to fork from (defaults to repo default branch)")
@click.option("--pr-title", default="Add migrated GitHub Actions workflows",
              show_default=True)
@click.option("--workflows-dir", default=lambda: output_str("workflows"),
              show_default="$ADO2GH_OUTPUT_DIR/workflows",
              help="Local root that holds the generated workflow tree")
@click.option("--dry-run", is_flag=True, default=False,
              help="List what would be pushed without making any GitHub writes")
def push_workflows(config, input_file, branch, base, pr_title,
                    workflows_dir, dry_run):
    """Commit generated workflow YAML to destination repos as a PR.

    The migration engine writes generated GitHub Actions YAML to the local
    filesystem only; this command takes that output and lands it on the
    destination repo via a feature branch + PR so the team can review.
    """
    _block_legacy_live_mutation("push-workflows", dry_run)
    import base64
    import requests
    from ado2gh.core.config_loader import ConfigLoader

    global_cfg, waves = ConfigLoader.load(config)
    _, gh = _load_clients(global_cfg)
    repos = _load_repos(input_file, global_cfg, waves)
    if not repos:
        console.print("[red]No repos. Use --input <file>[/red]")
        sys.exit(1)

    pushed_repos = 0
    for r in repos:
        wf_root = (Path(workflows_dir) / r.gh_org / r.gh_repo
                   / ".github" / "workflows")
        if not wf_root.exists():
            console.print(f"[yellow]skip {r.gh_org}/{r.gh_repo}: "
                          f"no local workflows at {wf_root}[/yellow]")
            continue
        wf_files = sorted(list(wf_root.glob("*.yml"))
                          + list(wf_root.glob("*.yaml")))
        if not wf_files:
            console.print(f"[yellow]skip {r.gh_org}/{r.gh_repo}: "
                          f"no .yml files in {wf_root}[/yellow]")
            continue

        console.print(f"\n[bold]{r.gh_org}/{r.gh_repo}[/bold] "
                      f"({len(wf_files)} workflow(s))")
        for f in wf_files:
            console.print(f"  - {f.name}")
        if dry_run:
            console.print("[yellow]  [DRY RUN] not pushed[/yellow]")
            continue

        try:
            base_branch = base or gh.get_default_branch(r.gh_org, r.gh_repo)
            try:
                base_sha = gh.get_branch_sha(r.gh_org, r.gh_repo, base_branch)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    console.print(
                        f"[red]  failed: destination repo "
                        f"{r.gh_org}/{r.gh_repo} is empty (no commits on "
                        f"{base_branch}). Run 'phase run' first to mirror "
                        f"the source code, or push an initial commit "
                        f"manually before retrying.[/red]"
                    )
                    continue
                raise
            try:
                gh.create_branch(r.gh_org, r.gh_repo, branch, base_sha)
                console.print(f"[green]  branch {branch} created off "
                              f"{base_branch}@{base_sha[:7]}[/green]")
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 422:
                    console.print(f"[yellow]  branch {branch} already exists; "
                                  f"updating files[/yellow]")
                else:
                    raise

            for f in wf_files:
                rel_path = f".github/workflows/{f.name}"
                existing_sha = gh.get_file_sha(r.gh_org, r.gh_repo,
                                               rel_path, branch)
                content_b64 = base64.b64encode(f.read_bytes()).decode("ascii")
                gh.put_file(
                    r.gh_org, r.gh_repo, rel_path, content_b64, branch,
                    f"Add migrated workflow: {f.name}",
                    sha=existing_sha,
                )
                console.print(f"  pushed {rel_path}")

            pr_body = (
                "Auto-generated GitHub Actions workflows migrated from ADO "
                "pipelines via `ado2gh`.\n\n"
                "**Review needed** — generated YAML may contain `TODO` "
                "placeholders for classic-pipeline conversions. See the "
                "`*_migration_notes.md` sidecar files in the local migration "
                "output for conversion warnings and unsupported tasks.\n"
            )
            try:
                pr = gh.create_pull_request(
                    r.gh_org, r.gh_repo, pr_title, pr_body,
                    head=branch, base=base_branch,
                )
                console.print(f"[green]  PR opened: {pr['html_url']}[/green]")
                pushed_repos += 1
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 422:
                    console.print(f"[yellow]  PR already open for "
                                  f"{branch} -> {base_branch}[/yellow]")
                    pushed_repos += 1
                else:
                    raise
        except Exception as exc:
            console.print(f"[red]  failed: {exc}[/red]")

    console.print(f"\n[bold]{pushed_repos}/{len(repos)} repo(s) "
                  f"workflows pushed[/bold]")


@cli.command("ado-cleanup")
@click.option("--config", "-c", required=True)
@click.option("--input", "-i", "input_file", default=None,
              help="Input file with repos to clean up")
@click.option("--db", default="migration_state.db", show_default=True)
@click.option("--disable-pipelines/--no-disable-pipelines", default=True,
              help="Disable ADO build pipelines for migrated repos")
@click.option("--add-redirect/--no-redirect", default=False,
              help="Push MIGRATION_NOTICE.md to ADO repo")
@click.option("--archive/--no-archive", default=False,
              help="Disable (archive) the ADO repo to prevent further pushes")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--phase", "-p", default=None,
              type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
@click.option("--plan", "plan_path", default="", type=click.Path(exists=True),
              help="Approved PEV plan; required for live cleanup")
@click.option("--run-id", default="",
              help="Completed PEV run; required for live cleanup")
@click.option("--approval-ticket", default="",
              help="External change/approval ticket; required for live cleanup")
@click.option("--approval-envelope", default="", type=click.Path(exists=True),
              help="Externally signed Ed25519 cleanup approval (strict governance)")
@click.option("--allow-source-mutation", is_flag=True, default=False,
              help="Explicitly allow redirect commit after parity validation")
def ado_cleanup(config, input_file, db, disable_pipelines, add_redirect, archive,
                dry_run, phase, plan_path, run_id, approval_ticket,
                approval_envelope,
                allow_source_mutation):
    """Post-migration ADO cleanup: disable pipelines, add redirect, archive repos.

    Reads repos from --input file, --phase filter, or config waves.
    Only run AFTER successful migration and validation."""
    from ado2gh.core.ado_cleanup import ADOCleanup, authorize_cleanup_capability
    from ado2gh.core.config_loader import ConfigLoader
    from ado2gh.governance import load_approval_envelope
    from ado2gh.state.db import StateDB

    global_cfg, waves = ConfigLoader.load(config)
    ado, gh = _load_clients(global_cfg)
    state = StateDB(db)

    approved_plan = None
    if plan_path:
        from ado2gh.pev.contracts import MigrationPlan
        approved_plan = MigrationPlan.from_dict(
            json.loads(Path(plan_path).read_text(encoding="utf-8"))
        )
        _verify_approved_plan_context(approved_plan, global_cfg, ado, gh)
    if not dry_run:
        if not plan_path or not run_id or not approval_ticket.strip():
            raise click.ClickException(
                "Live cleanup requires --plan, --run-id, and --approval-ticket"
            )
        pev_run = state.get_pev_run(run_id)
        if not pev_run or pev_run.get("plan_id") != approved_plan.plan_id:
            raise click.ClickException("Cleanup run and approved plan do not match")
        if pev_run.get("config_digest") != approved_plan.config_digest:
            raise click.ClickException(
                "Cleanup run configuration does not match the approved plan"
            )
        if pev_run.get("status") != "completed":
            raise click.ClickException(
                f"Cleanup requires a completed PEV run; observed {pev_run.get('status')}"
            )
        if add_redirect and not allow_source_mutation:
            raise click.ClickException(
                "--add-redirect changes the validated source HEAD; add "
                "--allow-source-mutation only when the approved cutover permits it"
            )
        if add_redirect and not archive:
            raise click.ClickException(
                "Live --add-redirect requires --archive in the same cutover so "
                "the sanctioned redirect commit is immediately frozen"
            )

    if approved_plan is not None:
        all_repos = [repo.to_repo_config() for repo in approved_plan.repositories]
        if input_file:
            selected = _load_repos(input_file, global_cfg)
            selected_keys = {
                (repo.ado_project.casefold(), repo.ado_repo.casefold())
                for repo in selected
            }
            approved_keys = {
                (repo.ado_project.casefold(), repo.ado_repo.casefold())
                for repo in all_repos
            }
            unapproved = selected_keys - approved_keys
            if unapproved:
                names = ", ".join(sorted(f"{p}/{r}" for p, r in unapproved))
                raise click.ClickException(
                    f"Cleanup selection contains repos outside the approved plan: {names}"
                )
            all_repos = [
                repo for repo in all_repos
                if (repo.ado_project.casefold(), repo.ado_repo.casefold()) in selected_keys
            ]
    elif input_file:
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
        cleanup_policy = dict(approved_plan.policy.get("cleanup", {}))
        if disable_pipelines and not cleanup_policy.get(
            "allow_disable_pipelines", False
        ):
            raise click.ClickException(
                "Pipeline disabling was not approved in the immutable plan"
            )
        if add_redirect and not cleanup_policy.get("allow_redirect", False):
            raise click.ClickException(
                "Source redirect commits were not approved in the immutable plan"
            )
        if archive and not cleanup_policy.get("allow_archive", False):
            raise click.ClickException(
                "Source archival was not approved in the immutable plan"
            )
        planned_by_source = {
            repo.source_key: repo for repo in approved_plan.repositories
        }
        if disable_pipelines:
            without_pipeline_scope = [
                f"{repo.ado_project}/{repo.ado_repo}"
                for repo in all_repos
                if "pipelines" not in planned_by_source[
                    f"{repo.ado_project}/{repo.ado_repo}"
                ].scopes
            ]
            if without_pipeline_scope:
                raise click.ClickException(
                    "Pipeline cleanup is outside approved scopes for: "
                    + ", ".join(sorted(without_pipeline_scope))
                )
        if archive:
            not_approved = [
                f"{repo.ado_project}/{repo.ado_repo}"
                for repo in all_repos
                if not planned_by_source[
                    f"{repo.ado_project}/{repo.ado_repo}"
                ].archive_source
            ]
            if not_approved:
                raise click.ClickException(
                    "Source archival is not approved per repository for: "
                    + ", ".join(sorted(not_approved))
                )
        # Reconcile the source snapshot immediately before cutover actions.
        from ado2gh.pev.contracts import (
            canonical_ref_snapshot,
            compute_source_refs_digest,
        )
        for repo in all_repos:
            planned = planned_by_source[f"{repo.ado_project}/{repo.ado_repo}"]
            source = ado.get_repo(repo.ado_project, planned.source_repo_id)
            observed_id = str(source.get("id", "")).strip()
            raw_default = source.get("defaultBranch")
            if raw_default in (None, ""):
                observed_default = ""
            elif (
                isinstance(raw_default, str)
                and raw_default.startswith("refs/heads/")
                and raw_default[len("refs/heads/"):]
            ):
                observed_default = raw_default[len("refs/heads/"):]
            else:
                raise click.ClickException(
                    f"Source drift blocks cleanup for {repo.ado_project}/"
                    f"{repo.ado_repo}: ADO returned an invalid default branch"
                )
            observed_branches = canonical_ref_snapshot(
                ado.list_refs(repo.ado_project, planned.source_repo_id, "heads/"),
                "heads",
            )
            observed_tags = canonical_ref_snapshot(
                ado.list_refs(repo.ado_project, planned.source_repo_id, "tags/"),
                "tags",
            )
            observed_digest = compute_source_refs_digest(
                observed_branches, observed_tags
            )
            if (
                observed_id != planned.source_repo_id
                or observed_default != planned.default_branch
                or observed_branches != planned.source_branch_refs
                or observed_tags != planned.source_tag_refs
                or observed_digest != planned.source_refs_digest
            ):
                raise click.ClickException(
                    f"Source drift blocks cleanup for {repo.ado_project}/{repo.ado_repo}: "
                    f"planned refs {planned.source_refs_digest[:12]}, observed "
                    f"{observed_digest[:12]}"
                )
    destructive_request = None
    destructive_capability_id = ""
    if approved_plan is not None:
        selected_source_keys = {
            f"{repo.ado_project}/{repo.ado_repo}" for repo in all_repos
        }
        inventory_tasks = {
            task.source_key: task
            for task in approved_plan.tasks
            if task.kind == "inventory" and task.scope == "pipelines"
            and task.source_key in selected_source_keys
        }

        # Pipeline disabling is destructive source-side work. Re-inventory and
        # require the exact approved digest immediately before the cleanup
        # worker starts so additions, deletions, type changes, and YAML drift
        # cannot be silently ignored.
        if disable_pipelines:
            from ado2gh.pipelines.inventory import PipelineInventoryBuilder
            try:
                PipelineInventoryBuilder(
                    ado,
                    state,
                    parallel=int(global_cfg.get("pipeline_parallel", 8)),
                    dry_run=False,
                    strict=True,
                ).build_for_projects(
                    sorted({repo.ado_project for repo in all_repos}),
                    include_releases=True,
                )
                for source_key, task in inventory_tasks.items():
                    project, repo_name = source_key.split("/", 1)
                    state.get_verified_pipeline_inventory_snapshot(
                        project,
                        repo_name,
                        str(task.metadata.get("inventory_digest", "")),
                    )
            except Exception as exc:
                raise click.ClickException(
                    f"Pipeline source drift blocks cleanup: {exc}"
                ) from exc

        if not dry_run:
            from ado2gh.core.ado_cleanup import build_cleanup_destructive_request
            from ado2gh.pev.contracts import content_digest

            target_web_url = str(global_cfg.get("gh_web_url", "")).rstrip("/")
            active_api_url = str(
                getattr(gh, "BASE", "")
                or global_cfg.get("gh_api_url", "https://api.github.com")
            ).rstrip("/")
            if not target_web_url:
                if active_api_url.casefold() == "https://api.github.com":
                    target_web_url = "https://github.com"
                else:
                    raise click.ClickException(
                        "GitHub Enterprise cleanup redirects require an explicit, "
                        "plan-bound global.gh_web_url"
                    )
            try:
                destructive_request = build_cleanup_destructive_request(
                    approved_plan,
                    run_id,
                    all_repos,
                    disable_pipelines=disable_pipelines,
                    add_redirect=add_redirect,
                    archive_repo=archive,
                    migration_date=(
                        datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        if add_redirect else ""
                    ),
                    target_web_url=target_web_url,
                )
            except (TypeError, ValueError) as exc:
                raise click.ClickException(str(exc)) from exc
            request_digest = content_digest(destructive_request)
            click.confirm(
                f"Authorize exact ADO cleanup {request_digest[:16]} on "
                f"{len(all_repos)} repos "
                f"(disable_pipelines={disable_pipelines}, "
                f"redirect={add_redirect}, archive={archive})?",
                abort=True,
            )
            approval = {
                "approval_ticket": approval_ticket.strip(),
                "actor": os.environ.get("USERNAME")
                or os.environ.get("USER", "unknown"),
                "request_digest": request_digest,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
            # Authorization and approval evidence are persisted only after the
            # interactive confirmation succeeds.
            destructive_capability_id, approval = authorize_cleanup_capability(
                state,
                approved_plan,
                run_id,
                destructive_request,
                approval_envelope=(
                    load_approval_envelope(approval_envelope)
                    if approval_envelope else None
                ),
                legacy_approval=approval,
                expected_ticket=approval_ticket.strip(),
            )
            state.record_validation_evidence(
                run_id,
                "cleanup_approval",
                "pass",
                evidence={
                    **approval,
                    "capability_id": destructive_capability_id,
                    "repositories": sorted(selected_source_keys),
                    "disable_pipelines": disable_pipelines,
                    "add_redirect": add_redirect,
                    "archive": archive,
                },
            )

    cleanup = ADOCleanup(
        ado,
        state,
        dry_run=dry_run,
        gh=gh,
        approved_plan=approved_plan,
        approved_run_id=run_id,
        destructive_capability_id=destructive_capability_id,
        destructive_request=destructive_request,
    )
    cleanup_results = cleanup.cleanup_repos(
        all_repos,
        disable_pipelines=disable_pipelines,
        add_redirect=add_redirect,
        archive_repo=archive,
    )
    if any(result.get("status") != "completed" for result in cleanup_results):
        raise click.ClickException("One or more ADO cleanup actions failed")


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
