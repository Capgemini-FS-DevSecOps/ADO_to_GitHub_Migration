# Contract: Profile Setup UI

**App**: `apps/migration-ui`

## Routes

| Route | Purpose | Who |
|-------|---------|-----|
| `/onboarding/profile` | Mandatory first profile after admin bootstrap | Admin (gate open) |
| `/settings/profiles/new` | Add additional profile | Admin (immediate active) or Operator (pending) |
| `/settings/profiles/pending` | Approval queue | Admin only |
| `/login` | Sign-in + **Create account** (post-bootstrap) | All unauthenticated |

## Login page (`/login`)

See [login-registration-ui.md](./login-registration-ui.md):
- After bootstrap: **Create account** link for operator self-registration.
- Bootstrap mode: admin creation only (no extra create-account link).

## Onboarding page (`/onboarding/profile`)

**Layout**:
- Minimal shell (no `NavTabs`); product header only.
- Reuses `ProfileWizard` with `mode="onboarding"`.
- No skip / cancel to dashboard.
- Steps: ADO source → GitHub target → validate → create (existing wizard steps).

**On success**:
- Admin: redirect to `/` (dashboard).
- Invalidate `onboarding` and `settings` queries.

**Copy**:
- Heading: "Set up your first deployment profile"
- Subtext: "Connect Azure DevOps and GitHub before using the migration console."

## AuthGate / layout behavior

After `GET /v1/auth/session` succeeds:

1. `GET /v1/onboarding/status`
2. If `needs_profile_setup` && `role === admin` && path not in `/onboarding/*`, `/login` → redirect `/onboarding/profile`
3. If `needs_profile_setup` && `role === operator` → show `blocked_message` banner on allowed pages; disable migration/agent actions

## Middleware (`middleware.ts`)

**Public paths** (when `REQUIRE_AUTH=true`):
- `/login`
- `/onboarding/profile`
- `_next/*`, `favicon.ico`

Protected routes require `ado2gh_session` cookie (existing).

## ProfileWizard modes

| Mode | Submit outcome message | Post-submit redirect |
|------|------------------------|----------------------|
| `onboarding` | "Profile created" | `/` |
| `settings-admin` | "Profile active" | `/settings/profiles/{id}/tokens` |
| `settings-operator` | "Submitted for approval" | `/settings/profiles` with pending badge |

## Settings profiles list

- Badge: **Default** on `is_default` active profile.
- Badge: **Pending** on `pending_approval`.
- Delete button disabled with tooltip when sole active profile.
- Admin link: "Pending approvals (N)" → `/settings/profiles/pending`.

## Bootstrap flow change (`LoginClient`)

On `POST /v1/auth/bootstrap` success:
- If response `redirect_path` present → use it.
- Else if `needs_profile_setup` from onboarding status → `/onboarding/profile`.
- Else `returnUrl` or `/`.

## Accessibility

- Wizard steps retain existing labels; onboarding page sets `h1` for setup title.
- Pending/denied states use `aria-live` for approval updates.
