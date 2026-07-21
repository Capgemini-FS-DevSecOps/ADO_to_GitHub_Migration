# Feature Specification: LLM Model Catalog, Validation, and Connectivity Settings

**Feature Branch**: `006-llm-model-catalog`

**Created**: 2026-06-16

**Status**: Draft

**Input**: LLM models in the migration settings should not need the user to specify the model ID. Provide all supported models either in an API call, or a preset of known models. It should contain a validate button that will ensure the connection to the model is available. If under a corporate proxy there should be settings to configure the model. There should also be an option to use local models (such as Ollama models/qwen/etc.).

## Clarifications

### Session 2026-06-16

- Q: Where should corporate proxy settings live (global, per-model, per-provider, or hybrid)? → A: **Global per environment** — one proxy applies to all outbound cloud catalog fetch and validation calls.
- Q: When catalog selection doesn't include the desired model, should manual override be allowed? → A: **Disabled by default** — manual model-ID override is hidden unless admin enables an environment-level “allow custom model ID” setting.
- Q: Must a model pass Validate before it can be enabled or set as default? → A: **Required** — a model MUST pass validation at least once before it can be enabled or set as default; connection must be proven before use.
- Q: When credentials allow live listing, which catalog source should the UI prefer? → A: **Live primary** — fetch live catalog when credentials and network allow; fall back to preset on failure with a visible “catalog may be stale” notice.
- Q: Should v1 include self-service custom CA/trust configuration in Settings? → A: **In scope v1** — admin can upload or paste a custom CA certificate in environment-level connectivity settings for corporate TLS inspection.
- Q: Analyze remediation (2026-06-16) — Where should Models page read `allow_custom_model_id` before Connectivity settings UI ships? → A: **Early read-only `GET /v1/settings/connectivity`** in foundational phase (Phase 2); full PUT/UI remains US3.
- Q: Analyze remediation (2026-06-16) — Audit when model enabled/set default? → A: Emit **`llm.model.enabled`** audit event (model id, enabled/default flags only; no secrets) when admin enables or sets default after validation passed.
- Q: Analyze remediation (2026-06-16) — SC-002 concurrency coverage? → A: Tests assert concurrent duplicate validate requests do not hang and complete within 30s (single-flight / in-flight reuse).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Select a Model From a Catalog (Priority: P1)

A platform admin opens Migration Settings → Models and adds or edits an LLM entry by choosing a provider and picking a model from a supported list—without typing an internal model identifier manually.

**Why this priority**: Manual model IDs are error-prone and block non-expert admins from onboarding models; a catalog is the core usability improvement.

**Independent Test**: Admin opens model settings → selects provider “OpenAI-compatible cloud” → sees a list of known models → selects one → saves with display name only → Agent tab shows the chosen model by friendly name.

**Acceptance Scenarios**:

1. **Given** admin role on the Models settings page, **When** admin starts adding a model, **Then** the UI presents a provider type and a selectable list of supported models for that provider (not a free-text model-ID field as the primary path).
2. **Given** a provider whose catalog is loaded from a live provider listing, **When** valid credentials are supplied and the listing succeeds, **Then** the live catalog is shown as the primary source (preset used only when live fetch fails or credentials are absent).
3. **Given** a provider whose catalog is maintained as a built-in preset (e.g., when live listing is unavailable or disallowed), **When** admin selects that provider, **Then** a curated preset list of known models is shown with the same pick-and-save flow.
4. **Given** a saved catalog selection, **When** an operator views the Agent model picker, **Then** they see the admin’s display name and provider label—not raw internal identifiers as the primary label.
5. **Given** operator role, **When** user attempts to add or change models, **Then** the action remains blocked per existing role boundaries (admin-only model management).

---

### User Story 2 - Validate Model Connection Before Use (Priority: P1)

Before saving or enabling a model, an admin runs **Validate** to confirm credentials, network reachability, and that the selected model responds—without starting a full agent migration session.

**Why this priority**: Admins need fast feedback that configuration works; waiting for an agent session failure is too late in enterprise setups.

