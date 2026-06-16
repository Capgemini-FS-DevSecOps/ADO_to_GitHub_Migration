# Feature Specification: Local Agent Development in the IDE

**Feature Branch**: `003-local-agent-ide`

**Created**: 2026-06-16

**Status**: Clarified

**Input**: Agents should also be able to run locally using this same framework (Spec Kit) or another framework that allows developers to run agentic AI locally on VS Code or another IDE.

## Clarifications

### Session 2026-06-16

- Q: Must developers sign in via platform login before using the local IDE agent? → A: Optional auth via configuration—**disabled by default** for documented local dev profiles; **enabled** for prod-like Docker Compose and Kubernetes profiles.
- Q: Which local integration architecture is canonical for IDE agents? → A: **Both paths**—agent HTTP service for full PEV sessions; MCP-style tool bridge for direct IDE tool calls (shared tool catalog).
- Q: What runs in lightweight local mode vs full mocks? → A: **Minimal real accelerator** (SQLite, no Redis/worker); stub or skip optional services (LLM, job queue).
- Q: Default LLM for local agent development? → A: **Stub LLM by default**; cloud LLM optional via environment configuration.
- Q: Where are local IDE agent actions audited? → A: **Same platform audit store** (`audit_events` in StateDB—SQLite or Postgres per profile).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Migration Agent Locally (Priority: P1)

A developer clones the repository and runs the migration agent on their machine from their IDE without deploying to a shared environment. They can execute planning, dry-run migration steps, and validation against a local or containerized backend with clear feedback in the IDE.

**Why this priority**: Local execution is the fastest path to build, debug, and demo agent behavior before production rollout.

**Independent Test**: Fresh clone → follow documented local start steps → invoke agent from IDE → receive plan and dry-run results without cloud dependencies.

**Acceptance Scenarios**:

1. **Given** a developer workstation with repository dependencies installed, **When** they start the documented local agent stack, **Then** the agent accepts requests and returns plan/execute/validate outcomes in dry-run mode by default.
2. **Given** the local agent is running, **When** the developer triggers a migration-related task from the IDE, **Then** progress and errors appear in the IDE or linked terminal without requiring the production web UI.
3. **Given** live (non-dry-run) actions are requested, **When** approval rules apply, **Then** the agent pauses for explicit human approval before mutating targets.

---

### User Story 2 - Spec Kit Workflow for Agent Features (Priority: P1)

A developer uses the same Spec Kit workflow (specify → plan → tasks → implement) to add or change agent capabilities. Feature specs, plans, and tasks for agent work live alongside application code so agent evolution is governed like any other product feature.

**Why this priority**: The user explicitly requires agents to run within the Spec Kit framework; consistency reduces drift between agent behavior and documented requirements.

**Independent Test**: Create a small agent enhancement via `/speckit-specify` through `/speckit-implement` → verify resulting agent behavior matches the spec acceptance scenarios.

**Acceptance Scenarios**:

1. **Given** a new agent capability described in a feature spec, **When** the developer completes the Spec Kit pipeline, **Then** implementation artifacts reference the spec and plan paths in the repository.
2. **Given** an existing agent feature spec, **When** the developer runs analyze/clarify commands, **Then** gaps between spec and current agent behavior are identifiable before merge.
3. **Given** agent skill definitions in the repository, **When** a developer reads them, **Then** they describe planner, executor, and validator responsibilities aligned with product specs.

---

### User Story 3 - IDE Integration (Cursor, VS Code, and peers) (Priority: P1)

A developer uses Cursor, VS Code, or another compatible IDE to converse with the agent, invoke tools, and review migration context (assignments, gates, readiness) from the editor without switching exclusively to a separate web console.

**Why this priority**: IDE-native agent use is the core user request—developers work where they already write and review code.

**Independent Test**: Configure IDE per quickstart → agent chat or command palette invokes local agent → tool results render in IDE session.

**Acceptance Scenarios**:

1. **Given** supported IDE configuration files in the repo, **When** the developer opens the project, **Then** agent commands/skills are discoverable from the IDE agent interface.
2. **Given** an active IDE session, **When** the developer asks for a phase plan or dry-run migration, **Then** the agent uses approved local tools only (no arbitrary shell or undisclosed actions).
3. **Given** the developer uses a different supported IDE, **When** they follow the alternate IDE setup section, **Then** the same agent capabilities are available with equivalent outcomes.

