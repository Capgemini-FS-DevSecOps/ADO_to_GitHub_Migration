# Contract: Profile Approval API

**Service**: Accelerator API  
**Base path**: `/v1/settings/profiles`

## List pending approvals

`GET /v1/settings/profiles/pending`

**Auth**: Admin only

**Response** `200`:

```json
{
  "items": [
    {
      "id": "profile-uuid",
      "name": "Operator sandbox",
      "ado_org_url": "https://dev.azure.com/ORG",
      "gh_org": "target-org",
      "status": "pending_approval",
      "submitted_by": "operator1",
      "approval": {
        "requested_at": "2026-06-16T12:00:00Z"
      },
      "created_at": "2026-06-16T12:00:00Z"
    }
  ]
}
```

Secrets never included.

---

## Approve profile

`POST /v1/settings/profiles/{profile_id}/approve`

**Auth**: Admin only

**Body**:

```json
{
  "reason": "Verified with platform team",
  "set_as_default": false
}
```

**Behavior**:
1. Profile MUST be `pending_approval`.
2. Re-validate ADO PAT and GitHub token from stored secrets.
3. On success: `status=active`, record `approval.decision=approved`, `decided_by`, `decided_at`.
4. If `set_as_default` or no other default exists, set `is_default=true`.
5. Emit audit `profile.approved`.

**Response** `200`: Public profile object.

**Errors**:
- `400` — re-validation failed (profile stays pending)
- `403` — non-admin
- `404` — profile not found
- `409` — not in pending state

---

## Deny profile

`POST /v1/settings/profiles/{profile_id}/deny`

**Auth**: Admin only

**Body**:

```json
{
  "reason": "Use shared production profile instead"
}
```

**Behavior**: `status=denied`, record approval metadata, audit `profile.denied`.

**Response** `200`: Public profile with `status=denied`.

---

## Appeal denied profile

`POST /v1/settings/profiles/{profile_id}/appeal`

**Auth**: Submitting operator only (`submitted_by` matches session)

**Body** (optional):

```json
{
  "note": "Credentials updated per your feedback"
}
```

**Behavior**:
- Profile MUST be `status=denied`.
- Sets `status=pending_approval`, clears denial decision for re-review.
- Unlimited appeals; audit `profile.appealed`.

**Response** `200`: Public profile with `status=pending_approval`.

**Errors**: `403` not submitter; `409` not denied state.

---

## Operator visibility

`GET /v1/settings/profiles/mine/pending`

**Auth**: Operator (or any authenticated user)

Returns pending profiles where `submitted_by` matches session username (single item or empty list).
