# Contract: Local Agent Profiles

**Config file**: `config/local-profiles.yaml` (to be implemented)  
**Env overrides**: Prefix `ADO2GH_`, `ACCELERATOR_URL`, `LLM_PROVIDER`

## Profile definitions

### `lightweight` (default for IDE dev)

| Setting | Value |
|---------|-------|
| `accelerator_url` | `http://localhost:8080` |
| `agent_url` | `http://localhost:8090` |
| `storage_backend` | `sqlite` |
| `sqlite_path` | `./migration_state.db` |
| `lightweight_mode` | `true` |
| `redis_url` | _(empty)_ |
| `auth_enabled` | `false` |
| `llm_provider` | `stub` |
| `dry_run_default` | `true` |
| **Services started** | accelerator, agent |
| **Services omitted** | redis, worker, web (optional) |

### `full`

| Setting | Value |
|---------|-------|
| `storage_backend` | `sqlite` |
| `lightweight_mode` | `false` |
| `redis_url` | `redis://localhost:6379/0` |
| `auth_enabled` | `false` (dev Compose default) |
| `llm_provider` | `stub` |
| `dry_run_default` | `true` |
| **Services started** | redis, accelerator, worker, agent, web |
| **Start command** | `docker compose up` or `scripts/run-local.ps1` |

### `prod-like`

| Setting | Value |
|---------|-------|
| `storage_backend` | `postgres` |
| `auth_enabled` | `true` |
| `llm_provider` | env-specific |
| `dry_run_default` | `true` (still default; live requires approval) |
| **Services started** | postgres, redis, accelerator, worker, agent, web |
| **Start command** | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` or K8s manifests from `002-login-bootstrap` |

## Environment variables

| Variable | Profiles | Description |
|----------|----------|-------------|
| `ADO2GH_STORAGE_BACKEND` | all | `sqlite` or `postgres` |
| `ADO2GH_SQLITE_PATH` | lightweight, full | Path to StateDB file |
| `ADO2GH_LIGHTWEIGHT_MODE` | lightweight | `true` enables inline job stub |
| `ADO2GH_AUTH_ENABLED` | all | Platform login gate |
| `ADO2GH_SESSION_COOKIE` | prod-like | Forward auth to accelerator |
| `ACCELERATOR_URL` | all | Agent → accelerator base URL |
| `LLM_PROVIDER` | all | `stub` default |
| `ADO_ORG_URL`, `ADO_PAT`, `GH_TOKEN` | optional | Required only for real ADO/GH calls |

## Profile selection

**API**: `profile_id` on `POST /v1/sessions`  
**CLI script**: `--profile lightweight|full|prod-like` on `run-local-agent.ps1`  
**Compose**: file choice (`docker-compose.lightweight.yml` vs default)

## Switching profiles

Same `assignment_id` and skills work across profiles; only backend topology and auth differ. Developers MUST restart stack when switching from lightweight → full to enable async worker.

## Validation checklist

- [ ] `lightweight` starts without Redis
- [ ] `full` processes jobs via worker
- [ ] `prod-like` rejects unauthenticated accelerator calls when auth on
- [ ] All profiles write `audit_events` on tool/session mutations
