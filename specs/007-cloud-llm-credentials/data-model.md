# Data Model: Cloud-Hosted LLM Credential Detection and Admin Approval

**Feature**: `007-cloud-llm-credentials` | **Date**: 2026-06-18 | **Updated**: 2026-06-19

## Entity Overview

```text
CloudCredentialsStore (file)
    └── CloudCredentialSource[]  (max 3: aws, foundry, gcp)

PlatformSuppliedModelConfig (env + synced LLMModelConfig)
    └── runtime availability when CloudCredentialSource approved + model enabled

LLMModelConfig (extended from 006)
    ├── credential_mode: byok | ambient
    ├── platform_supplied: bool
    └── provider adds bedrock, foundry, vertex
```

**Naming note**: Implementation module `platform_managed_model.py` syncs `PlatformSuppliedModelConfig`; persisted field is `platform_supplied` (not `platform_managed`). There is **no** deployment env mode flag.

---

## CloudCredentialSource

One record per hyperscaler (`aws` | `foundry` | `gcp`).

| Field | Type | Notes |
|-------|------|-------|
| `provider` | enum | `aws`, `foundry`, `gcp` |
| `service` | string | `bedrock`, `microsoft_foundry`, `vertex` |
| `status` | enum | `pending`, `approved`, `rejected`, `revoked` |
| `completeness` | enum | `complete`, `incomplete`, `absent` |
| `primary_method` | string | e.g. `instance_role`, `env_keys`, `web_identity`, `managed_identity`, `adc_file` |
| `alternate_methods` | string[] | Non-secret method labels |
| `region` | string? | AWS/GCP region if inferable |
| `project` | string? | GCP project id |
| `endpoint` | string? | Microsoft Foundry project endpoint host (no keys) |
| `missing_fields` | string[] | When incomplete — human labels e.g. `endpoint`, `AWS_REGION` |
| `admin_supplied_fields` | object | Non-secret values admin entered via UI (`PATCH`); merged at scan apply |
| `last_scan_at` | ISO8601 | |
| `scan_snapshot` | object | Normalized dict for FR-010 diff (no secrets) |
| `approved_at` | ISO8601? | |
| `approved_by` | string? | Username |
| `rejected_at` | ISO8601? | |
| `rejected_by` | string? | |
| `rejection_reason` | string? | Optional |
| `last_probe_at` | ISO8601? | |
| `last_probe_status` | enum? | `passed`, `failed` |
| `last_probe_category` | string? | credentials, network, model_not_found, timeout |
| `last_probe_message` | string? | User-safe |

**State transitions**:

```text
(absent) ──scan──► pending | incomplete
incomplete ──(PATCH complete fields)──► pending (when complete)
incomplete ──(approve)──► 400 incomplete_configuration
pending ──(approve+probe pass)──► approved
pending ──(approve+probe fail)──► pending (with error)
pending ──(reject)──► rejected
approved ──(revoke)──► revoked
approved ──(rescan material change)──► pending
revoked / rejected ──(approve)──► approved (after probe pass)
```

**Rules**:
- Cannot approve when `completeness != complete` (API `400 incomplete_configuration`).
- Admin may supply missing **non-secret** fields via `admin_supplied_fields` / `PATCH` until complete.
- Ambient LLM calls allowed only when `status == approved`.
- No secret fields stored or returned in `to_public()`.

**Storage**: embedded in `cloud_credentials.json` under `sources[]`.

---

## CloudCredentialsFile

Top-level store document.

| Field | Type | Notes |
|-------|------|-------|
| `sources` | CloudCredentialSource[] | |
| `last_full_scan_at` | ISO8601? | |

**Path**: `{ADO2GH_DATA_DIR}/cloud_credentials.json`

---

## PlatformSuppliedModelConfig

Runtime binding from deploy env (not a separate file). Synced by `platform_managed_model.sync_on_startup()`.

| Field | Source | Notes |
|-------|--------|-------|
| `provider` | Derived | `bedrock`, `foundry`, or `vertex` from env set |
| `model_id` | `ADO2GH_*_MODEL_ID` env | Read-only in UI |
| `region` | `AWS_REGION` / `GOOGLE_CLOUD_REGION` / Foundry region | |
| `endpoint` | `ADO2GH_FOUNDRY_ENDPOINT` | Foundry only |
| `synced_model_id` | LLM store | `mdl_*` after startup sync |
| `source_approved` | CloudCredentialsStore | **Internal** — not from env |
| `available` | computed | `source_approved` && model `enabled` |

**Rules**:
- Sync creates/updates `LLMModelConfig` with `platform_supplied: true`, `credential_mode: ambient`, `enabled: false` until validate.
- UI renders model SKU fields read-only.
- Agent may use model only when source approved + model validated and enabled.

---

## LLMModelConfig (extensions)

Extends [006 data model](../006-llm-model-catalog/data-model.md).

| Field | Type | Notes |
|-------|------|-------|
| `provider` | enum | Adds `bedrock`, `foundry`, `vertex` |
| `credential_mode` | enum | `byok` (default), `ambient` |
| `platform_supplied` | bool | Default false; true = read-only SKU from env |
| `ambient_provider` | enum? | `aws`, `foundry`, `gcp` when `credential_mode=ambient` |

**Rules**:
- `credential_mode=ambient` requires matching `CloudCredentialSource.status=approved`.
- BYOK model for same provider overrides ambient for that model record (spec US3).
- Revoke/rescan pending invalidates `validation_status` on ambient/platform-supplied models.

---

## Audit events

| Event | Payload (no secrets) |
|-------|----------------------|
| `cloud_credentials.scanned` | `providers_found`, `actor` |
| `cloud_credentials.approved` | `provider`, `primary_method`, `actor` |
| `cloud_credentials.rejected` | `provider`, `reason`, `actor` |
| `cloud_credentials.revoked` | `provider`, `actor` |
| `cloud_credentials.rescan_pending` | `provider`, `changed_fields[]` |
