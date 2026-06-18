"""Hyperscaler-agnostic LLM interface for agent reasoning."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import urlencode

from ado2gh.api.http_llm import build_llm_http_client
from ado2gh.api.llm_provider_registry import GITHUB_API_VERSION, get_provider_spec
from ado2gh.api.local_hosts import resolve_local_service_url

NO_LLM_CONFIGURED_MESSAGE = (
    "No LLM models are configured. Go to **Settings → LLM models** to add, "
    "validate, and enable a model, then start a new chat."
)


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


class UnavailableLLMProvider(LLMProvider):
    """Returned when the model registry has no usable agent models."""

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        return NO_LLM_CONFIGURED_MESSAGE


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible chat/completions (OpenAI, OpenRouter, GitHub Models, Azure)."""

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
        auth_style: str = "bearer",
        completions_path: str | None = None,
        azure_api_version: str = "2024-06-01",
    ):
        self.api_key = api_key
        self.model_id = model_id
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.extra_headers = extra_headers or {}
        self.auth_style = auth_style
        self.completions_path = completions_path or "/chat/completions"
        self.azure_api_version = azure_api_version

    def _request_url(self) -> str:
        path = self.completions_path.replace("{model_id}", self.model_id)
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        if self.auth_style == "azure-api-key":
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode({'api-version': self.azure_api_version})}"
        return url

    def _auth_headers(self) -> dict[str, str]:
        if self.auth_style == "azure-api-key":
            return {"api-key": self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {**self._auth_headers(), **self.extra_headers}
        with build_llm_http_client(for_cloud=True) as client:
            response = client.post(
                self._request_url(),
                headers=headers,
                json={"model": self.model_id, "messages": messages},
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model_id: str, base_url: str | None = None):
        self.api_key = api_key
        self.model_id = model_id
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        body: dict = {
            "model": self.model_id,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        with build_llm_http_client(for_cloud=True) as client:
            response = client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        return "".join(texts) or json.dumps(data)


class GeminiProvider(LLMProvider):
    """Google Gemini generateContent API."""

    def __init__(self, api_key: str, model_id: str, base_url: str | None = None):
        self.api_key = api_key
        self.model_id = model_id
        self.base_url = (
            base_url or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        with build_llm_http_client(for_cloud=True) as client:
            response = client.post(
                f"{self.base_url}/models/{self.model_id}:generateContent",
                params={"key": self.api_key},
                json=body,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return json.dumps(data)
        parts = candidates[0].get("content", {}).get("parts") or []
        texts = [p.get("text", "") for p in parts if p.get("text")]
        return "".join(texts) or json.dumps(data)


class OllamaProvider(LLMProvider):
    """Local/self-hosted Ollama chat completion."""

    def __init__(self, base_url: str, model_id: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.api_key = api_key

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        base = resolve_local_service_url(self.base_url)
        with build_llm_http_client(for_cloud=False) as client:
            response = client.post(
                f"{base}/api/chat",
                headers=headers or None,
                json={"model": self.model_id, "messages": messages, "stream": False},
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        message = data.get("message") or {}
        return message.get("content") or json.dumps(data)


def build_provider_from_config(cfg) -> LLMProvider | None:
    """Instantiate a runtime provider from a stored model config."""
    spec = get_provider_spec(cfg.provider)
    if not spec:
        return None
    if spec.kind == "stub":
        from ado2gh.agents.local.stub_llm import LocalStubLLM
        return LocalStubLLM()
    if spec.kind == "anthropic":
        if not cfg.api_key:
            return None
        return AnthropicProvider(cfg.api_key, cfg.model_id, cfg.base_url or None)
    if spec.kind == "gemini":
        if not cfg.api_key:
            return None
        base = cfg.base_url or spec.default_base_url
        return GeminiProvider(cfg.api_key, cfg.model_id, base)
    if spec.kind == "ollama":
        if not cfg.base_url:
            return None
        return OllamaProvider(cfg.base_url, cfg.model_id, cfg.api_key)
    if spec.kind == "openai_compatible":
        if not cfg.api_key:
            return None
        base = cfg.base_url or spec.default_base_url
        if not base:
            return None
        return OpenAIProvider(
            cfg.api_key,
            cfg.model_id,
            base,
            extra_headers=spec.runtime_headers(),
            auth_style=spec.auth_style,
            completions_path=spec.completions_path,
            azure_api_version=spec.azure_api_version,
        )
    return None


def _explicit_env_stub_enabled() -> bool:
    backend = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("ADO2GH_LLM_BACKEND")
        or ""
    ).lower()
    return backend in ("stub", "offline")


def _provider_from_model_config(model_id: str) -> LLMProvider | None:
    from ado2gh.api.llm_model_store import LLMModelStore

    cfg = LLMModelStore().get(model_id)
    if not cfg or not cfg.enabled:
        return None
    if cfg.provider not in ("stub", "offline") and cfg.validation_status != "passed":
        return None
    return build_provider_from_config(cfg)


def get_llm_provider(model_id: str | None = None) -> LLMProvider:
    """Resolve provider from registry, env override, or unconfigured."""
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    configured_id = model_id or os.environ.get("ADO2GH_LLM_MODEL_ID")
    if configured_id:
        resolved = _provider_from_model_config(configured_id)
        if resolved:
            return resolved

    default = store.get_default_model()
    if default:
        resolved = _provider_from_model_config(default.id)
        if resolved:
            return resolved

    for cfg in store.load():
        if cfg.enabled and cfg.provider in ("stub", "offline"):
            resolved = _provider_from_model_config(cfg.id)
            if resolved:
                return resolved

    if _explicit_env_stub_enabled():
        from ado2gh.agents.local.stub_llm import LocalStubLLM
        return LocalStubLLM()

    backend = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("ADO2GH_LLM_BACKEND")
        or ""
    ).lower()
    if backend == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if key:
            return OpenAIProvider(key, model)
    if backend == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        if key:
            return AnthropicProvider(key, model)
    if backend in ("github_copilot", "github_models", "copilot"):
        key = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
        model = os.environ.get("GITHUB_MODEL", "openai/gpt-4o")
        if key:
            spec = get_provider_spec("github_copilot")
            return OpenAIProvider(
                key,
                model,
                spec.default_base_url if spec else "https://models.github.ai/inference",
                extra_headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
            )
    if backend == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "")
        model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o")
        if key:
            spec = get_provider_spec("openrouter")
            return OpenAIProvider(
                key,
                model,
                spec.default_base_url if spec else "https://openrouter.ai/api/v1",
                extra_headers=spec.runtime_headers() if spec else {},
            )

    if not store.has_agent_ready_model():
        return UnavailableLLMProvider()
    return UnavailableLLMProvider()
