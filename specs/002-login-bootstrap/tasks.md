# Tasks: Login Page & Fresh-Instance Admin Bootstrap

**Input**: Design documents from `specs/002-login-bootstrap/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% on `ado2gh/auth/`)

**Organization**: Tasks grouped by user story. Partial implementation exists from hotfix session — completed items marked `[x]`.

**Context**: User reported dashboard API unreachable, no logout, no create-account entry — tasks T031–T038 address UI gaps; T039–T042 address Docker connectivity.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 [P] Add auth env vars to `.env.example` (`ADO2GH_AUTH_ENABLED`, `SESSION_SECRET`)
- [x] T002 [P] Extend `docker-compose.prod.yml` with `ADO2GH_AUTH_ENABLED=true` on accelerator
- [x] T003 [P] Add web Docker build args in `docker-compose.yml` and `docker-compose.prod.yml` for `NEXT_PUBLIC_*`
- [x] T004 [P] Update `apps/migration-ui/Dockerfile` with `NEXT_PUBLIC_ACCELERATOR_URL`, `NEXT_PUBLIC_REQUIRE_AUTH` build args
- [X] T005 [P] Add `SESSION_SECRET` and auth vars to `specs/002-login-bootstrap/contracts/deployment.md` quick reference
- [X] T006 [P] Add pytest-cov scope for `ado2gh/auth/` in `pyproject.toml` with 85% gate

---

## Phase 2: Foundational (Blocking Prerequisites)

- [x] T007 Extend SQLite schema in `ado2gh/state/db.py` — `platform_users`, `auth_sessions`
- [x] T008 Mirror auth tables in `ado2gh/state/postgres_db.py`
- [x] T009 Implement `ado2gh/auth/password.py` — hash/verify, min length policy
- [x] T010 Implement `ado2gh/auth/models.py` — `PlatformRole`, `PlatformUser`, `AuthSession`
- [x] T011 Implement `ado2gh/auth/service.py` — bootstrap, login, logout, session lookup
- [x] T012 Implement `services/accelerator_api/auth_routes.py` per `contracts/auth-api.md`
- [x] T013 Wire auth router + HTTP middleware in `services/accelerator_api/main.py` when `ADO2GH_AUTH_ENABLED=true`
- [X] T014 [P] Add `tests/test_auth_api.py` contract tests for bootstrap-status, bootstrap, login, logout, session
- [x] T015 [P] Add `tests/test_auth_service.py` for bootstrap and login flows

**Checkpoint**: API auth layer functional on SQLite and Postgres

---

## Phase 3: User Story 1 — First Admin Bootstrap (Priority: P1) 🎯 MVP

**Goal**: Fresh instance shows bootstrap login; first user becomes admin.

**Independent Test**: Empty `platform_users` → `/login` bootstrap form → admin session → dashboard loads.

### Tests for User Story 1

- [X] T016 [P] [US1] Add Playwright or RTL test for bootstrap flow in `apps/migration-ui/src/app/login/LoginClient.test.tsx`
- [X] T017 [P] [US1] Test concurrent bootstrap rejection in `tests/test_auth_service.py`

### Implementation for User Story 1

- [x] T018 [US1] Create `apps/migration-ui/src/app/login/LoginClient.tsx` bootstrap mode
- [x] T019 [US1] Create `apps/migration-ui/src/app/login/page.tsx` with Suspense wrapper
- [X] T020 [US1] Add confirm-password field and client validation per `contracts/login-ui.md` in `LoginClient.tsx`
- [x] T021 [US1] Implement `apps/migration-ui/src/lib/auth.ts` — bootstrap, login, session, logout with `credentials: 'include'`
- [x] T022 [US1] Style login page in `apps/migration-ui/src/app/globals.css` (OAI theme)
- [X] T023 [US1] Redirect to `returnUrl` query param after successful login/bootstrap

**Checkpoint**: US1 — bootstrap creates admin and lands on dashboard

---

## Phase 4: User Story 2 — Standard Login (Priority: P1)

**Goal**: Returning users sign in; protected routes require session; logout works.

**Independent Test**: Existing user login; unauthenticated `/migrate` → `/login`; logout clears session.

### Tests for User Story 2

- [X] T024 [P] [US2] Add middleware test or e2e redirect test for protected routes in `apps/migration-ui`

### Implementation for User Story 2

- [x] T025 [US2] Implement `apps/migration-ui/src/components/AuthGate.tsx` — session check + redirect
- [x] T026 [US2] Implement `apps/migration-ui/src/middleware.ts` — redirect when `NEXT_PUBLIC_REQUIRE_AUTH=true`
- [x] T027 [US2] Implement `apps/migration-ui/src/components/UserSessionBar.tsx` — sign in link + log out
- [x] T028 [US2] Wire `UserSessionBar` into `apps/migration-ui/src/components/AppShell.tsx` header
- [x] T029 [US2] Update `apps/migration-ui/src/lib/api.ts` — `credentials: 'include'`, 401 messaging
- [x] T030 [US2] Dashboard error links to `/login` and bootstrap in `apps/migration-ui/src/app/page.tsx`
- [X] T031 [US2] Show generic invalid-credentials message on login failure (no account enumeration)
- [X] T032 [US2] Skip `AppShell` / `NavTabs` on `/login` per `contracts/login-ui.md` (verify `AppShell.tsx`)

**Checkpoint**: US2 — login, logout, route protection complete

---

## Phase 5: User Story 3 — Docker & Kubernetes Deployment (Priority: P2)

**Goal**: Prod Compose enables auth; accelerator reachable from browser; login on first boot.

**Independent Test**: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build` → `/login` bootstrap → dashboard after auth.

