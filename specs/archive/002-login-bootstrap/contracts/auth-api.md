# Contract: Platform Authentication API

**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/auth`

## Bootstrap status

`GET /v1/auth/bootstrap-status`

**Auth**: None

**Response** `200`:

```json
{
  "needs_bootstrap": true,
  "message": "Create the first admin account to continue"
}
```

When `needs_bootstrap` is `false`, login page shows standard sign-in only.

---

## Bootstrap first admin (fresh instance only)

`POST /v1/auth/bootstrap`

**Auth**: None (only when `needs_bootstrap === true`)

```json
{
  "username": "admin",
  "password": "minimum-12-char-password",
  "display_name": "Platform Admin"
}
```

**Response** `201`:

```json
{
  "user": {
    "id": "usr_01...",
    "username": "admin",
    "role": "admin",
    "display_name": "Platform Admin"
  },
  "session_expires_at": "2026-06-16T20:00:00Z"
}
```

Sets `Set-Cookie: ado2gh_session=<token>; HttpOnly; Path=/; SameSite=Lax`

**Errors**:
- `403` — bootstrap not allowed (users already exist)
- `409` — concurrent bootstrap (another admin created)
- `400` — validation (weak password, invalid username)

---

## Login

`POST /v1/auth/login`

```json
{
  "username": "operator1",
  "password": "..."
}
```

**Response** `200`: same shape as bootstrap (without forcing `admin` role).

**Errors**: `401` generic `{ "detail": "Invalid credentials" }`

---

## Logout

`POST /v1/auth/logout`

**Auth**: Session cookie or Bearer token

**Response** `200`: `{ "ok": true }` — clears cookie.

---

## Current session

`GET /v1/auth/session`

**Auth**: Session required (except returns `401` when absent)

**Response** `200`:

```json
{
  "authenticated": true,
  "user": {
    "id": "usr_01...",
    "username": "operator1",
    "role": "operator",
    "display_name": "Ops User"
  },
  "expires_at": "2026-06-16T20:00:00Z",
  "permissions": {
    "can_coordinate": false,
    "can_operate": true,
    "can_approve": false,
    "can_manage_users": false
  }
}
```

---

## Create user (admin only)

`POST /v1/auth/users`

**Auth**: `admin` role

```json
{
  "username": "approver1",
  "password": "...",
  "role": "approver",
  "display_name": "Migration Approver"
}
```

**Response** `201`: user object (no password).

---

## List users (admin only)

`GET /v1/auth/users`

**Response**: `{ "users": [ ... ] }` — password hashes never returned.

---

## Middleware contract

When `ADO2GH_AUTH_ENABLED=true`:

- All `/v1/*` except `/health`, `/ready`, `/v1/auth/bootstrap-status`, `/v1/auth/bootstrap`, `/v1/auth/login` require valid session.
- `actor` for audit and agentic routes derived from `session.user.username`.
- Role checks use `PlatformRole` → existing `RBAC` mapping.

When `ADO2GH_AUTH_ENABLED=false` (local dev): open API; UI may still show login if `NEXT_PUBLIC_REQUIRE_AUTH=true`.
