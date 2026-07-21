# Feature Specification: Unified Migration UI

**Feature Branch**: `008-migration-ui-refactor`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "I need to refactor the migration agent such that the tabs in the front end are combined. I need to combine Discovery, Readiness, Workflows, Validation. During the startup phase I need to remove the scan portion and assignment into different waves. Scanning should just get all the information from all the ADO organizations and add them into a tab. The UI needs to be streamlined. The agent screen should take up the entire page and be clean. There should be no pipeline or any PEV indication happening in the screen it should look like Claude Code essentially. The accelerator needs to be extensible - do not have it assign repos to waves like it currently does, instead it should just migrate a repo and its dependencies from ADO to GitHub if a user specifies it in the Migrate tab. Users can bulk migrate by assigning repos i n the UI to a migration wave (can be custom named). Scanning/discovery should be intelligent enough such that it has a topological graph of the dependencies so the LLM knows what needs to be specified before (which it will display as a form to the user) before doing the full migration."

## Clarifications

### Session 2026-06-23 (clarification)

- Q: Dependency graph depth and complexity? → A: **Full transitive dependency graph (all dependencies recursively)** — agent and accelerator should migrate full end-to-end.
- Q: Wave migration strategy? → A: **Sequential execution (one wave at a time)** — dependencies that are repositories must be validated and included in the wave if a repo depends on another repo.
- Q: Pre-migration form required fields? → A: **Required fields: target GitHub organization, team/permission mapping, pipeline configuration; optional fields: repo description, topics, labels** — captures essential configuration while allowing optional metadata enrichment.
- Q: Scan result persistence? → A: **Persist scan results with manual refresh option** — allows users to work with discovered data without re-scanning every session, with refresh button for stale data.
- Q: Agent interface migration status visibility? → A: **Progress shown in conversation as agent messages** — migration progress visible as part of the agent conversation rather than separate UI elements.
- Q: Local stack definition for performance target (SC-008)? → A: **Full stack ready time (including database and dependencies)** — time from `docker compose up` or equivalent start command to all services being ready to accept requests, including database initialization and dependency loading.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unified Tab Structure (Priority: P1)

A platform admin accesses the migration accelerator UI and sees a streamlined interface with combined tabs that replace the previous separate Discovery, Readiness, Workflows, and Validation sections. The new structure organizes migration activities into a coherent workflow without redundant navigation.

**Why this priority**: This is the foundational UI change that enables all other improvements. The current fragmented tab structure creates cognitive overhead and complicates the migration workflow.

**Independent Test**: Admin opens the migration UI and sees the new unified tab structure with combined functionality. Navigation between migration activities is streamlined without losing access to any existing capabilities.

**Acceptance Scenarios**:

1. **Given** the migration accelerator UI is loaded, **When** the admin views the main navigation, **Then** they see a unified tab structure that combines Discovery, Readiness, Workflows, and Validation into a coherent workflow
2. **Given** the admin is viewing the unified interface, **When** they navigate between migration activities, **Then** the context is preserved and navigation is intuitive without redundant page loads

---

### User Story 2 - Streamlined Agent Interface (Priority: P1)

A platform admin uses the agent chat interface and sees a clean, full-page experience similar to Claude Code. The interface focuses on the conversation without pipeline status indicators, PEV (Plan-Execute-Validate) progress displays, or other operational clutter.

**Why this priority**: The current agent interface is cluttered with operational details that distract from the core conversation experience. A clean, Claude Code-like interface improves usability and focus.

**Independent Test**: Admin opens the agent chat screen and sees a full-page, clean interface with no pipeline or PEV indicators. The experience is comparable to Claude Code's minimalist design.

**Acceptance Scenarios**:

1. **Given** the agent chat interface is displayed, **When** the admin views the screen, **Then** the agent conversation takes up the entire page with no pipeline status or PEV indicators visible
2. **Given** the admin is interacting with the agent, **When** migration operations are in progress, **Then** operational details are hidden or minimized to maintain a clean conversational interface

---

### User Story 3 - Simplified Scanning and Discovery (Priority: P1)

A platform admin initiates a scan of their Azure DevOps organizations. The system retrieves all repository and pipeline information from all configured ADO organizations and presents it in a unified discovery tab without automatically assigning repositories to migration waves.

