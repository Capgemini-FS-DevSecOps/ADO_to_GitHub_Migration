---
spec: specs/003-local-agent-ide/spec.md
role: planner
---

# Planner skill — assignment-aware migration planning

Scope plans to the active migration assignment cohort (POC, pilot, or wave).

Steps: discover → readiness → plan phase → enqueue (live requires Approver).

Apply topological repo order from dependency graph before execution.

Respect `workflow_layout_policy`: modular (per-pipeline YAML) vs consolidated.
