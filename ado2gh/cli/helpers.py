"""Shared CLI helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ado2gh.logging_config import console, log
from ado2gh.models import DEFAULT_PHASES, GateStatus, PHASE_ORDER, PhaseType, next_phase


def load_clients(cfg_global: dict):
    """Initialize ADO + GH clients from env vars or config."""
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.clients.ado_token_manager import ADOTokenManager
    from ado2gh.clients.gh_client import GHClient
    from ado2gh.clients.token_manager import TokenManager

    ado_url = os.environ.get("ADO_ORG_URL") or cfg_global.get("ado_org_url", "")
    ado_pat = os.environ.get("ADO_PAT") or cfg_global.get("ado_pat", "")
    ado_vars = [f"ADO_PAT_{i}" for i in range(1, 20) if os.environ.get(f"ADO_PAT_{i}")]
    if not ado_url or (not ado_pat and not ado_vars):
        console.print("[red]ADO_ORG_URL + ADO_PAT (or ADO_PAT_1..N) required[/red]")
        sys.exit(1)

    if ado_vars:
        pats = [os.environ[v] for v in ado_vars if os.environ.get(v)]
        ado_tm = ADOTokenManager(pats)
        log.info("Loaded %d ADO tokens for load balancing", ado_tm.pat_count)
    elif ado_pat:
        ado_tm = ADOTokenManager.from_single(ado_pat)

    token_config = cfg_global.get("gh_token_config", "")
    gh_token_vars = [f"GH_TOKEN_{i}" for i in range(1, 20) if os.environ.get(f"GH_TOKEN_{i}")]

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

    app_id = os.environ.get("GH_APP_ID", "")
    install_id = os.environ.get("GH_APP_INSTALLATION_ID", "")
    key_path = os.environ.get("GH_APP_PRIVATE_KEY_PATH", "")
    if app_id and install_id and key_path:
        tm.configure_app_auth(app_id, install_id, key_path)
        log.info("GitHub App authentication configured")

    return ADOClient(ado_url, token_manager=ado_tm), GHClient(tm)


def load_repos(input_path: str, global_cfg: dict, waves: list = None) -> list:
    """Load repos from --input file, or fall back to waves in config."""
    from ado2gh.core.config_loader import ConfigLoader

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

    console.print(
        "[yellow]No repos specified. Use --input <file> or add waves to config.[/yellow]"
    )
    return []


def print_gate_result(result, phase: str):
    from rich.panel import Panel

    cfg = DEFAULT_PHASES[result.phase]
    color = {"pass": "green", "fail": "red", "override": "yellow"}.get(
        result.status.value, "white",
    )
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
    console.print(Panel(
        "\n".join(lines), title=f"Gate Check — {phase.upper()}", border_style=color,
    ))
