# Research: Login Page & Admin Bootstrap

**Feature**: `002-login-bootstrap` | **Date**: 2026-06-16

## R1 — Authentication mechanism

**Decision**: Server-side sessions stored in `auth_sessions` table with opaque session token in `HttpOnly` cookie; optional `Authorization: Bearer` header for API clients.

**Rationale**: Fits existing StateDB persistence; easy revocation on logout; avoids long-lived JWT in localStorage (XSS risk). Cookie name `ado2gh_session` configurable via env.

**Alternatives considered**:
- Pure JWT in localStorage — rejected (XSS exposure, harder revocation).
- External IdP only — rejected for v1 (SSO stretch goal FR-022a).

## R2 — Password hashing

**Decision**: `passlib` with `bcrypt` (`rounds=12`).

**Rationale**: Widely available, constitution CA-003 compliant, minimal deps.

**Alternatives considered**:
- Argon2 — stronger but extra native dep; can migrate later.

## R3 — Admin role definition

**Decision**: `PlatformRole.ADMIN` is highest privilege; maps to all `ProfileRole` values plus user-management APIs. First bootstrap user always receives `admin`.

**Rationale**: Matches user request; aligns with existing `RBAC.from_actor("approver")` superset pattern in `ado2gh/assignments/rbac.py`.

**Alternatives considered**:
- Bootstrap as `approver` only — rejected (cannot manage users or full platform config).

## R4 — Fresh instance detection

**Decision**: `SELECT COUNT(*) FROM platform_users` === 0 → bootstrap mode. No persistent `bootstrap_completed` flag (count is authoritative).

**Rationale**: Simple, self-healing if users table truncated in dev only (ops responsibility).

**Alternatives considered**:
- Env flag `ADO2GH_FORCE_BOOTSTRAP` — optional dev override documented in quickstart only.

## R5 — UI integration

**Decision**: New `/login` route; Next.js `middleware.ts` protects all routes except `/login`, `/health`, static assets. Login page uses existing `oai-styles.css` patterns (`oai-card`, `oai-button`).

**Rationale**: Minimal disruption to existing pages; OAI compliance (FR-023 from platform spec).

**Alternatives considered**:
- Modal login overlay — rejected (poor deep-link and session expiry UX).

## R6 — Docker deployment

**Decision**: Extend `docker-compose.yml` and `docker-compose.prod.yml` with:
- `JWT_SECRET` / `SESSION_SECRET` (signing cookie)
- `ADO2GH_SESSION_TTL_HOURS` (default 8)
- `ADO2GH_COOKIE_SECURE` (false dev, true prod)
- Web service: `NEXT_PUBLIC_REQUIRE_AUTH=true`

**Rationale**: Reuses existing compose topology; Postgres prod overlay already provides `ADO2GH_DATABASE_URL`.

**Alternatives considered**:
- Separate auth container — rejected (unnecessary for v1 scale).

## R7 — Kubernetes deployment

**Decision**: Add `deploy/kubernetes/` with:
- `postgres` StatefulSet or Deployment + PVC
- `accelerator` Deployment (2 replicas optional in example)
- `web` Deployment
- `ConfigMap` for non-secret env
- `secret.yaml.example` for `SESSION_SECRET`, `POSTGRES_PASSWORD`
- Optional `Ingress` example for TLS termination

**Rationale**: User explicitly requested K8s support; manifests are templates not tied to EKS/GKE specifics.

**Alternatives considered**:
- Helm chart — deferred to v2 (more moving parts).

## R8 — Protecting existing API

**Decision**: FastAPI dependency `require_auth` on existing `/v1/*` except: `/health`, `/ready`, `/v1/auth/*`. Gradual rollout flag `ADO2GH_AUTH_ENABLED` (default `true` in prod compose, `false` optional for local dev without login).

**Rationale**: Allows incremental migration from open local dev to secured prod without breaking existing scripts that use PAT-only profile APIs.

**Alternatives considered**:
- Auth on all routes immediately — may break unattended local CLI; env flag preserves escape hatch.

## R9 — Audit integration

**Decision**: Login success/failure, bootstrap, logout, user create → `audit_events` via existing `AuditWriter` with `event_type` prefix `auth_*`.

**Rationale**: Constitution CA-004; reuses FR-037 audit infrastructure from agentic platform.

## R10 — Testing strategy

**Decision**: Unit tests for `AuthService.bootstrap_first_admin`, concurrent bootstrap lock; contract tests for auth API shapes; UI smoke test for login form render.

**Rationale**: Constitution VI — scoped 85% on `ado2gh/auth/`.

**Alternatives considered**:
- E2E only — insufficient for race/bootstrap edge cases.