**Independent Test**: Admin enters credentials and selects a catalog model → clicks Validate → receives clear success or actionable failure within a short wait → enables or sets default only after validation passes.

**Acceptance Scenarios**:

1. **Given** a completed model configuration (provider, catalog selection, credentials where required), **When** admin clicks **Validate**, **Then** the system performs a minimal connectivity/inference check and returns pass or fail with a user-readable reason (no secrets in messages).
2. **Given** validation succeeds, **When** the result is shown, **Then** admin may save, enable, or set the model as default; validation timestamp/status is visible on the model record.
3. **Given** validation fails (bad key, unreachable host, unknown model, proxy misconfiguration, TLS/trust error, or timeout), **When** the result is shown, **Then** admin sees categorized guidance (credentials, network, proxy, model selection, TLS, timeout) without exposing secret values.
4. **Given** validation is in progress, **When** admin waits, **Then** the UI shows loading state and prevents duplicate concurrent validation requests for the same draft.
5. **Given** a previously validated model, **When** admin changes credentials, catalog selection, provider, base URL, **or** environment connectivity profile (proxy/CA), **Then** validation status is cleared until **Validate** is run again.

---

### User Story 3 - Corporate Proxy and Network Settings (Priority: P2)

An admin operating behind a corporate HTTP/HTTPS proxy configures proxy host, port, and optional authentication so cloud model providers remain reachable from the migration platform.

**Why this priority**: Many banking and insurance environments require explicit proxy configuration; without it, catalog and validation appear “broken” when the issue is network policy.

**Independent Test**: Admin enables proxy settings with test credentials → runs Validate against a cloud provider → succeeds where direct egress fails (or fails with proxy-specific guidance when misconfigured).

**Acceptance Scenarios**:

1. **Given** admin role, **When** admin configures optional proxy settings (enable flag, host, port, optional username/password) in environment-level connectivity settings, **Then** values are stored securely and never returned in plain text after save.
2. **Given** proxy enabled, **When** admin runs **Validate** or the system fetches a live model catalog, **Then** outbound requests use the configured proxy.
3. **Given** proxy authentication fails, **When** validation runs, **Then** the failure message indicates proxy/auth without leaking credentials.
4. **Given** no proxy configured, **When** admin validates a local-only provider, **Then** proxy settings do not block local connectivity checks.
5. **Given** corporate TLS inspection, **When** admin uploads or pastes a custom CA certificate in environment connectivity settings, **Then** cloud catalog fetch and validation trust that CA for outbound TLS (certificate stored securely, never echoed in UI after save).

---

### User Story 4 - Local and Self-Hosted Models (Priority: P2)

An admin registers local or self-hosted inference endpoints (e.g., Ollama, Qwen, or other on-prem runtimes) by specifying a base service address and choosing from models exposed by that service.

**Why this priority**: Regulated teams often mandate on-prem or air-gapped inference; cloud-only catalogs exclude a major deployment pattern.

**Independent Test**: Admin adds “Local / self-hosted” provider → enters base URL → system lists models from that endpoint → admin selects Qwen (or equivalent) → Validate succeeds → Agent session uses the local model.

**Acceptance Scenarios**:

1. **Given** admin role, **When** admin selects a local/self-hosted provider type, **Then** the UI prompts for a service base address (not a cloud API key unless the local gateway requires one).
2. **Given** a reachable local inference service, **When** admin requests the model list, **Then** available local model names are shown for selection (live discovery preferred; manual override only when discovery fails **and** the environment-level “allow custom model ID” setting is enabled).
3. **Given** a selected local model, **When** admin runs **Validate**, **Then** the system confirms the endpoint responds using the chosen model name.
4. **Given** local service unreachable, **When** listing or validation runs, **Then** admin sees network/service guidance without blocking the rest of Settings.
5. **Given** a configured local model marked default, **When** an agent session starts, **Then** inference uses the local selection; if validation has not passed, the model cannot be enabled or set as default (same rule as cloud models).

---

### Edge Cases

