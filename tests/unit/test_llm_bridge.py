"""Unit tests for LLM bridge — model resolution, capabilities, streaming."""
import pytest
from unittest.mock import MagicMock, patch

from ado2gh.agents.migration_agent.runtime.llm_bridge import (
    ModelCapabilities,
    ModelCapabilityError,
    _detect_capabilities,
    build_langchain_chat_model,
    resolve_langchain_llm,
    LLM_TIMEOUT_SECONDS,
)


def test_model_capabilities_defaults():
    caps = ModelCapabilities()
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True
    assert caps.supports_thinking is False
    assert caps.max_context_tokens == 128_000


def test_model_capabilities_max_token_budget():
    caps = ModelCapabilities(max_context_tokens=200_000)
    assert caps.max_token_budget == 160_000  # 80% of 200k


def test_detect_capabilities_openai():
    cfg = MagicMock(provider="openai")
    caps = _detect_capabilities(cfg)
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True
    assert caps.supports_thinking is False
    assert caps.max_context_tokens == 128_000


def test_detect_capabilities_anthropic():
    cfg = MagicMock(provider="anthropic")
    caps = _detect_capabilities(cfg)
    assert caps.supports_thinking is True
    assert caps.max_context_tokens == 200_000


def test_detect_capabilities_stub():
    cfg = MagicMock(provider="stub")
    caps = _detect_capabilities(cfg)
    assert caps.supports_streaming is False
    assert caps.max_context_tokens == 4_096


def test_detect_capabilities_explicit_override():
    cfg = MagicMock(provider="openai", capabilities={
        "supports_tool_calling": False,
        "supports_streaming": False,
        "supports_thinking": True,
        "max_context_tokens": 8_192,
    })
    caps = _detect_capabilities(cfg)
    assert caps.supports_tool_calling is False
    assert caps.supports_streaming is False
    assert caps.supports_thinking is True
    assert caps.max_context_tokens == 8_192


def test_detect_capabilities_unknown_provider():
    cfg = MagicMock(provider="unknown_provider")
    caps = _detect_capabilities(cfg)
    # Defaults for unknown provider
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True


def test_llm_timeout_seconds():
    assert LLM_TIMEOUT_SECONDS == 60


def test_build_langchain_chat_model_disabled():
    cfg = MagicMock(enabled=False)
    assert build_langchain_chat_model(cfg) is None


def test_build_langchain_chat_model_stub():
    cfg = MagicMock(enabled=True, provider="stub")
    model = build_langchain_chat_model(cfg)
    assert model is not None


def test_build_langchain_chat_model_openai_no_key():
    cfg = MagicMock(enabled=True, provider="openai", api_key=None, base_url=None)
    caps = ModelCapabilities()
    assert build_langchain_chat_model(cfg, caps) is None


def test_build_langchain_chat_model_openai_with_key():
    cfg = MagicMock(
        enabled=True,
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4",
    )
    caps = ModelCapabilities()
    model = build_langchain_chat_model(cfg, caps)
    assert model is not None


def test_build_langchain_chat_model_anthropic_no_key():
    cfg = MagicMock(enabled=True, provider="anthropic", api_key=None, base_url=None)
    caps = ModelCapabilities()
    assert build_langchain_chat_model(cfg, caps) is None


def test_build_langchain_chat_model_anthropic_with_key():
    cfg = MagicMock(
        enabled=True,
        provider="anthropic",
        api_key="sk-ant-test",
        base_url=None,
        model_id="claude-3-5-sonnet-20241022",
    )
    caps = ModelCapabilities()
    model = build_langchain_chat_model(cfg, caps)
    assert model is not None


def test_detect_capabilities_ollama():
    cfg = MagicMock(provider="ollama")
    caps = _detect_capabilities(cfg)
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True
    assert caps.supports_thinking is False
    assert caps.max_context_tokens == 32_768


def test_build_langchain_chat_model_ollama_no_key():
    cfg = MagicMock(
        enabled=True,
        provider="ollama",
        api_key="",
        base_url="http://localhost:11434",
        model_id="llama3",
    )
    caps = ModelCapabilities()
    model = build_langchain_chat_model(cfg, caps)
    assert model is not None


def test_build_langchain_chat_model_ollama_with_key():
    cfg = MagicMock(
        enabled=True,
        provider="ollama",
        api_key="bearer-token",
        base_url="http://localhost:11434",
        model_id="llama3",
    )
    caps = ModelCapabilities()
    model = build_langchain_chat_model(cfg, caps)
    assert model is not None


def test_build_langchain_chat_model_ollama_default_base_url():
    cfg = MagicMock(
        enabled=True,
        provider="ollama",
        api_key="",
        base_url="",
        model_id="llama3",
    )
    caps = ModelCapabilities()
    model = build_langchain_chat_model(cfg, caps)
    assert model is not None


def test_resolve_langchain_llm_ollama():
    cfg = MagicMock(
        enabled=True,
        provider="ollama",
        api_key="",
        base_url="http://localhost:11434",
        model_id="llama3",
        validation_status="passed",
    )
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        llm, degraded, unconfigured, caps = resolve_langchain_llm()
        assert llm is not None
        assert degraded is False
        assert unconfigured is False
        assert caps is not None
        assert caps.supports_tool_calling is True


def test_build_langchain_chat_model_unsupported_provider():
    cfg = MagicMock(enabled=True, provider="unknown", api_key="key")
    caps = ModelCapabilities()
    assert build_langchain_chat_model(cfg, caps) is None


def test_resolve_langchain_llm_unconfigured():
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=None):
        llm, degraded, unconfigured, caps = resolve_langchain_llm()
        assert llm is None
        assert unconfigured is True
        assert caps is None


def test_resolve_langchain_llm_disabled():
    cfg = MagicMock(enabled=False)
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        llm, degraded, unconfigured, caps = resolve_langchain_llm()
        assert llm is None
        assert unconfigured is True


def test_resolve_langchain_llm_rejects_no_tool_calling():
    cfg = MagicMock(
        enabled=True,
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4",
        validation_status="passed",
        capabilities={"supports_tool_calling": False},
    )
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        with pytest.raises(ModelCapabilityError):
            resolve_langchain_llm()


def test_resolve_langchain_llm_stub():
    cfg = MagicMock(enabled=True, provider="stub", model_id="stub-1")
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        llm, degraded, unconfigured, caps = resolve_langchain_llm()
        assert llm is not None
        assert degraded is True
        assert unconfigured is False
        assert caps is not None
        assert caps.supports_streaming is False


def test_resolve_langchain_llm_validation_failed():
    cfg = MagicMock(
        enabled=True,
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4",
        validation_status="failed",
    )
    with patch("ado2gh.agents.migration_agent.runtime.llm_bridge._get_model_config", return_value=cfg):
        llm, degraded, unconfigured, caps = resolve_langchain_llm()
        assert llm is None
        assert unconfigured is True
        assert caps is not None
