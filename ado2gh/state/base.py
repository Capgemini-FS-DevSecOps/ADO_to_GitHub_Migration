"""Abstract base class for state store backends.

Declares the interface that ``SQLiteStateDB`` and ``PostgresStateDB`` both
implement. Signatures live here; each backend supplies its own SQL. Row
values are returned as plain dicts keyed by column name so callers do not
depend on a driver's row type.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ado2gh.models import (
    BatchCheckpoint,
    ExecutionMode,
    MigrationStatus,
    PhaseGateResult,
    PhaseType,
    PipelineMetadata,
    RepoConfig,
    RiskScore,
)
from ado2gh.state.audit_query import AuditEventFilters

if TYPE_CHECKING:
    from ado2gh.auth.models import PlatformUser


class StateDBBase(ABC):
    """Abstract base for all state store backends.

    ``SCHEMA`` holds the backend's DDL; ``_init_db`` applies it on construction.
    """

    SCHEMA: str = ""

    # ── Initialization / connection ──────────────────────────────────────────

    @abstractmethod
    def _init_db(self) -> None:
        """Create the tables in ``SCHEMA`` and apply in-place column upgrades."""

    # ── Repo-scope migrations ────────────────────────────────────────────────

    @abstractmethod
    def upsert_migration(  # noqa: PLR0913  # row key plus outcome columns; no existing record model holds a scope outcome (exception-register.md)
        self,
        wave_id: int,
        repo: RepoConfig,
        scope: str,
        status: MigrationStatus,
        error: str | None = None,
        gh_migration_id: str | None = None,
        stats: dict | None = None,
    ) -> None:
        """Insert or update one ``migrations`` row for a repository scope.

        The row is keyed by ``(wave_id, ado_project, ado_repo, scope)``.
        ``started_at`` is set the first time the status is ``IN_PROGRESS`` and
        kept on later updates; ``completed_at`` is set for terminal statuses.

        Args:
            wave_id: Wave the migration belongs to.
            repo: Source and target coordinates of the repository.
            scope: Scope name, one of ``MigrationScope`` values.
            status: New lifecycle state.
            error: Failure message for ``FAILED`` rows.
            gh_migration_id: GitHub-side migration identifier, when known.
            stats: Scope statistics, stored as JSON.
        """

    @abstractmethod
    def get_wave_migrations(self, wave_id: int) -> list[dict]:
        """Return every ``migrations`` row of a wave in insertion order."""

    @abstractmethod
    def get_all_migrations(self) -> list[dict]:
        """Return every ``migrations`` row ordered by wave then insertion."""

    @abstractmethod
    def migration_status_counts(self) -> dict[str, int]:
        """Return the number of distinct repositories per migration status."""

    @abstractmethod
    def get_migration_repo_counts(self) -> dict[str, int]:
        """Return repository and pipeline totals without loading the rows.

        Returns:
            Keys ``total_repos``, ``completed_repos``, ``failed_repos`` and
            ``total_pipelines``. A repository with both completed and failed
            scopes is counted in neither bucket.
        """

    @abstractmethod
    def get_failed_migrations(self, wave_id: int | None = None) -> list[dict]:
        """Return ``FAILED`` rows, restricted to one wave when ``wave_id`` is given."""

    @abstractmethod
    def wave_summary(self, wave_id: int) -> dict[str, dict[str, int]]:
        """Return row counts of a wave grouped by scope and then by status."""

    @abstractmethod
    def mark_wave_run(
        self, wave_id: int, status: str, mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ) -> int:
        """Record the start or end of a wave run in ``wave_runs``.

        Args:
            wave_id: Wave being run.
            status: ``"started"`` opens a new run; any other value closes the
                open run with that status.
            mode: Whether the run is a preview; persisted as the boolean
                ``dry_run`` column. Defaults to ``DRY_RUN``, so a caller
                recording a live run must say so (CA-001).

        Returns:
            The new run's row id when opening, ``-1`` when closing.
        """

    # ── Pipeline inventory ───────────────────────────────────────────────────

    @abstractmethod
    def upsert_pipeline_inventory(self, meta: PipelineMetadata) -> None:
        """Insert or refresh one ``pipeline_inventory`` row keyed by project and pipeline id."""

    @abstractmethod
    def get_pipelines_for_repo(
        self, project: str, repo_name: str,
    ) -> list[PipelineMetadata]:
        """Return the pipelines attached to a repository, deduplicated by id.

        A pipeline matches when its ``repo_name`` equals ``repo_name`` or its
        name is ``repo_name`` optionally followed by ``-`` or ``_`` and a suffix.
        """

    @abstractmethod
    def get_all_inventory(self, project: str | None = None) -> list[dict]:
        """Return inventory rows, restricted to one project when given."""

    @abstractmethod
    def inventory_count(self, project: str | None = None) -> int:
        """Return the number of inventory rows, restricted to one project when given."""

    @abstractmethod
    def inventory_count_for_repo(self, project: str, repo_name: str) -> int:
        """Return the number of distinct pipelines :meth:`get_pipelines_for_repo` would return."""

    @abstractmethod
    def get_latest_repo_migrations(self) -> dict[str, dict]:
        """Return the newest ``repo``-scope row per repository, keyed ``project:repo``."""

    @abstractmethod
    def get_latest_pipeline_migrations(self) -> dict[str, dict]:
        """Return the newest pipeline migration row per pipeline, keyed ``project:pipeline_id``."""

    # ── Pipeline migrations ──────────────────────────────────────────────────

    @abstractmethod
    def upsert_pipeline_migration(  # noqa: PLR0913  # row key plus transform outcome columns; no existing record model holds a transform outcome (exception-register.md)
        self,
        wave_id: int,
        meta: PipelineMetadata,
        repo: RepoConfig,
        status: MigrationStatus,
        workflow_file: str | None = None,
        error: str | None = None,
        warnings: list | None = None,
        unsupported: list | None = None,
        transform_stats: dict | None = None,
    ) -> None:
        """Insert or update one ``pipeline_migrations`` row.

        The row is keyed by ``(wave_id, project, pipeline_id)``; timestamps
        follow the same rules as :meth:`upsert_migration`.

        Args:
            wave_id: Wave the migration belongs to.
            meta: The pipeline being converted.
            repo: Target repository; only ``gh_org`` and ``gh_repo`` are stored.
            status: New lifecycle state.
            workflow_file: Path of the generated workflow, when produced.
            error: Failure message for ``FAILED`` rows.
            warnings: Transform warnings, stored as JSON.
            unsupported: Unsupported task names, stored as JSON.
            transform_stats: Transform statistics, stored as JSON.
        """

    @abstractmethod
    def get_wave_pipeline_migrations(self, wave_id: int) -> list[dict]:
        """Return every pipeline migration row of a wave in insertion order."""

    @abstractmethod
    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]:
        """Return a wave's pipeline rows whose status is ``failed`` or ``pending``."""

    @abstractmethod
    def pipeline_migration_summary(self, wave_id: int) -> dict[str, dict[str, int]]:
        """Return a wave's pipeline row counts under ``by_status`` and ``by_complexity``."""

    @abstractmethod
    def reset_failed_pipeline_migrations(self, wave_id: int) -> None:
        """Delete a wave's ``failed`` pipeline rows so they can be retried."""

    # ── Risk scores ──────────────────────────────────────────────────────────

    @abstractmethod
    def prune_risk_scores_not_in(self, keys: set[tuple[str, str]]) -> int:
        """Delete risk scores whose ``(project, repo_name)`` is not in ``keys``.

        Returns:
            The number of rows deleted; ``0`` when ``keys`` is empty.
        """

    @abstractmethod
    def upsert_risk_score(self, score: RiskScore) -> None:
        """Insert or refresh one ``repo_risk_scores`` row keyed by project and repository."""

    @abstractmethod
    def get_all_risk_scores(self) -> list[dict]:
        """Return every risk score row ordered by ascending total score."""

    @abstractmethod
    def get_risk_scores_for_phase(self, phase: PhaseType | str | None) -> list[dict]:
        """Return risk score rows assigned to ``phase``, or all rows when it is ``None``."""

    @abstractmethod
    def count_repos_by_phase(
        self, phase_id: str, profile_id: str | None = None,
    ) -> dict[str, int]:
        """Count repositories assigned to a phase.

        Returns:
            ``risk_scores`` (rows in ``repo_risk_scores``) and ``profile_scan``
            (rows in ``profile_scan_repos``, restricted to ``profile_id`` when
            given).
        """

    @abstractmethod
    def reassign_phase_repos(
        self,
        from_phase: str,
        to_phase: str,
        profile_id: str | None = None,
    ) -> dict[str, int]:
        """Move every repository assigned to ``from_phase`` to ``to_phase``.

        Returns:
            Rows updated, keyed like :meth:`count_repos_by_phase`.
        """

    @abstractmethod
    def scan_repo_scores(self, profile_id: str | None = None) -> list[float]:
        """Return total scores from the profile scan, falling back to risk scores when empty."""

    # ── Phase gates ──────────────────────────────────────────────────────────

    @abstractmethod
    def upsert_phase_gate(self, result: PhaseGateResult) -> None:
        """Insert or refresh the single ``phase_gates`` row of ``result.phase``."""

    @abstractmethod
    def get_phase_gate(self, phase: PhaseType) -> dict | None:
        """Return the gate row of a phase, or ``None`` when it was never checked."""

    @abstractmethod
    def get_all_phase_gates(self) -> list[dict]:
        """Return every gate row in insertion order."""

    # ── Batch checkpoints ────────────────────────────────────────────────────

    @abstractmethod
    def upsert_batch_checkpoint(self, cp: BatchCheckpoint) -> None:
        """Insert or update one ``batch_checkpoints`` row keyed by phase and batch number.

        ``started_at`` is kept from the first write; ``repos_done``, ``status``
        and ``completed_at`` are refreshed.
        """

    @abstractmethod
    def get_last_completed_batch(self, phase: PhaseType) -> int:
        """Return the highest completed batch number of a phase, or ``-1`` when none."""

    # ── Profile scan ─────────────────────────────────────────────────────────

    @abstractmethod
    def save_profile_scan(self, profile_id: str, raw: dict[str, Any]) -> None:
        """Persist a discovery scan, keeping phase assignments operators made by hand.

        Args:
            profile_id: The migration profile the scan belongs to.
            raw: The scan result as produced by discovery, with a
                ``recommendations`` mapping of phase buckets to repositories.
        """

    @abstractmethod
    def replace_profile_scan(self, profile_id: str, raw: dict[str, Any]) -> None:
        """Persist a discovery scan, discarding phase assignments operators made by hand.

        Args:
            profile_id: The migration profile the scan belongs to.
            raw: The scan result, in the same layout :meth:`save_profile_scan` takes.
        """

    @abstractmethod
    def get_profile_scan_meta(self, profile_id: str) -> dict | None:
        """Return the ``profile_scans`` row of a profile, or ``None`` when never scanned."""

    @abstractmethod
    def get_profile_scan_repos(self, profile_id: str) -> list[dict]:
        """Return a profile's scanned repositories ordered by score, project and name."""

    @abstractmethod
    def update_profile_repo_phases(
        self, profile_id: str, assignments: list[dict[str, str]],
    ) -> int:
        """Apply per-repository phase assignments.

        Args:
            profile_id: The profile whose scan is updated.
            assignments: Items with ``project``, ``repo_name`` and ``assigned_phase``.

        Returns:
            The number of rows updated.
        """

    @abstractmethod
    def build_profile_scan_payload(
        self, profile_id: str,
    ) -> dict[str, Any] | None:
        """Rebuild the scan payload the console reads, or ``None`` when never scanned."""

    # ── Agentic platform ─────────────────────────────────────────────────────

    @abstractmethod
    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
    ) -> None:
        """Append one ``audit_events`` row stamped with the current UTC time.

        Args:
            event_id: Caller-generated unique id.
            event_type: Short machine name of what happened.
            profile_id: The profile the event belongs to.
            actor: Who caused it; empty when unknown.
            payload_json: Already-redacted event details, serialised as JSON.
        """

    @abstractmethod
    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """Return the newest audit events, restricted to one profile when given."""

    @abstractmethod
    def search_audit_events(
        self, filters: AuditEventFilters, *, limit: int = 20, offset: int = 0,
    ) -> list[dict]:
        """Return one page of audit events matching ``filters``, newest first."""

    @abstractmethod
    def count_audit_events(self, filters: AuditEventFilters) -> int:
        """Return the number of audit events matching ``filters``."""

    @abstractmethod
    def list_audit_event_types(
        self,
        profile_id: str | None = None,
        limit: int = 200,
        actor: str | None = None,
    ) -> list[str]:
        """Return distinct event types, optionally restricted to a profile or actor."""

    @abstractmethod
    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        """Return whether any scope of a repository is currently ``in_progress``."""

    # ── Platform users / auth ────────────────────────────────────────────────

    @abstractmethod
    def count_platform_users(self) -> int:
        """Return the number of platform users."""

    @abstractmethod
    def create_platform_user(
        self,
        user: PlatformUser,
        *,
        password_hash: str,
        status: str,
        created_at: str,
    ) -> None:
        """Insert one ``platform_users`` row.

        Args:
            user: Identity, username, role and display name of the account.
            password_hash: The stored password hash; never the password.
            status: A ``PlatformUserStatus`` value.
            created_at: ISO-8601 creation time.
        """

    @abstractmethod
    def get_platform_user_by_username(self, username: str) -> dict | None:
        """Return the user row with this username, or ``None``."""

    @abstractmethod
    def get_platform_user_by_id(self, user_id: str) -> dict | None:
        """Return the user row with this id, or ``None``."""

    @abstractmethod
    def list_platform_users(self) -> list[dict]:
        """Return every user ordered by username, without password hashes."""

    @abstractmethod
    def update_platform_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        """Update the given fields of a user.

        Returns:
            ``True`` when a row changed; ``False`` when nothing was given or
            the user does not exist.
        """

    @abstractmethod
    def delete_auth_sessions_for_user(self, user_id: str) -> None:
        """Delete every session of a user."""

    @abstractmethod
    def create_auth_session(
        self, token: str, user_id: str, expires_at: str, created_at: str,
    ) -> None:
        """Insert one ``auth_sessions`` row keyed by the session token."""

    @abstractmethod
    def get_auth_session(self, token: str) -> dict | None:
        """Return the session row for a token, or ``None``."""

    @abstractmethod
    def delete_auth_session(self, token: str) -> None:
        """Delete the session with this token, if any."""

    # ── Live execution approvals ─────────────────────────────────────────────

    @abstractmethod
    def create_live_execution_approval(  # noqa: PLR0913  # approval row columns; the only matching model is an API request type (GAP-021) (exception-register.md)
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
    ) -> dict:
        """Insert a ``pending`` approval request and return its row.

        Args:
            approval_id: Caller-generated unique id.
            requester_user_id: Id of the requesting user.
            requester_username: Username of the requesting user.
            scope_type: What is being approved, such as ``pipeline_run``.
            scope_id: Identifier of that scope.
            requested_at: ISO-8601 request time.
            assignment_id: Related assignment, when any.
            profile_id: Related profile, when any.
            reason_request: The requester's justification.
            context_json: Extra request context, serialised as JSON.

        Returns:
            The stored row, or an empty dict if it cannot be read back.
        """

    @abstractmethod
    def get_live_execution_approval(self, approval_id: str) -> dict | None:
        """Return the approval row with this id, or ``None``."""

    @abstractmethod
    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the newest ``pending`` approval for a scope, or ``None``."""

    @abstractmethod
    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the most recently decided ``approved`` approval for a scope, or ``None``."""

    @abstractmethod
    def get_live_execution_approval_for_scope(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the newest approval for a scope regardless of status, or ``None``."""

    @abstractmethod
    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """Return the newest approvals, filtered by status unless it is ``None`` or ``"all"``."""

    @abstractmethod
    def decide_live_execution_approval(
        self,
        approval_id: str,
        status: str,
        approver: PlatformUser,
        reason_decision: str,
        decided_at: str,
    ) -> dict | None:
        """Record a decision on a ``pending`` approval.

        A row that is already decided is returned unchanged.

        Args:
            approval_id: The approval to decide.
            status: ``"approved"`` or ``"denied"``.
            approver: The deciding user; id and username are stored.
            reason_decision: The approver's justification.
            decided_at: ISO-8601 decision time.

        Returns:
            The row after the decision, or ``None`` when the id is unknown.
        """
