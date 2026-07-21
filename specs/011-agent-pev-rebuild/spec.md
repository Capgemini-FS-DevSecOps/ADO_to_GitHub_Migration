# Feature Specification: Agent PEV Architecture Rebuild

**Feature Branch**: `011-agent-pev-rebuild`

**Created**: 2026-06-24

**Status**: Clarified

**Input**: User description: "The entire agent tab with LLM and PEV architecture is broken. It does not behave as was originally intended. A simplified Claude Code/Cascade/Cursor-like interface with a chat function that will use natural language processing and LLMs to interpret user inputs and migrate repositories from Azure DevOps to GitHub Actions. There is an orchestrator agent that communicates with a Planner agent (same LLM) which then communicates with an Executor Agent (same LLM) and finally a Validator agent (again same LLM). Each of these agents can be chained and looped through multiple times. Think of Claude Code or Cursor, it does not stop after 1 cycle, it continues until it either needs information from the user or the job is finished. Orchestrator handles general queries while PEV handles migrations. Completely hands-off migration: user says 'migrate this repo' → migrates everything including dependencies, asks user for information when needed, transforms Azure DevOps pipelines/bicep templates to GitHub workflows → works completely out of the box. Shared tools with read-only ADO access and read/write GitHub access with guardrails to prevent hallucination-based deletions."

## Clarifications

### Session 2026-06-24

- Q: Should the orchestrator handle both general queries and migration queries, or route migration to PEV? → A: **Orchestrator handles general queries directly** and routes migration execution to the PEV chain (Planner → Executor → Validator). The orchestrator interprets user intent, asks for missing information via dynamic forms, and decides when to invoke the PEV chain.
- Q: Should agents use the same LLM instance or separate instances with the same model? → A: **Same LLM provider instance** — agents are differentiated by system prompts and tool access, not by model. This ensures consistent reasoning quality and simplifies configuration.
- Q: How should the continuous loop work — event-driven or poll-based? → A: **Event-driven agent loop** — the orchestrator runs a continuous reasoning loop (think → act → observe → think) until it reaches a terminal state (job complete, needs user input, or unrecoverable error). The UI polls session status; the backend loop runs asynchronously.
- Q: Should the agent use the accelerator API or operate independently? → A: **Can use accelerator but not required to** — the agent has direct tool access to ADO and GitHub APIs. It may call the accelerator for complex operations (GEI-based migration, pipeline conversion) but can also operate independently for simpler tasks.
- Q: What happens when the LLM hallucinates a destructive action? → A: **Enterprise guardrails block destructive operations** — all GitHub write operations go through a validation layer that checks against allowlisted operations, confirms target resources exist before modification, and requires explicit user confirmation for any deletion or irreversible action.
- Q: Should Bicep template transformation be included? → A: **Yes** — the executor transforms Azure DevOps Bicep templates and ARM templates to GitHub Actions workflows where applicable, in addition to ADO pipeline conversion.

### Session 2026-06-24 (Clarify Workflow)

- Q: Should agent sessions survive a backend server restart? → A: **Persistent (SQLite/PostgreSQL)** — sessions stored in the existing storage backend, survive restarts, and can resume mid-migration. This aligns with Constitution Principle V (checkpoint and resume for long runs).
- Q: How many concurrent agent sessions should a single backend instance support? → A: **Up to 10 concurrent sessions** — supports a small migration team with multiple operators. The async loop with asyncio handles this without thread pool exhaustion.
- Q: How should agents communicate with each other internally? → A: **Structured JSON** — typed fields for instructions, parameters, results, and feedback. This enables deterministic extraction by the executor and field-by-field comparison by the validator. Also makes inter-agent messages auditable without parsing unstructured text.
- Q: Should batch phase migration (50+ repos in one command) be in scope for v1? → A: **Yes, v1 includes batch phase migration** — user can say "migrate all repos in wave 1" and the agent handles 50+ repos in one command. This introduces queuing and sequential processing requirements.
- Q: How should the agent manage LLM context window limits during long migrations? → A: **Sliding window with structured summaries** — after each PEV cycle, a compact JSON summary is generated. The LLM receives the last 2 PEV cycles in detail plus the running summary. Full message history remains in the session store for audit purposes.

### Session 2026-06-24 (Clarify Workflow — Round 2)

- Q: Should v1 include migration of ADO Boards, Test Plans, Artifacts, and Wiki, or only repos/pipelines/secrets? → A: **v1 includes all ADO resources** — repos, pipelines, secrets, service connections, Boards (work items→GitHub Issues), Test Plans, Artifacts (feeds/packages), and Wiki. The executor handles all resource types.
- Q: Should the session status field use a formal state machine with explicit transitions, or be informational only? → A: **Formal state machine** — explicit transition rules enforced by the backend. Enables correct resume-after-restart logic, prevents invalid states, and ensures accurate UI indicators.
- Q: Should there be an LLM response time timeout, and what happens on timeout? → A: **60s per-call timeout with 1 retry** — on second timeout, the agent reports a degraded state to the orchestrator, which informs the user and pauses the session. Aligns with constitution's fail-safe defaults.
- Q: What happens when two concurrent sessions try to migrate the same repo? → A: **Lock-based conflict detection** — repo-level locks in the session DB prevent concurrent migration of the same repo. The second operator is informed and can wait or choose a different repo.
- Q: Should the agent backend expose Prometheus-style metrics and health checks? → A: **Yes** — expose a `/metrics` endpoint (active sessions, PEV cycles, LLM latency histogram, guardrail blocks, tool call counts) and agent-specific `/health` check (LLM availability, session count, DB connectivity).

### Session 2026-06-24 (Clarify Workflow — Round 3)

- Q: Should the agent call ADO/GitHub APIs directly or route through the accelerator? → A: **Hybrid** — existing operations (discovery, GEI, pipeline conversion) route through the accelerator; new resource types (service connections, Boards, Test Plans, Artifacts, Wiki) use direct ADO/GitHub API calls with profile PATs.
- Q: What ID generation scheme should be used for entities? → A: **UUID v4 for all entities** — consistent with current codebase, globally unique, no central generator needed. Traceability via correlation fields in audit log.
- Q: What data retention policy for sessions and audit logs? → A: **90-day retention for session data** (messages, plans, cycle state), **indefinite retention for audit logs** (guardrail decisions, tool calls) for compliance.
- Q: What should the UI show for LLM unconfigured, empty discovery, and error states? → A: **Distinct UI states with actionable guidance** — LLM unconfigured shows setup prompt with link to settings; empty discovery shows scan prompt with button; errors show actionable summary with retry/contact options.
- Q: What happens when discovery data is empty and user requests migration? → A: **Auto-trigger discovery scan, then continue** — orchestrator detects empty discovery, runs scan automatically, proceeds to planning. User sees "Running discovery scan..." indicator. No manual tab switching required.

### Session 2026-06-24 (Clarify Workflow — Round 4)

- Q: Spec 010 vs 011 execution order for session_orchestrator.py? → A: **010 first, then 011** — 010 decomposes session_orchestrator.py into modules under 800 lines. 011 rewrites the decomposed modules for the continuous loop architecture.
- Q: How should ADO Boards (work items) be migrated to GitHub Issues? → A: **GitHub Issues Import (CSV) with fixed field mapping** — export ADO work items to CSV, map fields deterministically (Title→title, Description→body, Tags→labels, Area Path→label, State→open/closed), import via GitHub Issues Import API. Bulk-efficient and reproducible.
- Q: What exact GitHub target services for Test Plans, Artifacts, and Wiki? → A: **Define exact targets with planner override** — Test Plans → GitHub Issues with `test-case`/`test-suite` labels + milestones; Artifacts → GitHub Packages (supported types only, unsupported documented as gaps); Wiki → GitHub Wiki per-repo (markdown push). Planner may override per-migration with documented justification for edge cases.
- Q: What happens to in-progress work when user cancels? → A: **Rollback is a user option** — if user requests rollback, delete all GitHub resources created during the session. If user cancels without requesting rollback, complete the current operation and then stop. Orchestrator presents both options on cancellation.
- Q: What is the rollback scope? → A: **Delete all resources created during the session** — if the user opts into rollback, all GitHub resources created during this session's migration (repos, workflows, secrets, environments, issues, wiki pages, packages) are deleted. Rollback is not automatic — it is presented as an option when the user cancels.

