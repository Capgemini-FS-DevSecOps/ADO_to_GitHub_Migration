# Data Model: Unified Migration UI

**Feature**: 008-migration-ui-refactor
**Date**: 2026-06-23

## Overview

This document describes the data entities and their relationships for the unified migration UI feature. The data model supports scan result persistence, dependency graph analysis, migration waves, and pre-migration forms.

## Entities

### DiscoveryResult

Represents the aggregated repository and pipeline metadata from all scanned ADO organizations.

**Fields**:
- `id`: string (UUID) - Unique identifier for the scan result
- `organization_id`: string - ADO organization identifier
- `repository_id`: string - ADO repository identifier
- `repository_name`: string - Repository name
- `pipeline_count`: integer - Number of pipelines in the repository
- `last_scanned_at`: datetime - Timestamp of the last scan
- `scan_status`: enum (pending, completed, failed) - Status of the scan
- `metadata`: JSON - Additional repository metadata (language, size, etc.)

**Relationships**:
- One-to-many with `DependencyEdge` (repository can have many dependencies)
- Many-to-one with `MigrationWave` (repository can be assigned to a wave)

**Validation Rules**:
- `repository_id` must be unique per organization
- `scan_status` must be one of the defined enum values
- `last_scanned_at` must be a valid datetime

### DependencyEdge

Represents a dependency relationship between repositories in the dependency graph.

**Fields**:
- `id`: string (UUID) - Unique identifier for the dependency edge
- `source_repository_id`: string - The repository that has the dependency
- `target_repository_id`: string - The repository being depended on
- `dependency_type`: enum (pipeline, service, artifact) - Type of dependency
- `created_at`: datetime - Timestamp when the dependency was discovered

**Relationships**:
- Many-to-one with `DiscoveryResult` (both source and target)
- Used by `DependencyGraph` for topological sorting

**Validation Rules**:
- `source_repository_id` and `target_repository_id` must be different (no self-dependencies)
- `dependency_type` must be one of the defined enum values

### MigrationWave

Represents a user-defined grouping of repositories for coordinated bulk migration with custom naming.

**Fields**:
- `id`: string (UUID) - Unique identifier for the wave
- `name`: string (1-100 characters) - Custom wave name
- `description`: string (optional) - Wave description
- `status`: enum (draft, in_progress, completed, failed) - Wave execution status
- `created_at`: datetime - Timestamp when the wave was created
- `started_at`: datetime (optional) - Timestamp when wave execution started
- `completed_at`: datetime (optional) - Timestamp when wave execution completed
- `created_by`: string - User who created the wave

**Relationships**:
- One-to-many with `WaveRepository` (wave contains many repositories)
- One-to-many with `MigrationOperation` (wave has many migration operations)

**Validation Rules**:
- `name` must be 1-100 characters
- `status` must be one of the defined enum values
- `started_at` must be after `created_at`
- `completed_at` must be after `started_at` if set

### WaveRepository

Represents the association between a migration wave and a repository.

**Fields**:
- `id`: string (UUID) - Unique identifier for the association
- `wave_id`: string - Migration wave identifier
- `repository_id`: string - Repository identifier
- `organization_id`: string - ADO organization identifier
- `migration_order`: integer - Order in which the repository should be migrated (based on dependency graph)
- `status`: enum (pending, in_progress, completed, failed) - Migration status for this repository

**Relationships**:
- Many-to-one with `MigrationWave`
- Many-to-one with `DiscoveryResult`

**Validation Rules**:
- `wave_id` and `repository_id` combination must be unique
- `migration_order` must be a positive integer
- `status` must be one of the defined enum values

### PreMigrationForm

Represents the dynamic form generated based on dependency analysis to collect required specifications before migration execution.

