# Contract: Auth Self-Registration API

**Service**: Accelerator API  
**Base path**: `/v1/auth`

Extends [002-login-bootstrap auth-api](../../002-login-bootstrap/contracts/auth-api.md).

## Register (post-bootstrap)

`POST /v1/auth/register`

**Auth**: None (public, only when bootstrap complete)

**Precondition**: `needs_bootstrap === false` (`GET /bootstrap-status`)

**Body**:

```json
{
  "username": "operator1",
  "password": "minimum-12-characters",
  "display_name": "Ops User"
}
```

**Behavior**:
- Validates password strength (min 12 chars).
- Creates `platform_users` row with role `operator` (role field in body ignored if present).
- Sets session cookie (same as login).
- Audit `user.registered`.

**Response** `201`:

```json
{
  "user": {
    "id": "usr_...",
    "username": "operator1",
    "role": "operator",
    "display_name": "Ops User"
  },
  "session_expires_at": "2026-06-16T20:00:00Z"
}
```

**Errors**:
- `403` — `needs_bootstrap === true` (use bootstrap instead)
- `400` — weak password, invalid username
- `409` — username taken (generic message, no enumeration)

---

## Bootstrap status (extended)

`GET /v1/auth/bootstrap-status`

**Response** when bootstrap complete:

```json
{
  "needs_bootstrap": false,
  "auth_enabled": true,
  "registration_enabled": true,
  "message": "Sign in or create an account"
}
```

When `needs_bootstrap === true`, `registration_enabled: false`.
