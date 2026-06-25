"""Tests for scan empty-repo diagnostics."""
from ado2gh.api.scan_diagnostics import build_empty_scan_warnings


def test_empty_scan_warnings_lists_projects():
    details = [
        {"project": "infra", "repo_count": 0, "error": None},
        {"project": "pipelines", "repo_count": 0, "error": None},
    ]
    warnings = build_empty_scan_warnings(2, 0, details)
    assert any("0 Git repositories" in w for w in warnings)
    assert any("infra" in w for w in warnings)


def test_empty_scan_warnings_includes_errors():
    details = [{"project": "p1", "repo_count": 0, "error": "403 Forbidden"}]
    warnings = build_empty_scan_warnings(1, 0, details)
    assert any("failed" in w.lower() for w in warnings)
    assert any("403" in w for w in warnings)
