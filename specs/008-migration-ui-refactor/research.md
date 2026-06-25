# Research: Unified Migration UI

**Feature**: 008-migration-ui-refactor
**Date**: 2026-06-23

## Technical Decisions

### Dependency Graph Analysis

**Decision**: Implement full transitive dependency graph analysis using topological sorting (Kahn's algorithm or DFS-based approach).

**Rationale**: The spec requires full transitive dependency analysis (all dependencies recursively). Topological sorting is the standard algorithm for determining dependency order and detecting circular dependencies. This approach scales to 1000 repositories as specified in the assumptions.

**Alternatives considered**:
- Limited depth (direct + 1 level) - rejected per clarification requiring full transitive analysis
- Manual dependency ordering - rejected due to scalability and error-proneness

### Scan Result Persistence

**Decision**: Persist scan results in SQLite (local) / PostgreSQL (production) with manual refresh option.

**Rationale**: The spec requires persistence with manual refresh. Using the existing storage backend (SQLite/PostgreSQL) aligns with the current architecture and provides ACID guarantees for scan results. Manual refresh gives users control over data freshness.

**Alternatives considered**:
- In-memory only - rejected as data would be lost on restart
- Auto-refresh on timer - rejected per clarification requiring manual refresh

### Wave Execution Strategy

**Decision**: Sequential wave execution with dependency validation (dependencies that are repositories must be included in the wave).

**Rationale**: Sequential execution provides predictability and easier troubleshooting. Dependency validation ensures all required repositories are included before wave execution begins, preventing partial failures.

**Alternatives considered**:
- Parallel execution - rejected per clarification requiring sequential execution
- No dependency validation - rejected as it would cause failures when dependencies are missing

### Pre-Migration Form Fields

**Decision**: Required fields: target GitHub organization, team/permission mapping, pipeline configuration. Optional fields: repo description, topics, labels.

**Rationale**: This captures essential configuration for successful migration while allowing optional metadata enrichment. Required fields ensure LLM has necessary information; optional fields provide flexibility without blocking migration.

**Alternatives considered**:
- All fields optional - rejected as it would risk migration failures
- All fields required - rejected as it would create unnecessary friction

### Local Stack Performance Optimization

**Decision**: Optimize Docker Compose startup by parallelizing service initialization, using health checks, and implementing lazy loading for non-critical services.

**Rationale**: The 30-second target requires optimization across all services (accelerator API, agent API, UI). Parallel initialization, health checks, and lazy loading are standard Docker Compose optimization techniques.

**Alternatives considered**:
- Remove services - rejected as all services are required
- Use faster hardware - rejected as this is not a software solution

## Technology Stack Decisions

### Frontend: Next.js 14

**Decision**: Continue using Next.js 14 for the migration UI.

**Rationale**: The existing UI is built on Next.js 14. Maintaining the same framework reduces migration risk and leverages existing components and patterns. Next.js 14 supports the required full-page layout and clean interface design.

**Alternatives considered**:
- React SPA - rejected as it would require significant refactoring
- Vue.js - rejected as it would introduce a new framework

### Backend: FastAPI

**Decision**: Continue using FastAPI for accelerator API and agent API services.

**Rationale**: Both existing services use FastAPI. Maintaining FastAPI ensures compatibility with existing code, middleware, and patterns. FastAPI provides async support and automatic API documentation.

**Alternatives considered**:
- Flask - rejected as it would require significant refactoring
- Django - rejected as it would introduce unnecessary complexity

### Storage: SQLite (local) / PostgreSQL (production)

**Decision**: Use SQLite for local development and PostgreSQL for production.

**Rationale**: This aligns with the existing storage backend pattern. SQLite provides zero-configuration local development; PostgreSQL provides production-grade reliability and performance.

**Alternatives considered**:
- SQLite only - rejected for production scalability
- PostgreSQL only - rejected for local development complexity

## API Design Decisions

### Discovery API

**Decision**: Create new API endpoints for scanning and discovery without wave assignment.

**Rationale**: The spec requires scanning without automatic wave assignment. New endpoints will retrieve ADO organization data and persist results without triggering wave assignment logic.

**Alternatives considered**:
- Modify existing endpoints - rejected as it would break existing functionality
- Use existing endpoints with flags - rejected as it would create complexity

### Migration API

**Decision**: Create new API endpoints for on-demand migration with dependency resolution.

**Rationale**: The spec requires on-demand migration without wave assignment. New endpoints will handle single-repo migration with automatic dependency resolution.

**Alternatives considered**:
- Use existing wave-based endpoints - rejected as it would require wave assignment
- Create separate dependency resolution service - rejected as it would add unnecessary complexity

## UI Design Decisions

### Unified Tab Structure

**Decision**: Combine Discovery, Readiness, Workflows, and Validation into a unified navigation structure.

**Rationale**: The spec requires combining these tabs. A unified structure reduces cognitive overhead and simplifies navigation. The new structure will organize migration activities into a coherent workflow.

**Alternatives considered**:
- Keep separate tabs with improved navigation - rejected as it doesn't meet the spec requirement
- Create a single-page application - rejected as it would require significant refactoring

### Agent Interface

**Decision**: Implement clean, full-page agent interface without pipeline/PEV indicators.

**Rationale**: The spec requires a Claude Code-like experience. A full-page, clean interface focuses on the conversation. Progress will be shown as agent messages rather than separate UI elements.

**Alternatives considered**:
- Keep existing indicators - rejected as it doesn't meet the spec requirement
- Use collapsible side panel - rejected per clarification requiring conversation-based progress

## Performance Optimizations

### Scan Performance

**Decision**: Implement parallel ADO organization scanning with result aggregation.

**Rationale**: The spec requires scanning within 2 minutes for 500 repositories. Parallel scanning across organizations reduces total scan time. Result aggregation ensures consistent data presentation.

**Alternatives considered**:
- Sequential scanning - rejected as it would not meet the 2-minute target
- Caching - rejected as it would complicate the manual refresh requirement

### Migration Performance

**Decision**: Implement parallel repository migration within dependency constraints.

**Rationale**: The spec requires on-demand migration within 5 minutes for 20 dependencies. Parallel migration within dependency order reduces total migration time while respecting dependencies.

**Alternatives considered**:
- Sequential migration - rejected as it would not meet the 5-minute target
- Full parallel migration - rejected as it would violate dependency order

## Security Considerations

### Credential Handling

**Decision**: Reuse existing ADO credential configuration and authentication mechanisms.

**Rationale**: The spec assumes existing credential mechanisms will be reused. This reduces security risk by leveraging existing, tested credential handling.

**Alternatives considered**:
- New credential system - rejected as it would introduce security risk
- Hard-coded credentials - rejected as it violates security best practices

### Audit Trail

**Decision**: Extend existing audit and state persistence mechanisms for new workflow.

**Rationale**: The spec requires audit trails for all migration operations. Extending existing mechanisms ensures consistency and leverages proven audit patterns.

**Alternatives considered**:
- New audit system - rejected as it would create inconsistency
- No audit trail - rejected as it violates constitution principle V

## Testing Strategy

### Unit Tests

**Decision**: Use pytest for backend unit tests with >85% coverage requirement.

**Rationale**: The constitution requires 85% coverage on the `ado2gh` package. pytest is the existing testing framework and provides the required coverage reporting.

**Alternatives considered**:
- unittest - rejected as it would require framework migration
- No unit tests - rejected as it violates constitution principle VI

### Contract Tests

**Decision**: Use contract tests for API boundaries between frontend and backend.

**Rationale**: Contract tests ensure API compatibility between frontend and backend. This is critical for the new discovery and migration APIs.

**Alternatives considered**:
- Integration tests only - rejected as they are slower and less focused
- No contract tests - rejected as it would risk API compatibility issues

### Frontend Tests

**Decision**: Use vitest for frontend component tests.

**Rationale**: vitest is the modern testing framework for Next.js/TypeScript projects. It provides fast test execution and good IDE integration.

**Alternatives considered**:
- Jest - rejected as vitest is more modern and faster
- No frontend tests - rejected as it would reduce code quality

## Migration Strategy

### Backend Migration

**Decision**: Modify existing FastAPI services (accelerator_api, agent) to add new endpoints and modify existing behavior.

**Rationale**: Both services already exist and handle migration operations. Adding new endpoints and modifying existing behavior is less risky than creating new services.

**Alternatives considered**:
- New service for discovery - rejected as it would add operational complexity
- Monolithic service - rejected as it would violate existing service boundaries

### Frontend Migration

**Decision**: Refactor existing Next.js pages to create unified tab structure and clean agent interface.

**Rationale**: The existing UI has the required components. Refactoring is less risky than rebuilding from scratch and preserves existing functionality.

**Alternatives considered**:
- Rebuild from scratch - rejected as it would waste existing work
- Incremental migration - rejected as it would prolong the transition

## Open Questions Resolved

All technical questions from the spec have been resolved through research and clarification. No open questions remain for Phase 1 design.
