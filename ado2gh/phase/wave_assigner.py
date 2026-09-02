"""Auto-distributes scored repos into phases by risk band + cap."""
from __future__ import annotations

import re

from ado2gh.models import (
    DEFAULT_PHASES,
    PHASE_ORDER,
    PhaseType,
    RiskScore,
)


class WaveAssigner:
    def __init__(self, phase_configs: dict = None):
        self.phases = phase_configs or DEFAULT_PHASES

    def assign(self, scores: list[RiskScore],
               gh_org: str = "your-github-org") -> dict[PhaseType, list[RiskScore]]:
        sorted_scores = sorted(scores, key=lambda s: s.total_score)
        result = {p.value: [] for p in PHASE_ORDER}
        for score in sorted_scores:
            # Preserve any per-repo override already set on the score
            # (e.g. from the project/repo::gh_org/gh_repo input syntax).
            if not score.gh_org:
                score.gh_org = gh_org
            if not score.gh_repo:
                score.gh_repo = re.sub(r"[^a-zA-Z0-9\-_.]", "-", score.repo_name)
            assigned = False
            for phase_type in PHASE_ORDER:
                cfg = self.phases[phase_type]
                if score.total_score > cfg.risk_max:
                    continue
                if phase_type != PhaseType.WAVE3 and len(result[phase_type.value]) >= cfg.repo_cap:
                    continue
                score.assigned_phase = phase_type.value
                result[phase_type.value].append(score)
                assigned = True
                break
            if not assigned:
                score.assigned_phase = PhaseType.WAVE3.value
                result[PhaseType.WAVE3.value].append(score)
        return result

