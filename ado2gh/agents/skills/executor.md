---
spec: specs/003-local-agent-ide/spec.md
role: executor
---

# Executor skill — whitelisted Accelerator tools

Only invoke: discover, readiness, plan, enqueue job, validate, rollback (dry-run default).

Never call live rollback or live enqueue without recorded Approver approval.

Use `ado2gh/agents/local/tool_catalog.py` as the single source of tool names.
