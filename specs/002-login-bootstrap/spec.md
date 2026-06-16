# Feature Specification: Login Page & Fresh-Instance Admin Bootstrap

**Feature Branch**: `002-login-bootstrap`

**Created**: 2026-06-16

**Status**: Draft

**Input**: UI enhancement for a login page. Assign admin (highest privilege) when the database has no users or the instance is fresh. Must support Docker and Kubernetes deployment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First Admin Bootstrap (Priority: P1)

An operator deploys a fresh ADO2GH instance (empty user store). They open the migration console and see a login page in **bootstrap mode** that creates the first platform account with **admin** role automatically—no separate registration URL.

**Why this priority**: Without a first admin, RBAC, approvals, and gated live runs cannot be secured in production.

**Independent Test**: Start Postgres/SQLite with empty `platform_users` table → open UI → complete bootstrap form → session shows `role: admin` and all protected routes accessible.

**Acceptance Scenarios**:

1. **Given** zero platform users, **When** the operator submits valid credentials on the login page, **Then** the first user is created with `admin` role and receives an authenticated session.
2. **Given** zero platform users, **When** bootstrap completes, **Then** subsequent visits show standard login (not bootstrap creation) for additional users.
3. **Given** bootstrap already completed, **When** a new user attempts bootstrap-only endpoints, **Then** the request is rejected with `403`.

---

### User Story 2 - Standard Login (Priority: P1)

Returning operators authenticate with username/email and password on an OrchestrateAI-styled login page before accessing the migration console.

**Why this priority**: Core gate for all UI and API access in containerized deployments.

**Independent Test**: With existing users, login succeeds/fails appropriately; unauthenticated requests to protected UI routes redirect to `/login`.

**Acceptance Scenarios**:

1. **Given** a registered user, **When** correct credentials are submitted, **Then** session is established and user lands on dashboard.
2. **Given** invalid credentials, **When** login is submitted, **Then** error is shown without revealing whether the account exists.
3. **Given** no session, **When** user navigates to `/migrate`, **Then** they are redirected to `/login`.

---

### User Story 3 - Docker & Kubernetes Deployment (Priority: P2)

Platform teams deploy the stack via existing Docker Compose or new Kubernetes manifests with auth secrets, persistent user store, and health checks.

**Why this priority**: Explicit deployment requirement; auth must work in multi-container prod layouts.

**Independent Test**: `docker compose up` and `kubectl apply` paths both yield login page on web service; bootstrap works against Postgres in prod overlay.

**Acceptance Scenarios**:

1. **Given** `docker-compose.yml` + `docker-compose.prod.yml`, **When** services start, **Then** web UI exposes login and accelerator enforces session on protected routes.
2. **Given** Kubernetes manifests, **When** deployed with configured `JWT_SECRET` and `ADO2GH_DATABASE_URL`, **Then** pods become ready and bootstrap works on empty DB.
3. **Given** container restart, **When** users already exist, **Then** bootstrap mode is not re-enabled.

---

### User Story 4 - Admin User Management (Priority: P3)

Admins invite or create additional users with Coordinator, Operator, or Approver roles (subset of platform permissions).

**Why this priority**: After bootstrap, teams need least-privilege accounts without sharing admin credentials.

**Independent Test**: Admin creates operator account; operator cannot access admin-only user APIs.

**Acceptance Scenarios**:

1. **Given** admin session, **When** admin creates a user with `operator` role, **Then** that user can log in with assigned role only.
2. **Given** operator session, **When** operator calls user-management API, **Then** `403` is returned.

---

### Edge Cases

- Concurrent bootstrap attempts on fresh instance: only one admin created (transactional first-user win).
- Password policy: minimum length enforced; secrets never logged.
- Session expiry and logout clear client and server state.
- SQLite dev vs Postgres prod: same auth behavior, shared schema contract.
- LLM unavailable / accelerator down: login page shows service-unavailable state without leaking internals.

## Requirements *(mandatory)*

### Constitution Alignment

- **CA-001**: Bootstrap is not a destructive migration action; no dry-run path required.
- **CA-002**: Admin role assignment on bootstrap is explicit one-time operator action with audit event.
- **CA-003**: Passwords hashed (bcrypt/argon2); never stored or logged in plaintext.
- **CA-004**: Login, logout, bootstrap, and role changes recorded in audit store.

### Functional Requirements

- **FR-001**: UI MUST provide `/login` page styled with OrchestrateAI tokens (`oai-styles.css`).
- **FR-002**: System MUST detect fresh instance (`platform_users` count = 0) and present bootstrap flow on login page.
- **FR-003**: First successful account creation on fresh instance MUST assign `admin` role (highest privilege, superset of Coordinator + Operator + Approver).
- **FR-004**: System MUST persist platform users in the same storage backend as migration state (SQLite local, Postgres prod).
- **FR-005**: Accelerator API MUST expose auth endpoints: bootstrap status, login, logout, session.
- **FR-006**: Protected API routes MUST require valid session; map session role to existing RBAC (`ProfileRole` + `admin`).
- **FR-007**: Next.js UI MUST protect app routes via middleware or layout guard redirecting unauthenticated users to `/login`.
- **FR-008**: Docker Compose MUST pass auth-related env vars (`JWT_SECRET`, session TTL) to `accelerator` and `web` services.
- **FR-009**: Repository MUST include Kubernetes manifests (Deployment, Service, Ingress optional, Secret references) for web + accelerator + Postgres.
- **FR-010**: Admin MUST be able to create additional users with roles: `admin`, `approver`, `coordinator`, `operator`.
- **FR-011**: Bootstrap endpoints MUST be disabled once any user exists.
- **FR-012**: Login failures MUST use generic error messages (no account enumeration).

### Key Entities

- **PlatformUser**: Console operator account (username, password hash, role, timestamps).
- **AuthSession**: Server-side or signed session bound to user, expiry, user agent metadata optional.
- **BootstrapState**: Derived from user count; no separate flag required if count is authoritative.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Fresh instance bootstrap completes in under 60 seconds including first dashboard load.
- **SC-002**: 100% of protected UI routes reject unauthenticated access in automated tests.
- **SC-003**: Docker Compose prod overlay and K8s sample deploy both support login + bootstrap without manual DB seeding.
- **SC-004**: Zero plaintext passwords in logs, audit payloads, or API responses across test suite.

## Assumptions

- v1 uses username + password (extends FR-022 SSO stretch goal later; OIDC hooks documented).
- Single-tenant platform user store (not per-migration-profile credentials—that remains in profile settings).
- `admin` is a platform role above profile-scoped Coordinator/Operator/Approver.
- Kubernetes manifests target generic cluster (no cloud-specific ingress controller required in v1).
