"""Auto-distributes scored repos into phases by risk band + cap."""
from __future__ import annotations

import re

from ado2gh.models import (
    DEFAULT_PHASES,
    PHASE_ORDER,
    PhaseConfig,
    PhaseType,
    RiskScore,
)


class WaveAssigner:
    """Place scored repos into the first phase whose risk band and cap admit them."""

    def __init__(self, phase_configs: dict[PhaseType, PhaseConfig] | None = None) -> None:
        """Bind the per-phase risk ceilings and repo caps.

        Args:
            phase_configs: Phase definitions; defaults to ``DEFAULT_PHASES``.
        """
        self.phases = phase_configs or DEFAULT_PHASES

    def assign(self, scores: list[RiskScore],
               gh_org: str = "your-github-org") -> dict[PhaseType, list[RiskScore]]:
        """Assign each score to a phase, lowest risk first, and fill in blank GitHub targets.

        Repos that fit no earlier phase land in ``WAVE3``, which has no cap.
        Each score's ``assigned_phase`` is set in place.

        Args:
            scores: The repos to distribute.
            gh_org: Default GitHub org for scores that have none set.

        Returns:
            Scores grouped by phase value, in every phase of ``PHASE_ORDER``.
        """
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