### Session 2026-06-24 (Clarify Workflow — Round 5, post-analyze)

- Q: What happens when the executor encounters a repo that was already migrated in a previous session or PEV cycle (idempotency)? → A: **Detect-and-prompt** — the executor detects already-migrated resources via API checks, asks the orchestrator to prompt the user with three options: overwrite (re-execute all operations, replacing existing GitHub resources), skip (skip the already-migrated resource, validator confirms existing state is valid), or abort (stop the migration for this repo). This satisfies Constitution Principle V (idempotency for batch migration patterns).
- Q: How should ADO service connections be migrated to GitHub? → A: **Map to GitHub secrets + environments** — simple service connections (credential-based, e.g., Docker registry, npm feed) map to GitHub repo/org secrets. Deployment-scoped service connections (Azure RM, Kubernetes, environment-specific endpoints) map to GitHub environments with protection rules. The planner determines the mapping based on connection type and includes it in the migration plan.
- Q: What does "workflow dry-run trigger validation" (FR-035) mean concretely — GitHub Actions has no native dry-run mode? → A: **YAML validation + local workflow validation framework (e.g., `act`)** — the validator checks YAML syntax and required fields locally, then uses a local workflow simulation tool (such as `act`) to validate the workflow without dispatching it to GitHub. The validator MUST NOT run or dispatch the actual workflow on GitHub. If `act` is not available, the validator falls back to YAML syntax validation only and notes the reduced validation depth.
- Q: Which Bicep/ARM template constructs are automatically transformable and which are gaps? → A: **Hybrid — supported construct list + LLM best-effort with web search** — define a supported construct list (deploy, parameters, variables, resource for common Azure resource types, output, environment references). For constructs outside the supported list, the executor LLM attempts best-effort transformation and may use web search tools to look up documentation for unfamiliar Bicep constructs. Constructs that cannot be transformed are reported as gaps with the specific construct name and suggested manual conversion steps.
- Q: How is "95% accuracy for unambiguous inputs" (SC-003) defined and tested? → A: **Define labeled test corpus** — create a test set of 50+ inputs labeled as unambiguous (contains explicit migration verbs: migrate, move, convert, transfer + repo identifiers) or ambiguous (greetings, vague requests, multi-intent). 95% accuracy is measured against the unambiguous subset only. The test corpus is stored in `tests/unit/test_011_orchestrator.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hands-Off Single Repo Migration (Priority: P1)

A user opens the Agent tab, types "migrate Project/RepoName to GitHub", and the agent handles the entire migration autonomously. The orchestrator interprets the request, identifies the repository, resolves dependencies, and invokes the PEV chain. The planner creates a migration plan (including dependency order, pipeline conversion strategy, secret/service connection mapping). The executor performs the migration (git mirror, pipeline → workflow conversion, Bicep → GitHub Actions transformation, secret/service connection provisioning). The validator verifies the migration succeeded (git HEAD parity, workflow runs, dependency resolution). If any step fails, the validator sends feedback to the planner, which creates a revised plan. The loop continues until the migration is complete or the agent needs user input.

**Why this priority**: This is the core value proposition — a completely hands-off migration experience that works out of the box.

**Independent Test**: A user submits "migrate Project/RepoName" for a repo with 2 dependencies and 3 ADO pipelines. The agent completes the full migration (repos mirrored, pipelines converted to GitHub Actions workflows, Bicep templates transformed, secrets mapped) without any intermediate user interaction. The migrated repos' GitHub Actions workflows run successfully on the first push.

**Acceptance Scenarios**:

1. **Given** a user types "migrate Project/RepoName to GitHub", **When** the orchestrator processes the message, **Then** it identifies the repo from discovery data (or asks for clarification if ambiguous), resolves dependencies automatically, and starts the PEV chain without requiring the user to navigate separate discovery/planning/execution steps.
2. **Given** the planner agent receives a migration request, **When** it builds the plan, **Then** the plan includes: repo migration order (topological sort with dependencies), pipeline conversion strategy (ADO pipeline → GitHub Actions workflow mapping), Bicep/ARM template transformation steps, secret/service connection mapping, and dry-run vs live determination.
3. **Given** the executor agent receives a plan from the planner, **When** it executes, **Then** it performs git mirror migration via GEI or direct API, converts ADO pipelines to GitHub Actions workflows, transforms Bicep/ARM templates, provisions secrets and environments, and pushes to the migration branch — all deterministically per the plan.
4. **Given** the executor completes its work, **When** the validator runs, **Then** it verifies git HEAD SHA parity, confirms workflows exist on the migration branch, tests that workflows are syntactically valid, checks secret references resolve, and validates dependency order was respected.
5. **Given** the validator finds a failure (e.g., a workflow has a syntax error), **When** it sends feedback to the planner, **Then** the planner creates a revised plan targeting only the failed scope, the executor re-runs, and the validator re-checks — this loop continues up to the retry limit before escalating to the user.

---

### User Story 2 - Continuous Agent Loop (Priority: P1)

The agent does not stop after a single PEV cycle. Like Claude Code or Cursor, it continues working until the job is complete or it needs user input. The agent loop cycles through Orchestrator → Planner → Executor → Validator repeatedly, with each agent able to request information from the previous agent in the chain. The orchestrator decides when to ask the user vs. when to continue autonomously.

**Why this priority**: The current architecture stops after one tool call cycle. A true agentic loop is essential for hands-off migration.

**Independent Test**: A user submits a migration request for a repo with a missing secret. The planner identifies the missing secret, the orchestrator asks the user for the secret value, the user provides it, the planner revises the plan, the executor provisions the secret and completes the migration, the validator confirms — all within a single chat session without the user needing to re-submit the original request.

**Acceptance Scenarios**:

1. **Given** a PEV cycle completes with validation failures, **When** the validator sends feedback to the planner, **Then** the agent loop automatically starts a new PEV cycle with the revised plan — no user interaction required.
2. **Given** the planner needs information from the user (e.g., "should service connections be migrated?"), **When** it sends the request to the orchestrator, **Then** the orchestrator presents a dynamic form to the user, pauses the loop, and resumes with the user's response.
3. **Given** the executor encounters ambiguity in the planner's instructions, **When** it asks the planner for clarification, **Then** the planner responds with clarified instructions and the executor continues — this inter-agent communication happens without user involvement.
4. **Given** the retry limit is reached (3 cycles), **When** the validator still reports failures, **Then** the orchestrator presents a clear summary of what failed, what was tried, and asks the user how to proceed.
5. **Given** the migration completes successfully, **When** the validator reports pass, **Then** the orchestrator presents a summary to the user and the session returns to idle — ready for the next request.

---

### User Story 3 - Orchestrator Agent: User Intent Interpretation & Dynamic Forms (Priority: P1)

The orchestrator agent is the user-facing interface. It interprets natural language inputs, understands migration intent, creates dynamic forms to gather missing information, and routes migration work to the PEV chain. It handles general queries (greetings, migration questions, status checks) directly without invoking the PEV chain. It contains enterprise guardrails to prevent accidental deletions and information leakage.

**Why this priority**: The orchestrator is the entry point for all user interaction. If it cannot correctly interpret intent and gather information, the entire agent experience fails.

**Independent Test**: A user types "migrate repo to github" (vague request). The orchestrator responds with a dynamic form asking which repository (populated from discovery data), whether to do a dry-run or live migration, and whether to include dependencies. After the user submits the form, the orchestrator routes the complete request to the PEV chain.

**Acceptance Scenarios**:

1. **Given** a user types "migrate repo to github", **When** the orchestrator processes the message, **Then** it identifies that a repository_id is missing and presents a dynamic form with a text/select field populated with available repos from discovery data.
2. **Given** a user types "hello" or "what can you do?", **When** the orchestrator processes the message, **Then** it responds directly with a brief greeting and capability summary — no PEV chain invocation, no tool calls.
3. **Given** a user types "show migration status", **When** the orchestrator processes the message, **Then** it fetches and summarizes migration status directly (or delegates to the validator for status data) without starting a new PEV cycle.
4. **Given** the orchestrator determines a live (non-dry-run) migration is requested, **When** it presents the plan confirmation form, **Then** the form includes explicit warnings about live execution, requires confirmation, and enforces the approval policy (Approver sign-off for live execution).
5. **Given** the orchestrator detects a request that would delete or modify an existing GitHub resource, **When** it processes the request, **Then** it presents a confirmation form with the specific resource name, the planned action, and requires explicit user approval before proceeding.

---

### User Story 4 - Planner Agent: Migration Planning & Dependency Coordination (Priority: P1)

The planner agent receives migration requests from the orchestrator and creates detailed execution plans. It uses API calls (respecting rate limits) or discovery data to determine what needs to be migrated. It maps dependencies, coordinates with the orchestrator for user information (e.g., whether service connections or secrets are allowed), and sends complete execution instructions to the executor. The planner also takes feedback from the validator to create revised plans. The planner only plans — it never executes migration operations.

**Why this priority**: The planner is the brain of the PEV chain. Without accurate planning, the executor cannot function deterministically.

**Independent Test**: The planner receives "migrate Project/RepoA" where RepoA depends on RepoB and RepoC. The planner produces a plan with topological order [RepoB, RepoC, RepoA], pipeline conversion mappings for each repo, secret/service connection mappings, Boards/Test Plans/Artifacts/Wiki work items, and a dry-run flag. The plan is structured enough for the executor to execute without making any planning decisions.

**Acceptance Scenarios**:

1. **Given** the planner receives a migration request for Project/RepoA, **When** it queries discovery data and finds RepoA depends on RepoB, **Then** the plan includes both repos in topological order with RepoB first.
2. **Given** the planner identifies a missing secret needed for pipeline conversion, **When** it cannot determine the secret value from available data, **Then** it sends a request to the orchestrator asking the user for the secret value (via dynamic form), pauses, and resumes when the user responds.
3. **Given** the validator reports that a workflow conversion failed due to a missing variable group mapping, **When** the planner receives the feedback, **Then** it creates a revised plan that includes mapping the variable group to GitHub secrets before retrying the workflow conversion.
4. **Given** the planner is building a plan for a repo with 5 ADO pipelines, **When** it maps pipelines to GitHub Actions workflows, **Then** the plan specifies: pipeline name → workflow file name, trigger mapping, pool → runner mapping, variable groups → secrets mapping, and Bicep/ARM template transformation steps.
5. **Given** the planner is rate-limited by the ADO API, **When** it cannot fetch additional discovery data, **Then** it works with available data and flags any assumptions as "needs verification" in the plan, asking the orchestrator to confirm with the user if critical.

---

### User Story 5 - Executor Agent: Deterministic Execution (Priority: P1)

The executor agent interprets instructions from the planner and executes them deterministically. It does not plan — it migrates repos when the planner says to, creates secrets when the planner says to, migrates service connections when the planner says to, converts pipelines when the planner says to. If the executor encounters ambiguity in the planner's instructions, it asks the planner for clarification (not the user). The executor sends its exact output (what was created, what failed) to the validator. The executor has file/script modification capabilities and must be secured with guardrails.

**Why this priority**: The executor is the hands of the system. It must be deterministic and secure — any hallucination here causes real damage.

**Independent Test**: The executor receives a plan to migrate RepoA, convert 3 pipelines, provision 2 secrets, and migrate 2 service connections. The executor performs exactly those operations, no more, no less. The output sent to the validator lists: repo mirror status, 3 workflow files created (with paths), 2 secrets provisioned (names only, not values), 2 service connections migrated (to secrets or environments), and any failures with error details.

**Acceptance Scenarios**:

1. **Given** the executor receives a plan to migrate RepoA and convert its pipelines, **When** it executes, **Then** it performs exactly the operations specified in the plan — no additional repos migrated, no extra workflows created, no secrets deleted.
2. **Given** the executor encounters an ambiguous instruction (e.g., "convert pipelines" without specifying which format), **When** it cannot determine the correct action, **Then** it sends a clarification request back to the planner (not the user) and waits for a response.
3. **Given** the executor completes a git mirror migration, **When** it sends results to the validator, **Then** the output includes: source SHA, target SHA, migration branch name, files pushed (count), and any errors encountered.
4. **Given** the executor has access to modify files and scripts on GitHub, **When** it attempts a write operation, **Then** the operation goes through a guardrail layer that: verifies the target resource exists (for modifications), verifies the operation is in the approved plan, blocks any deletion not explicitly authorized, and logs the operation for audit.
5. **Given** the executor is transforming a Bicep template to a GitHub Actions workflow, **When** it generates the workflow file, **Then** the workflow uses GitHub Actions syntax, references appropriate secrets via `${{ secrets.NAME }}` syntax, and is pushed to the migration branch.

---

### User Story 6 - Validator Agent: Evidence-Based Validation (Priority: P1)

The validator agent receives both the planner's instructions (what was supposed to happen) and the executor's output (what actually happened). It validates using API calls or testing frameworks to confirm resources were created correctly. If something would fail or a test fails, it provides structured feedback to the planner for re-planning.

**Why this priority**: The validator is the quality gate. Without evidence-based validation, migrations appear successful but have hidden failures.

**Independent Test**: The executor reports it created a workflow file `ci.yml` on the migration branch. The validator queries the GitHub API to confirm the file exists at the reported path, checks the YAML syntax is valid, verifies all secret references in the workflow resolve to existing secrets, and runs local workflow simulation (e.g., `act`) to confirm the workflow is functional — the validator MUST NOT dispatch the actual workflow on GitHub (per FR-088). If any check fails, the validator sends structured feedback to the planner.

**Acceptance Scenarios**:

1. **Given** the executor reports a repo was migrated with SHA `abc123`, **When** the validator checks, **Then** it queries the GitHub API to confirm the repo exists, the default branch HEAD matches `abc123`, and the migration branch exists with the expected workflow files.
2. **Given** the executor reports 3 workflows were created, **When** the validator checks, **Then** it validates each workflow file: YAML syntax, required `on:` triggers present, job steps reference valid actions, secret references resolve, and runner labels are valid.
3. **Given** the executor reports a secret was provisioned, **When** the validator checks, **Then** it confirms the secret exists in the GitHub repo/org settings (name only, not value) and that workflow references to that secret are syntactically correct.
4. **Given** the validator finds a workflow with a syntax error, **When** it sends feedback to the planner, **Then** the feedback includes: the specific file path, the syntax error details, the line number (if available), and a recommendation (e.g., "fix the `runs-on` label" or "missing `uses:` in step").
5. **Given** the planner's plan specified topological order [RepoB, RepoC, RepoA], **When** the validator checks dependency order, **Then** it confirms RepoB was migrated before RepoC and RepoC before RepoA, and that cross-repo dependencies (e.g., artifact downloads) reference the correct migrated repos.

---

### User Story 7 - Shared Tool Access with Guardrails (Priority: P2)

All agents share a common tool set with different access levels. ADO tools are read-only (discover, list pipelines, read repos, query boards). GitHub tools are read/write (create repos, push workflows, create secrets, manage environments) with elevated privileges. Guardrails prevent the LLM from hallucinating destructive operations: no resource may be deleted without explicit user confirmation, no operation may be performed outside the approved plan, and all write operations are logged for audit.

**Why this priority**: Shared tools with proper guardrails are the foundation for the executor's write access to GitHub. Without guardrails, LLM hallucinations could delete production resources.

**Independent Test**: The executor attempts to delete a GitHub repo that was not in the migration plan. The guardrail layer blocks the operation, logs the attempt, and returns an error to the executor. The executor reports the blocked operation to the validator. No GitHub resource is modified.

**Acceptance Scenarios**:

1. **Given** all agents share a tool catalog, **When** the planner queries ADO for repo data, **Then** it uses read-only ADO tools (list repos, read pipelines, query boards) and cannot modify any ADO resource.
2. **Given** the executor has write access to GitHub, **When** it attempts to create a new workflow file, **Then** the operation is allowed only if: the file path is in the approved plan, the target repo is in the migration scope, and the operation is a create (not a delete).
3. **Given** the executor attempts to delete an existing GitHub resource (repo, workflow, secret, environment), **When** the guardrail layer intercepts the request, **Then** the operation is blocked unless: the deletion is explicitly in the approved plan AND the user has confirmed the deletion via a dynamic form.
4. **Given** any agent performs a GitHub write operation, **When** the operation completes, **Then** an audit record is created with: timestamp, agent role, tool name, target resource, operation type, result, and correlation to the session and plan.
5. **Given** the LLM generates a tool call with a hallucinated parameter (e.g., a repo name that doesn't exist), **When** the tool executes, **Then** the tool validates all parameters against known resources and returns a descriptive error rather than creating a resource with an invalid name.

---

### User Story 8 - Claude Code/Cursor-Like Chat Interface (Priority: P2)

The agent tab presents a simplified chat interface inspired by Claude Code, Cascade, and Cursor. The interface shows: a chat message list (user and assistant messages), a thinking/working indicator when agents are processing, dynamic forms rendered inline when user input is needed, and a session sidebar for managing multiple chat sessions. The interface does not expose internal agent-to-agent communication (planner → executor messages) to the user — only the orchestrator's user-facing messages are visible.

**Why this priority**: The UX must be simple and intuitive. The current interface exposes too much internal complexity (task timelines, tool calls, status messages).

**Acceptance Scenarios**:

1. **Given** a user opens the Agent tab, **When** the interface loads, **Then** they see a clean chat interface with a message input, a model selector, a dry-run toggle, and a session sidebar — no task timelines, no tool call details, no internal agent messages.
2. **Given** the agent loop is running (planner → executor → validator), **When** the user views the chat, **Then** they see a "Working..." indicator with optional expandable details showing which agent is currently active (e.g., "Planner is building migration plan...").
3. **Given** the orchestrator needs user input, **When** it presents a dynamic form, **Then** the form renders inline in the chat as a card with fields, a submit button, and a cancel button — the chat input is disabled while the form is pending.
4. **Given** the PEV chain completes a cycle, **When** the validator reports results, **Then** the orchestrator presents a user-facing summary (e.g., "Migration complete: 3 repos migrated, 5 workflows created, 2 secrets provisioned") — internal agent messages are not shown.
5. **Given** a user wants to start a new conversation, **When** they click "New chat", **Then** a new session is created and the previous session is preserved in the sidebar for later reference.

---

### User Story 9 - Enterprise Guardrails & Safety (Priority: P2)

The agent system includes enterprise-level guardrails to prevent accidental deletions, information leakage, and unauthorized operations. Guardrails operate at multiple layers: tool parameter validation, plan-vs-execution consistency checking, deletion confirmation, secret value masking, and audit logging.

**Why this priority**: Enterprise migrations cannot tolerate LLM-driven destructive operations. Guardrails are non-negotiable for production use.

**Acceptance Scenarios**:

1. **Given** the executor generates a tool call to delete a GitHub repo, **When** the guardrail layer evaluates the call, **Then** the deletion is blocked unless: it is explicitly in the approved plan, the user has confirmed via a dynamic form, and the approver policy is satisfied.
2. **Given** any agent handles secret values, **When** the values appear in messages, logs, or audit records, **Then** the values are masked (e.g., `***`) — only secret names are visible, never values.
3. **Given** the executor performs a write operation outside the approved plan, **When** the guardrail layer detects the deviation, **Then** the operation is blocked, an error is returned to the executor, and the attempt is logged for audit.
4. **Given** the orchestrator presents a plan to the user, **When** the plan includes destructive operations (repo deletion, pipeline disabling, ADO cleanup), **Then** each destructive operation is highlighted with a warning and requires individual confirmation.
5. **Given** an agent session is completed or terminated, **When** the session transcript is reviewed, **Then** every tool call, every agent decision, every user interaction, and every guardrail evaluation is traceable in the audit log.

---

### Edge Cases

- What happens when the LLM provider is unavailable mid-session? The agent MUST degrade gracefully — the orchestrator informs the user, any in-progress PEV cycle is safely paused, and session state is preserved for resumption.
- What happens when the ADO API rate limit is hit during planning? The planner MUST use available discovery data and flag gaps as assumptions. If critical data is missing, the planner asks the orchestrator to get confirmation from the user.
- What happens when the GitHub API returns a 403 (insufficient permissions) during execution? The executor MUST report the permission error to the validator, which sends it to the planner. The planner includes a step to request elevated permissions from the user via the orchestrator.
- What happens when two agents disagree (e.g., the planner says "create secret X" but the executor finds secret X already exists)? The executor MUST ask the planner for clarification — should it overwrite, skip, or use the existing secret? The planner decides based on the migration context.
- What happens when the migration involves a repo that has already been partially migrated? The planner MUST detect the partial migration state and create a plan that resumes from the last successful checkpoint rather than starting from scratch.
- What happens when the agent loop exceeds the maximum iteration count (e.g., 20 cycles)? The orchestrator MUST break the loop, present a summary of what was accomplished and what remains, and ask the user how to proceed.
- What happens when the user sends a new message while the PEV chain is running? The orchestrator MUST queue the message and process it after the current PEV cycle completes, or interrupt the cycle if the message is a cancellation request.
- What happens when a Bicep template cannot be automatically transformed to a GitHub Actions workflow? The executor MUST report the transformation failure with the specific Bicep constructs that could not be mapped. The planner creates a plan to either use a manual conversion step (asking the user) or skip the template with a documented gap.
- What happens when the executor hallucinates a file path or resource name? The guardrail layer MUST validate all resource references against actual GitHub/ADO API responses and block operations targeting non-existent resources.

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

- **CA-001**: The agent MUST provide a dry-run mode for all migration operations — no irreversible GitHub changes occur without explicit user confirmation and approver sign-off where required.
- **CA-002**: Destructive actions (repo deletion, workflow deletion, secret deletion, ADO pipeline disabling) MUST require explicit user confirmation via dynamic forms AND approver sign-off where policy requires it.
- **CA-003**: Secrets and credential values MUST NOT appear in chat messages, agent transcripts, audit logs, or UI — only secret names and masked indicators are visible.
- **CA-004**: All agent decisions, tool calls, inter-agent communications, user interactions, guardrail evaluations, and PEV cycle outcomes MUST be auditable with correlation to the session ID, plan ID, and migration run ID.
- **CA-005**: The agent MUST NOT modify or delete GitHub resources that are not in the approved migration plan — the guardrail layer enforces plan-vs-execution consistency.

### Functional Requirements

#### Agent architecture: four-agent chain with continuous loop

- **FR-001**: The system MUST implement four distinct agent roles: Orchestrator, Planner, Executor, and Validator — each using the same LLM provider instance but differentiated by system prompts and tool access levels.
- **FR-002**: The agent loop MUST be continuous — it cycles through Orchestrator → Planner → Executor → Validator repeatedly until a terminal state is reached (job complete, needs user input, unrecoverable error, or max iterations exceeded).
- **FR-003**: Each agent MUST be able to communicate with the adjacent agent in the chain: Orchestrator ↔ Planner, Planner ↔ Executor, Executor ↔ Validator, Validator → Planner (feedback loop).
- **FR-004**: The Orchestrator MUST be the only agent that communicates with the user — internal agent-to-agent messages are not shown in the chat interface.
- **FR-005**: The Planner MUST be able to request information from the Orchestrator (which asks the user) when the plan requires data not available from discovery or API calls.
- **FR-006**: The Executor MUST be able to request clarification from the Planner when instructions are ambiguous — this communication does not involve the user.
- **FR-007**: The Validator MUST send structured feedback to the Planner when validation fails, including: what was expected (from the plan), what was observed (from execution), the specific failure, and a recommended remediation.
- **FR-008**: The maximum PEV retry loop MUST be configurable (default: 3 cycles) before the orchestrator escalates to the user with a failure summary. (See FR-056 for consolidated limits configuration.)
- **FR-009**: The maximum total agent loop iterations MUST be configurable (default: 20) before the orchestrator breaks the loop and presents a partial completion summary. (See FR-056 for consolidated limits configuration.)
- **FR-057**: Agent sessions (including messages, migration plans, PEV cycle state, and iteration counters) MUST be persisted to the storage backend (SQLite/PostgreSQL per `ADO2GH_STORAGE_BACKEND`) and survive backend server restarts.
- **FR-058**: The system MUST support up to 10 concurrent active agent sessions per backend instance, managed via asyncio-compatible async processing.
- **FR-059**: Inter-agent communication MUST use structured JSON messages with typed fields: `message_type` (instruction|clarification_request|feedback|result), `from_role`, `to_role`, `payload` (typed per message_type), `correlation_ids`, and `timestamp`.
- **FR-060**: The system MUST support batch phase migration in v1 — a user can request "migrate all repos in wave 1" and the agent processes 50+ repos through sequential PEV cycles with a migration queue.
- **FR-061**: The agent MUST manage LLM context windows using a sliding window strategy: the LLM receives the last 2 PEV cycles in full detail plus a running JSON summary of prior cycles. Full message history is persisted in the session store for audit.
- **FR-062**: After each PEV cycle, the system MUST generate a compact JSON cycle summary containing: cycle number, repos processed, repos succeeded, repos failed, failures summary, and next action.
- **FR-065**: The executor MUST migrate all ADO resource types in v1: repos (git mirror), pipelines (→ GitHub Actions workflows), secrets (→ GitHub secrets), service connections (→ GitHub secrets + environments, see FR-085), Boards (work items → GitHub Issues), Test Plans (→ GitHub Issues with test-case/test-suite labels + milestones), Artifacts (feeds/packages → GitHub Packages), and Wiki (→ GitHub Wiki/docs). The planner MUST include per-resource-type work items in the migration plan.
- **FR-066**: The session status field MUST follow a formal state machine with enforced transitions: idle → thinking → planning → executing → validating → completed|failed; validating → planning (retry); any → awaiting_input → resume to previous state; awaiting_input → idle (user cancel); any → awaiting_approval → planning (approved) | failed (rejected). Invalid transitions MUST be rejected by the backend.
- **FR-067**: LLM calls MUST have a 60-second per-call timeout. On first timeout, the system retries the call once with the same prompt. On second timeout, the agent reports a degraded state to the orchestrator, which informs the user and pauses the session (status → awaiting_input).
- **FR-068**: The system MUST implement repo-level locks in the session DB to prevent concurrent migration of the same repo across sessions. When a session begins executing a migration for a repo, it acquires a lock. If another session attempts to migrate the same repo, the executor detects the lock and the orchestrator informs the second operator. Locks are released when the PEV cycle completes, fails, or the session is terminated.
- **FR-069**: The agent backend MUST expose a Prometheus-compatible `/metrics` endpoint with: active_sessions, pev_cycles_total, llm_call_duration_seconds (histogram), guardrail_blocks_total, tool_calls_total, and session_status_counts.
- **FR-070**: The agent backend MUST expose an agent-specific `/health` endpoint reporting: LLM provider availability, active session count, storage backend connectivity, and current configuration status.
- **FR-073**: For existing migration operations (discovery, GEI git mirror, pipeline conversion, readiness checks), the executor MUST route through the accelerator service API. For new resource types (service connections, Boards, Test Plans, Artifacts, Wiki), the executor MUST call ADO/GitHub APIs directly using the profile's configured PATs.
- **FR-074**: All entity IDs (session_id, plan_id, run_id, queue_id, lock_id, message_id) MUST be generated as UUID v4 strings. No sequential or namespaced ID schemes are used.
- **FR-075**: Session data (messages, plans, PEV cycle state, iteration counters) MUST be retained for 90 days from the session's last activity timestamp, after which it is eligible for cleanup. Audit logs (guardrail decisions, tool call logs, agent decisions) MUST be retained indefinitely.
- **FR-076**: The chat interface MUST display distinct UI states: (a) LLM unconfigured — shows a setup prompt with a link to settings; (b) empty discovery — shows a scan prompt with a button to trigger discovery; (c) error state — shows an actionable summary with retry and contact-admin options.
- **FR-077**: When a user requests a migration and discovery data is empty, the orchestrator MUST automatically trigger a profile discovery scan, display a "Running discovery scan..." indicator, and proceed to planning once the scan completes — without requiring the user to navigate to the Discovery tab.
- **FR-078**: ADO Boards (work items) MUST be migrated to GitHub Issues via the GitHub Issues Import API using CSV export with fixed field mapping: Title→title, Description→body, Tags→labels, Area Path→label (e.g., `area:backend`), Iteration Path→label (e.g., `iteration:sprint-42`), Assigned To→assignee, State→open/closed. Attachments are noted as markdown links `[attachment](url)` in the issue body. Links to other work items are noted as `#issue-number` references where possible, or as plain text ADO URLs otherwise.
- **FR-079**: ADO Test Plans MUST be migrated to GitHub Issues with `test-case` and `test-suite` labels, with test suites mapped to milestones. The planner may override this mapping per-migration with documented justification for edge cases.
- **FR-080**: ADO Artifacts (feeds/packages) MUST be migrated to GitHub Packages for supported types (npm, NuGet, Docker, Maven, PyPI). Unsupported artifact types MUST be documented as gaps — the executor reports them, the planner surfaces them to the user.
- **FR-081**: ADO Wiki MUST be migrated to GitHub Wiki per-repo — the executor enables the wiki via the GitHub API and pushes wiki content as markdown files.
- **FR-082**: When a user cancels a migration, the orchestrator MUST present two options: (a) rollback — delete all GitHub resources created during this session's migration, or (b) stop — complete the current operation and halt. The orchestrator presents a summary of what was created and what remains.
- **FR-083**: If the user opts into rollback on cancellation, the executor MUST delete all GitHub resources created during this session (repos, workflows, secrets, environments, issues, wiki pages, packages) as tracked by session correlation IDs. The guardrail layer ensures only resources created during this session are eligible for deletion. All rollback operations are logged for audit.
- **FR-084**: Spec 010 (Enterprise Audit Simplification) MUST execute before spec 011 — 010 decomposes `session_orchestrator.py` into modules under 800 lines, and 011 rewrites the decomposed modules for the continuous loop architecture. The 011 rewrite produces new modules that also satisfy the 800-line limit.
- **FR-085**: ADO service connections MUST be mapped to GitHub secrets and environments based on connection type. Simple credential-based connections (e.g., Docker registry, npm feed) map to GitHub repo/org secrets. Deployment-scoped connections (e.g., Azure RM, Kubernetes, environment-specific endpoints) map to GitHub environments with protection rules. The planner determines the mapping based on connection type and includes it in the migration plan.
- **FR-086**: The executor MUST implement idempotency for migration operations — when the executor encounters a repo that was already migrated (detected via API checks), it MUST ask the orchestrator to prompt the user with three options: overwrite (re-execute all operations, replacing existing GitHub resources), skip (skip the already-migrated resource, validator confirms existing state is valid), or abort (stop the migration for this repo). This satisfies Constitution Principle V (idempotency for batch migration patterns).
- **FR-087**: The executor MUST support Bicep/ARM template transformation using a hybrid approach: a supported construct list (deploy, parameters, variables, resource for common Azure resource types, output, environment references) is automatically transformed. For constructs outside the supported list, the executor LLM attempts best-effort transformation and may use web search tools to look up documentation. Constructs that cannot be transformed are reported as gaps with the specific construct name and suggested manual conversion steps.
- **FR-088**: The validator MUST validate workflows using local validation only — YAML syntax validation plus local workflow simulation (e.g., `act`) when available. The validator MUST NOT dispatch or run the actual workflow on GitHub. If local simulation tools are unavailable, the validator falls back to YAML syntax validation only and notes the reduced validation depth in the validation result.