- Provider catalog API is down or rate-limited: fall back to preset list with visible “catalog may be stale” notice; admin can still validate a known selection once credentials are configured.
- Live catalog returns models not yet in preset: admin can select them when listing succeeds; presets updated on a scheduled basis out of band.
- Validate timeout (slow proxy or cold local model): show timeout with retry; do not enable or set default automatically.
- Admin may save a draft configuration without passing validation, but **Enable** and **Set as default** remain disabled until validation passes at least once.
- Custom model ID override disabled by default: free-text model name field hidden until admin enables environment-level “allow custom model ID”; when enabled, override available for any provider after catalog/discovery failure.
- Proxy required for cloud but disabled: validation fails with explicit proxy hint.
- Local service exposes dozens of models: list is searchable/filterable in the UI.
- Mixed environment: one cloud model and one local model both configured; admin sets default; operators pick per session where permitted.
- Credential rotation: changing secrets invalidates prior validation status.
- Corporate TLS inspection: admin MAY upload or paste a custom CA in environment connectivity settings (v1); validation and catalog fetch use that trust in addition to the system store; TLS failures surface clearly when CA is missing or invalid.

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

This feature configures agent inference connectivity only; it does not execute migrations directly. Safeguards still apply to secrets and audit:

- **CA-003**: API keys, proxy passwords, custom CA material, and tokens MUST NOT appear in logs, UI after save, audit payloads, or validation error messages.
- **CA-004**: Model create/update/delete, validation attempts (pass/fail, not secrets), and proxy setting changes MUST be auditable with actor and timestamp.
- **Role separation**: Model management remains admin-only, consistent with feature `004-agent-pev-rbac`.

### Functional Requirements

- **FR-001**: The Models settings experience MUST offer provider-specific model selection from a catalog as the primary path; free-text model identifier entry MUST NOT be required for standard onboarding.
- **FR-002**: For each supported cloud provider type, the system MUST supply model choices via live catalog retrieval when credentials and network allow (**primary path**), and via a maintained preset of known models when live retrieval fails, credentials are absent, or policy disables live fetch (**fallback** with visible stale notice).
- **FR-003**: Each catalog entry MUST expose a human-readable name and enough description for admin selection; internal provider model identifiers MUST be stored automatically from the selection.
- **FR-004**: The Models settings UI MUST include a **Validate** action that checks connectivity and model availability for the current draft or saved configuration without running a full migration or agent PEV session.
- **FR-005**: Validation results MUST distinguish failure categories: invalid credentials (`credentials`), network/unreachable host (`network`), proxy misconfiguration (`proxy`), model not found (`model_not_found`), TLS/trust errors including missing or invalid custom CA (`tls`), and timeout (`timeout`).
- **FR-006**: Model records MUST store last validation status (never validated, passed, failed) and last validation time; changing credentials, provider, catalog selection, or base URL MUST reset validation to never validated until re-run. Changes to the **environment connectivity profile** (proxy, custom CA, override toggle) MUST invalidate validation on all registered models.
- **FR-007**: Admins MUST be able to configure optional corporate HTTP/HTTPS proxy settings (enable, host, port, optional authenticated proxy credentials) at the **environment level**; when enabled, the single proxy configuration applies to all outbound cloud catalog fetch and validation requests (not per-model or per-provider).
- **FR-007a**: Admins MUST be able to upload or paste an optional custom CA certificate at the environment level for corporate TLS inspection; stored securely (CA-003), applied to outbound cloud catalog fetch and validation TLS trust, and cleared/re-validated when the CA is changed.
- **FR-008**: Proxy credentials MUST be stored and displayed with the same redaction rules as LLM API keys (CA-003).
- **FR-009**: The system MUST support a local/self-hosted provider type where admin supplies a base service address and selects from models discovered from that service (e.g., Ollama-style and Qwen-class local runtimes).
- **FR-010**: Local model discovery failure MUST allow admin to retry or adjust base address; manual model name entry MUST be **disabled by default** and available only when admin enables an environment-level “allow custom model ID” setting (applies to cloud and local providers when catalog/discovery does not list the desired model).
- **FR-011**: Enabled models MUST remain selectable in the Agent model picker. A model MUST pass validation at least once before it can be enabled or set as default; draft saves without validation are allowed but enable/default controls stay disabled until validation succeeds.
- **FR-012**: Operators MUST remain read-only for Models settings; only platform admins (per existing RBAC) may create, update, delete, validate-on-behalf, or change proxy/model connectivity settings.
- **FR-013**: Validation and catalog fetch MUST enforce reasonable timeouts and **single-flight** behavior per configuration (separate locks for catalog vs validate) to avoid hung UI and duplicate outbound calls.
- **FR-014**: All model and connectivity configuration changes (including proxy and custom CA updates) MUST emit audit events without secret values (CA-004). Enabling a model or setting **`default_for_agent`** after validation passes MUST emit **`llm.model.enabled`** (model id and flag changes only).

