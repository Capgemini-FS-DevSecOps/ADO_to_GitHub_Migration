"""Phase orchestration commands."""
from __future__ import annotations

from pathlib import Path

import click
import yaml

from ado2gh.cli.helpers import load_clients, print_gate_result
from ado2gh.logging_config import console
from ado2gh.models import PHASE_ORDER, PhaseType

# Credentials may be set in the settings config; they are never copied into the
# generated phase config, which is a planning artefact meant to be shared (CA-003).
_SECRET_CONFIG_KEYS = frozenset({"ado_pat", "gh_token"})


def _write_phase_config(config_path: str, global_cfg: dict, assigned: dict) -> Path:
    """Write the risk-scored phase config beside the settings config.

    One wave is emitted per non-empty phase, in phase order, so that
    ``phase run --phase <name>`` finds its repos and ``phase plan`` can
    summarise them. The settings config's ``global`` block is copied over,
    minus any credential, so the generated file is runnable on its own.

    Args:
        config_path: Settings config the command was given.
        global_cfg: Its ``global`` block.
        assigned: Scores grouped by phase value, from ``WaveAssigner.assign``.

    Returns:
        The path written: ``migration_phase.yaml`` in the config's directory.
    """
    waves = []
    for phase in PHASE_ORDER:
        scores = assigned.get(phase.value, [])
        if not scores:
            continue
        waves.append({
            "wave_id": len(waves) + 1,
            "name": f"{phase.value}-wave",
            "description": f"{len(scores)} repo(s) risk-scored into {phase.value}",
            "phase": phase.value,
            "repos": [
                {
                    "ado_project": s.project,
                    "ado_repo": s.repo_name,
                    "gh_org": s.gh_org,
                    "gh_repo": s.gh_repo,
                    "risk_score": round(s.total_score, 2),
                    "phase": phase.value,
                }
                for s in scores
            ],
        })
    out_path = Path(config_path).parent / "migration_phase.yaml"
    settings = {k: v for k, v in global_cfg.items() if k not in _SECRET_CONFIG_KEYS}
    out_path.write_text(
        yaml.safe_dump({"global": settings, "waves": waves}, sort_keys=False),
        encoding="utf-8",
    )
    return out_path


