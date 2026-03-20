"""Auto-distributes scored repos into phases by risk band + cap."""
from __future__ import annotations

import re
from ado2gh.models import (
    DEFAULT_PHASES, PHASE_ORDER, PhaseType, RepoConfig, RiskScore, WaveConfig,
)


class WaveAssigner:
    def __init__(self, phase_configs: dict = None):
        self.phases = phase_configs or DEFAULT_PHASES

    def assign(self, scores: list[RiskScore],
               gh_org: str = "your-github-org") -> dict[PhaseType, list[RiskScore]]:
        sorted_scores = sorted(scores, key=lambda s: s.total_score)
        result = {p: [] for p in PHASE_ORDER}
        for score in sorted_scores:
            score.gh_org = gh_org
            score.gh_repo = re.sub(r"[^a-zA-Z0-9\-_.]", "-", score.repo_name)
            assigned = False
            for phase_type in PHASE_ORDER:
                cfg = self.phases[phase_type]
                if score.total_score > cfg.risk_max:
                    continue
                if phase_type != PhaseType.WAVE3 and len(result[phase_type]) >= cfg.repo_cap:
                    continue
                score.assigned_phase = phase_type
                result[phase_type].append(score)
                assigned = True
                break
            if not assigned:
                score.assigned_phase = PhaseType.WAVE3
                result[PhaseType.WAVE3].append(score)
        return result

    def to_wave_configs(self, assigned: dict, global_scopes: list,
                        global_cfg: dict) -> list[WaveConfig]:
        waves = []
        wave_id = 0
        for phase_type in PHASE_ORDER:
            scores = assigned.get(phase_type, [])
            if not scores:
                continue
            cfg = self.phases[phase_type]
            chunks = ([scores[i:i + cfg.batch_size]
                       for i in range(0, len(scores), cfg.batch_size)]
                      if phase_type == PhaseType.WAVE3 else [scores])
            for chunk_idx, chunk in enumerate(chunks):
                wave_id += 1
                label = (f"{phase_type.value.upper()}"
                         if len(chunks) == 1
                         else f"{phase_type.value.upper()}-batch{chunk_idx + 1}")
                repos = [
                    RepoConfig(
                        ado_project=s.project, ado_repo=s.repo_name,
                        gh_org=s.gh_org, gh_repo=s.gh_repo,
                        scopes=global_scopes, risk_score=s.total_score,
                        phase=phase_type.value, pipeline_parallel=cfg.pipeline_parallel,
                    )
                    for s in chunk
                ]
                waves.append(WaveConfig(
                    wave_id=wave_id, name=label, phase=phase_type.value,
                    description=(f"{phase_type.value.upper()} -- {len(repos)} repos, "
                                 f"risk {min(s.total_score for s in chunk):.1f}-"
                                 f"{max(s.total_score for s in chunk):.1f}"),
                    repos=repos, parallel=cfg.repo_parallel,
                    pipeline_parallel=cfg.pipeline_parallel,
                ))
        return waves
