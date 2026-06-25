"""Tests for linting configuration (US9)."""
from pathlib import Path


def test_ruff_configured():
    """Verify ruff is configured in pyproject.toml (T098)."""
    pyproject = Path("pyproject.toml")
    assert pyproject.exists(), "pyproject.toml should exist"
    
    content = pyproject.read_text()
    assert "[tool.ruff]" in content, "pyproject.toml should have [tool.ruff] section"
    assert "line-length" in content, "ruff should have line-length configured"


def test_mypy_configured():
    """Verify mypy is configured in pyproject.toml (T099)."""
    pyproject = Path("pyproject.toml")
    assert pyproject.exists(), "pyproject.toml should exist"
    
    content = pyproject.read_text()
    assert "[tool.mypy]" in content, "pyproject.toml should have [tool.mypy] section"
    assert "python_version" in content, "mypy should have python_version configured"
