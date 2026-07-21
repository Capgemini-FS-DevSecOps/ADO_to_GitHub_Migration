# Feature Specification: Cloud-Hosted LLM Credential Detection and Admin Approval

**Feature Branch**: `007-cloud-llm-credentials`

**Created**: 2026-06-18

**Status**: Draft

**Input**: In onboarding our agent into the agentic platform it will be deployed on an EC2 instance configured with Docker. LLMs used when deployed will be hosted on Bedrock. The current implementation allows users to bring their own LLM keys (which is fine). But it should also detect and see if there are required environment variables to connect to Bedrock/Microsoft Foundry/GCP Vertex AI and have admin approve the process.

## Clarifications

### Session 2026-06-18

- Q: What counts as a material change requiring re-approval after rescan (FR-010)? → A: **Method + config** — re-approval required if credential method, region, project, endpoint, or completeness changes.
- Q: When multiple credential methods exist for one cloud provider, how are sources modeled? → A: **One per provider** — single source per hyperscaler API id (`aws`, `foundry`, `gcp`); show primary method and note alternates. `foundry` = Microsoft Foundry (Azure).
- Q: When should live cloud credential probes run vs presence-only detection? → A: **Probe on approve/validate** — scan checks presence; live probe runs when admin clicks Approve or model Validate.
- Q: How should Bedrock model setup work after AWS approval? → A: **Platform-provided model** — model identifier is supplied by the platform team at deployment; admins do not choose from a catalog.
- Q: How does the platform team supply the fixed model configuration? → A: **Deploy-time env vars** — model ID and region set via Docker Compose or EC2 config by the platform team.

### Session 2026-06-16 (analyze remediation)

- Q: v1 hyperscaler scope (I1)? → A: **All three** — AWS (Amazon Bedrock), Microsoft Foundry, and Google Cloud Vertex AI are in v1 scope for presence detection, approval, probe, and validation.
- Q: US2 vs US3 validation boundary (I2)? → A: **US2 owns validation end-to-end** — approve + probe + audit + model validate/enable; US3 covers agent runtime consumption only.
- Q: Agent model availability (A3 / I4)? → A: **Approved = selectable option** — when admin approves a cloud source, ambient models for that provider become available to the agent as a normal model option. Availability is driven by **internal application state** (approved credential source), not a deployment environment flag. The agent API exposes which cloud-backed models are available; the UI reads that API.
- Q: Incomplete cloud configuration (U1)? → A: **Error + UI completion** — incomplete sources return a clear error; admin can supply missing non-secret configuration fields in the Cloud credentials UI to reach completeness before approval.
- Q: Rescan wait time (A1)? → A: **2–5 seconds** — rescan updates the source list within 2–5 seconds under normal single-tenant deployment conditions.

### Session 2026-06-19 (clarification)

- Q: Probe timeout and retry policy (FR-001a)? → A: **30-second timeout with 3 automatic retries using exponential backoff (1s, 2s, 4s)** — balances responsiveness with resilience against transient network issues common in cloud environments.
- Q: Rescan performance load conditions (FR-009)? → A: **Single-tenant deployment with no concurrent operations** — aligns with Assumptions section stating primary production is single-tenant EC2 instance; avoids over-engineering for multi-tenant scenarios not in v1 scope.
- Q: UI read-only enforcement mechanism (FR-007a)? → A: **Display as read-only text fields with tooltip explaining "Set by platform team via environment variables"** — provides clear visual feedback about why fields are read-only while maintaining familiar form layout; tooltip educates admins without blocking workflow.
- Q: Incomplete configuration error message format (US2/AC2)? → A: **Specific error listing missing fields: "Missing required fields: region, project_id. Please complete configuration before approving."** — provides actionable feedback that guides admin directly to the problem, reducing friction in approval workflow.

### Session 2026-06-20 (analyze polish)

