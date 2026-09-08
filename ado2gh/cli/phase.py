"""Phase orchestration commands."""
from __future__ import annotations

import click

from ado2gh.cli.helpers import load_clients, print_gate_result
from ado2gh.logging_config import console
from ado2gh.models import PHASE_ORDER, PhaseType


def register(cli):
    @cli.group("phase")
    def phase_group():
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
    def phase_run(config, phase_name, dry_run, force, db):
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
    def gate_check(config, phase_name, override, reason, db):
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
    @click.option("--config", "-c", required=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def phase_assign(config, db):
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.phase.risk_scorer import RiskScorer
        from ado2gh.phase.wave_assigner import WaveAssigner
        from ado2gh.state.factory import create_state_db
        global_cfg, _ = ConfigLoader.load(config)
        ado, _ = load_clients(global_cfg)
        state = create_state_db(db)
        scores = RiskScorer(ado, state).score_all()
        WaveAssigner(state).assign_and_write(scores, config)

    @phase_group.command("plan")
    @click.option("--config", "-c", required=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def phase_plan(config, db):
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
    @click.option("--config", "-c", required=True)
    @click.option("--db", default="migration_state.db", show_default=True)
    def phase_dashboard(config, db):
        from ado2gh.api.accelerator import Accelerator
        snap = Accelerator(db_path=db).status(db)
        console.print(snap)
