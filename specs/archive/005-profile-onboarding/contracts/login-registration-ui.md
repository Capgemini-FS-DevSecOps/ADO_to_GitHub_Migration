# Contract: Login Registration UI

**App**: `apps/migration-ui`  
**Route**: `/login` (and optional `/login/register`)

Extends [002 login-ui](../../002-login-bootstrap/contracts/login-ui.md).

## Modes

### Bootstrap mode (`needs_bootstrap === true`)

- Heading: "Create admin account"
- No **Create account** link (bootstrap is the only path).
- Submit: "Create admin & continue"

### Sign-in mode (`needs_bootstrap === false`)

- Heading: "Sign in"
- Primary form: username + password → `POST /v1/auth/login`
- Footer link: **Create account** → toggles registration sub-mode or navigates to `/login/register`

### Registration sub-mode

- Heading: "Create account"
- Fields: username, password, optional display name
- Password min 12 characters (match API)
- Submit: "Create account & sign in" → `POST /v1/auth/register`
- Link: "Already have an account? Sign in" → back to sign-in mode
- Role is **not** selectable (always operator server-side)

## Post-registration redirect

1. If `needs_profile_setup` and user is admin → `/onboarding/profile` (admin bootstrap path only).
2. If operator and zero active profiles → dashboard with `blocked_message` (cannot submit profiles yet).
3. Else `returnUrl` or `/`.

## Visibility rules

| `needs_bootstrap` | Create account shown |
|-------------------|----------------------|
| `true` | No |
| `false` | Yes |

## Accessibility

- Toggle between sign-in and register updates `h1` and focus first field.
- Registration errors use `oai-error` banner (generic API text).