### Tests for User Story 3

- [X] T033 [P] [US3] Add smoke script `scripts/auth-smoke.ps1` — health, bootstrap-status, optional bootstrap dry-run

### Implementation for User Story 3

- [x] T034 [US3] `docker-compose.prod.yml` — web `NEXT_PUBLIC_REQUIRE_AUTH=true` build args
- [X] T035 [US3] Add accelerator `healthcheck` in `docker-compose.yml` so web waits for API ready
- [X] T036 [US3] Document rebuild requirement when changing `NEXT_PUBLIC_*` in `specs/002-login-bootstrap/quickstart.md`
- [X] T037 [US3] Verify `deploy/kubernetes/` manifests include auth env (from plan) or scaffold if missing
- [X] T038 [US3] Add `CORS_ORIGINS` including `http://localhost:3000` with credentials in prod compose accelerator service

**Checkpoint**: US3 — Docker prod path shows login and API reachable

---

## Phase 6: User Story 4 — Admin User Management (Priority: P3)

**Goal**: Admin creates Coordinator/Operator/Approver accounts via API and UI.

**Independent Test**: Admin creates operator; operator cannot call `POST /v1/auth/users`.

### Implementation for User Story 4

- [X] T039 [P] [US4] Implement `POST /v1/auth/users` and `GET /v1/auth/users` in `services/accelerator_api/auth_routes.py`
- [X] T040 [US4] Add admin-only guard using `PlatformRole.ADMIN` in auth routes
- [X] T041 [US4] Create `apps/migration-ui/src/app/settings/users/page.tsx` admin user list + create form
- [X] T042 [US4] Add NavTabs link for Users (admin only) in `apps/migration-ui/src/components/NavTabs.tsx`

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T043 [P] Audit events for login, logout, bootstrap in `ado2gh/auth/service.py`
- [X] T044 [P] Extend `tests/test_audit_redaction.py` for auth audit payloads
- [X] T045 Run `specs/002-login-bootstrap/quickstart.md` validation end-to-end
- [X] T046 [P] Update root `README.md` login section with prod compose + first-boot bootstrap steps
- [X] T047 Mark `specs/002-login-bootstrap/spec.md` status Clarified after implementation

---

## Dependencies & Execution Order

| Story | Depends on | MVP? |
|-------|------------|------|
| US1 Bootstrap | Phase 2 | Yes |
| US2 Login/logout | Phase 2, US1 | Yes |
| US3 Docker | US1, US2 | Prod deploy |
| US4 User admin | US2 | Optional |

### Immediate fix path (user blocked today)

1. Rebuild web image: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build web accelerator`
2. Verify `curl http://localhost:8080/health` and `curl http://localhost:8080/v1/auth/bootstrap-status`
3. Open `http://localhost:3000/login` — bootstrap admin (12+ char password)
4. Use header **Log out** / **Sign in** after T027–T028 (implemented)

---

## Task Summary

| Phase | Story | Tasks | Done |
|-------|-------|-------|------|
| Setup | — | T001–T006 | 4/6 |
| Foundational | — | T007–T015 | 8/9 |
| US1 Bootstrap | P1 | T016–T023 | 5/8 |
| US2 Login | P1 | T024–T032 | 7/9 |
| US3 Docker | P2 | T033–T038 | 1/6 |
| US4 Admin users | P3 | T039–T042 | 0/4 |
| Polish | — | T043–T047 | 0/5 |
| **Total** | | **T001–T047** | **25/47** |

**Suggested MVP**: Complete T020, T023, T031, T035, T038, T045 (bootstrap UX + Docker readiness).

---

## Parallel Opportunities

- T005, T006, T014, T015 in parallel after Phase 2
- T039–T042 (US4) parallel with US3 polish
- T043, T044, T046 in parallel
