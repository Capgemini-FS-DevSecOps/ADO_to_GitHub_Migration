"""Registry of supported LLM agent platforms (catalog, validation, runtime)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


GITHUB_API_VERSION = "2022-11-28"
OPENROUTER_APP_TITLE = "ADO2GH Migration Agent"


@dataclass(frozen=True)
class LLMProviderSpec:
    id: str
    label: str
    kind: str
    description: str = ""
    default_base_url: str = ""
    catalog_path: str = ""
    preset_key: str = ""
    requires_api_key: bool = True
    requires_base_url: bool = False
    for_cloud: bool = True
    auth_style: str = "bearer"  # bearer | azure-api-key | query-key
    extra_headers: dict[str, str] = field(default_factory=dict)
    completions_path: str = "/chat/completions"
    azure_api_version: str = "2024-06-01"

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "kind": self.kind,
            "default_base_url": self.default_base_url or None,
            "requires_api_key": self.requires_api_key,
            "requires_base_url": self.requires_base_url,
            "preset_key": self.preset_key or self.id,
        }

    def runtime_headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.id == "openrouter":
            headers.setdefault("HTTP-Referer", "https://github.com/ado2gh/migration")
            headers.setdefault("X-Title", OPENROUTER_APP_TITLE)
        return headers


PROVIDER_SPECS: dict[str, LLMProviderSpec] = {
    "openai": LLMProviderSpec(
        id="openai",
        label="OpenAI",
        kind="openai_compatible",
        description="OpenAI platform models",
        default_base_url="https://api.openai.com/v1",
        catalog_path="https://api.openai.com/v1/models",
        preset_key="openai",
    ),
    "anthropic": LLMProviderSpec(
        id="anthropic",
        label="Anthropic",
        kind="anthropic",
        description="Claude models via Anthropic API",
        default_base_url="https://api.anthropic.com",
        catalog_path="https://api.anthropic.com/v1/models",
        preset_key="anthropic",
    ),
    "github_copilot": LLMProviderSpec(
        id="github_copilot",
        label="GitHub Copilot / Models",
        kind="openai_compatible",
        description="GitHub Models inference (PAT with models:read)",
        default_base_url="https://models.github.ai/inference",
        catalog_path="https://models.github.ai/catalog/models",
        preset_key="github_copilot",
        extra_headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    ),
    "openrouter": LLMProviderSpec(
        id="openrouter",
        label="OpenRouter",
        kind="openai_compatible",
        description="Multi-provider routing via OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        catalog_path="https://openrouter.ai/api/v1/models",
        preset_key="openrouter",
    ),
    "azure_openai": LLMProviderSpec(
        id="azure_openai",
        label="Azure OpenAI",
        kind="openai_compatible",
        description="Azure OpenAI deployment (base URL + deployment name)",
        requires_base_url=True,
        auth_style="azure-api-key",
        preset_key="azure_openai",
        completions_path="/deployments/{model_id}/chat/completions",
    ),
    "google_gemini": LLMProviderSpec(
        id="google_gemini",
        label="Google Gemini",
        kind="gemini",
        description="Gemini models via Google AI API",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        preset_key="google_gemini",
        auth_style="query-key",
    ),
    "ollama": LLMProviderSpec(
        id="ollama",
        label="Ollama",
        kind="ollama",
        description="Self-hosted Ollama or compatible runtime",
        requires_api_key=False,
        requires_base_url=True,
        for_cloud=False,
        preset_key="ollama",
    ),
    "stub": LLMProviderSpec(
        id="stub",
        label="Stub",
        kind="stub",
        description="Offline deterministic responses",
        requires_api_key=False,
        for_cloud=False,
        preset_key="stub",
    ),
    "offline": LLMProviderSpec(
        id="offline",
        label="Offline",
        kind="stub",
        description="Alias for stub provider",
        requires_api_key=False,
        for_cloud=False,
        preset_key="stub",
    ),
    "bedrock": LLMProviderSpec(
        id="bedrock",
        label="AWS Bedrock",
        kind="bedrock",
        description="Platform-supplied Bedrock via ambient credentials",
        requires_api_key=False,
        preset_key="bedrock",
    ),
    "foundry": LLMProviderSpec(
        id="foundry",
        label="Microsoft Foundry",
        kind="foundry",
        description="Platform-supplied Microsoft Foundry via ambient credentials",
        requires_api_key=False,
        requires_base_url=True,
        preset_key="foundry",
    ),
    "vertex": LLMProviderSpec(
        id="vertex",
        label="Google Vertex AI",
        kind="vertex",
        description="Platform-supplied Vertex via ambient credentials",
        requires_api_key=False,
        preset_key="vertex",
    ),
}


def get_provider_spec(provider_id: str) -> LLMProviderSpec | None:
    return PROVIDER_SPECS.get(provider_id)


def list_provider_specs(*, include_internal: bool = False) -> list[dict[str, Any]]:
    skip = {"offline"} if not include_internal else set()
    return [
        spec.to_public()
        for key, spec in PROVIDER_SPECS.items()
        if key not in skip
    ]


def is_known_provider(provider_id: str) -> bool:
    return provider_id in PROVIDER_SPECS
