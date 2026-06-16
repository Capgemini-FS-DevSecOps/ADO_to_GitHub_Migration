"""Deterministic stub LLM for local IDE agent development."""
from __future__ import annotations

from typing import Optional

from ado2gh.agents.llm_provider import LLMProvider


class LocalStubLLM(LLMProvider):
    """Offline planner responses without cloud API keys."""

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        """Return deterministic stub text for planning prompts."""
        snippet = (prompt or "")[:120].replace("\n", " ")
        return f"[stub-llm] Plan step for: {snippet}"


def get_local_stub_llm() -> LocalStubLLM:
    """Factory for local stub provider."""
    return LocalStubLLM()
