# Contract: Login Page UI

**App**: `apps/migration-ui`  
**Route**: `/login`

## Layout

- Full-page OrchestrateAI theme (black background, cyan accents per `oai-styles.css`).
- Centered `oai-card` with product title "ADO2GH Migration Console".
- No `AppShell` / `NavTabs` on login route.

## Modes

### Bootstrap mode (`needs_bootstrap === true`)

- Heading: "Create admin account"
- Subtext: "This instance has no users. The account you create will have full administrator access."
- Fields: username, password, confirm password, optional display name.
- Submit label: "Create admin & sign in"
- On success: redirect to `/` (dashboard).

### Standard login (`needs_bootstrap === false`)

- Heading: "Sign in"
- Fields: username, password.
- Submit label: "Sign in"
- On success: redirect to prior path or `/`.

## Error display

- Inline `oai-error` banner for API errors (generic text from API).
- Field-level validation for password mismatch / min length before submit.

## Session handling

- Rely on `HttpOnly` cookie from API (no token in JS).
- `GET /v1/auth/session` on load to skip login if already authenticated.
- Logout control in `AppShell` calls `POST /v1/auth/logout`.

## Route protection

`middleware.ts`:

- Public: `/login`, `_next/*`, `favicon.ico`
- Protected: all other app routes → redirect to `/login?returnUrl=...`

## Accessibility

- Labels on all inputs; focus ring on primary button; form errors linked via `aria-describedby`.
