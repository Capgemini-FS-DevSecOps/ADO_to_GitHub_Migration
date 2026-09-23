"""Route prefixes for the accelerator's Azure DevOps and GitHub proxy.

Both prefixes are used on two sides of one HTTP boundary:
``services/accelerator_api/routes/proxy_routes.py`` declares the FastAPI
routes that own them, and the modules under ``ado2gh/agents/migration_agent/``
build request paths against them as an HTTP client of that service. Defining
each prefix once here, instead of retyping the literal string on both sides,
keeps the client and the route it calls from silently drifting apart.
"""
from __future__ import annotations

# The accelerator's read-only proxy to the Azure DevOps REST API.
ADO_PROXY_PREFIX = "/v1/ado"

# The accelerator's proxy to the GitHub REST API. Read and write; write verbs
# are gated by role-based access control and audited (see proxy_routes.py).
GITHUB_PROXY_PREFIX = "/v1/github"

# The accelerator's live-execution approval queue. The agent service posts to
# this path from services/agent/routes/_helpers.py to open or reuse an
# approval row for a session's live-execution request.
PLATFORM_APPROVALS_PATH = "/v1/platform/approvals"