### Key Entities *(include if feature involves data)*

- **Model Catalog Entry**: A selectable model option within a provider (display name, description, internal provider model name, provider type, source: preset or live).
- **Registered Model**: Admin-saved configuration linking a catalog selection (or approved override) to credentials, enabled/default flags, validation status, and timestamps—extends the existing model registry from feature `004`.
- **Connectivity Profile**: Environment-level proxy, optional custom CA certificate, and **allow custom model ID** toggle (default off) governing manual model-name override; one profile per deployment environment shared by all cloud catalog fetch and validation traffic.
- **Validation Result**: Outcome of a connectivity check (status, failure category, user message, checked-at time); no secret payload.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admins can add a cloud model by catalog selection alone (no manual model-ID typing) in under 3 minutes including one successful validation.
- **SC-002**: At least 95% of validation attempts return a pass/fail result (not hang) within 30 seconds under normal network conditions.
- **SC-003**: 100% of validation failure messages shown to users contain no secret material (automated redaction audit on sample failures).
- **SC-004**: Admins can register and validate at least one local/self-hosted model (e.g., Ollama-exposed Qwen or equivalent) and use it in an agent session smoke test.
- **SC-005**: Support tickets or internal feedback related to “wrong model ID” or “model not found due to typo” decrease compared to the pre-catalog manual-entry flow (qualitative baseline: zero typos required for standard preset models).
- **SC-006**: Proxy-enabled environments: admins can complete catalog fetch or validation using configured proxy without editing environment files on the host (self-service in Settings).

## Assumptions

- Feature `004-agent-pev-rbac` model registry, admin-only RBAC, and Agent model picker exist; this feature enhances onboarding UX and connectivity—not greenfield auth.
- Initial preset catalogs cover the provider types already accepted in v1 (OpenAI-compatible cloud, Anthropic cloud) plus a local/self-hosted type; additional vendors can be added incrementally.
- Live catalog retrieval requires valid credentials supplied by admin before fetch; when credentials are absent or live fetch fails, preset catalog is shown with stale notice.
- Proxy settings are configured once per deployment environment and apply to all outbound cloud traffic from platform services; browser-side calls do not bypass server-side validation.
- Local inference services expose a discoverable model list compatible with common self-hosted conventions; exotic gateways may require preset or override fallback.
- TLS trust uses the system trust store **plus** any admin-configured custom CA in environment connectivity settings; changing the custom CA invalidates prior model validation status until re-run.
- “Validate” performs a minimal test prompt or provider health check—not a load or cost benchmark.
- Operators continue to select among admin-enabled models; they do not manage catalogs or proxy settings.

## Dependencies

- Existing LLM model registry and `/v1/settings/llm-models` surface from feature `004-agent-pev-rbac`.
- Platform admin role and `can_manage_models` capability from feature `004`.
- Agent runtime must resolve registered models (cloud and local) after validation/enabled flags are set.

## Out of Scope

- Automatic model cost optimization or routing across providers.
- Fine-tuning or training models.
- Per-operator personal API keys (central admin-managed credentials only).
- Migration execution, live-approval queue, or phase-gate changes (unchanged from `004`).
- Hosting or installing Ollama/local inference runtimes inside this product (admins supply reachable endpoints).
