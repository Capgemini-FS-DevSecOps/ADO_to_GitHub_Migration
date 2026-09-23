# Contract: Environment Connectivity Settings API

**Feature**: `006-llm-model-catalog`  
**Service**: Accelerator API (`services/accelerator_api`)  
**Base path**: `/v1/settings/connectivity`

## Get connectivity profile

`GET /v1/settings/connectivity`

**Auth**: `can_manage_models` (admin only)

**Response** `200`:

```json
{
  "proxy_enabled": false,
  "proxy_host": "",
  "proxy_port": 8080,
  "proxy_username": "",
  "proxy_password": "",
  "custom_ca_configured": true,
  "allow_custom_model_id": false,
  "updated_at": "2026-06-16T12:00:00Z",
  "updated_by": "admin"
}
```

`proxy_password` and raw CA PEM MUST always be `"***"` or omitted when configured (CA-003). `custom_ca_configured: true` indicates a CA is stored.

---

## Update connectivity profile

`PUT /v1/settings/connectivity`

**Auth**: `can_manage_models`

**Body** (partial update supported):

```json
{
  "proxy_enabled": true,
  "proxy_host": "proxy.corp.example",
  "proxy_port": 8080,
  "proxy_username": "user",
  "proxy_password": "secret",
  "custom_ca_pem": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
  "allow_custom_model_id": false
}
```

**Rules**:
- Omit `proxy_password` or send `"***"` to retain existing password.
- Omit `custom_ca_pem` or send `"***"` to retain existing CA.
- Send `"custom_ca_pem": ""` to clear custom CA.
- Any change to proxy fields, CA, or `allow_custom_model_id` → bulk reset all models' `validation_status` to `never_validated`.

**Response** `200`: public profile (secrets masked).

**Audit**: `connectivity.updated` — fields changed list without values (CA-004).

---

## Test connectivity (optional helper)

`POST /v1/settings/connectivity/test`

**Auth**: `can_manage_models`

Runs lightweight HEAD/GET to `https://api.openai.com/v1/models` or configurable probe URL using current proxy + CA settings (no model credentials required).

**Response** `200`:

```json
{
  "status": "passed",
  "category": null,
  "message": "Outbound TLS and proxy path succeeded."
}
```

Useful for admins configuring proxy/CA before adding models (quickstart scenario 3).

---

## RBAC

| Route | Capability |
|-------|------------|
| `GET/PUT/POST /v1/settings/connectivity*` | `can_manage_models` |

Operators receive `403` on all connectivity routes.

---

## UI route

**Route**: `/settings/connectivity` (admin only tab in Settings nav)

See [llm-models-ui.md](./llm-models-ui.md).