#### Orchestrator agent

- **FR-010**: The Orchestrator MUST interpret natural language user inputs and classify intent as: general chat, migration info query, or migration action.
- **FR-011**: For general chat and migration info queries, the Orchestrator MUST respond directly without invoking the PEV chain.
- **FR-012**: For migration actions, the Orchestrator MUST identify all required parameters (repo, phase, dry-run/live, scope) and present dynamic forms for any missing parameters before invoking the Planner.
- **FR-013**: The Orchestrator MUST create dynamic forms with: form_id, title, description, and fields (name, label, type: select/checkbox/text/textarea, options, required) — forms are rendered inline in the chat interface.
- **FR-014**: The Orchestrator MUST determine whether to do a dry-run or live migration based on user input and policy — it MUST NOT assume live execution without explicit user confirmation.
- **FR-015**: The Orchestrator MUST present plan summaries to the user for confirmation before live execution begins — the plan summary includes repos, scopes, order, and risks.
- **FR-016**: The Orchestrator MUST enforce enterprise guardrails: no destructive operation proceeds without user confirmation, no secret values are exposed in chat, and all operations are logged for audit.
- **FR-017**: The Orchestrator MUST handle user messages received while the PEV chain is running by queuing them for processing after the current cycle completes, or interrupting the cycle for cancellation requests.

