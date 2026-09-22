"""Registry of supported language model agent platforms (catalog, validation, runtime)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GITHUB_API_VERSION = "2022-11-28"
OPENROUTER_APP_TITLE = "ADO2GH Migration Agent"


@dataclass(frozen=True)
class LLMProviderSpec:
    """Static description of one supported language model provider.

    Carries everything the catalog, validation and runtime layers need to talk
    to a provider: its endpoints, how credentials are presented on the wire, and
    which extra headers the provider expects. Instances hold no credentials.
    """

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
        """Render the provider as the shape the settings UI consumes.

        Returns:
            dict[str, Any]: The provider identifier, human label, description and
            integration kind, the suggested default base URL (None when the
            provider has no fixed endpoint), whether an API key and an operator
            supplied base URL are required, and the preset catalog key to look
            up bundled model presets under.
        """
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
        """Build the non-authentication headers this provider expects on requests.

        Returns:
            dict[str, str]: A copy of the provider's static extra headers, plus
            the attribution headers OpenRouter uses to identify the calling
            application. Never contains credentials; callers add authentication
            headers separately.
        """
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
    """Look up the registered spec for a provider identifier.

    Args:
        provider_id: Provider identifier as stored on a model configuration.

    Returns:
        LLMProviderSpec | None: The matching provider spec, or None when the
        identifier is not registered.
    """
    return PROVIDER_SPECS.get(provider_id)


def list_provider_specs() -> list[dict[str, Any]]:
    """List the providers an operator may choose when configuring a model.

    Returns:
        list[dict[str, Any]]: One public provider description per selectable
        provider, in registration order. The ``offline`` entry is omitted
        because it is an internal alias of the stub provider and would appear
        as a duplicate choice.
    """
    return [
        spec.to_public()
        for key, spec in PROVIDER_SPECS.items()
        if key != "offline"
    ]


def is_known_provider(provider_id: str) -> bool:
    """Report whether a provider identifier is registered.

    Args:
        provider_id: Provider identifier to check.

    Returns:
        bool: True when the registry holds a spec for the identifier, including
        internal aliases that ``list_provider_specs`` hides.
    """
    return provider_id in PROVIDER_SPECS
