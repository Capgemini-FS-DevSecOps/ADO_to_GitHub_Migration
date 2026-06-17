# Quickstart: Agent PEV Console, Model Onboarding, and RBAC

**Feature**: `004-agent-pev-rbac`  
**Goal**: Validate SC-001 through SC-006 in Docker prod-like environment.

**References**: [spec.md](./spec.md) (incl. clarifications), [plan.md](./plan.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

## Prerequisites

- Docker Desktop
- Repository cloned; `pip install -e .` for local pytest
- Ports 3000 (UI), 8080 (accelerator), 8090 (agent) available
- Optional: valid OpenAI and Anthropic API keys for SC-004 live provider test

## Environment (prod-like multi-user)

```powershell
cd D:\GitHub\ADO_to_GitHub_Migration
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Verify auth enabled:

- `ADO2GH_AUTH_ENABLED=true`
- `NEXT_PUBLIC_REQUIRE_AUTH=true`

---

## Scenario 1: Bootstrap admin + register operator (P1)

**Proves**: FR-011 foundation, concurrent users setup

1. Open `http://localhost:3000/login` → bootstrap admin (`admin` / 12+ char password).
2. Open **incognito** window → **Create account** → register `operator1` (operator role).
3. Optionally register `approver1` (approver role) in a third browser/profile.
4. All sessions show distinct usernames in UserSessionBar.

**Expected**: Independent cookies; no forced cross-logout.

---

## Scenario 2: Admin onboards OpenAI + Anthropic models (P1)

**Proves**: FR-003, FR-004, SC-004

1. As **admin**, open `/settings/models`.
2. Add OpenAI-compatible model (provider `openai`, valid API key, enabled).
3. Add Anthropic model (provider `anthropic`, valid API key, enabled).
4. Save; confirm API keys never shown raw after save (network tab shows `***`).

**Expected**: Both models listed; agent runtime can invoke each; audit events `llm.model.created`.

---

## Scenario 3: Operator dry-run PEV from Agent tab (P1)

**Proves**: SC-001, FR-001, FR-002, FR-005

1. As **operator**, open `/agent`.
2. Select onboarded OpenAI or Anthropic model (or stub indicator if none).
3. Start session with dry-run enabled.
4. Observe phases: planning → executing → validating → completed.

**Expected**: No placeholder text; no live mutations; completes in < 5 minutes.

---

## Scenario 4: Operator read-only profiles + blocked mutations (P1)

**Proves**: SC-002, FR-007, FR-010

1. As **operator**, navigate to `/settings/profiles`.
2. **View** profile list and details — should succeed (read-only).
3. Attempt create/edit/delete — disabled in UI or `403` from API.

**Expected**: Read allowed; mutations 100% blocked.

4. Navigate to `/settings/models` — blocked or redirect (admin-only).

---

## Scenario 5: Unified live approval queue (P1)

**Proves**: SC-003, FR-008, FR-009

**Agent path**:

1. As **operator**, complete dry-run PEV → **Request live execution**.
2. Confirm `awaiting_approval`; no live migrate yet.
3. As **admin or approver** (other browser), open `/settings/approvals`.
4. Approve with reason → operator session resumes live PEV.

**Dashboard/pipeline path** (when implemented):

1. Operator initiates live migrate from dashboard or pipeline UI.
2. Same queue shows `scope_type: migrate_job` or `pipeline_run`.
3. Admin/approver approves → live execution proceeds.

**Deny path**: Deny with reason → operator sees message; no live execution.

---

## Scenario 6: Secret hygiene (P1)

**Proves**: SC-006, CA-003

Inspect Network tab, agent chat, and audit payloads after model save.

**Expected**: Zero raw API keys/tokens after save.

---

## Scenario 7: Concurrent sessions 30 minutes (P2)

**Proves**: SC-005, FR-011

Parallel admin + operator (+ optional approver) sessions for ≥ 30 minutes; no cross-user bleed.

---

## Scenario 8: Backend unreachable remediation (P2)

**Proves**: FR-013

Stop agent container; open `/agent` — remediation banner appears; clears after restart.

---

## Scenario 9: Dual AND gates (P2)

**Proves**: FR-014

**Prerequisites**: Assignment with phase gate not passed.

1. Operator requests live PEV with `assignment_id`.
2. Admin/approver approves platform queue.
3. Live migrate attempts → `409` if assignment gate fails.

**Expected**: Audit records platform approval + gate failure; no silent live mutation.

---

## Automated tests (CI)

```powershell
pytest tests/test_platform_rbac.py tests/test_live_approval_queue.py `
  tests/test_llm_model_runtime.py tests/test_agent_pev_live_gate.py -q
```

Coverage gate: ≥ 85% on changed `ado2gh` modules per constitution.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| 401 on Agent tab | `credentials: 'include'` on agent.ts |
| Operator cannot view profiles | FR-010 read-only GET should be allowed |
| Approver cannot see queue | `can_approve_live_execution`; `/settings/approvals` |
| Live runs without approval | PEV order; verify `awaiting_approval` before migrate |
| Gate fails after approval | Expected FR-014 AND behavior; check assignment gate |
