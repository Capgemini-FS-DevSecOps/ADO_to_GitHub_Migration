"""Abstract base class for state store backends.

Defines the shared interface that both SQLiteStateDB and PostgresStateDB
must implement.  Method signatures are declared here; each backend provides
backend-specific SQL in its concrete implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ado2gh.models import MigrationStatus, PipelineMetadata, RepoConfig


class StateDBBase(ABC):
    """Abstract base for all state store backends."""

    SCHEMA: str = ""

    # ── Initialization / connection ──────────────────────────────────────────

    @abstractmethod
    def _init_db(self) -> None: ...

    # ── Repo-scope migrations ────────────────────────────────────────────────

    @abstractmethod
    def upsert_migration(
        self,
        wave_id: int,
        repo: RepoConfig,
        scope: str,
        status: MigrationStatus,
        error: str = None,
        gh_migration_id: str = None,
        stats: dict = None,
    ) -> None: ...

    @abstractmethod
    def get_wave_migrations(self, wave_id: int) -> list[dict]: ...

    @abstractmethod
    def get_all_migrations(self) -> list[dict]: ...

    @abstractmethod
    def migration_status_counts(self) -> dict: ...

    @abstractmethod
    def get_migration_repo_counts(self) -> dict: ...

    @abstractmethod
    def get_failed_migrations(self, wave_id: int = None) -> list[dict]: ...

    @abstractmethod
    def wave_summary(self, wave_id: int) -> dict: ...

    @abstractmethod
    def mark_wave_run(
        self, wave_id: int, status: str, dry_run: bool = False,
    ) -> int: ...

    # ── Pipeline inventory ───────────────────────────────────────────────────

    @abstractmethod
    def upsert_pipeline_inventory(self, meta: PipelineMetadata) -> None: ...

    @abstractmethod
    def get_pipelines_for_repo(
        self, project: str, repo_name: str,
    ) -> list[PipelineMetadata]: ...

    @abstractmethod
    def get_all_inventory(self, project: str = None) -> list[dict]: ...

    @abstractmethod
    def inventory_count(self, project: str = None) -> int: ...

    @abstractmethod
    def inventory_count_for_repo(self, project: str, repo_name: str) -> int: ...

    @abstractmethod
    def get_latest_repo_migrations(self) -> dict[str, dict]: ...

    @abstractmethod
    def get_latest_pipeline_migrations(self) -> dict[str, dict]: ...

    @abstractmethod
    def clear_inventory(self, project: str = None) -> None: ...

    # ── Pipeline migrations ──────────────────────────────────────────────────

    @abstractmethod
    def upsert_pipeline_migration(
        self,
        wave_id: int,
        meta: PipelineMetadata,
        gh_org: str,
        gh_repo: str,
        status: MigrationStatus,
        workflow_file: str = None,
        error: str = None,
        warnings: list = None,
        unsupported: list = None,
        transform_stats: dict = None,
    ) -> None: ...

    @abstractmethod
    def get_wave_pipeline_migrations(self, wave_id: int) -> list[dict]: ...

    @abstractmethod
    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]: ...

    @abstractmethod
    def pipeline_migration_summary(self, wave_id: int) -> dict: ...

    @abstractmethod
    def reset_failed_pipeline_migrations(self, wave_id: int) -> None: ...

    # ── Risk scores ──────────────────────────────────────────────────────────

    @abstractmethod
    def prune_risk_scores_not_in(self, keys: set[tuple[str, str]]) -> int: ...

    @abstractmethod
    def upsert_risk_score(self, score) -> None: ...

    @abstractmethod
    def get_all_risk_scores(self) -> list: ...

    @abstractmethod
    def get_risk_scores_for_phase(self, phase) -> list: ...

    @abstractmethod
    def count_repos_by_phase(
        self, phase_id: str, profile_id: str | None = None,
    ) -> dict[str, int]: ...

    @abstractmethod
    def reassign_phase_repos(
        self,
        from_phase: str,
        to_phase: str,
        profile_id: str | None = None,
    ) -> dict[str, int]: ...

    @abstractmethod
    def scan_repo_scores(self, profile_id: str | None = None) -> list[float]: ...

    @abstractmethod
    def risk_score_count(self) -> int: ...

    # ── Phase gates ──────────────────────────────────────────────────────────

    @abstractmethod
    def upsert_phase_gate(self, result) -> None: ...

    @abstractmethod
    def get_phase_gate(self, phase) -> Optional[dict]: ...

    @abstractmethod
    def get_all_phase_gates(self) -> list: ...

    # ── Batch checkpoints ────────────────────────────────────────────────────

    @abstractmethod
    def upsert_batch_checkpoint(self, cp) -> None: ...

    @abstractmethod
    def get_batch_checkpoints(self, phase) -> list: ...

    @abstractmethod
    def get_last_completed_batch(self, phase) -> int: ...

    # ── Profile scan ─────────────────────────────────────────────────────────

    @abstractmethod
    def save_profile_scan(
        self,
        profile_id: str,
        raw: dict[str, Any],
        *,
        preserve_manual_assignments: bool = True,
    ) -> None: ...

    @abstractmethod
    def get_profile_scan_meta(self, profile_id: str) -> Optional[dict]: ...

    @abstractmethod
    def get_profile_scan_repos(
        self, profile_id: str, phase: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def update_profile_repo_phases(
        self, profile_id: str, assignments: list[dict[str, str]],
    ) -> int: ...

    @abstractmethod
    def build_profile_scan_payload(
        self, profile_id: str,
    ) -> Optional[dict[str, Any]]: ...

    # ── Agentic platform ─────────────────────────────────────────────────────

    @abstractmethod
    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
        created_at: str,
        assignment_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]: ...

    @abstractmethod
    def search_audit_events(self, **kwargs) -> list[dict]: ...

    @abstractmethod
    def count_audit_events(self, **kwargs) -> int: ...

    @abstractmethod
    def list_audit_event_types(
        self,
        profile_id: str | None = None,
        limit: int = 200,
        actor: str | None = None,
    ) -> list[str]: ...

    @abstractmethod
    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool: ...

    # ── Platform users / auth ────────────────────────────────────────────────

    @abstractmethod
    def count_platform_users(self) -> int: ...

    @abstractmethod
    def create_platform_user(
        self,
        user_id: str,
        username: str,
        password_hash: str,
        role: str,
        display_name: str,
        created_at: str,
        status: str = "active",
    ) -> None: ...

    @abstractmethod
    def get_platform_user_by_username(self, username: str) -> Optional[dict]: ...

    @abstractmethod
    def get_platform_user_by_id(self, user_id: str) -> Optional[dict]: ...

    @abstractmethod
    def list_platform_users(self) -> list[dict]: ...

    @abstractmethod
    def update_platform_user(self, user_id: str, **kwargs) -> bool: ...

    @abstractmethod
    def delete_auth_sessions_for_user(self, user_id: str) -> None: ...

    @abstractmethod
    def create_auth_session(
        self, token: str, user_id: str, expires_at: str, created_at: str,
    ) -> None: ...

    @abstractmethod
    def get_auth_session(self, token: str) -> Optional[dict]: ...

    @abstractmethod
    def delete_auth_session(self, token: str) -> None: ...

    # ── Live execution approvals ─────────────────────────────────────────────

    @abstractmethod
    def create_live_execution_approval(
        self,
        approval_id: str,
        requester_user_id: str,
        requester_username: str,
        scope_type: str,
        scope_id: str,
        requested_at: str,
        assignment_id: str | None = None,
        profile_id: str | None = None,
        reason_request: str | None = None,
        context_json: str | None = None,
    ) -> dict: ...

    @abstractmethod
    def get_live_execution_approval(self, approval_id: str) -> Optional[dict]: ...

    @abstractmethod
    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]: ...

    @abstractmethod
    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]: ...

    @abstractmethod
    def get_live_execution_approval_for_scope(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]: ...

    @abstractmethod
    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]: ...

    @abstractmethod
    def decide_live_execution_approval(
        self,
        approval_id: str,
        status: str,
        approver_user_id: str,
        approver_username: str,
        reason_decision: str,
        decided_at: str,
    ) -> Optional[dict]: ...
