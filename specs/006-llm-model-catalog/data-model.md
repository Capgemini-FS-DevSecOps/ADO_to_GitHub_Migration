# Data Model: LLM Model Catalog, Validation, and Connectivity Settings

**Feature**: `006-llm-model-catalog` | **Date**: 2026-06-16

## Entity Overview

```text
ConnectivityProfile (file store; one per environment)
    └── applied to → ModelCatalogService / ModelValidationService (cloud outbound)

LLMModelConfig (extended from 004)
    ├── catalog_source: preset | live | override
    ├── validation_status / validation_at / validation_category
    └── base_url (ollama/local)

ModelCatalogEntry (transient API DTO; not persisted)
ValidationResult (transient + persisted snapshot on LLMModelConfig)
```

---

## ConnectivityProfile

Environment-level network settings (spec clarifications Q1, Q2, Q5).

| Field | Type | Notes |
|-------|------|-------|
| `proxy_enabled` | bool | Default false |
| `proxy_host` | string | Hostname or IP |
| `proxy_port` | int | e.g. 8080 |
| `proxy_username` | string | Optional |
| `proxy_password` | secret | `_secrets` sidecar; masked `***` in API |
| `custom_ca_configured` | bool | True when PEM stored |
| `custom_ca_pem` | secret | `_secrets` sidecar; never returned after save |
| `allow_custom_model_id` | bool | Default **false** |
| `updated_at` | ISO8601 | |
| `updated_by` | string | Username from auth |

**Rules**:
- Update to proxy, CA, or `allow_custom_model_id` → set all `LLMModelConfig.validation_status` to `never_validated` (bulk invalidation).
- Audit event `connectivity.updated` without secret fields.

**Storage path**: `{ADO2GH_DATA_DIR}/connectivity_profile.json`

---

## LLMModelConfig (extensions)

Extends [004 data model](../004-agent-pev-rbac/data-model.md).

| Field | Type | Notes |
|-------|------|-------|
| `provider` | enum | Adds `ollama` (local/self-hosted) |
| `model_id` | string | Internal provider model name; set from catalog selection |
| `base_url` | string? | Required for `ollama`; optional override for OpenAI-compatible gateways |
| `catalog_source` | enum | `preset`, `live`, `override` |
| `catalog_label` | string? | Human name from catalog entry |
| `validation_status` | enum | `never_validated`, `passed`, `failed` |
| `validation_at` | ISO8601? | Last successful/failed check time |
| `validation_category` | string? | Last failure category when failed |
| `validation_message` | string? | User-safe message (no secrets) |
| `enabled` | bool | **Only true when `validation_status=passed`** |
| `default_for_agent` | bool | **Only true when `validation_status=passed`** |

**State transitions (validation)**:

```text
never_validated ──(Validate pass)──► passed
never_validated ──(Validate fail)──► failed
passed ──(credential/catalog/base_url/proxy/CA change)──► never_validated
failed ──(Validate pass)──► passed
```

**Enable gate**: API MUST reject create/update setting `enabled: true` or `default_for_agent: true` unless `validation_status === passed`.

---

## ModelCatalogEntry (DTO)

Returned by catalog endpoints; not persisted.

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Stable key within response (provider model id) |
| `display_name` | string | |
| `description` | string? | |
| `provider` | string | `openai`, `anthropic`, `ollama` |
| `source` | enum | `live`, `preset` |

**Catalog response wrapper**:

| Field | Type | Notes |
|-------|------|-------|
| `entries` | ModelCatalogEntry[] | |
| `source` | enum | `live`, `preset`, `mixed` |
| `stale` | bool | True when preset fallback used after live failure |

---

## ValidationResult (DTO + persisted fields)

| Field | Type | Notes |
|-------|------|-------|
| `status` | enum | `passed`, `failed` |
| `category` | string? | `credentials`, `network`, `proxy`, `model_not_found`, `timeout`, `tls` |
| `message` | string | User-safe |
| `validated_at` | ISO8601 | |

On success, copy into `LLMModelConfig` validation fields. Audit `llm.model.validated` with status + category only.

---

## Relationships

- **ConnectivityProfile → LLMModelConfig**: indirect; connectivity changes invalidate all model validations.
- **LLMModelConfig → AgentPEVSession**: unchanged from 004 (`selected_model_id`); picker lists `enabled=true` models only.
- **ModelCatalogEntry → LLMModelConfig**: on save, `model_id` and `catalog_*` fields copied from selection.