#### Planner agent

- **FR-018**: The Planner MUST create detailed migration plans from the Orchestrator's instructions. The Planner MUST prefer discovery data when available. The Planner MAY make ADO API calls only when discovery data is incomplete or stale (older than 24 hours), respecting rate limits.
- **FR-019**: The Planner MUST map repository dependencies using topological sort and include the migration order in the plan.
- **FR-020**: The Planner MUST include in every plan: repo migration order, pipeline-to-workflow conversion mappings, Bicep/ARM template transformation steps (where applicable), secret/service connection mapping, Boards (work items→Issues) mapping, Test Plans mapping, Artifacts (feeds→Packages) mapping, Wiki migration steps, dry-run/live flag, and per-repo work items with ready/blocked status.
- **FR-021**: The Planner MUST coordinate with the Orchestrator (which asks the user) when it needs information not available from discovery data (e.g., whether service connections should be migrated, secret values for provisioning).
- **FR-022**: The Planner MUST create revised plans from Validator feedback — the revised plan targets only the failed scopes and includes remediation steps.
- **FR-023**: The Planner MUST NOT execute any migration operations — it only plans and sends instructions to the Executor.
- **FR-024**: The Planner MUST document all assumptions made when discovery data is incomplete and flag them for Orchestrator confirmation.
- **FR-025**: The Planner MUST be rate-limit aware — when ADO API rate limits are hit, it works with available data and flags gaps rather than blocking indefinitely.

