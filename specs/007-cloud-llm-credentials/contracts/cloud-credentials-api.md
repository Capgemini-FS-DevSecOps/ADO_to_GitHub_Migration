# Contract: Cloud Credentials API

**Feature**: `007-cloud-llm-credentials`  
**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/settings/cloud-credentials`

**Auth**: `can_manage_models` (admin) for all mutation and list routes via `require_manage_models(request)` from `ado2gh.api.platform_rbac` — **first line of every handler** (same pattern as feature `006` LLM routes). Operators receive **403** `Missing capability: can_manage_models`.

**Related**: [agent-models-api.md](./agent-models-api.md) (agent picker), [platform-supplied-llm.md](./platform-supplied-llm.md) (env vars).

---

## Secret redaction (SC-003)

All cloud-credentials HTTP responses, audit payloads, and `to_public()` store serializers **MUST NOT** include:

| Forbidden | Examples |
|-----------|----------|
| Raw secret values | `AWS_SECRET_ACCESS_KEY`, `AZURE_CLIENT_SECRET`, API keys, tokens |
| Credential file contents | Contents of `GOOGLE_APPLICATION_CREDENTIALS` JSON |
| Keys matching patterns | `*secret*`, `*password*`, `*token*`, `*api_key*`, `*private_key*` (case-insensitive) |

**Allowed** (presence/summary only):
- `primary_method`, `alternate_methods`, `region`, `project`, `endpoint` (host URL without query secrets)
- `missing_fields` labels, `admin_supplied_fields` (non-secret only)
- `last_probe_category`, `last_probe_message` (user-safe, no echoed credentials)

**Contract tests (G1 / SC-003)** — `tests/contract/test_007_cloud_credentials_contracts.py`:
1. After scan with env containing fake `AWS_SECRET_ACCESS_KEY=supersecret`, response JSON MUST NOT contain `supersecret` or key name `AWS_SECRET_ACCESS_KEY` with a value.
2. Recursively assert no response key matches forbidden patterns above.
3. Audit event payloads for approve/reject/revoke/scan pass same redaction check.

---

## List sources (with optional scan)

`GET /v1/settings/cloud-credentials`

**Query**:
- `scan=true` — run presence scan before returning (first visit default in UI)

**Response** `200`:

```json
{
  "last_full_scan_at": "2026-06-18T12:00:00Z",
  "sources": [
    {
      "provider": "aws",
      "service": "bedrock",
      "status": "pending",
      "completeness": "complete",
      "primary_method": "instance_role",
      "alternate_methods": ["env_keys"],
      "region": "us-east-1",
      "project": null,
      "endpoint": null,
      "missing_fields": [],
      "admin_supplied_fields": {},
      "last_scan_at": "2026-06-18T12:00:00Z",
      "last_probe_status": null,
      "last_probe_category": null,
      "last_probe_message": null,
      "approved_at": null,
      "approved_by": null
    }
  ],
  "platform_model": {
    "provider": "bedrock",
    "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "region": "us-east-1",
    "read_only": true,
    "synced_model_id": "mdl_abc123",
    "available": false
  }
}
```

**Rules**:
- Never return secret values or credential file contents (see Secret redaction).
- `platform_model.available` is `true` only when source is **approved** and model is **enabled** (internal state).
- `platform_model` present when deploy-time model env vars are set for a provider.

---

## Rescan

`POST /v1/settings/cloud-credentials/scan`

**Auth**: `require_manage_models(request)`

**Response** `200`: same shape as GET list (after scan).

**Behavior**:
- Presence-only detection (FR-001) for **aws, foundry, gcp**.
- Completes within **2–5 seconds** p95 (FR-009).
- Material change on approved source → status `pending` (FR-010).
- Single-flight: concurrent scans return in-flight result or `409` with retry hint.

**Audit**: `cloud_credentials.scanned`

---

## Update incomplete configuration (U1)

`PATCH /v1/settings/cloud-credentials/{provider}`

**Path**: `provider` ∈ `aws`, `foundry`, `gcp`

**Body** — **non-secret fields only**:

```json
{
  "region": "us-east-1",
  "endpoint": "https://myfoundry.cognitiveservices.azure.com",
  "project": "my-gcp-project"
}
```

**Allowed field names**: `region`, `endpoint`, `project`, `tenant_id` (non-secret identifiers only).

**Forbidden field names** (request rejected `400 invalid_field`):
- `secret`, `password`, `token`, `api_key`, `client_secret`, `private_key`
- `AWS_SECRET_ACCESS_KEY`, `AZURE_CLIENT_SECRET`, `GOOGLE_APPLICATION_CREDENTIALS` (use host env for secrets)

**Response** `200`: updated source with recalculated `completeness`, `missing_fields`, merged `admin_supplied_fields`.

**Errors**:
- `400 invalid_field` — body contains forbidden key
- `400 incomplete_configuration` — still incomplete after merge (lists `missing_fields`)
- `404` unknown provider

---

## Approve source

`POST /v1/settings/cloud-credentials/{provider}/approve`

**Path**: `provider` ∈ `aws`, `foundry`, `gcp`

**Body** (optional):

```json
{ "note": "EC2 prod instance profile verified" }
```

**Behavior**:
- Reject **400** `incomplete_configuration` if `completeness != complete` (lists `missing_fields`).
- Run live probe (FR-001a); on success → `approved`; on failure → `pending` + probe fields.
- Audit: `cloud_credentials.approved` on success.

**Errors**: `404`, `400 incomplete_configuration`, `502` probe failed (`category`, `message`)

---

## Reject source

`POST /v1/settings/cloud-credentials/{provider}/reject`

```json
{ "reason": "optional" }
```

**Audit**: `cloud_credentials.rejected`

---

## Revoke approval

`POST /v1/settings/cloud-credentials/{provider}/revoke`

**Audit**: `cloud_credentials.revoked`; ambient models for provider invalidated.

---

## Platform-supplied model status

`GET /v1/settings/cloud-credentials/platform-model`

**Auth**: `require_manage_models(request)`

```json
{
  "provider": "bedrock",
  "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
  "region": "us-east-1",
  "synced_model_id": "mdl_abc123",
  "validation_status": "never_validated",
  "enabled": false,
  "source_approved": false
}
```

---

## Internal runtime

`get_ambient_credentials(provider)` → raises if not approved; used by `model_validation`, `llm_provider.get_llm_provider`.

Agent model list: see [agent-models-api.md](./agent-models-api.md).
