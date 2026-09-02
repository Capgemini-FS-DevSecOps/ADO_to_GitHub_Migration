"""Unit tests for ModelCapabilities — capability detection, validation, budget."""
import pytest
from unittest.mock import MagicMock, patch

from ado2gh.agents.migration_agent.runtime.llm_bridge import (
    ModelCapabilities,
    ModelCapabilityError,
    _detect_capabilities,
    _PROVIDER_CAPABILITY_DEFAULTS,
)


def test_capabilities_dataclass_fields():
    caps = ModelCapabilities()
    assert hasattr(caps, "supports_tool_calling")
    assert hasattr(caps, "supports_streaming")
    assert hasattr(caps, "supports_thinking")
    assert hasattr(caps, "max_context_tokens")


def test_capabilities_max_token_budget_80_percent():
    caps = ModelCapabilities(max_context_tokens=100_000)
    assert caps.max_token_budget == 80_000


def test_capabilities_max_token_budget_default():
    caps = ModelCapabilities()
    assert caps.max_token_budget == 102_400  # 80% of 128k


def test_provider_defaults_include_all_providers():
    expected = {"openai", "openai_compatible", "github_copilot", "github_models",
                "openrouter", "anthropic", "stub", "offline"}
    assert expected.issubset(set(_PROVIDER_CAPABILITY_DEFAULTS.keys()))


def test_provider_defaults_anthropic_has_thinking():
    caps = _detect_capabilities(MagicMock(provider="anthropic"))
    assert caps.supports_thinking is True


def test_provider_defaults_openai_no_thinking():
    caps = _detect_capabilities(MagicMock(provider="openai"))
    assert caps.supports_thinking is False


def test_provider_defaults_stub_no_streaming():
    caps = _detect_capabilities(MagicMock(provider="stub"))
    assert caps.supports_streaming is False


def test_explicit_capabilities_override_provider_defaults():
    cfg = MagicMock(provider="openai", capabilities={
        "supports_tool_calling": True,
        "supports_streaming": False,
        "supports_thinking": True,
        "max_context_tokens": 50_000,
    })
    caps = _detect_capabilities(cfg)
    assert caps.supports_streaming is False
    assert caps.supports_thinking is True
    assert caps.max_context_tokens == 50_000


def test_explicit_capabilities_partial_override():
    cfg = MagicMock(provider="openai", capabilities={
        "max_context_tokens": 50_000,
    })
    caps = _detect_capabilities(cfg)
    # Non-overridden fields use provider defaults
    assert caps.supports_tool_calling is True
    assert caps.max_context_tokens == 50_000


def test_no_tool_calling_raises_error():
    """Models without tool calling should be rejected."""
    from ado2gh.agents.migration_agent.runtime.llm_bridge import resolve_langchain_llm

    cfg = MagicMock(
        enabled=True,
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_id="gpt-3.5",
        validation_status="passed",
        capabilities={"supports_tool_calling": False},
    )
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        with pytest.raises(ModelCapabilityError, match="tool calling"):
            resolve_langchain_llm()


def test_unknown_provider_uses_safe_defaults():
    caps = _detect_capabilities(MagicMock(provider="totally_unknown"))
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True
    assert caps.supports_thinking is False
    assert caps.max_context_tokens == 128_000