#### Executor agent

- **FR-026**: The Executor MUST interpret and execute instructions from the Planner deterministically — it does not make planning decisions.
- **FR-027**: The Executor MUST perform: git mirror migration (via GEI or direct API), ADO pipeline → GitHub Actions workflow conversion, Bicep/ARM template → GitHub Actions transformation, secret/environment provisioning, Boards (work items→GitHub Issues) migration, Test Plans migration, Artifacts (feeds→GitHub Packages) migration, Wiki migration, and workflow branch push.
- **FR-028**: The Executor MUST send exact output to the Validator: what was created (resource names, paths, SHAs), what failed (error details, error codes), and what was skipped (with reasons).
- **FR-029**: The Executor MUST request clarification from the Planner (not the user) when instructions are ambiguous — it does not guess or default.
- **FR-030**: The Executor MUST operate through a guardrail layer (see FR-051) for all GitHub write operations — the guardrail validates: operation is in the approved plan, target resource exists (for modifications), deletion operations have explicit authorization, and all parameters reference valid resources.
- **FR-031**: The Executor MUST NOT delete any GitHub resource unless: the deletion is explicitly in the approved plan, the user has confirmed via a dynamic form, and the approver policy is satisfied.
- **FR-032**: The Executor MUST transform Bicep templates to GitHub Actions workflow syntax using the hybrid approach defined in FR-087 — supported constructs are automatically transformed, unsupported constructs are attempted via LLM best-effort with web search, and remaining gaps are reported for manual conversion.
- **FR-033**: The Executor MUST be secured with the principle of least privilege — it can only access tools and resources within the migration scope defined by the plan.

