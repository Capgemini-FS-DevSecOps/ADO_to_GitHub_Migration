# Data Model: Platform Authentication

**Feature**: `002-login-bootstrap` | **Date**: 2026-06-16

## Entities

### PlatformUser

Console operator (distinct from migration profile PATs / GitHub tokens).

| Field | Type | Constraints |
|-------|------|-------------|
| `id` | TEXT (UUID) | PK |
| `username` | TEXT | UNIQUE, NOT NULL, 3–64 chars |
| `password_hash` | TEXT | NOT NULL |
| `role` | TEXT | NOT NULL — `admin`, `approver`, `coordinator`, `operator` |
| `display_name` | TEXT | optional |
| `created_at` | TEXT (ISO8601) | NOT NULL |
| `updated_at` | TEXT | NOT NULL |
| `last_login_at` | TEXT | nullable |
| `disabled` | INTEGER | 0/1, default 0 |

**Validation**:
- Password min length 12 on create/bootstrap (configurable `ADO2GH_MIN_PASSWORD_LENGTH`).
- Username alphanumeric + `_` `-` only.
- Only `admin` may assign `admin` role to others.

### AuthSession

| Field | Type | Constraints |
|-------|------|-------------|
| `id` | TEXT | PK — opaque token (url-safe random) |
| `user_id` | TEXT | FK → `platform_users.id` |
| `created_at` | TEXT | NOT NULL |
| `expires_at` | TEXT | NOT NULL |
| `revoked` | INTEGER | 0/1 |
| `user_agent` | TEXT | optional, truncated 256 |

**Lifecycle**:
- Created on successful login/bootstrap.
- Revoked on logout or expiry.
- Cleanup job optional (delete expired rows on login).

### BootstrapState (derived)

Not stored. `needs_bootstrap = (COUNT(platform_users) == 0)`.

## Relationships

```text
PlatformUser 1 ── * AuthSession
```

Migration profile credentials (`ui_settings.json`, profile PATs) remain separate — platform auth gates UI/API access; profile tokens still required for ADO/GH operations.

## Role hierarchy

```text
admin       → all platform + user management + all ProfileRole capabilities
approver    → ProfileRole.APPROVER (+ operator read paths)
coordinator → ProfileRole.COORDINATOR (+ operator)
operator    → ProfileRole.OPERATOR only
```

Mapping implemented in `ado2gh/auth/rbac_map.py` → `RBAC.from_list(...)`.

## State transitions

### Bootstrap (fresh instance)

```text
[no users] --submit bootstrap form--> [admin user created] --session--> [authenticated]
```

### Standard login

```text
[unauthenticated] --valid credentials--> [session active]
[session active] --logout/expiry--> [unauthenticated]
```

## SQLite schema (append to StateDB)

```sql
CREATE TABLE IF NOT EXISTS platform_users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    disabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES platform_users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
```

Postgres: same columns with `SERIAL` omitted; FK on `user_id`.

## Audit events

| event_type | payload (redacted) |
|------------|-------------------|
| `auth_bootstrap` | `username`, `role: admin` |
| `auth_login_success` | `username`, `role` |
| `auth_login_failure` | `username` (no password) |
| `auth_logout` | `username` |
| `auth_user_created` | `username`, `role`, `created_by` |
