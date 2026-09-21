"""Health and metrics route handlers for the agent service."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ado2gh.agents.migration_agent.constants import HEALTH_GRAPH_COMPILE_TIMEOUT_SECONDS
from ado2gh.auth.service import auth_enabled
from services.agent.profiles import capability_matrix
from services.agent.routes._helpers import (
    _check_accelerator,
    _profile,
    _resolve_model_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    """Report agent health, accelerator reachability, and LLM readiness.

    The route is authentication-exempt (``services/agent/main.py``
    ``_AGENT_AUTH_EXEMPT``), so it reports only booleans and fixed remediation
    text. It used to also return the accelerator URL and the raw connection
    error, and both describe internal network topology to a caller who was
    never authenticated — anyone who can reach the port could learn it (register
    item THR-02-004). That detail is logged instead of returned.

    Returns:
        A status document naming the selected model, the active deployment
        profile, session and storage health, and — when a dependency is
        unreachable or unconfigured — the steps needed to restore it.
    """
    reachable, err = await _check_accelerator()
    if not reachable:
        logger.warning("accelerator health probe failed: %s", err)
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

    # Graph compiled status. Bounded: compiling opens the checkpointer database,
    # and waiting on it with no timeout here would wedge the whole health check
    # (and the Starlette test client's blocking portal with it) whenever that
    # stalls. A status check must always return promptly rather than hang on a
    # slow dependency (register item GAP-062).
    graph_compiled = False
    checkpointer_ok = False
    try:
        from ado2gh.agents.migration_agent.graph import get_compiled_graph
        graph = await asyncio.wait_for(
            get_compiled_graph(), timeout=HEALTH_GRAPH_COMPILE_TIMEOUT_SECONDS,
        )
        graph_compiled = graph is not None
        checkpointer_ok = True  # If graph compiled, checkpointer is attached
    except TimeoutError:
        logger.warning(
            "graph compilation did not finish within %ss; reporting not compiled",
            HEALTH_GRAPH_COMPILE_TIMEOUT_SECONDS,
        )
    except Exception:
        pass

    body: dict[str, Any] = {
        "status": "ok" if reachable and not llm_degraded and storage_ok else "degraded",
        "service": "agent",
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
async def metrics() -> PlainTextResponse:
    """Expose agent counters and live session gauges in Prometheus text format."""
    from ado2gh.agents.metrics import get_metrics_collector
    from services.agent.routes._helpers import _sessions

    collector = get_metrics_collector()
    # FR-102: session gauges reflect current in-memory sessions
    statuses = [s.get("status", "idle") for s in _sessions.values()]
    active = ("planning", "executing", "validating", "thinking")
    collector.set_gauge("active_sessions", sum(1 for s in statuses if s in active))
    for status in set(statuses):
        collector.set_session_status(status, statuses.count(status))
    return PlainTextResponse(content=collector.to_prometheus_text(), media_type="text/plain")
