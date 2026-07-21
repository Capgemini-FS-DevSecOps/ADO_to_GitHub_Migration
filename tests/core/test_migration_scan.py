"""Tests for migration scan risk bucketing."""
from ado2gh.models import PHASE_ORDER, RiskScore
from ado2gh.phase.wave_assigner import WaveAssigner


def _score(project: str, repo: str, total: float) -> RiskScore:
    return RiskScore(project=project, repo_name=repo, total_score=total)


def test_wave_assigner_buckets_by_risk():
    scores = [
        _score("P1", "low-risk", 10.0),
        _score("P1", "mid-risk", 40.0),
        _score("P2", "high-risk", 75.0),
        _score("P2", "very-high", 95.0),
    ]
    assigned = WaveAssigner().assign(scores, gh_org="acme-github")
    assert len(assigned["poc"]) >= 1
    assert all(s.assigned_phase for s in scores)
    assert sum(len(v) for v in assigned.values()) == len(scores)


def test_phase_order_covers_all_buckets():
    assigner = WaveAssigner()
    scores = [_score("P", f"repo-{i}", float(i * 5)) for i in range(20)]
    assigned = assigner.assign(scores, gh_org="org")
    for phase in PHASE_ORDER:
        assert phase.value in assigned
