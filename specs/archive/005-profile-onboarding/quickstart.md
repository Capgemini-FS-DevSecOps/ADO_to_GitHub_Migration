# Quickstart: Deployment Profile Onboarding and Governance

**Feature**: `005-profile-onboarding`  
**Goal**: Validate SC-001–SC-006 from [spec.md](./spec.md).

**References**: [profile-onboarding-api.md](./contracts/profile-onboarding-api.md), [profile-approval-api.md](./contracts/profile-approval-api.md), [profile-setup-ui.md](./contracts/profile-setup-ui.md), [data-model.md](./data-model.md)

## Prerequisites

- Docker Desktop running
- Valid ADO PAT and GitHub PAT (for live validation scenarios)
- Repo root: `D:\GitHub\ADO_to_GitHub_Migration`

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

Wait for `accelerator` healthy and web on `http://localhost:3000`.

## Scenario 1: Admin bootstrap → mandatory profile (P1)

**Proves**: User Story 1, FR-001, SC-001, SC-002

1. Open `http://localhost:3000/login`
2. Create bootstrap admin account
3. **Expected**: Redirect to `/onboarding/profile` (not dashboard)
4. Try navigating to `/` manually → redirected back to onboarding
5. Submit invalid ADO PAT → error, no profile saved
6. Submit valid ADO + invalid GitHub → error, no profile saved
7. Submit valid ADO + valid GitHub → success → dashboard loads
8. `GET http://localhost:8080/v1/onboarding/status` (with session cookie) → `needs_profile_setup: false`, `active_profile_count: 1`

## Scenario 2: Cannot delete last active profile (P1)

**Proves**: User Story 2, FR-004, SC-003

1. As admin with exactly one active profile, attempt delete via UI or:

```powershell
curl.exe -s -X DELETE http://localhost:8080/v1/settings/profiles/{PROFILE_ID} `
  -H "Cookie: ado2gh_session=..." 
```

2. **Expected**: `409` with last-active-profile error
3. Create second profile (admin) → delete non-default → succeeds
4. Delete default profile → another active profile becomes default

## Scenario 3: Operator pending profile (P1)

**Proves**: User Story 3, FR-007–FR-009, SC-004, SC-006

**Setup**: Admin creates operator user (or use existing admin UI when implemented).

1. Sign in as operator in a **second browser** (concurrent session)
2. Go to `/settings/profiles/new`, complete wizard with valid credentials
3. **Expected**: Message "Submitted for approval"; profile not in migration profile picker
4. Attempt migration or agent action with pending profile id → `403 profile_not_active`
5. As admin in first browser, open `/settings/profiles/pending`
6. Approve with reason
7. **Expected**: Operator sees profile active without container restart; selectable for migrations

## Scenario 4: Credential masking (P2)

**Proves**: User Story 4, CA-003, SC-005

1. After profile save, open browser devtools → Network
2. Reload settings API responses
3. **Expected**: `ado_pat` and token fields show `***` only
4. Check accelerator logs during approval flow
5. **Expected**: No `ghp_` or ADO PAT substrings in log lines

## Scenario 5: Zero profiles recovery (P2)

**Proves**: User Story 5

1. With two profiles, delete one then attempt delete second → blocked
2. (Dev-only) Manually remove profiles from data dir or use API after adding second profile, leaving zero active
3. Admin login → redirected to `/onboarding/profile`
4. Operator login → blocked message, no migration actions

## Scenario 6: Concurrent sessions (P2)

**Proves**: FR-011 alignment with multi-session Docker

1. Browser A: admin session active on dashboard
2. Browser B: operator session active on settings
3. Admin approves operator pending profile in Browser A
4. **Expected**: Both sessions remain valid; operator sees update on refresh

## Scenario 7: Create account on login (P2)

**Proves**: User Story 6, FR-018–FR-020, SC-009

1. Bootstrap admin and complete first profile (Scenario 1).
2. Sign out. Open `/login` in a **second browser**.
3. **Expected**: **Create account** link visible (not bootstrap mode).
4. Register `operator1` with valid password.
5. **Expected**: Signed in as operator; session cookie set; role operator in `GET /v1/auth/session`.
6. Attempt register as admin via API with `role: admin` in body → rejected.

## Automated tests (post-implementation)

```powershell
pip install -e .
pytest tests/test_profile_governance.py tests/test_profile_onboarding_api.py tests/test_profile_approval_flow.py -q
pytest --cov=ado2gh/api --cov-report=term-missing --cov-fail-under=85
```

**Expected**: All pass; coverage ≥ 85% on changed `ado2gh/api` modules.
