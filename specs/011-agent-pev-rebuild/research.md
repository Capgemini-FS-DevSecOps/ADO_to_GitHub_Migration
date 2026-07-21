# Research: Agent PEV Architecture Rebuild

**Feature**: 011-agent-pev-rebuild
**Date**: 2026-06-24

## Research Tasks

### R1: Continuous Agent Loop Architecture

**Decision**: Event-driven async loop using asyncio. The orchestrator runs a continuous reasoning loop (think → act → observe → think) that cycles through Planner → Executor → Validator until a terminal state is reached. The loop is driven by `pev_cycle.py` which manages cycle state, iteration counters, and context window updates.

**Rationale**: The existing `process_user_message` in `session_orchestrator.py` already uses an iteration-based loop with `max_iterations=8`. The new design extends this to a formal PEV cycle with configurable limits (20 total, 3 PEV retries). asyncio is already used throughout `services/agent/main.py` for concurrent session handling.

**Alternatives considered**:
- Poll-based loop (rejected — adds latency and complexity without benefit)
- State machine-driven loop without explicit PEV cycling (rejected — loses the structured feedback loop between Planner/Executor/Validator)
- Multi-process architecture (rejected — asyncio is sufficient for 10 concurrent sessions and LLM I/O is the bottleneck, not CPU)

### R2: Persistent Session Storage

**Decision**: Extend the existing `ado2gh/state/` infrastructure with new tables for agent sessions, messages, plans, PEV cycles, repo locks, and rollback records. Use the same `StorageConfig.from_env()` pattern for SQLite/PostgreSQL/DynamoDB selection.

**Rationale**: The existing `StateDB` class in `ado2gh/state/db.py` provides a proven SQLite schema pattern. The `create_state_db()` factory in `ado2gh/state/factory.py` already handles backend selection via `ADO2GH_STORAGE_BACKEND`. New tables follow the same pattern: `CREATE TABLE IF NOT EXISTS` with JSON columns for complex data.

**Alternatives considered**:
- Redis-based session store (rejected — adds a new dependency; SQLite/PG is sufficient and already supported)
- File-based persistence (rejected — no query support, poor concurrency)
- Separate session database (rejected — increases operational complexity; same DB is simpler)

### R3: Formal State Machine Implementation

**Decision**: Implement `session_state_machine.py` with an explicit transition table. States: idle, thinking, planning, executing, validating, awaiting_input, awaiting_approval, completed, failed. The `transition()` method validates the current state → target state pair against an allowed set and raises on invalid transitions.

**Rationale**: The existing codebase uses string-based status fields without enforcement (e.g., `session["status"] = "awaiting_approval"` in `main.py:1199`). This allows invalid states. A formal state machine prevents bugs in resume-after-restart logic and ensures UI indicators are accurate.

