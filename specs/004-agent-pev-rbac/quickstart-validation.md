# Quickstart validation log (T058)

**Feature**: `004-agent-pev-rbac`  
**Date**: 2026-06-16  
**Method**: API-level pytest (`tests/test_004_quickstart_scenarios.py`) + manual UI checklist

## Automated results (CI-runnable)

| Scenario | Quickstart section | Automated | Status |
|----------|-------------------|-----------|--------|
| 1 | Bootstrap + operator/approver | `TestQuickstartScenario1BootstrapAndUsers` | PASS |
| 2 | OpenAI + Anthropic onboarding | `TestQuickstartScenario2LlmOnboarding` | PASS |
| 3 | Operator dry-run PEV | `TestQuickstartScenario3OperatorDryRunPev` | PASS |
| 4 | Read-only profiles | `TestQuickstartScenario4OperatorReadOnlyProfiles` | PASS |
| 5 | Unified live approval | `TestQuickstartScenario5UnifiedLiveApproval` | PASS |
| 6 | Secret hygiene | `TestQuickstartScenario6SecretHygiene` | PASS |
| 7 | 30-minute concurrent soak | — | MANUAL |
| 8 | Remediation banner | `TestQuickstartScenario8Remediation` | PASS |
| 9 | Dual AND gates | `TestQuickstartScenario9DualAndGates` | PASS |

Run:

```powershell
pytest tests/test_004_quickstart_scenarios.py -v
```

## Manual UI checklist (browser)

Requires prod-like stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

| Step | Verify |
|------|--------|
| Scenario 1 | Incognito register `operator1`; UserSessionBar shows distinct usernames |
| Scenario 3 | `/agent` — PEV phases visible; no placeholder copy |
| Scenario 4 | `/settings/profiles` read-only for operator; `/settings/models` blocked |
| Scenario 5 | Operator request-live → `/settings/approvals` approve → live PEV resumes |
| Scenario 7 | Admin + operator parallel ≥30 min — no session bleed |
| Scenario 8 | `docker compose stop agent` → `/agent` shows remediation; restart clears |

## Notes

- Dashboard/pipeline live enqueue validated via API in `test_operator_live_migrate_blocked`.
- Scenario 7 intentionally manual (soak duration).
