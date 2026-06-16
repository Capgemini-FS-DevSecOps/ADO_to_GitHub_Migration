"""FR-019 policy rules evaluation."""
from ado2gh.phase.policy_rules import PolicyEvaluator, PolicyRules


def test_bulk_wave_max_repos():
    rules = PolicyRules(bulk_wave_max_repos=5)
    ev = PolicyEvaluator(rules)
    failures = ev.check_live_migration(repo_count=10, execution_phase="wave1", passed_phases=set())
    assert any("bulk_wave_max" in f for f in failures)


def test_program_order():
    rules = PolicyRules(program_order_phases=["poc", "pilot"])
    ev = PolicyEvaluator(rules)
    failures = ev.check_live_migration(repo_count=1, execution_phase="wave1", passed_phases={"poc"})
    assert any("program_order" in f for f in failures)


def test_pass_when_rules_met():
    ev = PolicyEvaluator(PolicyRules())
    assert ev.check_live_migration(1, "pilot", {"poc"}) == []