- Provider API ids: `aws`, `foundry`, `gcp`. Contract file: `platform-supplied-llm.md`.
- Agent model list: `GET /v1/agent/models` (see FR-016). FR-007b is the user-facing outcome; FR-016 is the API contract.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover Cloud LLM Credential Sources (Priority: P1)

A platform admin deploys the migration agent on a cloud-hosted virtual machine using containers. On first access to Settings, the system reports whether the host environment appears to have the configuration needed to reach hosted large-language-model services on **Amazon Bedrock**, **Microsoft Foundry**, and **Google Cloud Vertex AI**—without exposing secret values.

**Why this priority**: Operators onboarding on EC2 or similar hosts need to know immediately whether instance roles, mounted secrets, or environment configuration are present before configuring models manually.

**Independent Test**: Deploy on a host with AWS instance credentials configured → admin opens cloud credential settings → sees a detected AWS/Bedrock source marked “pending approval” with a non-secret summary (e.g., region, credential method). Repeat for Microsoft Foundry and Vertex AI hosts to confirm all three hyperscalers are supported in v1.

**Acceptance Scenarios**:

1. **Given** the host has sufficient AWS configuration for Bedrock (e.g., instance role, standard access-key environment variables, or region variables), **When** admin opens cloud LLM credential settings or triggers a scan, **Then** the system lists an AWS/Bedrock credential source with status **pending**, method label (e.g., instance role vs environment variables), and region if known—without displaying keys or tokens.
2. **Given** the host has Microsoft Foundry configuration (e.g., tenant/client/endpoint environment variables or managed identity indicators), **When** a scan runs, **Then** a Microsoft Foundry source appears with pending status and which required settings are present vs missing.
3. **Given** the host has Vertex AI configuration (e.g., project/region variables or application-default credential file path present), **When** a scan runs, **Then** a GCP Vertex source appears with pending status and a non-secret summary.
4. **Given** no cloud LLM configuration is detectable, **When** admin views the page, **Then** the system states that no hosted cloud LLM credentials were found and that bring-your-own-key model setup remains available.
5. **Given** operator (non-admin) role, **When** user opens settings or calls cloud credential APIs, **Then** cloud credential detection results are not shown or mutations return denial per existing admin-only settings pattern.

---

### User Story 2 - Admin Approves, Validates, and Enables Cloud Credentials (Priority: P1)

An admin reviews each detected cloud credential source, explicitly approves or rejects use of that source, and—after approval—validates and enables the platform-supplied model for that provider. Until approved, detected credentials MUST NOT be used for model catalog fetch, validation, or agent inference—even if technically present on the host.

**Why this priority**: Enterprise and regulated deployments require human attestation before the application uses ambient cloud credentials that may grant broad API access; validation must complete in the same admin workflow as approval.

**Independent Test**: Detected Bedrock source pending → admin completes missing fields if incomplete → admin clicks Approve → live probe succeeds → source shows approved with audit record → admin validates and enables the platform-supplied Bedrock model without pasting an API key. Reject path leaves BYOK as the only cloud path for that provider.

**Acceptance Scenarios**:

1. **Given** a pending detected source with **complete** configuration, **When** admin approves it, **Then** status becomes **approved** only after a live credential probe succeeds (or remains **pending** with failure reason if probe fails), approver identity and timestamp are recorded, and an audit event is written (no secret values).
2. **Given** a pending detected source with **incomplete** configuration, **When** admin attempts approve or save, **Then** the system returns a clear error listing missing fields and allows the admin to supply missing non-secret configuration in the Cloud credentials UI until completeness is reached.
3. **Given** a pending detected source, **When** admin rejects it, **Then** status becomes **rejected** with optional reason, and the platform does not use that source for LLM calls.
4. **Given** an approved source and a platform-supplied model for that provider, **When** admin runs Validate and Enable, **Then** validation uses approved ambient credentials (no pasted API key where ambient access suffices), enablement follows feature `006` validate-before-enable rules, and failures are categorized (credentials, network, model not found, timeout) without exposing secrets.
5. **Given** an approved source, **When** admin revokes approval, **Then** status returns to rejected or revoked, enabled models depending on that source are disabled or marked requiring re-validation, and agents fall back to other approved models or show degraded state.
6. **Given** a rejected source, **When** admin attempts to enable a model that depends solely on that source without BYOK credentials, **Then** the action is blocked with guidance to approve the source or enter explicit credentials.
7. **Given** admin approval, rejection, revocation, and rescan actions, **When** audit history is reviewed, **Then** each action is attributable to a user and time without logging secret material.

