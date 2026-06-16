"""LLM provider stub."""
from ado2gh.agents.llm_provider import StubLLMProvider, get_llm_provider


def test_stub_complete():
    p = StubLLMProvider()
    out = p.complete("hello world")
    assert "stub" in out.lower()


def test_get_default_provider():
    p = get_llm_provider()
    assert p.complete("x")
