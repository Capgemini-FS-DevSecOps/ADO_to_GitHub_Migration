"""LLM model catalog, provider registry, and platform-managed model integration.

Consolidated from scattered modules in ado2gh/api/ per FR-027.
"""
from ado2gh.api.llm.http_llm import build_llm_http_client as build_llm_http_client
from ado2gh.api.llm.llm_model_store import (
    LLMModelConfig as LLMModelConfig,
)
from ado2gh.api.llm.llm_model_store import (
    LLMModelStore as LLMModelStore,
)
from ado2gh.api.llm.llm_model_store import (
    invalidate_all_model_validations as invalidate_all_model_validations,
)
from ado2gh.api.llm.llm_model_store import (
    invalidate_ambient_models as invalidate_ambient_models,
)
from ado2gh.api.llm.llm_provider_registry import (
    LLMProviderSpec as LLMProviderSpec,
)
from ado2gh.api.llm.llm_provider_registry import (
    get_provider_spec as get_provider_spec,
)
from ado2gh.api.llm.llm_provider_registry import (
    is_known_provider as is_known_provider,
)
from ado2gh.api.llm.llm_provider_registry import (
    list_provider_specs as list_provider_specs,
)
from ado2gh.api.llm.model_catalog import list_catalog as list_catalog
from ado2gh.api.llm.model_validation import (
    validate_draft as validate_draft,
)
from ado2gh.api.llm.model_validation import (
    validate_saved as validate_saved,
)
from ado2gh.api.llm.platform_managed_model import (
    PlatformSuppliedModelConfig as PlatformSuppliedModelConfig,
)
from ado2gh.api.llm.platform_managed_model import (
    platform_model_payload as platform_model_payload,
)
from ado2gh.api.llm.platform_managed_model import (
    platform_model_status as platform_model_status,
)
from ado2gh.api.llm.platform_managed_model import (
    read_platform_config as read_platform_config,
)
from ado2gh.api.llm.platform_managed_model import (
    sync_on_startup as sync_on_startup,
)

__all__ = [
    "LLMModelConfig",
    "LLMModelStore",
    "invalidate_ambient_models",
    "invalidate_all_model_validations",
    "LLMProviderSpec",
    "get_provider_spec",
    "list_provider_specs",
    "is_known_provider",
    "list_catalog",
    "validate_draft",
    "validate_saved",
    "build_llm_http_client",
    "PlatformSuppliedModelConfig",
    "read_platform_config",
    "sync_on_startup",
    "platform_model_payload",
    "platform_model_status",
]
