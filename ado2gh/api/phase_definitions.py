"""Configurable migration phases for risk-based repo assignment."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ado2gh.models import RiskScore


@dataclass
class PhaseDefinition:
    """User-configurable migration phase (stored in ui_settings.json)."""

    id: str
    name: str
    risk_max: float
    repo_cap: int = 9999
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def risk_min(self) -> float:
        return 0.0


def default_phase_definitions() -> list[PhaseDefinition]:
    return [
        PhaseDefinition(id="poc", name="POC", risk_max=25.0, repo_cap=10, order=0),
        PhaseDefinition(id="pilot", name="Pilot", risk_max=45.0, repo_cap=100, order=1),
        PhaseDefinition(id="wave1", name="Wave 1", risk_max=65.0, repo_cap=500, order=2),
        PhaseDefinition(id="wave2", name="Wave 2", risk_max=80.0, repo_cap=1000, order=3),
        PhaseDefinition(id="wave3", name="Wave 3", risk_max=100.0, repo_cap=9999, order=4),
    ]


def parse_phase_definitions(raw: list[dict[str, Any]] | None) -> list[PhaseDefinition]:
    if not raw:
        return default_phase_definitions()
    phases = [PhaseDefinition(**{**p, "order": p.get("order", i)}) for i, p in enumerate(raw)]
    return sorted(phases, key=lambda p: p.order)


def phase_risk_bands(phases: list[PhaseDefinition]) -> list[dict[str, Any]]:
    """Return phases with computed risk_min for UI display."""
    ordered = sorted(phases, key=lambda p: p.order)
    bands: list[dict[str, Any]] = []
    prev = 0.0
    for p in ordered:
        bands.append({
            **p.to_dict(),
            "risk_min": round(prev, 1),
            "risk_max": round(p.risk_max, 1),
        })
        prev = p.risk_max
    return bands


def validate_phases(phases: list[PhaseDefinition]) -> list[str]:
    errors: list[str] = []
    if len(phases) < 1:
        errors.append("At least one migration phase is required.")
        return errors

    ordered = sorted(phases, key=lambda p: p.order)
    ids: set[str] = set()
    for i, p in enumerate(ordered):
        if not p.id or not re.match(r"^[a-z0-9][a-z0-9_-]*$", p.id):
            errors.append(f"Phase {i + 1}: id must be a lowercase slug (got {p.id!r}).")
        if p.id in ids:
            errors.append(f"Duplicate phase id: {p.id}")
        ids.add(p.id)
        if not (p.name or "").strip():
            errors.append(f"Phase {p.id or i + 1}: name is required.")
        if p.risk_max <= 0 or p.risk_max > 100:
            errors.append(f"Phase {p.name}: risk_max must be between 0 and 100.")
        if p.repo_cap < 1:
            errors.append(f"Phase {p.name}: repo_cap must be at least 1.")

    for i in range(1, len(ordered)):
        if ordered[i].risk_max <= ordered[i - 1].risk_max:
            errors.append(
                f"Phase risk thresholds must increase: {ordered[i - 1].name} "
                f"({ordered[i - 1].risk_max}) must be less than {ordered[i].name} "
                f"({ordered[i].risk_max})."
            )

    if ordered[-1].risk_max < 100:
        errors.append("The last phase must have risk_max of 100 to cover the full score range.")

    return errors


def evaluate_coverage(
    phases: list[PhaseDefinition],
    scores: list[float],
) -> dict[str, Any]:
    """Check whether phase bands cover observed scan scores and span 0–100."""
    ordered = sorted(phases, key=lambda p: p.order)
    errors = validate_phases(phases)
    if not scores:
        return {
            "spans_0_100": ordered[-1].risk_max >= 100 if ordered else False,
            "covers_scan_min": True,
            "covers_scan_max": True,
            "scan_score_min": None,
            "scan_score_max": None,
            "errors": errors,
            "gaps": [],
        }

    lo, hi = min(scores), max(scores)
    gaps: list[str] = []
    prev = 0.0
    for p in ordered:
        if p.risk_max <= prev:
            gaps.append(f"No band above {prev} until {p.name} ({p.risk_max})")
        prev = p.risk_max

    covers_min = lo <= ordered[0].risk_max
    covers_max = hi <= ordered[-1].risk_max
    if not covers_min:
        gaps.append(
            f"Lowest scan score ({lo:.1f}) is above the first phase ceiling "
            f"({ordered[0].name} ≤ {ordered[0].risk_max})."
        )
    if not covers_max:
        gaps.append(
            f"Highest scan score ({hi:.1f}) exceeds the last phase ceiling "
            f"({ordered[-1].name} ≤ {ordered[-1].risk_max})."
        )

    return {
        "spans_0_100": ordered[-1].risk_max >= 100 and ordered[0].risk_max > 0,
        "covers_scan_min": covers_min,
        "covers_scan_max": covers_max,
        "scan_score_min": round(lo, 1),
        "scan_score_max": round(hi, 1),
        "errors": errors,
        "gaps": gaps,
    }


def span_phases_to_scan(
    phases: list[PhaseDefinition],
    scores: list[float],
) -> list[PhaseDefinition]:
    """Redistribute risk_max thresholds so bands span observed scan scores."""
    if not phases:
        return default_phase_definitions()
    ordered = sorted(phases, key=lambda p: p.order)
    result = [PhaseDefinition(**p.to_dict()) for p in ordered]
    if not scores:
        result[-1].risk_max = 100.0
        return result

    lo, hi = min(scores), max(scores)
    n = len(result)
    if n == 1:
        result[0].risk_max = 100.0
        return result

    if lo == hi:
        for i, p in enumerate(result[:-1]):
            p.risk_max = round(max(lo, 1.0), 1)
        result[-1].risk_max = 100.0
        return result

    span = hi - lo
    step = span / n
    for i, p in enumerate(result[:-1]):
        p.risk_max = round(lo + step * (i + 1), 1)
    result[-1].risk_max = 100.0
    return result


def slugify_phase_id(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "phase"
    candidate = base
    n = 2
    while candidate in existing:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def new_phase(name: str, existing: list[PhaseDefinition]) -> PhaseDefinition:
    ids = {p.id for p in existing}
    order = max((p.order for p in existing), default=-1) + 1
    prev_max = sorted(existing, key=lambda p: p.order)[-1].risk_max if existing else 0
    risk_max = min(100.0, round(prev_max + (100.0 - prev_max) / 2, 1)) if existing else 100.0
    return PhaseDefinition(
        id=slugify_phase_id(name, ids),
        name=name.strip() or "New phase",
        risk_max=max(risk_max, prev_max + 1),
        repo_cap=9999,
        order=order,
    )


class ConfigurableWaveAssigner:
    """Assign repos to phases using user-defined risk bands."""

    def __init__(self, phases: list[PhaseDefinition] | None = None):
        self.phases = sorted(phases or default_phase_definitions(), key=lambda p: p.order)

    def assign(
        self,
        scores: list[RiskScore],
        gh_org: str = "your-github-org",
    ) -> dict[str, list[RiskScore]]:
        result: dict[str, list[RiskScore]] = {p.id: [] for p in self.phases}
        last = self.phases[-1]
        for score in sorted(scores, key=lambda s: s.total_score):
            if not score.gh_org:
                score.gh_org = gh_org
            if not score.gh_repo:
                score.gh_repo = re.sub(r"[^a-zA-Z0-9\-_.]", "-", score.repo_name)
            assigned = False
            for phase in self.phases:
                if score.total_score > phase.risk_max:
                    continue
                if phase.id != last.id and len(result[phase.id]) >= phase.repo_cap:
                    continue
                score.assigned_phase = phase.id
                result[phase.id].append(score)
                assigned = True
                break
            if not assigned:
                score.assigned_phase = last.id
                result[last.id].append(score)
        return result

    def phase_label(self, phase_id: str) -> str:
        for p in self.phases:
            if p.id == phase_id:
                return p.name
        return phase_id


def phase_rationale(phase: PhaseDefinition, scores: list[RiskScore]) -> str:
    if not scores:
        return "No repos assigned to this phase."
    low = min(s.total_score for s in scores)
    high = max(s.total_score for s in scores)
    cap = phase.repo_cap if phase.repo_cap < 9999 else "unlimited"
    return (
        f"{len(scores)} repos with risk scores {low:.1f}–{high:.1f} "
        f"(phase ceiling: {phase.risk_max:.0f}). Cap: {cap} repos."
    )
