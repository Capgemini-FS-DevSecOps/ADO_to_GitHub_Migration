"""Agent service  Planner-Executor-Validator loop with MCP tool exposure."""
from __future__ import annotations

import os

import httpx

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.agents.migration_agent.constants import TOOL_CATALOG_VERSION
from ado2gh.agents.migration_agent.metrics import (
    increment_metric,
    observe_llm_duration,
    get_metric,
)
from ado2gh.auth.service import AuthService, SESSION_COOKIE, auth_enabled, permissions_for


def _metrics_get(name: str) -> float:
    """Read a metric value for Prometheus output."""
    return get_metric(name)

from ado2gh.agents.migration_agent.route_helpers import (
    ACCEL_URL,
    RunStatus,
    _accel_get_impl as _accel_get,
    _accel_headers,
    _accel_post_impl as _accel_post,
    _add_message,
    _audit,
    _build_migration_plan,
    _check_accelerator,
    _ensure_migration_plan,
    _enqueue_session_live_approval,
    _get_accessible_session,
    _platform_user,
    _profile,
    _resolve_model_id,
    _runs,
    _session_accel_token,
    _session_payload,
    _session_token_from_request,
    _sessions,
    _try_start_pev_run,
)
from services.agent.routes.run_routes import router as run_router
from services.agent.routes.session_routes import router as session_router
from services.agent.routes.mcp_routes import router as mcp_router

app = FastAPI(title="ADO2GH Agent API", version="5.1.0")
app.include_router(run_router)
app.include_router(session_router)
app.include_router(mcp_router)


@app.on_event("startup")
def _sync_platform_supplied_model() -> None:
    from ado2gh.api.llm.platform_managed_model import sync_on_startup

    sync_on_startup()


@app.on_event("startup")
def _recover_sessions_on_restart() -> None:
    """T061: On startup, load persisted sessions and mark active ones for resume."""
    try:
        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
        store = MigrationSessionStore()
        sessions = store.list_sessions()
        for s in sessions:
            status = s.get("status", "idle")
            if status in ("planning", "executing", "validating", "thinking"):
                # Mark as awaiting_input — user can resume via next message
                store.update_session_status(s["session_id"], "awaiting_input")
    except Exception:
        pass  # Non-fatal — sessions will be created fresh on user interaction


@app.get("/health")
async def health():
    """T072: Health endpoint with LLM provider availability, session count, storage status."""
    from ado2gh.api.llm.llm_model_store import LLMModelStore
    from ado2gh.agents.migration_agent.graph import get_compiled_graph
    import os

    # LLM provider availability
    store = LLMModelStore()
    has_agent_ready = store.has_agent_ready_model()
    ready_models = store.list_agent_ready_models()
    default_model = store.get_default_model()

    # Session counts
    active_count = sum(
        1 for s in _sessions.values()
        if s.get("status") in ("planning", "executing", "validating", "thinking")
    )
    idle_count = sum(1 for s in _sessions.values() if s.get("status", "idle") == "idle")
    completed_count = sum(1 for s in _sessions.values() if s.get("status") == "completed")
    failed_count = sum(1 for s in _sessions.values() if s.get("status") == "failed")

    # Storage backend
    storage_backend = os.environ.get("ADO2GH_STORAGE_BACKEND", "sqlite")

    # Graph compiled status
    try:
        graph = get_compiled_graph()
        graph_compiled = graph is not None
    except Exception:
        graph_compiled = False

    return {
        "status": "healthy",
        "llm_provider": {
            "available": has_agent_ready,
            "ready_model_count": len(ready_models),
            "default_model_id": default_model.id if default_model else None,
        },
        "sessions": {
            "active": active_count,
            "idle": idle_count,
            "completed": completed_count,
            "failed": failed_count,
            "total": len(_sessions),
        },
        "storage": {
            "backend": storage_backend,
        },
        "graph": {
            "compiled": graph_compiled,
        },
        "configuration": {
            "storage_backend": storage_backend,
        },
    }


@app.get("/metrics")
async def metrics():
    """T071: Prometheus-compatible metrics endpoint."""
    active_count = sum(
        1 for s in _sessions.values()
        if s.get("status") in ("planning", "executing", "validating", "thinking")
    )
    idle_count = sum(1 for s in _sessions.values() if s.get("status", "idle") == "idle")
    completed_count = sum(1 for s in _sessions.values() if s.get("status") == "completed")
    failed_count = sum(1 for s in _sessions.values() if s.get("status") == "failed")

    lines = [
        f"# HELP active_sessions Number of sessions in active PEV state",
        f"# TYPE active_sessions gauge",
        f"active_sessions {active_count}",
        f"# HELP idle_sessions Number of idle sessions",
        f"# TYPE idle_sessions gauge",
        f"idle_sessions {idle_count}",
        f"# HELP completed_sessions Number of completed sessions",
        f"# TYPE completed_sessions gauge",
        f"completed_sessions {completed_count}",
        f"# HELP failed_sessions Number of failed sessions",
        f"# TYPE failed_sessions gauge",
        f"failed_sessions {failed_count}",
        f"# HELP total_sessions Total number of sessions",
        f"# TYPE total_sessions gauge",
        f"total_sessions {len(_sessions)}",
        f"# HELP pev_cycles_total Total number of PEV cycles started",
        f"# TYPE pev_cycles_total counter",
        f"pev_cycles_total {_metrics_get('pev_cycles_total')}",
        f"# HELP llm_call_duration_seconds LLM call duration in seconds",
        f"# TYPE llm_call_duration_seconds histogram",
        f'llm_call_duration_seconds_sum {_metrics_get("llm_call_duration_seconds_sum")}',
        f'llm_call_duration_seconds_count {_metrics_get("llm_call_duration_seconds_count")}',
        f"# HELP guardrail_blocks_total Total guardrail blocks",
        f"# TYPE guardrail_blocks_total counter",
        f"guardrail_blocks_total {_metrics_get('guardrail_blocks_total')}",
        f"# HELP tool_calls_total Total tool calls invoked",
        f"# TYPE tool_calls_total counter",
        f"tool_calls_total {_metrics_get('tool_calls_total')}",
    ]
    return "\n".join(lines) + "\n"


_AGENT_AUTH_EXEMPT = {
    "/health",
    "/metrics",
    "/v1/llm/status",
    "/v1/llm-status",
    "/v1/mcp/tools",
}


@app.middleware("http")
async def agent_auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/v1/") or path in _AGENT_AUTH_EXEMPT:
        return await call_next(request)
    if path.startswith("/v1/internal/"):
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    session = AuthService().get_session(token)
    if not session:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    request.state.platform_user = session.user
    request.state.permissions = permissions_for(session.user.role)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