**Alternatives considered**:
- Enum-based states without transition validation (rejected — doesn't prevent invalid transitions)
- Third-party state machine library (rejected — adds dependency; a simple transition table is sufficient)
- Database-level constraints (rejected — complex to implement across SQLite/PG/DynamoDB)

### R4: LLM Call Timeout and Retry

**Decision**: Add a `complete_with_timeout()` method to `LLMProvider` that wraps the existing `complete()` call with `asyncio.wait_for()` (60s timeout). On first timeout, retry once with the same prompt. On second timeout, raise `LLMTimeoutError` which the PEV cycle catches and reports to the orchestrator.

**Rationale**: The existing `OpenAIProvider.complete()` already uses `timeout=60.0` for HTTP requests (line 123 of `llm_provider.py`). However, this is an HTTP-level timeout, not an application-level retry. The new wrapper adds retry logic and graceful degradation.

**Alternatives considered**:
- Use httpx retry transport (rejected — doesn't handle semantic timeouts like slow reasoning)
- No timeout (rejected — async loop can hang indefinitely on slow LLM)
- Exponential backoff retry (rejected — spec specifies exactly 1 retry, not exponential)

### R5: Repo-Level Lock Persistence

**Decision**: Extend the existing `ado2gh/api/repo_lock.py` with a persistent lock store backed by the session database. The current `RepoLockManager` is in-memory (`self._locks: dict[str, RepoLock]`). New `RepoLockStore` class in `repo_lock_store.py` uses the same DB connection for persistence. Stale locks from crashed sessions are cleaned up on server startup.

**Rationale**: The existing `RepoLockManager` is explicitly designed for future DB backing (docstring: "The interface is intentionally small so it can later be backed by SQLite for multi-process deployments without changing call sites"). The `RepoLock` dataclass already has the right fields. We just need to add a DB-backed implementation.

**Alternatives considered**:
- Distributed lock service (Redis/ZooKeeper) (rejected — overkill for 10 concurrent sessions)
- File-based locks (rejected — poor concurrency, no query support)
- Keep in-memory only (rejected — locks must survive server restarts per FR-072)

### R6: ADO Boards → GitHub Issues CSV Import

**Decision**: Export ADO work items via ADO REST API (`_apis/wit/workitems`), transform to GitHub Issues CSV format (title, body, labels, assignees), and import via GitHub's Issues Import API (`/repos/{owner}/{repo}/import/issues`). Fixed field mapping defined in `resource_mapping.py`.

**Rationale**: GitHub's Issues Import API is designed for bulk imports and handles rate limiting gracefully. CSV is a simple, auditable format. Fixed mapping is deterministic and reproducible. The ADO REST API for work items is well-documented and returns JSON that can be directly transformed.

**Alternatives considered**:
- Direct API creation (one issue at a time) (rejected — slow for large work item counts, hits rate limits)
- LLM-assisted mapping (rejected — introduces non-determinism in field mapping)
- GitHub Projects import (rejected — GitHub Projects is a different product; issues are the right target)

### R7: ADO Test Plans → GitHub Issues with Labels

**Decision**: Map ADO test suites to GitHub milestones, test cases to GitHub Issues with `test-case` and `test-suite` labels. Use the same CSV import pipeline as Boards. Test steps are included in the issue body as a structured checklist.

**Rationale**: GitHub has no native test plan product. Issues with labels + milestones provide queryable, filterable test case tracking. Milestones group test suites logically. The planner may override this mapping for edge cases with documented justification.

**Alternatives considered**:
- Map to GitHub Projects (rejected — less queryable than Issues with labels)
- Skip Test Plans in v1 (rejected — spec FR-065 requires all resource types)
- Create a custom test tracking system (rejected — out of scope, adds complexity)

### R8: ADO Artifacts → GitHub Packages

**Decision**: Use GitHub Packages API to publish supported package types (npm, NuGet, Docker, Maven, PyPI). The executor reads ADO Artifacts feed data via ADO REST API (`_apis/packaging/feeds`), downloads packages, and re-publishes to GitHub Packages. Unsupported types (e.g., universal packages) are documented as gaps.

**Rationale**: GitHub Packages supports the same package formats as ADO Artifacts for the most common types. The publish API is well-documented. Unsupported types are surfaced to the user rather than silently skipped, aligning with the constitution's fail-safe defaults.

**Alternatives considered**:
- Migrate all artifact types including unsupported (rejected — GitHub Packages doesn't support universal packages)
- Skip Artifacts in v1 (rejected — spec FR-065 requires all resource types)
- Use a third-party artifact migration tool (rejected — adds external dependency)

### R9: ADO Wiki → GitHub Wiki

**Decision**: Enable GitHub Wiki per-repo via the GitHub API (`PUT /repos/{owner}/{repo}/pages` or wiki enablement endpoint), clone the ADO Wiki git repo, transform wiki pages to markdown, and push to the GitHub Wiki git remote.

**Rationale**: GitHub Wiki is a git-backed wiki per repository. ADO Wiki is also git-backed. The transformation is primarily format conversion (ADO Wiki supports markdown + HTML, GitHub Wiki supports markdown only). HTML pages are converted to markdown or documented as gaps.

**Alternatives considered**:
- Migrate to GitHub Pages (rejected — different product, more complex setup)
- Migrate to a docs folder in the repo (rejected — loses wiki semantics)
- Skip Wiki in v1 (rejected — spec FR-065 requires all resource types)

### R10: Hybrid API Routing

**Decision**: The executor routes existing operations (discovery, GEI git mirror, pipeline conversion, readiness checks) through the accelerator service API via `httpx` calls. New resource types (service connections, Boards, Test Plans, Artifacts, Wiki) use direct ADO/GitHub REST API calls using the profile's configured PATs.

**Rationale**: The accelerator already has proven, tested migration code for repos, pipelines, and secrets. Rewriting these would be wasteful and risky. New resource types don't exist in the accelerator, so direct API calls are necessary. The profile PATs are already available via the existing profile system (`ado2gh/agents/local/profiles.py`).

**Alternatives considered**:
- Route everything through the accelerator (rejected — requires extending the accelerator with Boards/Test Plans/Artifacts/Wiki, which is a larger scope change)
- Route everything via direct API (rejected — discards proven, tested migration code)
- Create a new microservice for new resource types (rejected — adds operational complexity)

### R11: Context Window Management

**Decision**: Implement `context_window.py` with a sliding window strategy. The LLM receives the last 2 PEV cycles in full detail plus a running JSON summary of prior cycles. Full message history is persisted in the session store for audit and never lost. The `PevCycleSummary` JSON structure is generated after each cycle by `pev_cycle.py`.

**Rationale**: The existing `process_user_message` already has a token budget system (`max_token_budget=32000`, `accumulated_tokens`). The new design formalizes this into a structured sliding window that preserves audit history while keeping LLM context within model limits.

**Alternatives considered**:
- Truncate messages (rejected — loses information needed for planning)
- Summarize all prior cycles (rejected — loses detail needed for recent retry decisions)
- No context management (rejected — will exceed model context window on 50+ repo batch migrations)

### R12: Rollback Tracking

**Decision**: Implement `rollback_tracker.py` that records every GitHub resource created during a session (repos, workflows, secrets, environments, issues, wiki pages, packages) with correlation IDs. When the user opts into rollback on cancellation, the executor deletes only resources tracked by the current session. The guardrail layer ensures only session-created resources are eligible.

**Rationale**: The constitution requires "scope-targeted rollback" and "human-in-the-loop" for destructive actions. Tracking resources at creation time enables precise rollback without affecting pre-existing resources. The rollback is a user option, not automatic, satisfying the HITL requirement.

**Alternatives considered**:
- Git-based rollback (revert commits) (rejected — doesn't handle created repos, secrets, issues)
- No rollback (rejected — spec FR-082/FR-083 requires rollback option)
- Automatic rollback on any failure (rejected — spec says rollback is a user choice)

### R13: Prometheus Metrics Endpoint

**Decision**: Implement `metrics.py` with a simple in-memory counter/histogram collector. Expose via FastAPI endpoint at `/metrics` with Prometheus-compatible text format. Track: active_sessions, pev_cycles_total, llm_call_duration_seconds (histogram), guardrail_blocks_total, tool_calls_total, session_status_counts.

**Rationale**: Prometheus text format is simple to generate without external libraries. In-memory collection is sufficient for a single-instance deployment. For multi-instance, Prometheus scraping handles aggregation.

**Alternatives considered**:
- Use prometheus_client library (rejected — adds dependency; simple text format is sufficient)
- Use OpenTelemetry (rejected — overkill for current scope)
- External metrics service (rejected — adds operational complexity)

### R14: Spec 010 → 011 Execution Order

**Decision**: Spec 010 (Enterprise Audit Simplification) executes first, decomposing `session_orchestrator.py` into modules under 800 lines. Spec 011 then rewrites those decomposed modules for the continuous loop architecture. New modules produced by 011 also satisfy the 800-line limit.

**Rationale**: The user explicitly confirmed this order. Decomposing first means 011's rewrite works on already-modular code, producing new modules that are sized correctly from the start. This avoids wasted effort of decomposing code that's about to be rewritten.

**Alternatives considered**:
- 011 first, then 010 (rejected by user)
- Merge into one spec (rejected by user)
