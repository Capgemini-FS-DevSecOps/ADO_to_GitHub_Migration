# Contract: Platform RBAC

**Feature**: `004-agent-pev-rbac`  
**Services**: Accelerator API, Agent service, Migration UI

## Roles

Platform roles (stored on `platform_users.role`):

| Role | Description |
|------|-------------|
| `admin` | Full settings, models, users, live approval |
| `coordinator` | Assignment coordination + operate (no settings/models) |
| `operator` | Dry-run operations, request live (no settings/models) |
| `approver` | Approve live execution queue (no settings/models) |

Assignment-scoped roles (001) remain separate; see FR-014 in [spec.md](../spec.md).

---

## Permissions payload

`GET /v1/auth/session` includes:

```json
{
  "user": {
    "id": "usr_01",
    "username": "operator1",
    "role": "operator",
    "display_name": "Operator One"
  },
  "permissions": {
    "can_coordinate": false,
    "can_operate": true,
    "can_approve": false,
    "can_approve_live_execution": false,
    "can_manage_users": false,
    "can_manage_settings": false,
    "can_manage_models": false
  },
  "expires_at": "2026-06-16T20:00:00Z"
}
```

UI MUST gate features on `permissions`, not hard-coded role strings alone.

---

## Capability matrix

| Action | Required capability |
|--------|---------------------|
| View deployment profiles (read-only) | `can_operate` (GET only; mutations need `can_manage_settings`) |
| Create/update/delete deployment profile | `can_manage_settings` |
| Activate/deactivate profile | `can_manage_settings` |
| CRUD LLM models | `can_manage_models` |
| User management (`/v1/auth/users`) | `can_manage_users` |
| Start agent dry-run session | `can_operate` |
| Request live execution | `can_operate` |
| Approve/deny live queue | `can_approve_live_execution` |
| View dashboards / discovery (read) | `can_operate` |

---

## API enforcement (accelerator)

| Route pattern | Guard |
|---------------|-------|
| `POST/PUT/DELETE /v1/settings/profiles*` | `can_manage_settings` |
| `GET /v1/settings/profiles*` | authenticated (`can_operate` or higher); operators read-only |
| `POST/PUT/DELETE /v1/settings/llm-models*` | `can_manage_models` |
| `GET /v1/settings/llm-models` | authenticated or dev-open |
| `GET/POST /v1/platform/approvals*` | list/approve: `can_approve_live_execution`; create: `can_operate` |

Returns `403` with `{ "detail": "..." }` — no silent ignore.

---

## UI enforcement

| Surface | Operator | Admin | Approver |
|---------|----------|-------|----------|
| Settings → Profiles | read-only view; mutations disabled + tooltip | full CRUD | read-only (same as operator) |
| Settings → Models | blocked | enabled | blocked |
| Settings → Approvals | hidden | enabled | enabled |
| Agent tab live approve | hidden | visible if `can_approve_live_execution` | visible |
| UserSessionBar | role + capability hints | same | same |

Role hints (SC-007): short labels on Settings nav — “Admin only”, “Operator: dry-run only”.

---

## Agent service enforcement

When auth enabled:

| Route | Guard |
|-------|-------|
| `POST /v1/sessions` | `can_operate` |
| `POST .../request-live` | `can_operate` + session owner |
| `POST .../approve` | `can_approve_live_execution` |
| `GET /v1/sessions/{id}` | owner or approver/admin |

---

## Audit attribution

All platform-governed actions MUST set:

- `actor`: authenticated username
- `payload.role`: platform role
- `payload.user_id`: platform user id

Forbidden: `actor: "local-developer"` in auth-enabled deployments.