---

### User Story 3 - Use Approved Cloud Credentials in Agent Sessions (Priority: P2)

After a cloud source is approved and its platform-supplied model is validated and enabled, operators can select that model in agent sessions. Approved cloud-backed models appear as normal options exposed by the agent API—not as a hidden deployment flag.

**Why this priority**: Delivers hyperscaler-backed agents on EC2 without operators pasting API keys; agent runtime must reflect admin-approved ambient credentials.

**Independent Test**: Approve AWS source → validate and enable platform Bedrock model → operator starts agent session → agent API lists the Bedrock model as available → operator selects it and receives responses.

**Acceptance Scenarios**:

1. **Given** an approved and validated ambient model for a provider, **When** the agent API is queried for available models, **Then** that model appears as a selectable option with provider and display name.
2. **Given** approved ambient models for multiple providers, **When** operator opens the agent, **Then** each enabled ambient model appears alongside stub, offline, and BYOK models per existing picker behavior.
3. **Given** both an approved ambient source and BYOK credentials for the same provider, **When** admin saves a model, **Then** explicit BYOK credentials take precedence for that model record (documented behavior; ambient used only when the model is configured to use platform credentials).
4. **Given** inference against an approved source fails (permissions, network, or model access), **When** the failure is surfaced in the agent, **Then** the message is categorized without exposing secrets.
5. **Given** a previously approved source demoted to pending after rescan, **When** operator uses an ambient model for that provider, **Then** the agent reports degraded state with re-approve guidance.

---

### User Story 4 - Rescan After Deployment Changes (Priority: P3)

An admin re-runs credential discovery after infrastructure changes (new instance role, updated environment variables, or container restart) so the platform reflects the current host configuration.

**Why this priority**: EC2 and container deployments change over time; one-time detection becomes stale.

**Independent Test**: Change AWS region env → admin clicks Rescan → pending or updated source reflects new region within 2–5 seconds; prior approval policy defined in FR-010 applies.

**Acceptance Scenarios**:

1. **Given** admin role, **When** admin triggers **Rescan**, **Then** detection runs again and updates the list of sources and completeness indicators within **2–5 seconds** under normal single-tenant deployment conditions.
2. **Given** rescan detects a change to credential method, region, project, endpoint, or completeness on a previously approved source, **When** rescan completes, **Then** approval status returns to **pending** and ambient credentials MUST NOT be used until admin re-approves.
3. **Given** rescan in progress, **When** admin waits, **Then** duplicate concurrent scans are prevented or deduplicated.

---

### Edge Cases

- Host exposes credentials for multiple clouds simultaneously (e.g., AWS role plus GCP service account file)—each **provider** is listed and approved independently (one source per AWS, Microsoft Foundry, or GCP).
- Multiple credential methods for the same provider (e.g., instance role and environment variables)—shown as **one** source with a primary method label and non-secret list of alternate methods detected; approval applies to the provider, not each method separately.
- Partial configuration (some required variables set, others missing)—source shown as **incomplete** with which settings are missing; approve returns error until admin completes missing fields in UI or host configuration.
- Approved credentials later removed from host—completeness drops on rescan, source returns to **pending** per FR-010, and validation or agent calls fail gracefully with re-scan/re-approve guidance.
- Local/dev deployment without cloud metadata—empty detection list; BYOK and stub/offline models unaffected; no cloud API calls during scan.
- Docker container receives credentials via env file or secrets mount—presence detection runs inside the application container context; live probes use the same runtime context on Approve/Validate.
- Admin approves but corporate proxy (existing connectivity settings) blocks egress—validation failure attributes network/proxy, not false “approved” success.

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

