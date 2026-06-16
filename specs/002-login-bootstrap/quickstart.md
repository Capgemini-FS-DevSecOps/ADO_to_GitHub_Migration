# Quickstart: Login & Admin Bootstrap

**Feature**: `002-login-bootstrap`

## Prerequisites

- Docker with Compose v2
- Optional: kubectl + cluster for K8s path
- Python 3.9+ for local pytest

## 1. Docker Compose — fresh instance bootstrap

```bash
# Generate a session secret
export ADO2GH_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export ADO2GH_AUTH_ENABLED=true
export NEXT_PUBLIC_REQUIRE_AUTH=true

# Prod-like stack (Postgres)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

1. Open http://localhost:3000 — redirects to `/login`.
2. Confirm bootstrap UI ("Create admin account").
3. Create user `admin` with password ≥ 12 chars.
4. Verify dashboard loads and `GET http://localhost:8080/v1/auth/session` returns `role: admin`.

**Reset bootstrap** (dev only): wipe Postgres volume or delete `platform_users` rows.

## 2. Docker Compose — standard login

With users present:

1. Open `/login` — standard sign-in form (no bootstrap text).
2. Wrong password → generic error, no session cookie.
3. Correct password → redirect to dashboard.
4. Logout from shell → returns to `/login`.

## 3. Auth disabled (legacy local dev)

```bash
export ADO2GH_AUTH_ENABLED=false
export NEXT_PUBLIC_REQUIRE_AUTH=false
docker compose up web accelerator
```

API open without session; useful for CLI-only workflows.

## 4. Kubernetes sample deploy

```bash
cp deploy/kubernetes/secret.yaml.example deploy/kubernetes/secret.yaml
# Edit SESSION_SECRET and POSTGRES_PASSWORD

kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl apply -f deploy/kubernetes/secret.yaml
kubectl apply -f deploy/kubernetes/configmap.yaml
kubectl apply -f deploy/kubernetes/postgres.yaml
kubectl apply -f deploy/kubernetes/accelerator-deployment.yaml
kubectl apply -f deploy/kubernetes/web-deployment.yaml
# Optional: kubectl apply -f deploy/kubernetes/ingress.yaml.example
```

Port-forward web: `kubectl port-forward -n ado2gh svc/web 3000:3000`

Run bootstrap flow as in section 1.

## 5. Automated tests

```bash
pip install -e ".[dev,api]"
pytest tests/test_auth_bootstrap.py tests/test_auth_login.py tests/contract/test_auth_contract.py -q
```

Expected: bootstrap assigns `admin`; second bootstrap returns `403`; login/logout session lifecycle passes.

## 6. API smoke (curl)

```bash
# Bootstrap status
curl -s http://localhost:8080/v1/auth/bootstrap-status

# Bootstrap (fresh DB only)
curl -s -c cookies.txt -X POST http://localhost:8080/v1/auth/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"SecurePass123!","display_name":"Admin"}'

# Session
curl -s -b cookies.txt http://localhost:8080/v1/auth/session
```

## Contracts

- API: [contracts/auth-api.md](./contracts/auth-api.md)
- UI: [contracts/login-ui.md](./contracts/login-ui.md)
- Deploy: [contracts/deployment.md](./contracts/deployment.md)
- Data: [data-model.md](./data-model.md)