**Fields**:
- `id`: string (UUID) - Unique identifier for the form
- `repository_id`: string - Repository identifier
- `organization_id`: string - ADO organization identifier
- `target_github_org`: string (required) - Target GitHub organization
- `team_mapping`: JSON (required) - Team/permission mapping configuration
- `pipeline_config`: JSON (required) - Pipeline configuration
- `repo_description`: string (optional) - Repository description
- `topics`: array of strings (optional) - Repository topics
- `labels`: array of strings (optional) - Repository labels
- `form_status`: enum (draft, submitted, validated) - Form status
- `created_at`: datetime - Timestamp when the form was generated
- `submitted_at`: datetime (optional) - Timestamp when the form was submitted

**Relationships**:
- Many-to-one with `DiscoveryResult`
- Used by `MigrationOperation` for migration execution

**Validation Rules**:
- `target_github_org`, `team_mapping`, and `pipeline_config` are required
- `repo_description`, `topics`, and `labels` are optional
- `form_status` must be one of the defined enum values

### MigrationOperation

Represents a single migration operation (on-demand or wave-based).

**Fields**:
- `id`: string (UUID) - Unique identifier for the operation
- `repository_id`: string - Repository identifier
- `organization_id`: string - ADO organization identifier
- `operation_type`: enum (on_demand, wave) - Type of migration operation
- `wave_id`: string (optional) - Wave identifier if wave-based
- `pre_migration_form_id`: string - Pre-migration form identifier
- `status`: enum (pending, in_progress, completed, failed, rolled_back) - Operation status
- `dry_run`: boolean - Whether this is a dry-run operation
- `confirmed_at`: datetime (optional) - Timestamp when user confirmed the operation
- `started_at`: datetime (optional) - Timestamp when operation started
- `completed_at`: datetime (optional) - Timestamp when operation completed
- `error_message`: string (optional) - Error message if operation failed
- `audit_log`: JSON - Audit trail of state changes

**Relationships**:
- Many-to-one with `MigrationWave` (optional)
- Many-to-one with `PreMigrationForm`
- One-to-many with `AuditEvent`

**Validation Rules**:
- `operation_type` must be one of the defined enum values
- `wave_id` is required if `operation_type` is `wave`
- `pre_migration_form_id` is required
- `dry_run` must be set before operation starts
- `status` must be one of the defined enum values

### AuditEvent

Represents an audit event for tracking state changes (constitution principle V).

**Fields**:
- `id`: string (UUID) - Unique identifier for the audit event
- `operation_id`: string - Migration operation identifier
- `event_type`: enum (scan, approve, reject, revoke, migrate, rollback) - Type of event
- `previous_state`: JSON - Previous state before the event
- `new_state`: JSON - New state after the event
- `user_id`: string - User who triggered the event
- `reason`: string (optional) - Reason for the event (required for destructive actions)
- `timestamp`: datetime - Timestamp when the event occurred

**Relationships**:
- Many-to-one with `MigrationOperation`

**Validation Rules**:
- `event_type` must be one of the defined enum values
- `reason` is required for destructive events (reject, revoke, rollback)
- `timestamp` must be a valid datetime

## State Transitions

### MigrationWave Status Transitions

```
draft → in_progress → completed
draft → in_progress → failed
in_progress → completed
in_progress → failed
```

### MigrationOperation Status Transitions

```
pending → in_progress → completed
pending → in_progress → failed
in_progress → completed
in_progress → failed → rolled_back
```

### PreMigrationForm Status Transitions

```
draft → submitted → validated
draft → submitted → draft (if validation fails)
```

## Indexes

For performance optimization (SC-008: local stack startup <30s):

- `DiscoveryResult`: index on `(organization_id, repository_id)`
- `DependencyEdge`: index on `source_repository_id` and `target_repository_id`
- `MigrationWave`: index on `created_by` and `status`
- `WaveRepository`: index on `(wave_id, migration_order)`
- `MigrationOperation`: index on `repository_id` and `status`
- `AuditEvent`: index on `operation_id` and `timestamp`

## Storage Schema

### SQLite (local development)

Tables will be created with the above structure using SQLite's JSON support for JSON fields.

### PostgreSQL (production)

Tables will be created with the above structure using PostgreSQL's JSONB type for JSON fields for better query performance.

## Data Migration

Existing migration state data will be preserved during the schema migration. New tables will be created alongside existing tables to avoid breaking existing functionality.