This feature configures LLM access for the agent; it does not directly execute migrations. Safeguards still apply to credential handling and audit:

- **CA-001**: N/A for migration execution; LLM validation remains a non-destructive preview path before enabling models.
- **CA-002**: Using ambient cloud credentials for LLM access MUST require explicit admin approval (this feature).
- **CA-003**: Secrets MUST NOT appear in logs, UI, audit payloads, or API responses after save; detection summaries show presence/method only.
- **CA-004**: Approve, reject, revoke, and rescan actions MUST be auditable with actor and timestamp.

### Functional Requirements

- **FR-001**: System MUST scan the deployment environment on demand and on initial admin access to cloud LLM credential settings for **presence** indicators of Amazon Bedrock, Microsoft Foundry, and Google Cloud Vertex AI connectivity (environment variables, credential file paths, instance/metadata role availability)—without calling cloud provider APIs during scan. **v1 MUST support all three hyperscalers.**
- **FR-001a**: System MUST run a **live credential probe** when admin approves a pending source or validates a model that uses approved ambient credentials; scan alone MUST NOT call cloud provider APIs. **v1 MUST implement live probes for AWS, Microsoft Foundry, and Vertex AI.**
- **FR-002**: Detection MUST report, per cloud provider (at most one source each for AWS, Microsoft Foundry, and GCP): primary credential method, any alternate methods detected (non-secret labels only), completeness (required settings present vs missing), and recommended region/project/endpoint when inferable—never raw secret values.
- **FR-002a**: When configuration is **incomplete**, system MUST return a clear error on approve/save attempts and MUST allow admin to supply missing **non-secret** configuration fields in the Cloud credentials UI until completeness is reached.
- **FR-003**: Each detected source MUST have a lifecycle status: `pending`, `approved`, `rejected`, or `revoked`, with approver and timestamp when applicable.
- **FR-004**: Only admins MAY approve, reject, or revoke detected cloud credential sources.
- **FR-005**: Until a source is **approved**, the platform MUST NOT use ambient credentials from that source for model catalog fetch, model validation, or agent LLM inference.
- **FR-006**: Bring-your-own-key model onboarding (feature `006-llm-model-catalog`) MUST remain fully supported; ambient credentials are an additional path, not a replacement.
- **FR-007**: After admin approves a cloud source, admins MUST be able to validate and enable the **platform-supplied** model for that provider without entering an API key or selecting from a catalog when the approved source supplies sufficient access (US2).
- **FR-007a**: Platform-supplied model identifier and region MAY be set by the platform team via deployment environment variables (Docker Compose, EC2 user-data, or equivalent); admins MUST NOT change the model SKU in the UI for platform-supplied deployments.
- **FR-007b**: When a cloud credential source is **approved** and its model is validated and enabled, that model MUST be **selectable in agent sessions** per **FR-016** (internal approval state only—not a deployment env flag).
- **FR-008**: Model validation and enablement rules from feature `006` (validate before enable) MUST apply to models using approved ambient credentials. **Validation and enablement complete in User Story 2.**
- **FR-009**: System MUST provide a manual **Rescan** action for admins to refresh detection after deployment changes. Rescan MUST update results within **2–5 seconds** under normal single-tenant deployment conditions.
- **FR-010**: When rescan detects a change to credential **method**, **region**, **project**, **endpoint**, or **completeness** on a previously approved source, system MUST set status to **pending**, disable ambient use for that source, and require admin re-approval before continued use. Unchanged approved sources remain approved. Snapshot comparison MUST run on every scan apply.
- **FR-011**: Approval, rejection, revocation, and rescan events MUST be written to the platform audit trail with actor identity and non-secret metadata.
- **FR-012**: Operators MUST NOT approve cloud credential sources or mutate cloud credential settings; API calls without `can_manage_models` MUST receive **403**.
- **FR-013**: Failure to detect cloud credentials MUST NOT block stub, offline, local, or BYOK provider configuration.
- **FR-014**: On startup, when platform-supplied model environment variables are present, system MUST register or sync a read-only model record from those variables (no admin catalog selection).
- **FR-015**: Microsoft Foundry and Google Cloud Vertex platform-supplied deployments MUST use the same deploy-time env var pattern as AWS when the platform team supplies fixed model configuration for those clouds.
- **FR-016**: The agent API **`GET /v1/agent/models`** MUST list enabled models including cloud-backed entries when the matching source is **approved**; the UI MUST read this endpoint (implements FR-007b). Must not infer availability from deployment environment flags.

