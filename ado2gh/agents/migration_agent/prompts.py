"""Prompt loader for migration agent system prompts.

Loads .md files from the prompts/ directory and caches them.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_PROMPT_FILES = {
    "orchestrator": "orchestrator.md",
    "planner": "planner.md",
    "executor": "executor.md",
    "validator": "validator.md",
}


@lru_cache(maxsize=8)
def get_prompt(role: str) -> str:
    """Return the system prompt for the given agent role."""
    filename = _PROMPT_FILES.get(role)
    if not filename:
        raise KeyError(f"Unknown agent role: {role}")
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


