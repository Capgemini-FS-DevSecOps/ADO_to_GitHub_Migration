# Contract: Dependency Logging & Provisioning

**Components**: Accelerator CLI/worker/API, Agent service, `create_state_db`

## Accelerator — missing dependency logging

When readiness or pre-flight checks fail, emit to **console** (Rich) and **run logs** (API/UI):

```text
[WARNING] workflow-readiness: Payments/api-gateway — MISSING secret 'NUGET_FEED_TOKEN' (required by pipeline 'ci-build')
[WARNING] workflow-readiness: Payments/api-gateway — MISSING environment 'production' (required by release pipeline)
[HINT]    Map via: ado2gh service-connections --config migration.yaml
[HINT]    Or agent: "create secret NUGET_FEED_TOKEN for Payments/api-gateway"
```

**Rules**:
- Log secret/connection **names** only—never values, PATs, or token prefixes.
- Each blocker includes: `repo`, `check_id`, `resource_name`, `source` (pipeline/workflow).
- Dry-run logs same blockers but labels `would block live execution`.

## Storage backend selection

| Environment | `ADO2GH_STORAGE_BACKEND` | Connection |
|-------------|--------------------------|------------|
| Local CLI | `sqlite` (default) | `ADO2GH_SQLITE_PATH` or `migration_state.db` |
| Dev Docker | `sqlite` | `/app/data/migration_state.db` volume |
| Cloud / hyperscaler | `postgres` | `ADO2GH_DATABASE_URL=postgresql://...` |

Production compose: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`

**Required tables** (both backends): migrations, assignments, audit_events, agent_sessions, repo_dependency_edges, readiness_check_results.

## Agent — provision missing dependency

### Detect (validator/planner tool)

`ado2gh_workflow_readiness` returns `blockers[]` with `provisionable: true` when GH API can create resource.

### Prompt (agent UI / chat)

```text
Missing repository secret `NUGET_FEED_TOKEN` for Payments/api-gateway.
Create this secret in GitHub now? (yes/no)
```

### Confirm provision

`POST /v1/sessions/{session_id}/provision`

```json
{
  "blocker_id": "secret:NUGET_FEED_TOKEN:Payments/api-gateway",
  "action": "create_repo_secret",
  "confirmed": true,
  "value_source": "secure_modal"
}
```

**`value_source`**: `secure_modal` | `vault_ref` — never `chat_message`.

### Executor tools (whitelist)

| Tool | Creates |
|------|---------|
| `ado2gh_create_repo_secret` | GitHub repo secret |
| `ado2gh_create_org_secret` | Org secret (may require Approver) |
| `ado2gh_create_environment` | GitHub Environment |
| `ado2gh_link_oidc` | OIDC / service connection mapping per manifest |

After success: re-run `ado2gh_workflow_readiness`; append audit event; notify operator in chat.

### Manual accelerator (no auto agent prompt)

CLI equivalent:

```bash
ado2gh service-connections --config migration.yaml
# logs list missing mappings; operator provisions manually
```

Optional future: `ado2gh provision-secret --repo ... --name ...` (explicit operator command).

## API — readiness with log bundle

`GET /v1/profiles/{profile_id}/workflow-readiness?repos=...`

Response includes `log_lines[]` mirroring console output for UI run panel.
