# Implementation Plan: Login Page & Fresh-Instance Admin Bootstrap

**Branch**: `002-login-bootstrap` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: UI login page with automatic admin assignment on fresh/empty user database; Docker and Kubernetes deployment support.

## Summary

Add platform authentication to the migration console: an OrchestrateAI-styled **login page**, **session-based auth** on the Accelerator API, and a **one-time bootstrap** path that creates the first user as **admin** when `platform_users` is empty. Extend existing Docker Compose files and add **Kubernetes manifests** so auth works in containerized prod (Postgres-backed user store).

Integrates with existing assignment RBAC (`Coordinator`, `Operator`, `Approver`) by mapping platform roles into `ado2gh.assignments.rbac.RBAC`; `admin` is a superset role for user management and all migration actions.

## Technical Context

**Language/Version**: Python >= 3.9 (`ado2gh` auth module); TypeScript / Next.js 14 (`apps/migration-ui`)

**Primary Dependencies**: FastAPI + `python-jose` or signed cookies; `passlib[bcrypt]` for password hashing; existing pydantic, httpx; Next.js middleware for route protection

**Storage**: `platform_users` and `auth_sessions` tables in SQLite (`StateDB`) and Postgres (`PostgresStateDB`); same backend switch as migration state (`ADO2GH_STORAGE_BACKEND`)

**Testing**: pytest for auth service, bootstrap race, session middleware; Playwright or React Testing Library for login page; contract tests for `contracts/auth-api.md`; maintain **85% coverage** on new `ado2gh/auth/` package (CI scoped gate)

**Target Platform**: Linux containers — Docker Compose (dev/prod) and Kubernetes (Deployment + Service + ConfigMap/Secret)

**Project Type**: Web UI + REST API extension (no new standalone service)

**Performance Goals**: Login p95 < 500ms; bootstrap check cached per request with single COUNT query

**Constraints**: No plaintext passwords in logs/audit; bootstrap disabled after first user; CSRF-safe cookie settings in prod (`Secure`, `HttpOnly`, `SameSite=Lax`)

**Scale/Scope**: Small user cardinality (operators per enterprise); sessions TTL configurable (default 8h)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Dedicated `ado2gh/auth/` module; thin FastAPI routes |
| II. Documentation | Docstrings on auth service, password utils, middleware |
| III. Deprecation | Profile PAT flows remain; UI login wraps platform layer |
| IV. Architecture & Naming | `auth/`, `platform_users`, `/v1/auth/*` — clear domain names |
| V. Enterprise Safeguards | Audit events for login/bootstrap; secrets in env/K8s Secret |
| VI. Testing (85%+) | Auth package tests + contract tests in CI |

**Result**: [x] PASS — all gates satisfied

**Post-design re-check**: [x] PASS — data model uses existing StateDB backends; no unjustified complexity.

## Project Structure

### Documentation (this feature)

```text
specs/002-login-bootstrap/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1 validation guide
├── contracts/           # API + UI contracts
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
ado2gh/
├── auth/
│   ├── __init__.py
│   ├── models.py          # PlatformRole enum, User, Session dataclasses
│   ├── password.py        # hash/verify
│   ├── service.py         # login, logout, bootstrap, create_user
│   ├── middleware.py      # FastAPI dependency get_current_user
│   └── rbac_map.py        # PlatformRole → ProfileRole mapping
├── state/
│   ├── db.py              # + platform_users, auth_sessions tables/methods
│   └── postgres_db.py     # mirror auth tables

services/accelerator_api/
├── main.py                # include auth routes; protect existing /v1/* (except health, auth)

apps/migration-ui/
├── src/app/login/page.tsx           # OAI login + bootstrap UX
├── src/middleware.ts                # redirect unauthenticated → /login
├── src/lib/auth.ts                  # session cookie / API client helpers
└── src/components/AppShell.tsx      # show user + logout

deploy/
├── kubernetes/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secret.yaml.example
│   ├── postgres.yaml
│   ├── accelerator-deployment.yaml
│   ├── web-deployment.yaml
│   └── ingress.yaml.example

docker-compose.yml           # + JWT_SECRET, SESSION_COOKIE_NAME
docker-compose.prod.yml        # unchanged Postgres wiring; auth uses same DB

tests/
├── test_auth_bootstrap.py
├── test_auth_login.py
├── test_auth_middleware.py
└── contract/test_auth_contract.py
```

**Structure Decision**: Extend monorepo layout—auth logic in `ado2gh/auth/`, UI in existing `migration-ui`, K8s under `deploy/kubernetes/` (new). No separate auth microservice in v1.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | — | — |

## Rollback & Phase Gates

Not applicable — auth feature does not alter migration phase gates. Existing assignment gates remain unchanged; auth middleware adds session identity to audit `actor` field.
