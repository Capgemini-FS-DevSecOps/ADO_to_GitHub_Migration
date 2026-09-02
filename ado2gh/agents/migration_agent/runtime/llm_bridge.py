"""Bridge between existing LLM model configs and LangChain ChatModels.

Resolves model configs from the LLMModelStore and builds LangChain
BaseChatModel instances for use in the LangGraph agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

LLM_TIMEOUT_SECONDS = 60


class ModelCapabilityError(Exception):
    """Raised when a model lacks required capabilities (e.g. tool calling)."""


@dataclass
class ModelCapabilities:
    """Capabilities detected for a specific LLM model."""
    supports_tool_calling: bool = True
    supports_streaming: bool = True
    supports_thinking: bool = False
    max_context_tokens: int = 128_000

    @property
    def max_token_budget(self) -> int:
        """Effective token budget for context window (80% of max)."""
        return int(self.max_context_tokens * 0.8)


_PROVIDER_CAPABILITY_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 128_000},
    "openai_compatible": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 128_000},
    "github_copilot": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 128_000},
    "github_models": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 128_000},
    "openrouter": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 128_000},
    "anthropic": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": True, "max_context_tokens": 200_000},
    "ollama": {"supports_tool_calling": True, "supports_streaming": True, "supports_thinking": False, "max_context_tokens": 32_768},
    "stub": {"supports_tool_calling": True, "supports_streaming": False, "supports_thinking": False, "max_context_tokens": 4_096},
    "offline": {"supports_tool_calling": True, "supports_streaming": False, "supports_thinking": False, "max_context_tokens": 4_096},
}


def _detect_capabilities(cfg: Any) -> ModelCapabilities:
    """Detect capabilities from model config, with provider-based defaults."""
    provider = getattr(cfg, "provider", "") or ""
    model_id = str(getattr(cfg, "id", None) or getattr(cfg, "model_id", None) or "")
    defaults = _PROVIDER_CAPABILITY_DEFAULTS.get(provider, {})
    # Check if config has explicit capabilities
    explicit = getattr(cfg, "capabilities", None)
    if explicit and isinstance(explicit, dict):
        caps = ModelCapabilities(
            supports_tool_calling=explicit.get("supports_tool_calling", defaults.get("supports_tool_calling", True)),
            supports_streaming=explicit.get("supports_streaming", defaults.get("supports_streaming", True)),
            supports_thinking=explicit.get("supports_thinking", defaults.get("supports_thinking", False)),
            max_context_tokens=explicit.get("max_context_tokens", defaults.get("max_context_tokens", 128_000)),
        )
    else:
        caps = ModelCapabilities(
            supports_tool_calling=defaults.get("supports_tool_calling", True),
            supports_streaming=defaults.get("supports_streaming", True),
            supports_thinking=defaults.get("supports_thinking", False),
            max_context_tokens=defaults.get("max_context_tokens", 128_000),
        )
    if not caps.supports_thinking and _model_id_implies_thinking(model_id, provider):
        caps.supports_thinking = True
    return caps


def _model_id_implies_thinking(model_id: str | None, provider: str) -> bool:
    """Heuristic: Qwen and similar models expose reasoning/thinking tokens."""
    mid = (model_id or "").lower()
    if "qwen" in mid or "deepseek-r1" in mid or "reasoning" in mid:
        return True
    if provider in ("ollama", "openai_compatible") and ("think" in mid or "r1" in mid):
        return True
    return False


def _get_model_config(model_id: str | None = None):
    """Resolve a model config from the store, falling back to the default."""
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    store = LLMModelStore()
    cfg = None
    if model_id:
        cfg = store.get(model_id)
    if not cfg:
        default = store.get_default_model()
        if default:
            cfg = store.get(default.id)
    return cfg


def build_langchain_chat_model(cfg: Any, capabilities: ModelCapabilities | None = None) -> BaseChatModel | None:
    """Build a LangChain ChatModel from a stored model config.

    Returns None when the config is invalid or the provider is unsupported.
    """
    if not cfg or not cfg.enabled:
        return None

    if capabilities is None:
        capabilities = _detect_capabilities(cfg)

    provider = cfg.provider
    streaming = capabilities.supports_streaming
    timeout = LLM_TIMEOUT_SECONDS

    if provider in ("stub", "offline"):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        return GenericFakeChatModel(messages=iter(["[stub] processed."]))

    if provider in ("openai_compatible", "openai", "github_copilot", "github_models", "openrouter"):
        from langchain_openai import ChatOpenAI

        from ado2gh.api.llm.llm_provider_registry import get_provider_spec

        spec = get_provider_spec(provider)
        base_url = cfg.base_url or (spec.default_base_url if spec else None)
        if not base_url and provider == "openai":
            base_url = "https://api.openai.com/v1"
        if not base_url:
            return None
        if not cfg.api_key:
            return None

        extra_headers = spec.runtime_headers() if spec else {}
        kwargs: dict[str, Any] = {
            "model": cfg.model_id,
            "api_key": cfg.api_key,
            "base_url": base_url,
            "streaming": streaming,
            "timeout": timeout,
        }
        if extra_headers:
            kwargs["default_headers"] = extra_headers
        if spec and spec.auth_style == "azure-api-key":
            kwargs["default_headers"] = {
                **extra_headers,
                "api-key": cfg.api_key,
            }
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        if not cfg.api_key:
            return None
        from langchain_anthropic import ChatAnthropic
        kwargs: dict[str, Any] = {
            "model": cfg.model_id,
            "api_key": cfg.api_key,
            "streaming": streaming,
            "timeout": timeout,
        }
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return ChatAnthropic(**kwargs)

    if provider == "ollama":
        from langchain_openai import ChatOpenAI

        from ado2gh.api.local_hosts import resolve_local_service_url

        base_url = cfg.base_url or "http://localhost:11434"
        resolved = resolve_local_service_url(base_url)
        ollama_base_url = f"{resolved}/v1"
        kwargs: dict[str, Any] = {
            "model": cfg.model_id,
            "api_key": cfg.api_key or "ollama",
            "base_url": ollama_base_url,
            "streaming": streaming,
            "timeout": timeout,
        }
        return ChatOpenAI(**kwargs)

    return None


def resolve_langchain_llm(
    model_id: str | None = None,
) -> tuple[BaseChatModel | None, bool, bool, ModelCapabilities | None]:
    """Resolve a LangChain ChatModel from the model store.

    Returns (chat_model, degraded, unconfigured, capabilities):
      - chat_model: the LangChain ChatModel or None
      - degraded: True when using a stub/offline model
      - unconfigured: True when no usable model is available
      - capabilities: ModelCapabilities dataclass or None
    """
    cfg = _get_model_config(model_id)
    if not cfg or not cfg.enabled:
        return None, False, True, None

    capabilities = _detect_capabilities(cfg)

    # T028: Reject models without tool calling
    if not capabilities.supports_tool_calling:
        raise ModelCapabilityError(
            f"Model '{cfg.model_id}' does not support tool calling, which is required for the migration agent."
        )

    if cfg.provider in ("stub", "offline"):
        model = build_langchain_chat_model(cfg, capabilities)
        return model, True, False, capabilities

    if cfg.validation_status != "passed":
        return None, False, True, capabilities

    model = build_langchain_chat_model(cfg, capabilities)
    if model is None:
        return None, False, True, capabilities
    return model, False, False, capabilities
