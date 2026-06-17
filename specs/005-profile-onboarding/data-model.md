# Data Model: Deployment Profile Onboarding and Governance

**Feature**: `005-profile-onboarding` | **Date**: 2026-06-16

## Overview

Extends **MigrationProfile** and **UISettings** in `SettingsStore` with lifecycle and governance fields. **ProfileOnboardingGate** is derived at read time. **ProfileApprovalRequest** is embedded in profile rows with `status=pending_approval` (not a separate table in v1).

## Entities

### MigrationProfile (extended)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | UUID |
| `name` | string | yes | Display name |
| `ado_org_url` | string | yes | ADO organization URL |
| `ado_pat` | string | yes | Stored secret (never public) |
| `gh_org` | string | yes | Target GitHub org |
| `github_tokens` | list[GitHubToken] | yes | At least one on setup |
| `status` | enum | yes | `active`, `pending_approval`, `denied`, `inactive` |
| `is_default` | boolean | yes | Exactly one `active` profile is default |
| `submitted_by` | string | optional | Platform username (operator submissions) |
| `approval` | ProfileApprovalMetadata | optional | Present when pending/denied/approved history |
| `created_at` | datetime | yes | UTC ISO |
| `updated_at` | datetime | yes | UTC ISO |
| `last_scan_at` | datetime | optional | Discovery scan |
| `scan_summary` | object | optional | Non-secret scan metadata |

**Validation rules**:
- `status=active` ⇒ credentials non-empty; ADO/GitHub validated at creation or approval
- `is_default=true` ⇒ `status=active`
- At most one profile with `is_default=true` among active profiles
- Cannot delete or deactivate last `active` profile

**State transitions**:

```text
(admin setup)     → active (+ is_default if first)
(operator setup)  → pending_approval
pending_approval  → active (admin approve + re-validate)
pending_approval  → denied (admin deny)
active            → inactive (admin deactivate, if another active remains)
inactive          → active (admin reactivate)
any non-pending   → deleted (if invariant allows)
```

### ProfileApprovalMetadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `requested_at` | datetime | yes | Operator submit time |
| `decided_at` | datetime | optional | Approval/denial time |
| `decided_by` | string | optional | Admin username |
| `decision` | enum | optional | `approved`, `denied` |
| `reason` | string | optional | Denial or approval note |
| `validation_snapshot` | object | optional | Non-secret counts from last validation |

### ProfileOnboardingGate (derived)

| Field | Type | Description |
|-------|------|-------------|
| `needs_profile_setup` | boolean | `count(status=active) === 0` |
| `active_profile_count` | integer | Active profiles |
| `default_profile_id` | string | optional |
| `pending_approval_count` | integer | Admin queue size |
| `redirect_path` | string | `/onboarding/profile` when setup required for admin |
| `can_submit_profile` | boolean | `false` for operators when `active_profile_count === 0` |

**Rules**:
- `needs_profile_setup` blocks admin console (except onboarding routes)
- Operators with zero active profiles: `blocked_message`, cannot run migrations, **cannot submit** new profiles (`can_submit_profile: false` per FR-017)
- Operators with ≥1 active profile may submit profiles into `pending_approval` (not active until admin approves)

### GitHubToken (existing)

Unchanged; `to_public()` masks `token` as `***`.

### AuditEvent (existing table — extended event types)

| `event_type` | `actor` | `payload` (no secrets) |
|--------------|---------|------------------------|
| `profile.created` | username | profile_id, status, role |
| `profile.validation_failed` | username | provider, message |
| `profile.approved` | admin | profile_id, reason |
| `profile.denied` | admin | profile_id, reason |
| `profile.appealed` | operator | profile_id |
| `profile.deleted` | username | profile_id |
| `profile.default_changed` | username | profile_id |
| `user.registered` | username | role (operator) |

### PlatformUser registration (extends 002)

Self-service path when `needs_bootstrap === false`:

| Field | Rule |
|-------|------|
| `role` | Always `operator` on register |
| `username` | Unique, normalized lowercase |
| `password` | Min 12 chars (same as bootstrap) |

No `pending` state for user accounts in v1 — immediate active operator session after register.

## Relationships

```text
PlatformUser (002) ──submits──► MigrationProfile (pending_approval)
PlatformUser (admin) ──approves──► MigrationProfile (active)
UISettings.active_profile_id ──► MigrationProfile (must be active + default preferred)
Migration runs / agent sessions ──use──► MigrationProfile (active only)
```

## Storage layout

**File**: `{ADO2GH_DATA_DIR}/ui_settings.json`

```json
{
  "active_profile_id": "uuid",
  "migration_profiles": [
    {
      "id": "...",
      "status": "active",
      "is_default": true,
      "submitted_by": "admin",
      "approval": null,
      "_secrets": { "ado_pat": "...", "github_tokens": [...] }
    }
  ]
}
```

Public API uses `to_public()` — secrets stripped, status fields included.
