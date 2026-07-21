# Contract: LLM Model Registry API

**Feature**: `004-agent-pev-rbac`  
**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/settings/llm-models`

## List models (operator read)

`GET /v1/settings/llm-models`

**Auth**: Any authenticated user when auth enabled; open in dev.

**Response** `200`:

```json
{
  "models": [
    {
      "id": "mdl_abc123",
      "display_name": "GPT-4o Mini",
      "provider": "openai",
      "model_id": "gpt-4o-mini",
      "api_key": "***",
      "enabled": true,
      "default_for_agent": true,
      "created_at": "2026-06-16T12:00:00Z",
      "updated_at": "2026-06-16T12:00:00Z"
    }
  ]
}
```

Secrets MUST appear as `"***"` only (CA-003). Never return raw `api_key` after initial save.

---

## Create model (admin)

`POST /v1/settings/llm-models`

**Auth**: `can_manage_models` (admin only)

```json
{
  "display_name": "GPT-4o Mini",
  "provider": "openai",
  "model_id": "gpt-4o-mini",
  "api_key": "sk-...",
  "enabled": true,
  "default_for_agent": true
}
```

**Validation**:
- Trim `api_key` whitespace; reject empty for non-stub providers.
- Valid `provider`: `openai`, `anthropic`, `stub`, `offline`.
- **v1 acceptance**: runtime MUST support both `openai` and `anthropic` provider types (SC-004).

**Response** `201`: public model object (api_key masked).

**Audit**: `llm.model.created` with actor username.

---

## Update model (admin)

`PUT /v1/settings/llm-models/{model_id}`

**Auth**: `can_manage_models`

Partial update supported. Omit `api_key` to retain existing secret; send `"api_key": ""` to clear (stub only).

---

## Delete model (admin)

`DELETE /v1/settings/llm-models/{model_id}`

**Auth**: `can_manage_models`

**Response** `200`: `{ "deleted": "mdl_abc123" }`

**Audit**: `llm.model.deleted`

---

## Runtime resolution (server-only)

Agent service calls internal helper (not HTTP):

```python
get_llm_provider(model_id: str | None) -> LLMProvider
```

- Loads secret from `LLMModelStore` by id.
- Invalid/revoked key → stub provider + `degraded=True`.
- No model configured → stub (FR-005).

---

## Settings UI contract

**Route**: `/settings/models` (admin only)

| Action | Visible |
|--------|---------|
| List models | admin |
| Add / edit / delete | admin |
| View list in Agent picker | all authenticated (enabled models only) |

Operator navigating to `/settings/models` → redirect or 403 with explanation (FR-007).
