# Contract: LLM Models & Connectivity Settings UI

**Feature**: `006-llm-model-catalog`  
**App**: `apps/migration-ui`

## Settings navigation

| Tab | Route | Visible | Notes |
|-----|-------|---------|-------|
| Profiles | `/settings/profiles` | per 004/005 | |
| Models | `/settings/models` | `can_manage_models` | Catalog picker + Validate |
| Connectivity | `/settings/connectivity` | `can_manage_models` | Proxy, CA, override toggle |
| Approvals | `/settings/approvals` | per 004 | |

Extend `visibleSettingsTabs()` in `permissions.ts` with Connectivity tab (admin only).

---

## Models page (`/settings/models`)

### List section

Each model row shows:
- Display name, provider, catalog label
- Validation badge: `Passed` / `Failed` / `Not validated` + timestamp
- Enabled / default flags (read-only indicators)

### Add / edit form

| Field | Control | Notes |
|-------|---------|-------|
| Display name | text | Required |
| Provider | select | `openai`, `anthropic`, `ollama`, `stub` |
| API key | password | Required for cloud; optional for ollama gateway |
| Base URL | text | Shown when provider = `ollama` |
| Model | searchable select | Loaded from `GET .../catalog` after provider + credentials/base URL |
| Custom model ID | text | Hidden unless `allow_custom_model_id` AND (override expanded OR catalog error) |

**Catalog load flow**:
1. Admin selects provider (+ enters api_key or base_url).
2. UI fetches catalog; shows loading spinner.
3. If `stale: true`, show banner “Catalog may be outdated”.
4. Admin picks catalog entry → internal `model_id` set automatically (FR-001).

**Actions**:

| Button | Enabled when | Behavior |
|--------|--------------|----------|
| **Validate** | provider + model selected + required secrets | `POST .../validate`; show category on failure |
| **Save** | display name + model selection | Saves draft; does not bypass validation gate for enable |
| **Enable** | validation passed | toggle or checkbox |
| **Set as default** | validation passed | single default rule from 004 |

Enable and Set default controls **disabled** until Validate succeeds (FR-011).

**Operator**: page shows access-denied hint (existing `modelsAccessDeniedMessage`).

---

## Connectivity page (`/settings/connectivity`)

| Field | Control |
|-------|---------|
| Enable proxy | checkbox |
| Proxy host | text |
| Proxy port | number |
| Proxy username | text |
| Proxy password | password (masked after save) |
| Custom CA certificate | textarea or file upload → paste PEM |
| Allow custom model ID | checkbox (default off) |

**Actions**:
- **Save** — `PUT /v1/settings/connectivity`
- **Test connection** (optional) — `POST .../connectivity/test`

On save success, show notice: “Model validations reset — re-validate models before enabling.”

---

## Agent tab model picker

Unchanged from 004: lists **enabled** models only. Show warning icon if admin disabled model after prior session (edge case).

---

## Error / loading states

- Catalog fetch failure with preset fallback → banner + preset list (not empty state error).
- Validate in progress → disable Validate button, show spinner (FR-013 single-flight).
- Validate timeout → message with Retry.

---

## Accessibility

- Catalog select supports keyboard search/filter when >10 entries (FR edge case).
- Validation status exposed as text badge (not color-only).