#### Validator agent

- **FR-034**: The Validator MUST receive both the Planner's plan (what was supposed to happen) and the Executor's output (what actually happened) as context.
- **FR-035**: The Validator MUST verify migration outcomes using API calls and local validation frameworks: git HEAD SHA parity, workflow file existence and YAML syntax, secret reference resolution, service connection migration verification (secrets exist and environments exist with correct protection rules per FR-085), dependency order compliance, local workflow simulation validation (per FR-088, MUST NOT dispatch actual workflow on GitHub), Boards (work items→Issues) migration verification, Test Plans migration verification, Artifacts (feeds→Packages) migration verification, and Wiki migration verification.
- **FR-036**: The Validator MUST send structured feedback to the Planner when validation fails: expected state, observed state, specific failure, file path (if applicable), and recommended remediation.
- **FR-037**: The Validator MUST validate that the Executor did not perform any operations outside the approved plan — any deviation is reported as a validation failure.
- **FR-038**: The Validator MUST confirm Bicep/ARM template transformations (per FR-087) produced syntactically valid GitHub Actions workflows — invalid YAML, missing required fields, or unsupported Bicep constructs that were not transformed are flagged as failures with the specific construct name.
- **FR-039**: The Validator MUST report pass/fail per scope (repo content, pipelines/workflows, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki) with evidence suitable for audit review.

#### Shared tools and access control

- **FR-040**: All agents MUST share a common tool catalog with role-based access levels: Orchestrator (read-only ADO + read-only GitHub + user interaction tools), Planner (read-only ADO + read-only GitHub + discovery tools), Executor (read-only ADO + read/write GitHub + migration tools), Validator (read-only ADO + read-only GitHub + validation tools).
- **FR-041**: ADO tools MUST be read-only for all agents — no agent can modify, create, or delete ADO resources (except ADO pipeline disabling when explicitly approved as part of migration cleanup).
- **FR-042**: GitHub tools MUST enforce write guardrails (see FR-051 for comprehensive guardrail definition): all write operations go through a validation layer that checks plan authorization, resource existence, and deletion confirmation.
- **FR-043**: The tool catalog MUST validate all parameters against known resources before execution — hallucinated resource names, invalid paths, and non-existent repos are rejected with descriptive errors.
- **FR-044**: All tool executions MUST be logged for audit with: timestamp, agent role, tool name, parameters (with secrets masked), result, and correlation IDs.

#### Chat interface

- **FR-045**: The Agent tab MUST present a Claude Code/Cursor-like chat interface with: message list (user and assistant messages), message input, model selector, dry-run toggle, and session sidebar.
- **FR-046**: The chat interface MUST NOT show internal agent-to-agent messages (planner → executor, executor → validator) — only the Orchestrator's user-facing messages are visible.
- **FR-047**: The chat interface MUST show a working indicator when the PEV chain is running, with optional expandable details showing which agent is currently active.
- **FR-048**: Dynamic forms MUST render inline in the chat as cards with fields, submit button, and cancel button — the chat input is disabled while a form is pending.
- **FR-049**: The chat interface MUST support multiple sessions per profile, with session persistence and a sidebar for switching between sessions.
- **FR-050**: The chat interface MUST auto-scroll to the latest message and preserve scroll position when loading history.

#### Enterprise guardrails

- **FR-051**: A guardrail layer MUST intercept all GitHub write operations and enforce: plan authorization (operation is in the approved plan), resource validation (target exists for modifications), deletion confirmation (user-approved via dynamic form), and parameter validation (all references are valid).
- **FR-052**: Secret values MUST be masked in all agent messages, logs, audit records, and UI — only secret names are visible, never values.
- **FR-053**: The agent MUST NOT perform any GitHub write operation that is not in the approved migration plan (enforced by guardrail layer, see FR-051) — deviations are blocked and logged.
- **FR-054**: Destructive operations (repo deletion, workflow deletion, secret deletion, ADO pipeline disabling) MUST be highlighted in the plan summary with individual confirmation requirements.
- **FR-055**: All agent operations MUST be auditable with: timestamp, agent role, tool name, parameters (masked), result, guardrail decision, session ID, plan ID, and run ID.
- **FR-056**: The agent system MUST support configurable max iterations (default: 20 total loop cycles, 3 PEV retry cycles) before breaking and escalating to the user.
- **FR-063**: For batch phase migrations, the planner MUST generate a migration queue with per-repo work items ordered by topological dependency. The executor processes repos sequentially from the queue, and the validator validates each repo before the next begins.
- **FR-064**: The session store MUST persist: session metadata, all agent messages (inter-agent and user-facing), migration plans (all revisions), PEV cycle summaries, guardrail decisions, iteration counters, and repo-level locks — enabling full session reconstruction after server restart.
- **FR-071**: The session store MUST persist the current state machine state and enough context (current PEV cycle, current repo in queue, executor progress) to resume the agent loop from the correct point after a server restart.
- **FR-072**: Repo-level locks MUST survive server restarts — a lock is only released when the owning session completes, fails, or is explicitly terminated by the user. Stale locks from crashed sessions MUST be cleaned up on server restart with audit logging.

