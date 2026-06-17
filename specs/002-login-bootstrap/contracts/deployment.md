# Contract: Docker & Kubernetes Deployment

**Feature**: `002-login-bootstrap`

## Docker Compose (required env)

| Variable | Service | Description |
|----------|---------|-------------|
| `ADO2GH_AUTH_ENABLED` | accelerator | `true` in prod |
| `SESSION_SECRET` | accelerator | Cookie signing secret (32+ bytes); also accepted as env `SESSION_SECRET` |
| `ADO2GH_SESSION_TTL_HOURS` | accelerator | Default `8` |
| `ADO2GH_COOKIE_SECURE` | accelerator | `true` when TLS terminates in front |
| `ADO2GH_MIN_PASSWORD_LENGTH` | accelerator | Default `12` |
| `NEXT_PUBLIC_REQUIRE_AUTH` | web | `true` enables middleware |
| `ADO2GH_STORAGE_BACKEND` | accelerator | `postgres` in prod overlay |
| `ADO2GH_DATABASE_URL` | accelerator | Postgres DSN (users + migration state) |

**Volumes**: Existing `ado2gh-data` or Postgres `pgdata` — user tables live in same DB.

**Health**: `/health` and `/ready` remain unauthenticated for orchestrator probes.

## Kubernetes (manifest paths)

```text
deploy/kubernetes/
├── namespace.yaml
├── configmap.yaml          # ADO2GH_AUTH_ENABLED, TTL, non-secrets
├── secret.yaml.example     # SESSION_SECRET, POSTGRES_PASSWORD
├── postgres.yaml           # Deployment + PVC + Service
├── accelerator-deployment.yaml
├── web-deployment.yaml
└── ingress.yaml.example    # optional TLS host
```

**Probe paths**:
- accelerator: `GET /health` :8080
- web: `GET /login` :3000 (or `/` with auth redirect)

**Init order**: Postgres ready → accelerator ready → web.

## Fresh instance validation

1. Apply manifests with empty Postgres volume.
2. Open Ingress/NodePort URL → `/login` shows bootstrap mode.
3. Create admin → dashboard accessible.
4. Scale accelerator to 2 replicas → session still valid (shared Postgres sessions).

## Security notes

- Replace `secret.yaml.example` with sealed secrets / external secret operator in real clusters.
- Do not commit `SESSION_SECRET` to git.
- Set `ADO2GH_COOKIE_SECURE=true` when Ingress provides HTTPS.
