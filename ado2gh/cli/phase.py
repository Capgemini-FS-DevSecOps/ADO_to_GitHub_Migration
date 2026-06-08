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
    @click.option("--config", "-c", required=True)
    @click.option("--phase", "-p", "phase_name", required=True,
                  type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
    @click.option("--dry-run", is_flag=True, default=False)
    @click.option("--force", is_flag=True, default=False)
    @click.option("--db", default="migration_state.db", show_default=True)
    def phase_run(config, phase_name, dry_run, force, db):
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.contracts import PhaseRunRequest
        accel = Accelerator(db_path=db)
        result = accel.run_phase(PhaseRunRequest(
            config_path=config, phase=phase_name, dry_run=dry_run, force=force, db_path=db,
        ))
        console.print(result)

    @phase_group.command("gate-check")
    @click.option("--config", "-c", required=True)
    @click.option("--phase", "-p", "phase_name", required=True,
                  type=click.Choice(["poc", "pilot", "wave1", "wave2", "wave3"]))
    @click.option("--override", is_flag=True, default=False)
    @click.option("--reason", default="")
    @click.option("--db", default="migration_state.db", show_default=True)
    def gate_check(config, phase_name, override, reason, db):
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.phase.gate_checker import PhaseGateChecker
        from ado2gh.state.factory import create_state_db
        ConfigLoader.load(config)
        checker = PhaseGateChecker(create_state_db(db))
        result = checker.check(PhaseType(phase_name), override=override, reason=reason)
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
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.phase.progress_tracker import ProgressTracker
        from ado2gh.state.factory import create_state_db
        from rich.panel import Panel
        global_cfg, waves = ConfigLoader.load(config)
        state = create_state_db(db)
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
