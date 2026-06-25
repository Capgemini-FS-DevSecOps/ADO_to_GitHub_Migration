# Contract: Profile Onboarding API

**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/onboarding`

## Onboarding status

`GET /v1/onboarding/status`

**Auth**: Valid session required

**Response** `200`:

```json
{
  "needs_profile_setup": true,
  "active_profile_count": 0,
  "default_profile_id": null,
  "pending_approval_count": 0,
  "redirect_path": "/onboarding/profile",
  "role": "admin",
  "blocked_message": null
}
```

When `needs_profile_setup` is `true` and `role` is `admin`, clients MUST redirect to `redirect_path`.

When `needs_profile_setup` is `true` and `role` is `operator`:

```json
{
  "needs_profile_setup": true,
  "active_profile_count": 0,
  "blocked_message": "No deployment profile is active. Contact an administrator or submit a profile for approval.",
  "can_submit_profile": true
}
```

---

## Profile setup (extended existing route)

`POST /v1/settings/profiles/setup`

**Auth**: Valid session

**Body** (unchanged fields):

```json
{
  "name": "Production ADO → GitHub",
  "ado_org_url": "https://dev.azure.com/MYORG",
  "ado_pat": "...",
  "gh_org": "my-github-org",
  "github_token": "ghp_...",
  "github_token_name": "Primary"
}
```

**Behavior**:
- Validates ADO and GitHub before persist (existing).
- **Admin**: `status=active`, `is_default=true` if first active profile, sets `active_profile_id` when appropriate.
- **Operator**: `status=pending_approval`, `submitted_by=username`, does NOT set as active default.

**Response** `200`: `MigrationProfileResponse` with public fields + `status`, `is_default`, `submitted_by`.

**Errors**:
- `400` — validation failed (ADO or GitHub)
- `403` — operator blocked on fresh instance with zero profiles (only admin may create first profile)

---

## Active profile invariant

`DELETE /v1/settings/profiles/{profile_id}`

**Auth**: Admin only

**Body** (required when deleting the current default profile):

```json
{
  "new_default_profile_id": "other-active-profile-uuid"
}
```

**Behavior**:
- Rejects if sole `active` profile (`409` `last_active_profile`).
- **Auth**: Admin only (operators `403`).
- If deleting current default, `new_default_profile_id` MUST be another `active` profile; server sets it as default before delete.

`POST /v1/settings/profiles/{profile_id}/deactivate`

**Auth**: Admin only. Same last-active invariant as delete.

---

## Set default

`POST /v1/settings/profiles/{profile_id}/set-default`

**Auth**: Admin

**Behavior**: Profile MUST be `active`. Clears `is_default` on others.

**Response** `200`:

```json
{
  "default_profile_id": "uuid"
}
```

---

## Non-active profile guard (all migration routes)

When `profile_id` is resolved and `status !== active`:

**Response** `403`:

```json
{
  "detail": "Profile is not active",
  "code": "profile_not_active",
  "status": "pending_approval"
}
```

Applies to: discovery scan, migration runs, agent assignment tools using profile context.