### Key Entities *(include if feature involves data)*

- **AgentSession**: A conversation session between the user and the orchestrator. Contains: session_id, profile_id, model_id, status (idle/thinking/planning/executing/validating/awaiting_input/awaiting_approval/completed/failed), messages, pending_form, migration_plan, dry_run flag, and iteration counters.
- **MigrationPlan**: Structured output from the Planner. Contains: plan_id, repos (in topological order), per-repo work items (pipeline conversions, Bicep transformations, secret/service connection mappings), dry_run flag, assumptions, blocked items, and revision number (incremented on each planner re-plan from validator feedback).
- **ExecutorResult**: Output from the Executor sent to the Validator. Contains: plan_id, per-repo results (git mirror status, workflow files created, secrets provisioned, service connections migrated, Bicep transformations applied), failures (with error details), and skipped items (with reasons).
- **ValidationResult**: Output from the Validator sent to the Planner. Contains: plan_id, per-scope pass/fail (git, pipelines, secrets, service_connections, dependencies, boards, test_plans, artifacts, wiki), evidence (API responses, YAML validation results), failures (with expected vs observed), and recommended remediation.
- **GuardrailDecision**: Record of a guardrail evaluation. Contains: timestamp, agent role, tool name, operation type, target resource, decision (allow/block), reason, plan reference, and session correlation.
- **AgentMessage**: Internal message between agents. Contains: from_role, to_role, message_type (instruction/clarification_request/feedback/result), payload (typed JSON per message_type), timestamp, and correlation IDs. All inter-agent messages use structured JSON format.
- **PevCycleSummary**: Compact JSON summary generated after each PEV cycle. Contains: cycle_number, repos_processed, repos_succeeded, repos_failed, failures (scoped to repo + scope), next_action (replan|continue|escalate|complete), and timestamp. Used for LLM context window management.
- **MigrationQueue**: Ordered queue of per-repo work items for batch phase migrations. Contains: queue_id, plan_id, items (ordered by topological dependency), current_index, completed_items, and failed_items.
- **RepoLock**: Repo-level lock preventing concurrent migration of the same repo. Contains: repo_key, session_id, acquired_at, released_at, and lock_state (active|released|stale).
- **SessionStateMachine**: Formal state machine for session status. States: idle, thinking, planning, executing, validating, awaiting_input, awaiting_approval, completed, failed. Transitions: idle→thinking, thinking→planning, planning→executing, executing→validating, validating→completed|failed|planning(retry), any→awaiting_input, awaiting_input→resume(previous_state)|idle(cancel), any→awaiting_approval, awaiting_approval→planning(approved)|failed(rejected).
- **MetricsSnapshot**: Prometheus-compatible metrics export. Contains: active_sessions, pev_cycles_total, llm_call_duration_seconds (histogram buckets), guardrail_blocks_total, tool_calls_total, session_status_counts (per status).
- **RetentionPolicy**: Data retention configuration. Session data retained for 90 days from last activity; audit logs retained indefinitely. Cleanup is a scheduled backend task that purges expired session data while preserving audit log entries.
- **RollbackRecord**: Record of resources created during a session that are eligible for rollback. Contains: session_id, resource_type (repo|workflow|secret|environment|issue|wiki_page|package), resource_name, github_org, created_at, correlation_id, and rollback_status (eligible|deleted|failed). Used when user opts into rollback on cancellation.
- **ResourceMapping**: Fixed mapping configuration for ADO→GitHub resource migration. Contains: ado_type, github_target, field_mappings (ado_field→github_field), label_mappings, and override_allowed (boolean). The planner may override with documented justification.
- **ServiceConnectionMapping**: Mapping of an ADO service connection to a GitHub target. Contains: ado_connection_name, connection_type (simple_credential|deployment_scoped), github_target_type (secret|environment), github_target_name, protection_rules (for environments), and correlation_id. Determined by the planner based on connection type.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can type "migrate Project/RepoName to GitHub" and the agent completes the full migration (repos, pipelines→workflows, Bicep→Actions, secrets, Boards→Issues, Test Plans, Artifacts→Packages, Wiki) without any intermediate user interaction for repos with complete discovery data.
- **SC-002**: The agent loop continues autonomously through Planner → Executor → Validator cycles until the migration is complete or the retry limit is reached — no single-cycle termination.
- **SC-003**: The orchestrator correctly classifies user intent (general chat vs migration info vs migration action) with 95% accuracy for unambiguous inputs. "Unambiguous" is defined as inputs containing explicit migration verbs (migrate, move, convert, transfer) and repo identifiers. Accuracy is measured against a labeled test corpus of 50+ inputs stored in `tests/unit/test_011_orchestrator.py`.
- **SC-004**: The executor performs only operations specified in the approved plan — zero unauthorized GitHub write operations pass the guardrail layer.
- **SC-005**: The validator detects 100% of workflow syntax errors, missing secrets, and git HEAD SHA mismatches before reporting migration success.
- **SC-006**: No secret values appear in chat messages, agent transcripts, audit logs, or UI — verified by automated scanning of all output channels.
- **SC-007**: The chat interface shows only orchestrator user-facing messages — internal agent-to-agent communications are not visible to the user.
- **SC-008**: The agent gracefully handles LLM unavailability, ADO rate limits, and GitHub permission errors without crashing or leaving the session in an unrecoverable state.
- **SC-009**: All agent operations are traceable in the audit log with correlation to session, plan, and run identifiers — 100% audit coverage.
- **SC-010**: The agent can resume a partially completed migration from the last successful checkpoint rather than restarting from scratch.
- **SC-011**: Bicep/ARM templates that can be automatically transformed (supported constructs: deploy, parameters, variables, resource, output, environment references) produce syntactically valid GitHub Actions workflows. Constructs outside the supported list are attempted via LLM best-effort transformation with web search. Constructs that cannot be transformed are documented as gaps with the specific construct name and suggested manual conversion, not silently skipped.
- **SC-012**: The agent loop respects configurable iteration limits (default: 20 total, 3 PEV retries) and escalates to the user with a clear summary when limits are reached.
- **SC-013**: Agent sessions survive a backend server restart and can resume mid-migration from the last persisted PEV cycle state — verified by killing the server mid-migration and confirming the session resumes correctly on restart.
- **SC-014**: The backend supports up to 10 concurrent agent sessions without session state corruption or cross-session interference — verified by running 10 simultaneous migration sessions and confirming each completes independently.
- **SC-015**: A user can request "migrate all repos in wave 1" (50+ repos) and the agent processes all repos sequentially through the PEV loop with a migration queue — no manual per-repo invocation required.
- **SC-016**: LLM context window usage stays within model limits during a 50+ repo batch migration — the sliding window with structured summaries prevents token overflow errors.
- **SC-017**: The executor migrates all ADO resource types: repos, pipelines, secrets, service connections, Boards (work items→Issues), Test Plans, Artifacts, and Wiki — each resource type has dedicated work items in the plan and validation in the validator.
- **SC-018**: The session state machine rejects all invalid state transitions — verified by attempting disallowed transitions (e.g., completed→executing) and confirming they are rejected with an error.
- **SC-019**: LLM calls that exceed 60 seconds trigger the timeout protocol (retry once, then degrade) — verified by simulating a slow LLM response and confirming the session pauses with a user-facing message.
- **SC-020**: Two concurrent sessions attempting to migrate the same repo are detected — the second session receives a clear message that the repo is locked, and no concurrent write operations occur.
- **SC-021**: The `/metrics` endpoint exposes Prometheus-compatible metrics and the `/health` endpoint reports agent-specific status — verified by querying both endpoints and confirming expected fields are present.
- **SC-022**: The executor routes existing operations through the accelerator and new resource types (service connections, Boards, Test Plans, Artifacts, Wiki) via direct API calls — verified by inspecting tool call logs and confirming the correct routing per resource type.
- **SC-023**: All entity IDs are UUID v4 format — verified by inspecting generated session, plan, run, queue, lock, and message IDs and confirming they match the UUID v4 format.
- **SC-024**: Session data older than 90 days is eligible for cleanup while audit logs are retained indefinitely — verified by inserting a session with a 100-day-old timestamp and confirming it is purged while its audit log entries remain.
- **SC-025**: The UI shows distinct states for LLM unconfigured (setup prompt), empty discovery (scan prompt), and errors (actionable summary) — verified by simulating each state and confirming the correct UI is displayed.
- **SC-026**: A user requesting migration with empty discovery data gets an automatic discovery scan — verified by clearing discovery data, requesting migration, and confirming the scan runs automatically before planning begins.
- **SC-027**: ADO Boards work items are migrated to GitHub Issues via CSV import with fixed field mapping — verified by migrating a test project with work items and confirming issues are created with correct titles, labels, and assignees.
- **SC-028**: ADO Test Plans are migrated to GitHub Issues with test-case/test-suite labels and milestones — verified by migrating a test plan and confirming issues have correct labels and milestone assignments.
- **SC-029**: ADO Artifacts are migrated to GitHub Packages for supported types (npm, NuGet, Docker, Maven, PyPI) and unsupported types are documented as gaps — verified by migrating a mix of supported and unsupported packages.
- **SC-030**: ADO Wiki is migrated to GitHub Wiki per-repo — verified by migrating a project with wiki content and confirming the GitHub wiki is enabled and contains the migrated markdown pages.
- **SC-031**: When a user cancels a migration, the orchestrator presents rollback and stop options — verified by cancelling mid-migration and confirming both options are presented with a summary of created resources.
- **SC-032**: If the user opts into rollback, all GitHub resources created during the session are deleted — verified by creating resources during a migration, cancelling with rollback, and confirming all session-created resources are deleted while pre-existing resources are preserved.
- **SC-033**: ADO service connections are migrated to GitHub secrets (simple credential-based) and GitHub environments (deployment-scoped) — verified by migrating a project with both connection types and confirming the correct GitHub target is used per connection type.
- **SC-034**: The executor detects already-migrated repos and prompts the user with overwrite/skip/abort options — verified by attempting to migrate a repo that was previously migrated and confirming the prompt appears with all three options.
- **SC-035**: The validator validates workflows locally only (YAML syntax + local simulation) and does NOT dispatch actual workflows on GitHub — verified by inspecting validator API calls and confirming no workflow dispatch events are sent to GitHub.

