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
        """Serialise the phase to a plain dictionary.

        Returns:
            dict[str, Any]: The phase's ``id``, ``name``, ``risk_max``,
            ``repo_cap`` and ``order`` values, suitable for storing in
            ``ui_settings.json`` or returning from an API response.
        """
        return asdict(self)

    @property
    def risk_min(self) -> float:
        """Return the phase's own lower risk bound.

        A phase definition carries only a ceiling; its real floor is the
        previous phase's ceiling, which is a property of the ordered ladder
        rather than of the phase itself. Use :func:`phase_risk_bands` to compute
        the true per-phase floor.

        Returns:
            float: Always ``0.0`` — the placeholder floor for a phase considered
            on its own.
        """
        return 0.0


def default_phase_definitions() -> list[PhaseDefinition]:
    """Build the five built-in migration phases.

    Returns:
        list[PhaseDefinition]: The stock poc/pilot/wave1/wave2/wave3 ladder in
        ascending order, with risk ceilings rising from 25 to 100 and repo caps
        rising from 10 to effectively unlimited. Used whenever the operator has
        not configured phases of their own.
    """
    return [
        PhaseDefinition(id="poc", name="POC", risk_max=25.0, repo_cap=10, order=0),
        PhaseDefinition(id="pilot", name="Pilot", risk_max=45.0, repo_cap=100, order=1),
        PhaseDefinition(id="wave1", name="Wave 1", risk_max=65.0, repo_cap=500, order=2),
        PhaseDefinition(id="wave2", name="Wave 2", risk_max=80.0, repo_cap=1000, order=3),
        PhaseDefinition(id="wave3", name="Wave 3", risk_max=100.0, repo_cap=9999, order=4),
    ]


def parse_phase_definitions(raw: list[dict[str, Any]] | None) -> list[PhaseDefinition]:
    """Deserialise stored phase settings into phase definitions.

    Args:
        raw: Phase dictionaries as persisted in settings, or ``None``/empty when
            phases have never been configured. A missing ``order`` key is filled
            in from the entry's position in the list.

    Returns:
        list[PhaseDefinition]: The parsed phases sorted by ``order``, or the
        built-in defaults when ``raw`` holds nothing.

    Raises:
        TypeError: If an entry contains keys that are not phase fields.
    """
    if not raw:
        return default_phase_definitions()
    phases = [PhaseDefinition(**{**p, "order": p.get("order", i)}) for i, p in enumerate(raw)]
    return sorted(phases, key=lambda p: p.order)


def phase_risk_bands(phases: list[PhaseDefinition]) -> list[dict[str, Any]]:
    """Compute the closed risk band for each phase, for display in the console.

    Each phase stores only a ceiling, so a phase's floor is taken from the
    previous phase's ceiling; the first phase starts at zero.

    Args:
        phases: The phases to band, in any order; they are sorted by ``order``.

    Returns:
        list[dict[str, Any]]: One dictionary per phase in ascending order
        carrying every phase field plus a ``risk_min`` floor and a rounded
        ``risk_max`` ceiling, so the console can render contiguous bands.
    """
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
    """Check a phase ladder for the invariants risk assignment relies on.

    Verifies that at least one phase exists, that every id is a unique lowercase
    slug, that names are non-empty, that risk ceilings sit in ``(0, 100]`` and
    strictly increase across the ladder, that repo caps are positive, and that
    the final phase reaches 100 so the whole score range is covered.

    Args:
        phases: The phases to validate, in any order; they are sorted by
            ``order`` before the sequence checks run.

    Returns:
        list[str]: One human-readable message per problem found, ready to show
        to the operator. Empty when the ladder is valid.
    """
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
    """Redistribute risk ceilings so the bands span the observed scan scores.

    Spreads the phases evenly across the range between the lowest and highest
    observed risk score, always pinning the last phase at 100 so no repo can
    fall outside the ladder.

    Args:
        phases: The phases to rescale, in any order; they are sorted by
            ``order``. An empty list yields the built-in defaults.
        scores: Risk scores observed in the latest scan. When empty, only the
            last phase is adjusted (to 100) and the rest are left alone.

    Returns:
        list[PhaseDefinition]: New phase objects in ascending order with
        rewritten ``risk_max`` values; the phases passed in are not mutated.
    """
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


class ConfigurableWaveAssigner:
    """Assign repos to phases using user-defined risk bands."""

    def __init__(self, phases: list[PhaseDefinition] | None = None) -> None:
        """Store the phase ladder this assigner works against.

        Args:
            phases: The configured phases, in any order; they are sorted by
                ``order`` and kept on the instance. Falls back to the built-in
                ladder when ``None`` or empty.
        """
        self.phases = sorted(phases or default_phase_definitions(), key=lambda p: p.order)

    def assign(
        self,
        scores: list[RiskScore],
        gh_org: str = "your-github-org",
    ) -> dict[str, list[RiskScore]]:
        """Fill in GitHub targets on risk scores and return them unassigned.

        Phase bucketing is deprecated: the console now drives migration from
        risk scores and bulk waves, so every repo is returned in a single
        bucket. Each score is still given a GitHub org and a GitHub repo name
        (the ADO repo name with characters GitHub rejects replaced by hyphens)
        when it does not already carry them.

        Args:
            scores: Risk scores to prepare. Mutated in place to fill in missing
                ``gh_org`` and ``gh_repo`` values.
            gh_org: GitHub organisation applied to scores that have none.

        Returns:
            dict[str, list[RiskScore]]: A single ``"unassigned"`` bucket holding
            every score that was passed in.
        """
        # Phase-based assignment is deprecated; all repos are returned in a single
        # unassigned bucket so the UI can use risk scores and bulk waves instead.
        for score in scores:
            if not score.gh_org:
                score.gh_org = gh_org
            if not score.gh_repo:
                score.gh_repo = re.sub(r"[^a-zA-Z0-9\-_.]", "-", score.repo_name)
        return {"unassigned": scores}
