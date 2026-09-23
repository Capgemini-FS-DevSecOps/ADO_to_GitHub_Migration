# Contract: LLM Model Catalog & Validation API

**Feature**: `006-llm-model-catalog`  
**Service**: Accelerator API (`services/accelerator_api`)  
**Extends**: [004 llm-model-api.md](../../004-agent-pev-rbac/contracts/llm-model-api.md)

## Browse catalog

`GET /v1/settings/llm-models/catalog`

**Auth**: `can_manage_models` (admin)

**Query parameters**:

| Param | Required | Description |
|-------|----------|-------------|
| `provider` | yes | `openai`, `anthropic`, `ollama` |
| `api_key` | for cloud | Required for live OpenAI fetch; optional for Anthropic (preset-only) |
| `base_url` | for ollama | e.g. `http://localhost:11434` |

**Behavior** (FR-002, clarification Q4):
1. **OpenAI**: attempt live `GET /v1/models`; on failure → preset from `llm_presets.json` with `stale: true`.
2. **Anthropic**: return preset list (`source: preset`); `stale: false`.
3. **Ollama**: `GET {base_url}/api/tags`; no proxy applied.

**Response** `200`:

```json
{
  "entries": [
    {
      "id": "gpt-4o-mini",
      "display_name": "GPT-4o Mini",
      "description": "Fast, cost-effective",
      "provider": "openai",
      "source": "live"
    }
  ],
  "source": "live",
  "stale": false
}
```

**Errors**:
- `400` — missing required params
- `403` — not admin
- `502` — discovery failed and no preset available (rare)

---

## Validate draft configuration

`POST /v1/settings/llm-models/validate`

**Auth**: `can_manage_models`

**Body** (unsaved model):

```json
{
  "display_name": "GPT-4o Mini",
  "provider": "openai",
  "model_id": "gpt-4o-mini",
  "api_key": "sk-...",
  "base_url": null,
  "catalog_source": "live"
}
```

**Behavior**:
- Uses environment `ConnectivityProfile` for cloud proxy + custom CA.
- 30s timeout (SC-002, FR-013).
- Single-flight: concurrent validate for same draft key returns `409` or waits (implementation choice; must not duplicate outbound calls).

**Response** `200`:

```json
{
  "status": "passed",
  "category": null,
  "message": "Model responded successfully.",
  "validated_at": "2026-06-16T18:00:00Z"
}
```

**Failure** `200` (not 5xx for user errors):

```json
{
  "status": "failed",
  "category": "proxy",
  "message": "Could not reach provider through configured proxy.",
  "validated_at": "2026-06-16T18:00:00Z"
}
```

Categories MUST be one of: `credentials`, `network`, `proxy`, `model_not_found`, `timeout`, `tls` (FR-005).

**Audit**: `llm.model.validated` — status + category only (CA-003).

---

## Validate saved model

`POST /v1/settings/llm-models/{model_id}/validate`

**Auth**: `can_manage_models`

Uses stored secrets + model fields. On `passed`, updates model validation fields. Does **not** auto-enable.

---

## Create / update model (extended)

Existing `POST/PUT /v1/settings/llm-models` from 004 with additional rules:

**New fields**:

```json
{
  "catalog_source": "live",
  "catalog_label": "GPT-4o Mini",
  "base_url": "http://ollama.internal:11434",
  "enabled": true,
  "default_for_agent": false
}
```

**Validation rules** (FR-011):
- Reject `enabled: true` or `default_for_agent: true` unless request includes `validation_status: passed` from immediate prior validate **or** model already has `validation_status=passed` and unchanged connectivity-sensitive fields.
- Prefer flow: **Validate draft → Save with enabled**.

**Override** (FR-010):
- `catalog_source: override` + free-text `model_id` allowed only when `ConnectivityProfile.allow_custom_model_id === true`.

**Provider values**: `openai`, `anthropic`, `ollama`, `stub`, `offline`

**On save**: if credentials, catalog selection, `base_url`, or provider change → reset `validation_status` to `never_validated`.

---

## List models (extended response)

`GET /v1/settings/llm-models` adds per model:

```json
{
  "validation_status": "passed",
  "validation_at": "2026-06-16T17:00:00Z",
  "catalog_source": "live",
  "catalog_label": "GPT-4o Mini",
  "base_url": null
}
```

Operator view unchanged: enabled models only in agent picker; no connectivity secrets.

---

## Runtime (server-only)

`get_llm_provider(model_id)` extended:
- Resolve `ollama` via `OllamaProvider(base_url, model_id)`.
- Cloud providers use shared httpx client with connectivity profile when calling provider APIs from agent service (mirror accelerator validation client).

Invalid/disabled/unvalidated model used in session → stub + `degraded: true`.
