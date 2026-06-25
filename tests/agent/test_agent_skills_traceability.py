"""Skill markdown traceability to feature spec.
NOTE: skills/ directory deleted in spec 012 — skip pending rewrite.
"""
import pytest

pytest.skip("skills/ directory deleted in spec 012", allow_module_level=True)

from pathlib import Path

SKILLS = [
    Path("ado2gh/agents/skills/planner.md"),
    Path("ado2gh/agents/skills/executor.md"),
    Path("ado2gh/agents/skills/validator.md"),
]
SPEC_REF = "specs/003-local-agent-ide/spec.md"


def test_skills_reference_spec():
    for path in SKILLS:
        text = path.read_text(encoding="utf-8")
        assert SPEC_REF in text
