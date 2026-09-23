
from ado2gh.api.phase_definitions import (
    ConfigurableWaveAssigner,
    default_phase_definitions,
    evaluate_coverage,
    span_phases_to_scan,
    validate_phases,
)
from ado2gh.models import RiskScore


def test_default_phases_validate():
    phases = default_phase_definitions()
    assert not validate_phases(phases)
    assert len(phases) == 5


def test_validate_requires_last_phase_100():
    phases = default_phase_definitions()
    phases[-1].risk_max = 90
    errors = validate_phases(phases)
    assert any("100" in e for e in errors)


def test_assigner_returns_unassigned_bucket():
    phases = default_phase_definitions()
    assigner = ConfigurableWaveAssigner(phases)
    scores = [
        RiskScore(project="P", repo_name="low", total_score=10),
        RiskScore(project="P", repo_name="high", total_score=90),
    ]
    assigned = assigner.assign(scores)
    assert "unassigned" in assigned
    assert len(assigned["unassigned"]) == 2
    assert not any(getattr(s, "assigned_phase", None) for s in assigned["unassigned"])


def test_span_phases_to_scan_covers_range():
    phases = default_phase_definitions()
    spanned = span_phases_to_scan(phases, [12.0, 34.0, 67.0])
    assert spanned[-1].risk_max == 100.0
    assert spanned[0].risk_max > 12.0


def test_evaluate_coverage_flags_gaps():
    phases = default_phase_definitions()
    cov = evaluate_coverage(phases, [5.0, 95.0])
    assert cov["covers_scan_min"]
    assert cov["covers_scan_max"]


def test_validate_min_one_phase():
    errors = validate_phases([])
    assert any("At least one" in e for e in errors)
