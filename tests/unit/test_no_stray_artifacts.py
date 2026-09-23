"""Test that no stray virtual environment or cache artifacts exist under ado2gh/."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_stray_venv_under_ado2gh():
    """ado2gh/__pycache__/ib-ai-agent/ must not exist on disk."""
    stray = REPO_ROOT / "ado2gh" / "__pycache__" / "ib-ai-agent"
    assert not stray.exists(), f"Stray artifact directory still exists: {stray}"


def test_no_venv_directories_under_ado2gh():
    """No .venv directories should exist under ado2gh/."""
    for path in (REPO_ROOT / "ado2gh").rglob(".venv"):
        assert False, f"Virtual environment found under ado2gh/: {path}"