## Assumptions

- The existing LLM provider abstraction (`ado2gh/agents/llm_provider.py`) supports the OpenAI-compatible chat completions API and can be reused for all four agent roles with different system prompts.
- The existing tool catalog (`ado2gh/agents/local/tool_catalog.py`) can be extended with role-based access levels and guardrail interceptors without a complete rewrite.
- The existing discovery data model provides sufficient information (repos, pipelines, dependencies, service connections) for the planner to build complete migration plans without additional ADO API calls in most cases.
- The existing GEI-based migration tooling and pipeline conversion logic in the accelerator can be invoked by the executor as tools, enabling reuse of proven migration code.
- The existing session management infrastructure in `services/agent/main.py` (in-memory session store) will be replaced with persistent session storage backed by SQLite/PostgreSQL (per `ADO2GH_STORAGE_BACKEND`) to support server restart recovery and checkpoint/resume.
- Bicep/ARM template transformation to GitHub Actions uses a hybrid approach: a supported construct list (deploy, parameters, variables, resource for common Azure resource types, output, environment references) is automatically transformed. For constructs outside the supported list, the executor LLM attempts best-effort transformation and may use web search tools to look up documentation. Constructs that cannot be transformed are reported as gaps with the specific construct name and suggested manual conversion steps.
- ADO service connections are migrated to GitHub secrets (simple credential-based connections) and GitHub environments (deployment-scoped connections with protection rules). The planner determines the mapping based on connection type.
- The executor implements idempotency via detect-and-prompt: when a repo was already migrated, the executor asks the orchestrator to prompt the user with overwrite/skip/abort options. This prevents duplicate resource creation in batch migrations.
- The validator validates workflows locally only (YAML syntax + local simulation via `act` or similar). The validator MUST NOT dispatch or run the actual workflow on GitHub. If local simulation tools are unavailable, the validator falls back to YAML syntax validation only.
- The existing dynamic form system (form_id, fields, types) is sufficient for all user interaction needs — no new form types are required.
- The existing audit infrastructure (IdeAuditBridge) can be extended to log agent-to-agent communications and guardrail decisions.
- The agent loop runs asynchronously on the backend; the UI polls session status at a reasonable interval (800ms-1s) to show progress without overwhelming the server.
- The backend supports up to 10 concurrent agent sessions via asyncio. LLM API rate limits are per-key, not per-session, so concurrent sessions share the same rate limit budget.
- Inter-agent messages use structured JSON with typed fields (not free-form text) to enable deterministic parsing by the executor and field-by-field comparison by the validator.
- Batch phase migration (50+ repos) is in scope for v1. The planner generates a migration queue with topological ordering; the executor processes repos sequentially. Parallel execution within a single session is deferred to v2.
- LLM context window management uses a sliding window: last 2 PEV cycles in full detail plus a running JSON summary of prior cycles. Full message history is persisted in the session store for audit and never lost.
- The same LLM model is used for all four agents — no model routing or per-agent model selection is needed in v1.
- The existing `session_orchestrator.py` (2281 lines) will be significantly refactored or rewritten to implement the continuous loop architecture — the current single-turn tool-call pattern is insufficient.
- The existing `pev_coordinator.py` review functions will be refactored to support the continuous feedback loop between validator and planner, rather than the current one-shot review pattern.
- The existing `AgentChat.tsx` component will be simplified to hide internal agent messages and show only orchestrator user-facing messages, with an optional expandable details panel for power users.
- v1 includes migration of all ADO resource types: repos, pipelines, secrets, service connections, Boards (work items→GitHub Issues), Test Plans, Artifacts (feeds→GitHub Packages), and Wiki. Each resource type requires dedicated tool implementations in the executor and validation logic in the validator.
- The session status field follows a formal state machine with enforced transitions. The backend rejects invalid transitions. Resume-after-restart uses the persisted state to determine where to continue.
- LLM calls have a 60-second per-call timeout with 1 retry. On second timeout, the session is paused with a user-facing message. This prevents the async loop from hanging indefinitely.
- Repo-level locks in the session DB prevent concurrent migration of the same repo across sessions. Locks survive server restarts and are cleaned up on restart with audit logging.
- The agent backend exposes a Prometheus-compatible `/metrics` endpoint and an agent-specific `/health` endpoint for operational monitoring in enterprise deployments.
- The executor uses a hybrid API routing strategy: existing operations (discovery, GEI, pipeline conversion) route through the accelerator service; new resource types (service connections, Boards, Test Plans, Artifacts, Wiki) use direct ADO/GitHub API calls with profile PATs. This avoids rewriting the accelerator while enabling new resource types.
- All entity IDs use UUID v4, consistent with the existing codebase. Traceability is achieved via correlation fields (session_id, plan_id, run_id) in the audit log rather than human-readable IDs.
- Session data is retained for 90 days from last activity; audit logs are retained indefinitely for compliance. A scheduled cleanup task purges expired session data.
- The chat interface shows distinct UI states for LLM unconfigured, empty discovery, and error conditions — each with actionable guidance (setup prompt, scan button, retry/contact options).
- When discovery data is empty and a user requests migration, the orchestrator auto-triggers a discovery scan and proceeds to planning without requiring manual tab switching. This aligns with the hands-off migration value proposition.
- ADO Boards (work items) are migrated to GitHub Issues via the GitHub Issues Import API using CSV export with fixed field mapping. This is deterministic, bulk-efficient, and reproducible.
- ADO Test Plans are migrated to GitHub Issues with test-case/test-suite labels and milestones. ADO Artifacts are migrated to GitHub Packages for supported types (npm, NuGet, Docker, Maven, PyPI); unsupported types are documented as gaps. ADO Wiki is migrated to GitHub Wiki per-repo. The planner may override these mappings per-migration with documented justification.
- When a user cancels a migration, the orchestrator presents two options: rollback (delete all session-created GitHub resources) or stop (complete current operation and halt). Rollback is not automatic — it is a user choice. All rollback deletions go through the guardrail layer and are audited.
- Spec 010 (Enterprise Audit Simplification) executes before spec 011. 010 decomposes session_orchestrator.py into modules under 800 lines. 011 rewrites those decomposed modules for the continuous loop architecture, producing new modules that also satisfy the 800-line limit.
