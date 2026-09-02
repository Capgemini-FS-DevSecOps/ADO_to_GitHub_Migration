"""Health and metrics route handlers for the agent service."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ado2gh.agents.migration_agent.route_helpers import (
    ACCEL_URL,
    _check_accelerator,
    _profile,
    _resolve_model_id,
)
from ado2gh.auth.service import auth_enabled
from services.agent.profiles import capability_matrix

router = APIRouter()


@router.get("/health")
async def health():
    reachable, err = await _check_accelerator()
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(None)
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    store = LLMModelStore()
    enabled_models = store.list_agent_ready_models()
    profile = _profile()

    # T078: Active session count and storage backend connectivity
    active_session_count = 0
    storage_ok = True
    try:
        from ado2gh.agents.migration_agent.session.store import SessionStore
        ss = SessionStore()
        sessions = ss.list_sessions()
        active_session_count = sum(1 for s in sessions if s.get("status") not in ("completed", "failed", "cancelled", "idle"))
    except Exception:
        storage_ok = False

    # Graph compiled status
    graph_compiled = False
    checkpointer_ok = False
    try:
        from ado2gh.agents.migration_agent.graph import get_compiled_graph
        graph = await get_compiled_graph()
        graph_compiled = graph is not None
        checkpointer_ok = True  # If graph compiled, checkpointer is attached
    except Exception:
        pass

    body: dict[str, Any] = {
        "status": "ok" if reachable and not llm_degraded and storage_ok else "degraded",
        "service": "agent",
        "accelerator_url": ACCEL_URL,
        "accelerator_reachable": reachable,
        "profile": profile.profile_id if profile else "lightweight",
        "selected_model_id": selected_model_id,
        "models_configured": len(enabled_models),
        "llm_degraded": llm_degraded,
        "llm_unconfigured": llm_unconfigured,
        "auth_enabled": auth_enabled(),
        "active_session_count": active_session_count,
        "storage_backend_ok": storage_ok,
        "graph_compiled": graph_compiled,
        "checkpointer_ok": checkpointer_ok,
        "remediation_steps": [],
    }
    if profile:
        body["capabilities"] = capability_matrix(profile)
    if not reachable:
        body["connection_error"] = err or "Connection refused"
        body["remediation_steps"] = [
            "Restart accelerator — a live GEI/git-mirror run may have blocked the API (stop and start port 8080)",
            "Start accelerator: docker compose up accelerator  (or .\\scripts\\dev\\run-local-agent.ps1)",
            "Verify ADO2GH_DATA_DIR is shared between accelerator and agent containers",
            "Check ACCELERATOR_URL matches running service (http://localhost:8080 for local dev)",
            "Ensure browser sends session cookie (credentials: include)",
        ]
    elif llm_unconfigured:
        body["remediation_steps"] = [
            "Open Settings → LLM models and add a provider (OpenAI, Anthropic, or Ollama)",
            "Run Validate on the model, then enable it and mark as default for agent",
            "Start a new agent chat after at least one model is enabled",
        ]
    elif llm_degraded:
        body["remediation_steps"] = [
            "Add and validate an LLM model under Settings → LLM models",
            "Enable the model and optionally mark it as default for agent",
            "Rebuild agent/accelerator after changing docker-compose volume mounts",
        ]
    return body


@router.get("/metrics")
async def metrics():
    """T077: Prometheus-compatible metrics endpoint (FR-069, FR-102)."""
    from fastapi.responses import PlainTextResponse

    from ado2gh.agents.metrics import get_metrics_collector
    from ado2gh.agents.migration_agent.route_helpers import _sessions

    collector = get_metrics_collector()
    # FR-102: session gauges reflect current in-memory sessions
    statuses = [s.get("status", "idle") for s in _sessions.values()]
    active = ("planning", "executing", "validating", "thinking")
    collector.set_gauge("active_sessions", sum(1 for s in statuses if s in active))
    for status in set(statuses):
        collector.set_session_status(status, statuses.count(status))
    return PlainTextResponse(content=collector.to_prometheus_text(), media_type="text/plain")
