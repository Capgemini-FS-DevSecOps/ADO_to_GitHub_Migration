"""Language model status and agent model catalogue routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from services.agent.routes._helpers import (
    _resolve_model_id,
)

router = APIRouter()


@router.get("/v1/agent/models")
def agent_models(_request: Request) -> dict[str, Any]:
    """List the language models the agent can use, with the default one marked."""
    from ado2gh.api.agent_models import list_agent_models

    return list_agent_models()


@router.get("/v1/llm/status")
def llm_status() -> dict[str, Any]:
    """Report whether a language model is configured, which model is selected, and how degraded it is."""
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    store = LLMModelStore()
    default = store.get_default_model()
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(None)
    enabled = store.list_agent_ready_models()
    return {
        "provider": default.provider if default else None,
        "available": not llm_unconfigured,
        "degraded": llm_degraded,
        "unconfigured": llm_unconfigured,
        "message": (
            "No LLM models configured — add one under Settings → LLM models"
            if llm_unconfigured
            else ("Using deterministic stub planner" if llm_degraded else f"model={selected_model_id}")
        ),
        "backend": default.provider if default else None,
        "models_configured": len(enabled),
        "default_model_id": default.id if default else None,
        "selected_model_id": selected_model_id,
    }