---

### User Story 4 - Pluggable Local Agent Host (Priority: P2)

A developer who prefers an alternative local agent host (not the default IDE agent UI) can connect the same migration tool surface through a documented, versioned integration contract so behavior stays consistent across hosts.

**Why this priority**: User allows Spec Kit or another framework; interoperability prevents lock-in to a single IDE vendor.

**Independent Test**: Connect alternate local host using published integration guide → execute same dry-run scenario as Story 1 → comparable results and audit entries.

**Acceptance Scenarios**:

1. **Given** the integration contract document, **When** a third-party local agent host registers available tools, **Then** tool names and inputs match the contract without private forks.
2. **Given** two different local hosts, **When** both invoke the same dry-run migration, **Then** outcomes and guardrails (dry-run default, approval gates) are equivalent.
3. **Given** a host that does not support a optional capability, **When** it connects, **Then** the developer sees a clear capability matrix (supported vs degraded mode).

---

### User Story 5 - Lightweight Local Mode (Priority: P3)

A developer on a constrained laptop can run a minimal local profile (core agent + stub or single backend service) to iterate on prompts, skills, and planner logic without starting the full multi-service production topology.

**Why this priority**: Lowers barrier for agent authors; not every change requires full Docker Compose.

**Independent Test**: Start lightweight profile → run planner/validator unit paths → no requirement for full Redis/worker topology.

**Acceptance Scenarios**:

1. **Given** lightweight mode documentation, **When** the developer starts it, **Then** agent planning and validation run against a **real local accelerator** (SQLite) with Redis/worker omitted; optional services (LLM, job queue) use stubs or degraded behavior clearly labeled in the IDE.
2. **Given** full stack is later started, **When** the developer switches profiles, **Then** the same agent session configuration works without reauthoring skills.

---

### Edge Cases

- Local accelerator or storage backend is unreachable: agent reports actionable connection guidance, not opaque failures.
- Developer attempts live migration without approver role: blocked with same rules as hosted environment (when platform auth is enabled); when auth is disabled for local dev, live mutations still require explicit in-session approval per CA-002.
- Platform login on local dev: skipped by default (`ADO2GH_AUTH_ENABLED=false` profile); prod-like local profiles require login before IDE agent can call protected backend routes.
- Secrets referenced in prompts: never echoed back in IDE chat or local logs.
- Concurrent local sessions: no cross-session credential leakage; sessions are isolated.
- LLM provider unavailable: agent uses stub LLM by default in local profiles; when cloud LLM is configured but unreachable, degrade to stub with visible “LLM unavailable” state in IDE.
- OS differences (Windows, macOS, Linux): documented paths for local start; one primary reference OS per quickstart with notes for others.

## Requirements *(mandatory)*

### Constitution Alignment

This feature extends the agent execution path; migration safeguards apply:

- **CA-001**: Local agent MUST default to dry-run/preview for migration mutations; live execution requires explicit approval consistent with hosted behavior.
- **CA-002**: Destructive local actions (live migrate, rollback, cleanup) MUST require human confirmation or approver workflow—not silent execution from IDE chat.
- **CA-003**: Tokens, passwords, and connection secrets MUST NOT appear in IDE transcripts, local log files, or agent skill examples committed to the repo.
- **CA-004**: Local agent sessions that perform migration actions MUST produce auditable records in the **same platform audit store** as hosted runs (`audit_events` in the configured StateDB backend), including session id, actor, action, and outcome.

### Functional Requirements

