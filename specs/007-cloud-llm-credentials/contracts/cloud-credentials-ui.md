# Contract: Cloud Credentials Settings UI



**Feature**: `007-cloud-llm-credentials`  

**App**: `apps/migration-ui`



## Routes



| Page | Path | Capability |

|------|------|------------|

| Cloud credentials | `/settings/cloud-credentials` | `can_manage_models` |



Add tab in `permissions.ts` next to Connectivity and Models.



---



## Cloud credentials page



### Layout



1. **Header** — “Cloud LLM credentials” + short explanation (detect ambient AWS / Microsoft Foundry / GCP config; approve before use).

2. **Platform-supplied model** (when env-synced model exists) — read-only card: provider, model ID, region; **Validate / Enable** CTA (US2).

3. **Source cards** — one per provider (`aws`, `foundry`, `gcp`); show “Not detected” when `completeness: absent`.

4. **Incomplete configuration editor** (FR-002a / U1):

   - When `completeness: incomplete`, show `missing_fields` and editable inputs for **non-secret** fields (region, endpoint, project, tenant ID).

   - **Save** calls `PATCH /v1/settings/cloud-credentials/{provider}`.

   - **Approve** disabled until complete; attempting approve shows error banner with missing fields.

5. **Actions per card**:

   - **Approve** — enabled when `pending` + `complete`; shows probe spinner; error banner on probe fail.

   - **Reject** — optional reason modal.

   - **Revoke** — when `approved`.

6. **Rescan** — global button; disables cards while scanning; expect update within 2–5 seconds.



### Status badges



| Status | Label |

|--------|-------|

| `pending` | Pending approval |

| `approved` | Approved |

| `rejected` | Rejected |

| `revoked` | Revoked |

| `incomplete` | Incomplete configuration |



Show `missing_fields` list for incomplete.



### Empty state



No sources detected → message + BYOK hint + link to Models settings.



---



## Models page changes (US2)



When platform-supplied model env vars are synced:

- Banner: “Model provided by platform team (read-only).”

- Disable provider/model SKU edits for platform-supplied record.

- **Validate** and **Enable** buttons available after cloud source approved.



---



## Agent tab changes (US3)



- Model picker reads **`GET /v1/agent/models`** per [agent-models-api.md](./agent-models-api.md) (U2).

- Approved + enabled cloud-backed models appear as normal selectable options.

- When ambient model unavailable (source pending/revoked), show degraded banner with link to Cloud credentials settings.

- **Do not** hide picker based on deployment environment flags.



---



## API helpers (`lib/cloudCredentials.ts`)



- `fetchCloudCredentials(scan?: boolean)`

- `scanCloudCredentials()`

- `updateCloudCredential(provider, fields)`

- `approveCloudCredential(provider)`

- `rejectCloudCredential(provider, reason?)`

- `revokeCloudCredential(provider)`

- `fetchPlatformModel()`



All use `credentials: 'include'`.



---



## Permissions tests



Extend `permissions.test.ts`:

- Admin sees `/settings/cloud-credentials` tab.

- Operator does not.



Contract tests (G3): operator receives 403 on all cloud-credentials API routes.


