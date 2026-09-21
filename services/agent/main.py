"""Agent service  Planner-Executor-Validator loop."""
# ruff: noqa: E402  -- imports below intentionally follow ensure_gei_dotnet_env()
from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable

import httpx  # noqa: F401 -- re-exported; tests patch services.agent.main.httpx.AsyncClient

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.auth.service import SESSION_COOKIE, AuthService, auth_enabled, permissions_for
from services.agent.routes._helpers import (
    # RunStatus, _accel_get, _accel_headers, _accel_post: re-exported so tests
    # can import/patch them via services.agent.main (_helpers' lazy
    # wrappers look up sys.modules["services.agent.main"] for patches).
    RunStatus,  # noqa: F401
    _accel_headers,  # noqa: F401
    _sessions,  # noqa: F401
)
from services.agent.routes._helpers import (
    _accel_get_impl as _accel_get,  # noqa: F401
)
from services.agent.routes._helpers import (
    _accel_post_impl as _accel_post,  # noqa: F401
)
from services.agent.routes.execution_routes import router as execution_router
from services.agent.routes.form_routes import router as form_router
from services.agent.routes.message_routes import router as message_router
from services.agent.routes.model_routes import router as model_router
from services.agent.routes.plan_routes import router as plan_router
from services.agent.routes.run_routes import router as run_router
from services.agent.routes.session_routes import router as session_router

logger = logging.getLogger(__name__)

app = FastAPI(title="ADO2GH Agent API", version="5.1.0")
app.include_router(run_router)
app.include_router(session_router)
app.include_router(message_router)
app.include_router(form_router)
app.include_router(plan_router)
app.include_router(execution_router)
app.include_router(model_router)


@app.on_event("startup")
def _sync_platform_supplied_model() -> None:
    from ado2gh.api.llm.platform_managed_model import sync_on_startup

    sync_on_startup()


@app.on_event("startup")
def _configure_agent_tracing() -> None:
    from ado2gh.agents.migration_agent.runtime.tracing import configure_tracing_from_env

    configure_tracing_from_env()


@app.on_event("startup")
def _recover_sessions_on_restart() -> None:
    """On startup, load persisted sessions and mark the active ones for resume (T061)."""
    try:
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
        store = MigrationSessionStore()
        sessions = store.list_sessions()
        for s in sessions:
            status = s.get("status", "idle")
            if status in ("planning", "executing", "validating", "thinking"):
                # Mark as awaiting_input — user can resume via next message
                store.update_session_status(s["session_id"], "awaiting_input")
    except Exception:
        pass  # Non-fatal — sessions will be created fresh on user interaction


_AGENT_AUTH_EXEMPT = {
    "/health",
    "/metrics",
    "/v1/llm/status",
}

# /v1/internal/ is the service-to-service range the accelerator uses to resume or
# deny a live migration, so it carries no operator session cookie. Guard it with a
# shared secret instead. The secret is mandatory whenever authentication is on: an
# unset token used to open the range unconditionally (GAP-003), leaving the published
# agent port one request away from an unauthenticated live-execution resume. It now
# fails closed, and the hardened manifests supply the token — docker-compose.prod.yml
# refuses to start without it and deploy/kubernetes/secret.yaml.example carries it.
# The dev docker-compose.yml still leaves it empty, which is harmless there because
# it also turns authentication off, so the middleware never reaches this check.
INTERNAL_TOKEN_HEADER = "x-ado2gh-internal-token"
_INTERNAL_TOKEN = os.environ.get("ADO2GH_INTERNAL_TOKEN", "")
if not _INTERNAL_TOKEN:
    logger.warning(
        "ADO2GH_INTERNAL_TOKEN is unset — /v1/internal/ (live-migration approval) "
        "is refused whenever ADO2GH_AUTH_ENABLED is on. Set it on the agent and the "
        "accelerator."
    )


@app.middleware("http")
async def agent_auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Authenticate every /v1/ request before it reaches a route handler.

    Health, metrics and LLM-status paths are exempt. Service-to-service calls
    under /v1/internal/ must carry the shared secret in the
    ``x-ado2gh-internal-token`` header; every other /v1/ path needs a valid
    operator session cookie. A request that fails either check gets a 401 and
    never reaches its handler.

    Args:
        request: The incoming HTTP request.
        call_next: The next handler in the middleware chain.

    Returns:
        The handler response, or a 401 JSON response when authentication fails.
    """
    if not auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/v1/") or path in _AGENT_AUTH_EXEMPT:
        return await call_next(request)
    if path.startswith("/v1/internal/"):
        supplied = request.headers.get(INTERNAL_TOKEN_HEADER, "")
        if _INTERNAL_TOKEN and hmac.compare_digest(supplied, _INTERNAL_TOKEN):
            return await call_next(request)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
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