- **FR-001**: The product MUST provide a documented way to run migration agents on a developer machine without a shared cloud deployment.
- **FR-002**: Local agent execution MUST support the planner → executor → validator flow described in the agentic migration platform spec, scoped to assignments and phases where configured.
- **FR-003**: Spec Kit MUST remain the primary workflow for specifying, planning, tasking, and implementing changes to agent behavior within this repository.
- **FR-004**: The repository MUST include IDE-oriented entry points (commands, skills, or equivalent) so developers can invoke agent capabilities from Cursor or VS Code without custom one-off scripts per developer.
- **FR-005**: The repository MUST document setup for at least one additional IDE or editor that supports the same integration pattern (e.g., VS Code when Cursor is primary).
- **FR-006**: A versioned integration contract MUST define how external local agent hosts discover tools, required inputs, dry-run defaults, and approval semantics. The contract MUST cover **both** the agent HTTP session API and the MCP-style tool bridge, with identical tool names and guardrails.
- **FR-006a**: Direct IDE tool invocation MUST use the MCP-style bridge to the accelerator; full planner → executor → validator sessions MUST use the local agent HTTP service.
- **FR-007**: Local agents MUST use the same tool allowlist and role rules as hosted agents (no additional privileged tools in local mode).
- **FR-008**: Developers MUST be able to point the local agent at a local migration backend or a remote accelerator URL via configuration—not hardcoded endpoints.
- **FR-009**: Local start documentation MUST cover full-stack (containers) and lightweight profiles, with explicit capability differences. Lightweight profile MUST run a **minimal real accelerator** (SQLite backend) without Redis/worker; optional services (LLM, job queue) MAY be stubbed.
- **FR-010**: When the local backend is unavailable, the agent MUST surface connection and remediation steps to the developer within the IDE session.
- **FR-011**: Agent skills and prompts in the repository MUST be versioned artifacts referenced by specs/plans (traceable to Spec Kit features).
- **FR-012**: Local development MUST NOT require committing personal credentials; configuration uses environment variables or local secret files excluded from version control.
- **FR-013**: Platform login for local IDE agent use MUST be **configurable**: disabled by default on documented local dev profiles; enabled on prod-like Docker Compose and Kubernetes profiles (aligned with login-bootstrap feature).
- **FR-014**: Local agent profiles MUST default to a **stub LLM** (no cloud API key required); cloud LLM MUST be optional via environment configuration for developers who want live reasoning.
- **FR-015**: Local migration actions invoked through either integration path MUST write audit events to the **same platform audit store** as hosted runs (not a separate dev-only log).

### Key Entities

- **LocalAgentProfile**: Named runtime profile (`full` vs `lightweight`), backend endpoints, dry-run default, LLM availability mode. Lightweight = accelerator + SQLite only; full = Compose stack including Redis/worker.
- **IDESession**: Developer’s active IDE agent conversation bound to a profile and optional assignment context.
- **ToolContract**: Published catalog of invocable migration operations, inputs, outputs, and approval requirements—**shared identically** across agent HTTP sessions and MCP-style tool bridge.
- **AgentSkillPack**: Versioned markdown or structured instructions for planner, executor, and validator roles aligned to specs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new developer completes local agent setup and a successful dry-run migration inquiry in under 15 minutes using only repository documentation.
- **SC-002**: 100% of migration mutation tools invoked from local IDE default to dry-run until explicit approval is recorded.
- **SC-003**: The same dry-run acceptance scenario passes in Cursor and at least one alternate documented IDE/host without changing migration business rules.
- **SC-004**: Agent feature changes delivered through Spec Kit include traceable links from spec acceptance scenarios to shipped skills or behavior in the same release.
- **SC-005**: Zero occurrences of credential material in local agent transcripts, sample configs, or **audit event payloads** across the documented test checklist.
- **SC-006**: When the backend is down, 90% of pilot developers (internal dogfood) report they can resolve startup issues using only the error guidance shown in the IDE session.

## Assumptions

- Primary audience is internal developers and partner engineers extending migration agents, not end-customer operators in production.
- Cursor is the reference IDE; VS Code is the required secondary path due to shared extension/agent models.
- “Another framework” means alternate local agent hosts that can consume the same tool contract via **either** the agent HTTP service or the MCP-style bridge—not replacing Spec Kit for product specification inside this repo.
- Local LLM defaults to **stub/offline** for all local profiles; cloud LLM is opt-in via environment variables (hyperscaler choice remains environment-specific).
- Hosted web UI remains available; local IDE path complements rather than replaces the migration console.
- Docker Compose from the repository is the reference full-stack local backend; lightweight mode runs accelerator + SQLite only (no Redis/worker), with stubs for optional LLM and queue services.
- Platform user login is optional for local dev (default off); prod-like local deployments enable login to mirror hosted security.
