# Research: Deployment Profile Onboarding and Governance

**Feature**: `005-profile-onboarding` | **Date**: 2026-06-16

## R1: Onboarding gate placement (UI vs API-only)

**Decision**: Dual layer — API returns authoritative `needs_profile_setup`; Next.js `middleware.ts` + `AuthGate` redirect admins to `/onboarding/profile` when gate is open.

**Rationale**: Middleware alone cannot call accelerator without edge config; client `AuthGate` on layout fetches gate status after session; middleware can allow `/onboarding/*` and `/login` while blocking other routes when a lightweight cookie flag is set (optional `ado2gh_onboarding=1` set by login response).

**Alternatives considered**:
- API-only enforcement → operators could load broken dashboard; poor UX.
- Middleware-only without API → stale redirects, no single source of truth.

## R2: Profile lifecycle storage

**Decision**: Extend `MigrationProfile` in `ui_settings.json` / `SettingsStore` with `status`, `is_default`, `submitted_by`, `approval` metadata (approver, reason, timestamps). Pending approvals listed by filtering `status=pending_approval`.

**Rationale**: Profiles already live in settings store; avoids new persistence layer for v1. StateDB `audit_events` for audit trail.

**Alternatives considered**:
- New Postgres table only → breaks SQLite dev parity.
- Separate approvals table in StateDB → valid for v2 if queue grows; v1 adds migration complexity.

## R3: Operator visibility of pending profiles

**Decision**: Submitting operator + all admins see pending items; other operators do not (per spec assumption).

**Rationale**: Matches enterprise least-privilege; submitter can track status.

**Alternatives considered**:
- All operators see all pending → rejected (credential metadata leakage risk).

## R4: Re-validation on admin approval

**Decision**: Approval endpoint MUST re-run `validate_ado_pat` and `validate_github_token` before setting `status=active`; failure returns 400 with message, profile stays pending.

**Rationale**: Spec assumption — stale credentials should not activate silently.

**Alternatives considered**:
- Trust submit-time validation only → rejected for expired PAT risk.

## R5: Default profile on delete

**Decision**: When deleting the current default active profile, admin MUST select `new_default_profile_id` in the delete confirmation request before deletion proceeds (spec clarification 2026-06-16).

**Rationale**: Explicit operator control over which profile becomes default; avoids surprising auto-promotion.

**Alternatives considered**:
- Auto-promote oldest active profile → rejected per clarified spec (Q3).

## R9: Post-bootstrap self-registration

**Decision**: `POST /v1/auth/register` available only when `needs_bootstrap === false`. Creates `operator` role user, validates password strength, sets session cookie on success. Login page shows **Create account** link toggling registration form.

**Rationale**: User requirement for multi-session Docker testing; operator-only prevents privilege escalation.

**Alternatives considered**:
- Admin-only user creation in settings → rejected; blocks operator onboarding UX.
- Open admin registration → rejected (security).
- Email verification → out of scope v1.

## R10: Denied profile appeal

**Decision**: `POST /v1/settings/profiles/{id}/appeal` by submitting operator; unlimited appeals; each emits `profile.appealed` audit; status `denied` → `pending_approval`.

**Rationale**: Spec clarification session 2026-06-16.

## R6: First profile after bootstrap

**Decision**: `POST /v1/auth/bootstrap` response includes `redirect_path: "/onboarding/profile"` when `active_profile_count === 0`. Login UI and `LoginClient` honor this over `returnUrl`.

**Rationale**: Explicit server-driven redirect after admin creation.

**Alternatives considered**:
- Client-only heuristic → race if settings not loaded.

## R7: Integration with existing ProfileWizard

**Decision**: Reuse `ProfileWizard` component on `/onboarding/profile` with `mode=onboarding` (hide nav, block skip). Settings `/settings/profiles/new` uses `mode=settings` (admin immediate active vs operator pending).

**Rationale**: Single validation UX; reduces duplicate forms.

**Alternatives considered**:
- New wizard component → unnecessary duplication.

## R8: Migration/agent API guards

**Decision**: All endpoints that resolve `get_active_profile()` or accept `profile_id` MUST reject non-`active` profiles with `403` and code `profile_not_active`.

**Rationale**: FR-008 enforcement at API boundary, not only UI.

**Alternatives considered**:
- UI-only disable → bypass via API/clients.