**Why this priority**: The current startup scan and wave assignment process is rigid and inflexible. Simplifying scanning to just gather information enables more flexible migration planning.

**Independent Test**: Admin triggers a scan, the system retrieves all ADO organization data, and the results appear in a discovery tab without wave assignments. The admin can then manually organize repos for migration.

**Acceptance Scenarios**:

1. **Given** the admin has configured ADO organization credentials, **When** they initiate a scan, **Then** the system retrieves all repository and pipeline information from all configured organizations
2. **Given** the scan completes, **When** the admin views the discovery tab, **Then** all discovered repositories and their metadata are displayed without automatic wave assignments

---

### User Story 4 - On-Demand Migration with Dependencies (Priority: P1)

A platform admin selects a repository in the Migrate tab and initiates migration. The system migrates the repository and its dependencies from ADO to GitHub as a single operation, without requiring pre-assignment to a wave.

**Why this priority**: This makes the accelerator more extensible by removing the rigid wave assignment requirement. Admins can migrate individual repos and their dependencies on demand.

**Independent Test**: Admin selects a repo in the Migrate tab, clicks migrate, and the system migrates the repo plus all its dependencies in one operation. No wave assignment is required.

**Acceptance Scenarios**:

1. **Given** the admin is viewing the Migrate tab with discovered repositories, **When** they select a repository and initiate migration, **Then** the system migrates the repository and its dependencies from ADO to GitHub
2. **Given** a migration is in progress, **When** the system processes dependencies, **Then** all dependent repositories are migrated as part of the same operation without requiring separate wave assignments

---

### User Story 5 - Bulk Migration with Custom Waves (Priority: P2)

A platform admin wants to migrate multiple repositories in a coordinated batch. They assign repositories to a custom-named migration wave in the UI and initiate bulk migration. The system processes all repos in the wave according to their dependency order.

**Why this priority**: Bulk migration is essential for large-scale migrations. Custom wave names provide flexibility for organizing migration campaigns.

**Independent Test**: Admin assigns multiple repos to a custom-named wave (e.g., "Q3 Production Migration"), initiates the wave, and all repos are migrated in dependency order. The wave name is preserved for tracking.

**Acceptance Scenarios**:

1. **Given** the admin is viewing the Migrate tab, **When** they assign multiple repositories to a custom-named wave, **Then** the wave is created with the specified name and repositories are associated with it
2. **Given** a custom wave has been defined, **When** the admin initiates migration for the wave, **Then** all repositories in the wave are migrated in dependency order according to the topological graph

---

### User Story 6 - Intelligent Dependency Graph with Pre-Migration Form (Priority: P2)

A platform admin initiates migration for a repository with complex dependencies. The system analyzes the dependency graph and displays a form showing what needs to be specified (e.g., target organization, team mapping, pipeline configuration) before the full migration proceeds. The LLM uses this information to guide the migration.

**Why this priority**: Complex migrations require pre-configuration. The dependency-aware form ensures admins provide necessary information before migration begins, reducing failures and rework.

**Independent Test**: Admin selects a repo with dependencies, the system displays a pre-migration form with required fields based on the dependency analysis. Admin completes the form and migration proceeds with correct configuration.

**Acceptance Scenarios**:

1. **Given** the admin selects a repository for migration, **When** the system analyzes the dependency graph, **Then** it displays a form showing all required specifications based on the dependencies (target org, team mappings, pipeline configs)
2. **Given** the pre-migration form is displayed, **When** the admin completes the required fields, **Then** the LLM uses this information to execute the migration with correct configuration

---

### Edge Cases

- What happens when scanning fails for some ADO organizations but succeeds for others?
- How does the system handle circular dependencies in the dependency graph?
- What happens when a user attempts to migrate a repository that is already in progress in another wave?
- How does the system handle ADO credential expiration during a long-running migration?
- What happens when the dependency graph is too large to display effectively in the UI?
- How does the system handle migration failures mid-wave for some repositories but not others?

## Requirements *(mandatory)*

### Constitution Alignment *(mandatory for migration-execution features)*

When this feature touches migration run, cleanup, rollback, or gate override:

- **CA-001**: Operators MUST have a dry-run or preview path before irreversible actions
- **CA-002**: Destructive actions MUST require explicit confirmation or documented override with reason
- **CA-003**: Secrets MUST NOT appear in logs, reports, or persisted artifacts
- **CA-004**: State changes MUST be auditable (state DB, structured logs, or reports)

### Functional Requirements

- **FR-001**: System MUST combine Discovery, Readiness, Workflows, and Validation tabs into a unified navigation structure with two tabs: "Discovery" (combining discovery + readiness) and "Migrate" (combining workflows + validation + migration execution)
- **FR-002**: System MUST display the agent chat interface as a full-page, clean interface where the conversation fills the entire viewport with no sidebar, no header bar, no pipeline status indicators, and no PEV progress displays
- **FR-003**: System MUST retrieve all repository and pipeline information from all configured ADO organizations during scan without automatic wave assignment
- **FR-004**: System MUST present scan results in a unified discovery tab with all discovered repositories and metadata
- **FR-005**: System MUST support on-demand migration of individual repositories and their dependencies without requiring wave assignment
- **FR-006**: System MUST allow users to assign repositories to custom-named migration waves for bulk migration
- **FR-007**: System MUST process bulk migrations in dependency order based on a topological graph
- **FR-008**: System MUST analyze dependency graphs and display pre-migration forms with required specifications before migration execution
- **FR-009**: System MUST use pre-migration form data to guide LLM-driven migration execution
- **FR-010**: System MUST maintain audit trails for all migration operations regardless of wave assignment status
- **FR-011**: System MUST provide dry-run or preview capability before executing irreversible migration actions
- **FR-012**: System MUST require explicit confirmation before executing destructive migration actions
- **FR-013**: System MUST optimize local development stack startup time to enable rapid iteration during development
- **FR-014**: System MUST report per-organization scan status when partial scan failures occur (some orgs succeed, others fail)
- **FR-015**: System MUST detect circular dependencies in the dependency graph and display a clear error message identifying the cycle
- **FR-016**: System MUST reject concurrent migration of the same repository with a 409 Conflict response
- **FR-017**: System MUST detect ADO credential expiration during long-running migrations and report the error with retry guidance
- **FR-018**: System MUST collapse or paginate dependency graph visualizations exceeding 100 nodes for UI readability
- **FR-019**: System MUST continue remaining repository migrations within a wave when individual repos fail, and report failures in the wave status

### Key Entities

- **Migration Wave**: A user-defined grouping of repositories for coordinated bulk migration with custom naming
- **Dependency Graph**: A topological representation of repository dependencies used to determine migration order and pre-migration requirements
- **Pre-Migration Form**: A dynamic form generated based on dependency analysis to collect required specifications before migration execution
- **Discovery Result**: The aggregated repository and pipeline metadata from all scanned ADO organizations

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admins can navigate the unified tab structure and complete migration workflows in under 3 minutes
- **SC-002**: The agent interface displays no pipeline or PEV indicators, maintaining a clean conversational experience
- **SC-003**: Scanning retrieves data from all configured ADO organizations within 2 minutes for organizations with up to 500 repositories
- **SC-004**: On-demand migration of a repository with dependencies completes within 5 minutes for repos with up to 20 dependencies
- **SC-005**: Bulk migration waves process repositories in correct dependency order with 100% accuracy
- **SC-006**: Pre-migration forms correctly identify all required specifications for complex dependency graphs in 95% of cases
- **SC-007**: 90% of users report improved usability compared to the previous fragmented tab interface *(post-launch metric, out of scope for initial implementation)*
- **SC-008**: Local development stack (accelerator API, agent API, UI) starts up within 30 seconds on a typical developer machine

## Assumptions

- Existing ADO credential configuration and authentication mechanisms will be reused
- The current migration execution engine can be adapted to support on-demand migration without wave assignment
- Dependency analysis can be performed using existing repository metadata and pipeline configuration
- The LLM agent can be configured to use pre-migration form data for migration guidance
- Existing audit and state persistence mechanisms will be extended to support the new workflow
- The UI framework (Next.js) supports the required full-page layout and clean interface design
- Custom wave names are user-defined strings with reasonable length limits (e.g., 1-100 characters)
- Dependency graph analysis can handle organizations with up to 1000 repositories without performance degradation
