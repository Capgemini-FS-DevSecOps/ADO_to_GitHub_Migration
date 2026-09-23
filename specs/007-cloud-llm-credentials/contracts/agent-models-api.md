# Contract: Agent Models API (Cloud-Backed Selection)

**Feature**: `007-cloud-llm-credentials`  
**Service**: Agent (`services/agent`)  
**Resolves**: U2 — finalized endpoint (no “or extend existing” ambiguity)

## Endpoint

`GET /v1/agent/models`

**Auth**: Same session auth as other `/v1/sessions` routes (not admin-only).

**Purpose**: Expose **selectable** LLM models for the agent UI. Cloud-backed entries appear when internal state says `source_approved` and model `enabled` — never from deployment env mode flags (FR-007b, FR-016).

**Relationship to existing routes**:
- `GET /v1/llm/status` remains for degraded/unconfigured messaging and default model id.
- `GET /v1/agent/models` is the **canonical list** for model picker options (AgentChat reads this).

---

## Response `200`

```json
{
  "models": [
    {
      "id": "mdl_abc123",
      "display_name": "Platform Bedrock (Claude)",
      "provider": "bedrock",
      "credential_mode": "ambient",
      "cloud_provider": "aws",
      "platform_supplied": true,
      "source_approved": true,
      "enabled": true,
      "validation_status": "passed"
    },
    {
      "id": "mdl_byok_1",
      "display_name": "OpenAI GPT-4",
      "provider": "openai",
      "credential_mode": "byok",
      "cloud_provider": null,
      "platform_supplied": false,
      "source_approved": null,
      "enabled": true,
      "validation_status": "passed"
    }
  ],
  "default_model_id": "mdl_abc123"
}
```

**Inclusion rules**:
- Include all models where `enabled=true` and `validation_status=passed`.
- For `credential_mode=ambient`, include only if matching `CloudCredentialSource.status=approved`.
- Omit ambient models when source is `pending`, `rejected`, or `revoked`.

**Field notes**:
- `cloud_provider`: `aws` | `foundry` | `gcp` for ambient models; `null` for BYOK/stub/offline.
- `platform_supplied`: true when model SKU came from deploy env sync.

---

## Errors

| Code | When |
|------|------|
| `401` | Unauthenticated (when auth enabled) |

---

## UI consumption (`AgentChat.tsx`)

1. On mount, `fetch('/v1/agent/models', { credentials: 'include' })`.
2. Populate picker from `models[]`.
3. On empty `models`, show degraded banner linking to Settings (admin: cloud credentials + models).
4. Do **not** read `ADO2GH_*` env vars in the browser.

---

## Tests (contract)

- Approved+enabled ambient model present in list (T037).
- Pending source removes ambient model from list (T038).
- Response passes secret redaction rules from [cloud-credentials-api.md](./cloud-credentials-api.md#secret-redaction-sc-003).
