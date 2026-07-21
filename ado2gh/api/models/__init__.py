"""ORM models for the unified migration UI feature (feature 008).

Provides dataclass-style models for DiscoveryResult, DependencyEdge,
MigrationWave, WaveRepository, PreMigrationForm, MigrationOperation,
and MigrationAuditEvent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class DependencyType(str, Enum):
    PIPELINE = "pipeline"
    SERVICE = "service"
    ARTIFACT = "artifact"


class WaveStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class WaveRepoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class FormStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"


class OperationType(str, Enum):
    ON_DEMAND = "on_demand"
    WAVE = "wave"


class OperationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class AuditEventType(str, Enum):
    SCAN = "scan"
    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"
    MIGRATE = "migrate"
    ROLLBACK = "rollback"


@dataclass
class DiscoveryResult:
    """Aggregated repository and pipeline metadata from a scan."""
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    repository_id: str = ""
    repository_name: str = ""
    pipeline_count: int = 0
    last_scanned_at: Optional[str] = None
    scan_status: str = ScanStatus.PENDING.value
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> tuple:
        import json
        return (
            self.id, self.organization_id, self.repository_id,
            self.repository_name, self.pipeline_count,
            self.last_scanned_at, self.scan_status,
            json.dumps(self.metadata),
        )

    @classmethod
    def from_row(cls, row: tuple) -> DiscoveryResult:
        import json
        return cls(
            id=row[0], organization_id=row[1], repository_id=row[2],
            repository_name=row[3], pipeline_count=row[4],
            last_scanned_at=row[5], scan_status=row[6],
            metadata=json.loads(row[7]) if row[7] else {},
        )


@dataclass
class DependencyEdge:
    """A dependency relationship between two repositories."""
    id: str = field(default_factory=_uuid)
    source_repository_id: str = ""
    target_repository_id: str = ""
    dependency_type: str = DependencyType.PIPELINE.value
    created_at: Optional[str] = None

    def __post_init__(self):
        if self.source_repository_id and self.target_repository_id:
            if self.source_repository_id == self.target_repository_id:
                raise ValueError("source_repository_id and target_repository_id must differ")
        if self.created_at is None:
            self.created_at = _now()

    def to_row(self) -> tuple:
        return (
            self.id, self.source_repository_id,
            self.target_repository_id, self.dependency_type,
            self.created_at,
        )

    @classmethod
    def from_row(cls, row: tuple) -> DependencyEdge:
        return cls(
            id=row[0], source_repository_id=row[1],
            target_repository_id=row[2], dependency_type=row[3],
            created_at=row[4],
        )


@dataclass
class MigrationWave:
    """A user-defined grouping of repositories for bulk migration."""
    id: str = field(default_factory=_uuid)
    name: str = ""
    description: Optional[str] = None
    status: str = WaveStatus.DRAFT.value
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_by: str = ""

    def __post_init__(self):
        if self.name and not (1 <= len(self.name) <= 100):
            raise ValueError("name must be 1-100 characters")

    def to_row(self) -> tuple:
        return (
            self.id, self.name, self.description,
            self.status, self.created_at,
            self.started_at, self.completed_at,
            self.created_by,
        )

    @classmethod
    def from_row(cls, row: tuple) -> MigrationWave:
        return cls(
            id=row[0], name=row[1], description=row[2],
            status=row[3], created_at=row[4],
            started_at=row[5], completed_at=row[6],
            created_by=row[7],
        )


@dataclass
class WaveRepository:
    """Association between a migration wave and a repository."""
    id: str = field(default_factory=_uuid)
    wave_id: str = ""
    repository_id: str = ""
    organization_id: str = ""
    migration_order: int = 0
    status: str = WaveRepoStatus.PENDING.value

    def to_row(self) -> tuple:
        return (
            self.id, self.wave_id, self.repository_id,
            self.organization_id, self.migration_order,
            self.status,
        )

    @classmethod
    def from_row(cls, row: tuple) -> WaveRepository:
        return cls(
            id=row[0], wave_id=row[1], repository_id=row[2],
            organization_id=row[3], migration_order=row[4],
            status=row[5],
        )


@dataclass
class PreMigrationForm:
    """Dynamic form generated from dependency analysis."""
    id: str = field(default_factory=_uuid)
    repository_id: str = ""
    organization_id: str = ""
    target_github_org: str = ""
    team_mapping: dict[str, Any] = field(default_factory=dict)
    pipeline_config: dict[str, Any] = field(default_factory=dict)
    repo_description: Optional[str] = None
    topics: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    form_status: str = FormStatus.DRAFT.value
    created_at: str = field(default_factory=_now)
    submitted_at: Optional[str] = None

    def to_row(self) -> tuple:
        import json
        return (
            self.id, self.repository_id, self.organization_id,
            self.target_github_org,
            json.dumps(self.team_mapping),
            json.dumps(self.pipeline_config),
            self.repo_description,
            json.dumps(self.topics),
            json.dumps(self.labels),
            self.form_status, self.created_at,
            self.submitted_at,
        )

    @classmethod
    def from_row(cls, row: tuple) -> PreMigrationForm:
        import json
        return cls(
            id=row[0], repository_id=row[1], organization_id=row[2],
            target_github_org=row[3],
            team_mapping=json.loads(row[4]) if row[4] else {},
            pipeline_config=json.loads(row[5]) if row[5] else {},
            repo_description=row[6],
            topics=json.loads(row[7]) if row[7] else [],
            labels=json.loads(row[8]) if row[8] else [],
            form_status=row[9], created_at=row[10],
            submitted_at=row[11],
        )


@dataclass
class MigrationOperation:
    """A single migration operation (on-demand or wave-based)."""
    id: str = field(default_factory=_uuid)
    repository_id: str = ""
    organization_id: str = ""
    operation_type: str = OperationType.ON_DEMAND.value
    wave_id: Optional[str] = None
    pre_migration_form_id: Optional[str] = None
    status: str = OperationStatus.PENDING.value
    dry_run: bool = False
    confirmed_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    def to_row(self) -> tuple:
        import json
        return (
            self.id, self.repository_id, self.organization_id,
            self.operation_type, self.wave_id,
            self.pre_migration_form_id, self.status,
            1 if self.dry_run else 0,
            self.confirmed_at, self.started_at,
            self.completed_at, self.error_message,
            json.dumps(self.audit_log),
        )

    @classmethod
    def from_row(cls, row: tuple) -> MigrationOperation:
        import json
        return cls(
            id=row[0], repository_id=row[1], organization_id=row[2],
            operation_type=row[3], wave_id=row[4],
            pre_migration_form_id=row[5], status=row[6],
            dry_run=bool(row[7]),
            confirmed_at=row[8], started_at=row[9],
            completed_at=row[10], error_message=row[11],
            audit_log=json.loads(row[12]) if row[12] else [],
        )


@dataclass
class MigrationAuditEvent:
    """An audit event tracking state changes for migration operations."""
    id: str = field(default_factory=_uuid)
    operation_id: str = ""
    event_type: str = AuditEventType.MIGRATE.value
    previous_state: dict[str, Any] = field(default_factory=dict)
    new_state: dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    reason: Optional[str] = None
    timestamp: str = field(default_factory=_now)

    def __post_init__(self):
        if self.event_type in (AuditEventType.REJECT.value, AuditEventType.REVOKE.value, AuditEventType.ROLLBACK.value):
            if not self.reason:
                raise ValueError(f"reason is required for {self.event_type} events")

    def to_row(self) -> tuple:
        import json
        return (
            self.id, self.operation_id, self.event_type,
            json.dumps(self.previous_state),
            json.dumps(self.new_state),
            self.user_id, self.reason, self.timestamp,
        )

    @classmethod
    def from_row(cls, row: tuple) -> MigrationAuditEvent:
        import json
        return cls(
            id=row[0], operation_id=row[1], event_type=row[2],
            previous_state=json.loads(row[3]) if row[3] else {},
            new_state=json.loads(row[4]) if row[4] else {},
            user_id=row[5], reason=row[6], timestamp=row[7],
        )