### Key Entities

- **CloudCredentialSource**: A detected ambient configuration for exactly one hosted LLM provider per cloud (Amazon Bedrock, Microsoft Foundry, or Google Cloud Vertex AI); attributes include provider, primary method, alternate methods detected, completeness, non-secret configuration summary (including admin-supplied missing fields), last scan time, and approval status.
- **CloudCredentialApproval**: Admin decision binding on a source—approved, rejected, or revoked—with actor, timestamp, and optional rejection reason.
- **PlatformSuppliedModelConfig**: Read-only model binding from deploy-time environment variables (model ID, region, provider) set by the platform team; synced on startup; not editable in admin UI. **Runtime availability** is governed by approval state, not env flags.
- **LLM model record** (existing): May reference use of platform-approved ambient credentials vs explicit BYOK secrets per provider; validation status governs enablement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a correctly configured Docker deployment for any supported hyperscaler (Amazon Bedrock, Microsoft Foundry, or Google Cloud Vertex AI) with platform-supplied model config, admins can complete detect → approve → validate → enable in under 10 minutes without pasting an API key or choosing a model.
- **SC-002**: 100% of approval, rejection, revocation, and rescan actions produce an audit record attributable to an admin within the retention period.
- **SC-003**: Zero secret values from detected credentials appear in settings UI, API responses, or audit payloads in acceptance testing.
- **SC-004**: When no ambient credentials are present, admins can still onboard models via BYOK in the same session without errors from the detection feature.
- **SC-005**: After host credential configuration changes, admins can rescan and reach a correct pending/approved state without redeploying the application; rescan completes within 2–5 seconds.

## Assumptions

- Primary production deployment for this onboarding path is a single-tenant EC2 instance running the platform via Docker Compose (or equivalent container orchestration on that host).
- **v1 scope includes all three hyperscalers**: Amazon Bedrock (AWS), Microsoft Foundry (Azure), and Google Cloud Vertex AI (GCP).
- Platform-supplied model SKUs are defined by the platform team at deploy time (not admin-selected). Microsoft’s official product name is **Microsoft Foundry** (formerly Azure AI Foundry / Azure AI Studio).
- Ambient credentials are provided via standard cloud patterns (instance/profile roles, environment variables, or mounted credential files)—not by storing new secrets inside the migration product except via existing secure BYOK entry patterns.
- Feature `004-agent-pev-rbac` admin role and `can_manage_models` capability gate approval UI and APIs via `require_manage_models` (same guard pattern as feature `006` LLM settings routes).
- Feature `006-llm-model-catalog` provides catalog, validation, and connectivity proxy behavior; this feature adds ambient credential discovery, approval, and validation.
- Detection runs in the application runtime environment (containers included) so Docker-mounted env and secrets are visible to the scan.
- v1 does not auto-approve any detected source; every ambient path requires explicit admin approval.

## Dependencies

- Feature `006-llm-model-catalog` — model catalog, validation, connectivity settings.
- Feature `004-agent-pev-rbac` — admin-only model and settings management, audit patterns.
- Feature `001-agentic-migration-platform` — agent sessions consume enabled LLM models.
