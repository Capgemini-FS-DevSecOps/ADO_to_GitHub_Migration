"""Guards the provider registry as the single source for capabilities, base
URLs and catalog fetchers, and that the LangChain bridge no longer keeps its
own copy of provider capability defaults.
"""
from __future__ import annotations

import ado2gh.agents.migration_agent.runtime.llm_bridge as llm_bridge
from ado2gh.api.llm.llm_provider_registry import PROVIDER_SPECS, is_known_provider


def test_bridge_no_longer_holds_its_own_provider_capability_map():
    """The old parallel registry must be gone from the bridge module."""
    assert not hasattr(llm_bridge, "_PROVIDER_CAPABILITY_DEFAULTS")


def test_every_registered_provider_has_populated_capabilities():
    """Every provider spec carries capability defaults, not just some."""
    for provider_id, spec in PROVIDER_SPECS.items():
        assert spec.capabilities, f"{provider_id} has no capabilities"
        for key in (
            "supports_tool_calling",
            "supports_streaming",
            "supports_thinking",
            "max_context_tokens",
        ):
            assert key in spec.capabilities, f"{provider_id} missing {key}"


def test_every_provider_with_a_base_url_reads_it_from_the_spec():
    """Cloud providers with a fixed endpoint, and self-hosted ollama, declare
    their base URL on the spec rather than relying on a literal in the bridge.
    """
    providers_with_fixed_base_url = {
        "openai",
        "anthropic",
        "github_copilot",
        "openrouter",
        "google_gemini",
        "ollama",
    }
    for provider_id in providers_with_fixed_base_url:
        spec = PROVIDER_SPECS[provider_id]
        assert spec.default_base_url, f"{provider_id} has no default_base_url"


def test_every_provider_with_a_catalog_has_a_registered_fetcher():
    """Importing model_catalog wires a catalog_fetcher onto every provider
    that has a catalog strategy, so _catalog_result never needs a per-provider
    branch to add a new one.
    """
    # Import triggers the wiring that attaches catalog_fetcher to specs.
    import ado2gh.api.llm.model_catalog  # noqa: F401

    providers_with_a_catalog = {
        "openai",
        "anthropic",
        "github_copilot",
        "openrouter",
        "azure_openai",
        "google_gemini",
        "ollama",
    }
    for provider_id in providers_with_a_catalog:
        spec = PROVIDER_SPECS[provider_id]
        assert spec.catalog_fetcher is not None, f"{provider_id} has no catalog_fetcher"
        assert callable(spec.catalog_fetcher)

    providers_without_a_catalog = set(PROVIDER_SPECS) - providers_with_a_catalog
    for provider_id in providers_without_a_catalog:
        assert PROVIDER_SPECS[provider_id].catalog_fetcher is None


def test_is_known_provider_still_covers_every_registered_id():
    """Sanity check that the registry itself is untouched in shape."""
    for provider_id in PROVIDER_SPECS:
        assert is_known_provider(provider_id)


def test_credential_check_timeout_stays_fast_and_separate_from_request_timeout():
    """A credential connectivity check must fail fast (15 seconds), distinct
    from the longer per-request timeout (30 seconds) used for real data calls.
    """
    from ado2gh.clients.gh_client import DEFAULT_GITHUB_CLIENT_SETTINGS

    assert DEFAULT_GITHUB_CLIENT_SETTINGS.credential_check_timeout_seconds == 15.0
    assert DEFAULT_GITHUB_CLIENT_SETTINGS.request_timeout_seconds == 30
