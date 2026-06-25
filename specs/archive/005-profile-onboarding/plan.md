# Implementation Plan: Deployment Profile Onboarding and Governance

**Branch**: `005-profile-onboarding` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-profile-onboarding/spec.md`

**Plan addendum**: After initial boot, login screen MUST offer **Create account** for operator self-registration.

## Summary

Extend the migration console with:

1. **Mandatory admin profile onboarding** — live-validated ADO + GitHub credentials before console access.
2. **Profile governance** — minimum one active default profile; admin-only delete; operator pending profiles with approve/deny/**appeal**; manual default selection when deleting the current default.
3. **Post-bootstrap login registration** — **Create account** on `/login` for self-service **operator** accounts (enables multi-session Docker testing).

Reuses `ProfileWizard`, `credential_validation.py`, `SettingsStore`, and `002-login-bootstrap` auth; adds `profile_governance`, onboarding gate API, approval/appeal routes, and login registration UI/API.

## Technical Context

**Language/Version**: Python >= 3.9 (`ado2gh`, `services/accelerator_api`); TypeScript / Next.js 14 (`apps/migration-ui`)

**Primary Dependencies**: FastAPI; `SettingsStore`; `validate_ado_pat` / `validate_github_token`; `ado2gh/auth` (`AuthService`, platform users in StateDB)

**Storage**: `ui_settings.json` (+ prod data dir); StateDB `platform_users`, `auth_sessions`, `audit_events`

**Testing**: pytest (governance, onboarding API, approval/appeal, registration); UI redirect tests; **85%** coverage on changed `ado2gh/api` + `ado2gh/auth`

**Target Platform**: Docker Compose prod overlay; auth enabled (`ADO2GH_AUTH_ENABLED=true`)

**Performance Goals**: Onboarding status < 200ms; credential validation network-bound < 30s p95

**Constraints**: CA-003 secret masking; CA-004 audit for profile + user lifecycle; operator cannot create first profile or self-register as admin

**Scale/Scope**: Single-tenant; concurrent admin + operator browser sessions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | `profile_governance` + `AuthService.register_operator` thin routes |
| II. Documentation | Docstrings on gate, approval, appeal, registration |
| III. Deprecation | Guarded delete; no open admin self-registration |
| IV. Architecture & Naming | Domain-clear modules and routes |
| V. Enterprise Safeguards | HITL approvals, audit, operator-only self-reg |
| VI. Testing (85%+) | Branch coverage on invariant + registration |

**Result**: [x] PASS

**Post-design re-check**: [x] PASS

## Project Structure

### Documentation (this feature)

```text
specs/005-profile-onboarding/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── profile-onboarding-api.md
│   ├── profile-approval-api.md
│   ├── profile-setup-ui.md
│   ├── auth-registration-api.md
│   └── login-registration-ui.md
└── tasks.md
```

### Source Code (repository root)

```text
ado2gh/
├── api/
│   ├── settings_store.py       # status, is_default, submitted_by, approval
│   ├── profile_governance.py   # gate, invariant, appeal
│   └── credential_validation.py
├── auth/
│   └── service.py              # register_operator()

services/accelerator_api/
├── main.py
└── auth_routes.py              # POST /register

apps/migration-ui/src/
├── app/login/
│   ├── LoginClient.tsx         # Create account toggle / link
│   └── register/               # optional dedicated route
├── app/onboarding/profile/
├── app/settings/profiles/pending/
├── components/ProfileWizard.tsx
├── components/AuthGate.tsx
├── middleware.ts
└── lib/auth.ts                 # register()

tests/
├── test_profile_governance.py
├── test_profile_onboarding_api.py
├── test_profile_approval_flow.py
└── test_auth_registration.py
```

## Complexity Tracking

No violations.

## Phase 0 & Phase 1 Artifacts

| Artifact | Path | Status |
|----------|------|--------|
| Research | [research.md](./research.md) | Updated |
| Data model | [data-model.md](./data-model.md) | Updated |
| Onboarding API | [contracts/profile-onboarding-api.md](./contracts/profile-onboarding-api.md) | Updated |
| Approval API | [contracts/profile-approval-api.md](./contracts/profile-approval-api.md) | Updated |
| Setup UI | [contracts/profile-setup-ui.md](./contracts/profile-setup-ui.md) | Updated |
| Registration API | [contracts/auth-registration-api.md](./contracts/auth-registration-api.md) | New |
| Login UI | [contracts/login-registration-ui.md](./contracts/login-registration-ui.md) | New |
| Quickstart | [quickstart.md](./quickstart.md) | Updated |

## Implementation Notes (for `/speckit-tasks`)

1. `GET /v1/onboarding/status` — gate for profile setup.
2. `POST /v1/auth/register` — post-bootstrap only; role `operator`; sets session cookie.
3. Login UI — **Create account** when `needs_bootstrap === false`; bootstrap mode unchanged.
4. Profile `status`: `active`, `pending_approval`, `denied`, `inactive`.
5. Delete default — **admin picks replacement** in confirmation (`new_default_profile_id` on delete).
6. Deny → `denied`; `POST .../appeal` → `pending_approval` (unlimited, audited).
7. Operator profile submit blocked when `active_profile_count === 0`.
8. Admin bootstrap/login → `/onboarding/profile` when no active profiles.
9. Audit: `user.registered`, `profile.approved`, `profile.denied`, `profile.appealed`, etc.
10. Guard `services/agent/main.py` and Agent tab UI for active deployment profiles only (FR-008).
