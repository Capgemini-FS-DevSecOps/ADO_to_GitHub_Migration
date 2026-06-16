"""Hyperscaler-agnostic LLM interface for agent reasoning."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """Abstract LLM backend (Bedrock, OpenAI, local stub)."""

    @abstractmethod
    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        """Return model text completion."""


class StubLLMProvider(LLMProvider):
    """Deterministic stub for tests and offline mode."""

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        snippet = (prompt or "")[:80]
        return f"[stub] processed: {snippet}"


def get_llm_provider() -> LLMProvider:
    """Resolve provider from env; defaults to stub (LLM_PROVIDER or ADO2GH_LLM_BACKEND)."""
    backend = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("ADO2GH_LLM_BACKEND")
        or "stub"
    ).lower()
    if backend in ("stub", "offline", ""):
        from ado2gh.agents.local.stub_llm import LocalStubLLM
        return LocalStubLLM()
    if backend == "unavailable":
        return StubLLMProvider()
    return StubLLMProvider()