def register(cli: click.Group) -> None:
    """Attach the ``phase`` command group to the top-level CLI group.

    Args:
        cli: The root Click group that the ``phase`` group is registered on.
    """

    @cli.group("phase")
    def phase_group() -> None:
        """Phase orchestration: assign / plan / run / gate-check / dashboard."""
        pass

    @phase_group.command("run")
    @click.option("--config", "-c", required=True,
                  help="Phase config file, normally migration_phase.yaml.")
    @click.option("--phase", "-p", "phase_name", required=True,
                  type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]),
                  help="Phase to run. Phases go in order: poc, pilot, wave1, wave2, wave3.")
    @click.option("--dry-run", is_flag=True, default=False,
                  help="Show what would happen. Creates nothing, pushes nothing.")
    @click.option("--force", is_flag=True, default=False,
                  help="Go ahead even though the previous phase's gate did not pass. "
                       "Does NOT get you past a blocked gate: record the override "
                       "first with 'phase gate-check --override --reason \"...\"'.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="Migration state database file.")
    def phase_run(
        config: str, phase_name: str, db: str, *, dry_run: bool, force: bool,
    ) -> None:
        """Migrate the repos assigned to one phase, in batches, with checkpoints.

        Every phase after poc checks the previous phase's gate before it starts.
        If that gate has not passed, or was never checked, the run stops and
        nothing is migrated.

        To go ahead anyway, record the override as its own step first:

        \b
            ado2gh phase gate-check -c <config> -p <prior> --override --reason "..."
            ado2gh phase run       -c <config> -p <next>

        --force on its own is not enough. Against a blocked gate it stops with
        "Gate blocked for prior phase <name>: forcing past it requires
        override_reason". There is no --reason flag here on purpose: an override
        is a separate act, with a named person and a stated reason behind it.
        """
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.contracts import PhaseRunRequest
        accel = Accelerator(db_path=db)
        result = accel.run_phase(PhaseRunRequest(
            config_path=config, phase=phase_name, dry_run=dry_run, force=force,
            # `phase run` has no --reason flag (frozen CLI surface), so --force
            # past a blocking gate is refused. Escalate via:
            #   phase gate-check --phase <prior> --override --reason "..."
            override_reason="",
            db_path=db,
        ))
        console.print(result)

    @phase_group.command("gate-check")
    @click.option("--config", "-c", required=True,
                  help="Phase config file, normally migration_phase.yaml.")
    @click.option("--phase", "-p", "phase_name", required=True,
                  type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]),
                  help="Phase whose gate to check.")
    @click.option("--override", is_flag=True, default=False,
                  help="Accept a failing gate and let the next phase run. "
                       "Needs --reason with text in it.")
    @click.option("--reason", default="",
                  help="Why you are overriding. Required with --override, and kept "
                       "on the gate record for the audit trail.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="Migration state database file.")
    def gate_check(
        config: str, phase_name: str, reason: str, db: str, *, override: bool,
    ) -> None:
        """Check a phase's gate, or record an operator override of it.

        The gate measures how much of the phase actually succeeded against the
        thresholds in the config. 'phase run' will not start the next phase
        until this phase's gate passes or is overridden here.

        Add --override --reason "..." when you have decided to accept the
        failures and move on. Both are needed: --override without a reason is
        rejected. The reason is stored and shown in audit history, so write it
        for the person who reads it months from now.
        """
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.phase.gate_checker import PhaseGateChecker
        from ado2gh.state.factory import create_state_db
        ConfigLoader.load(config)
        checker = PhaseGateChecker(create_state_db(db))
        phase = PhaseType(phase_name)
        if override:
            if not reason.strip():
                raise click.UsageError("--override requires a non-empty --reason")
            result = checker.override(phase, reason.strip())
        else:
            result = checker.check(phase)
        print_gate_result(result, phase_name)

    @phase_group.command("assign")
    @click.option("--config", "-c", required=True,
                  help="Settings config file with the ADO organisation and target org.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="Migration state database file the risk scores are written to.")
    def phase_assign(config: str, db: str) -> None:
        """Risk-score every repo in the organisation and assign it to a phase.

        Writes migration_phase.yaml next to the settings config, one wave per
        non-empty phase, and stores each repo's risk score in the state
        database. Run this before 'phase plan' and 'phase run'.
        """
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.phase.repo_scoring import score_org_repos
        from ado2gh.phase.wave_assigner import WaveAssigner
        from ado2gh.state.factory import create_state_db
        global_cfg, _ = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        state = create_state_db(db)
        gh_org = global_cfg.get("gh_org", "")
        scores = score_org_repos(ado, state, gh_org=gh_org)
        assigned = WaveAssigner().assign(scores, gh_org=gh_org)
        for score in scores:
            state.upsert_risk_score(score)
        state.prune_risk_scores_not_in({(s.project, s.repo_name) for s in scores})
        out_path = _write_phase_config(config, global_cfg, assigned)
        console.print(f"Scored {len(scores)} repo(s) -> {out_path}")
        for phase in PHASE_ORDER:
            console.print(f"  {phase.value}: {len(assigned.get(phase.value, []))} repo(s)")

    @phase_group.command("plan")
    @click.option("--config", "-c", required=True,
                  help="Phase config file, normally migration_phase.yaml.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="Migration state database file.")
    def phase_plan(config: str, db: str) -> None:
        """Show how many repos and waves each phase holds, without running anything."""
        from rich.panel import Panel

        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.state.factory import create_state_db
        global_cfg, waves = ConfigLoader.load(config)
        create_state_db(db)
        for phase in PHASE_ORDER:
            phase_waves = [w for w in waves if w.phase == phase.value]
            repos = sum(len(w.repos) for w in phase_waves)
            console.print(Panel(
                f"Phase {phase.value}: {repos} repos across {len(phase_waves)} wave(s)",
                border_style="blue",
            ))

    @phase_group.command("dashboard")
    # Accepted for consistency with the sibling phase commands; the snapshot is
    # read entirely from --db, so the value is never used here.
    @click.option("--config", "-c", required=True, expose_value=False,
                  help="Phase config file, normally migration_phase.yaml.")
    @click.option("--db", default="migration_state.db", show_default=True,
                  help="Migration state database file the snapshot is read from.")
    def phase_dashboard(db: str) -> None:
        """Print a snapshot of migration progress across every phase."""
        from ado2gh.api.accelerator import Accelerator
        snap = Accelerator(db_path=db).status(db)
        console.print(snap)
