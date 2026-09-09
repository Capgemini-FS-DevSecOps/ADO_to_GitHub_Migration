"""Profile management mixin for SettingsStore."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ado2gh.api.settings_models import GitHubToken, MigrationProfile


class ProfileMixin:
    """Profile CRUD, approval workflow, and query methods."""

    def get_profile(self, profile_id: str) -> Optional[MigrationProfile]:
        """Look up a single migration profile by identifier.

        Args:
            profile_id: Identifier of the profile to fetch.

        Returns:
            Optional[MigrationProfile]: The stored profile with that
            identifier regardless of its lifecycle status, or ``None`` when
            no profile matches.

        """
        return next((p for p in self.load().migration_profiles if p.id == profile_id), None)

    def get_default_profile(self) -> Optional[MigrationProfile]:
        """Resolve the profile used when none has been explicitly selected.

        Returns:
            Optional[MigrationProfile]: The active profile flagged as the
            default, falling back to the first active profile when no flag
            is set, or ``None`` when no profile is active.

        """
        from ado2gh.api.profile_governance import get_default_profile
        return get_default_profile(self.load().migration_profiles)

    def get_active_profile(self) -> Optional[MigrationProfile]:
        """Resolve the profile that migrations and scans should run against.

        Returns:
            Optional[MigrationProfile]: The currently selected profile when
            it still exists and is active, otherwise the default profile,
            or ``None`` when no profile is active.

        """
        s = self.load()
        if not s.active_profile_id:
            return self.get_default_profile()
        prof = next((p for p in s.migration_profiles if p.id == s.active_profile_id), None)
        if prof and prof.status == "active":
            return prof
        return self.get_default_profile()

    def setup_profile(
        self,
        data: dict[str, Any],
        *,
        role: str = "admin",
        submitted_by: str = "",
    ) -> MigrationProfile:
        """Create the first or an additional profile during onboarding.

        An admin submission is stored as active immediately and becomes the
        default and the selected profile when it is the first active one.
        An operator submission is stored as pending approval instead.

        Args:
            data: Profile fields as supplied by the onboarding form: name,
                ADO organization URL, ADO credential, GitHub organization,
                and optionally a first GitHub credential and its label.
            role: Platform role of the submitter; anything other than the
                admin role is treated as an operator submission.
            submitted_by: Identity recorded as the submitter, kept only for
                operator submissions so the approval workflow knows who may
                appeal a denial.

        Returns:
            MigrationProfile: The newly created and persisted profile,
            carrying its generated identifier and its resulting status.

        Raises:
            ValueError: If an operator submits while no active profile
                exists yet, which an admin must create first.

        """
        from ado2gh.auth.models import PlatformRole

        settings = self.load()
        now = datetime.now(timezone.utc).isoformat()
        tok = GitHubToken(
            id=str(uuid.uuid4()),
            name=data.get("github_token_name", "Primary"),
            token=data.get("github_token", ""),
            created_at=now,
            updated_at=now,
        )
        active_count = len([p for p in settings.migration_profiles if p.status == "active"])
        is_admin = role == PlatformRole.ADMIN.value
        status = "active" if is_admin else "pending_approval"
        if not is_admin and active_count == 0:
            raise ValueError("operator_submit_blocked")

        prof = MigrationProfile(
            id=str(uuid.uuid4()),
            name=data["name"],
            ado_org_url=data.get("ado_org_url", ""),
            ado_pat=data.get("ado_pat", ""),
            gh_org=data.get("gh_org", ""),
            github_tokens=[tok] if tok.token else [],
            status=status,
            is_default=is_admin and active_count == 0,
            submitted_by=submitted_by if not is_admin else "",
            approval={},
            created_at=now,
            updated_at=now,
        )
        settings.migration_profiles.append(prof)
        if is_admin:
            if not settings.active_profile_id or active_count == 0:
                settings.active_profile_id = prof.id
            if active_count == 0:
                prof.is_default = True
        self.save(settings)
        return prof

    def record_scan_summary(self, profile_id: str, scan: dict[str, Any]) -> MigrationProfile:
        """Store the headline results of a discovery scan on a profile.

        Args:
            profile_id: Identifier of the profile that was scanned.
            scan: Raw scan result to summarise; its scan timestamp, project
                and repository counts and per-phase recommendations are
                read, and everything else is discarded.

        Returns:
            MigrationProfile: The updated profile, with its last scan
            timestamp and a trimmed summary holding the counts and, for
            each recommended phase, the repository count, risk range and
            rationale.

        Raises:
            KeyError: If no profile has that identifier.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        prof.last_scan_at = scan.get("scanned_at", datetime.now(timezone.utc).isoformat())
        prof.scan_summary = {
            "projects_scanned": scan.get("projects_scanned", 0),
            "repos_scanned": scan.get("repos_scanned", 0),
            "recommendations": {
                phase: {
                    "repo_count": bucket.get("repo_count", 0),
                    "risk_min": bucket.get("risk_min", 0),
                    "risk_max": bucket.get("risk_max", 0),
                    "rationale": bucket.get("rationale", ""),
                }
                for phase, bucket in scan.get("recommendations", {}).items()
            },
        }
        prof.updated_at = datetime.now(timezone.utc).isoformat()
        self.save(settings)
        return prof

    def upsert_profile(self, data: dict[str, Any], profile_id: str | None = None) -> MigrationProfile:
        """Create a profile or edit an existing one.

        Args:
            data: Profile fields to apply: name, ADO organization URL, ADO
                credential and GitHub organization. On an edit, omitted
                fields keep their current value and the stored ADO
                credential is left untouched when the submitted value is
                the mask marker returned by the public representation.
            profile_id: Identifier of the profile to edit; when omitted a
                new profile is created with a generated identifier.

        Returns:
            MigrationProfile: The created or updated profile after it has
            been persisted. It becomes the selected profile when no profile
            was selected before.

        Raises:
            KeyError: If a profile identifier is given but no profile
                has it.

        """
        settings = self.load()
        now = datetime.now(timezone.utc).isoformat()
        if profile_id:
            existing = next((p for p in settings.migration_profiles if p.id == profile_id), None)
            if not existing:
                raise KeyError(profile_id)
            if data.get("ado_pat") and data["ado_pat"] != "***":
                existing.ado_pat = data["ado_pat"]
            existing.name = data.get("name", existing.name)
            existing.ado_org_url = data.get("ado_org_url", existing.ado_org_url)
            existing.gh_org = data.get("gh_org", existing.gh_org)
            existing.updated_at = now
            prof = existing
        else:
            prof = MigrationProfile(
                id=str(uuid.uuid4()),
                name=data["name"],
                ado_org_url=data.get("ado_org_url", ""),
                ado_pat=data.get("ado_pat", ""),
                gh_org=data.get("gh_org", ""),
                github_tokens=[],
                created_at=now,
                updated_at=now,
            )
            settings.migration_profiles.append(prof)
        if not settings.active_profile_id:
            settings.active_profile_id = prof.id
        self.save(settings)
        return prof

    def delete_profile(self, profile_id: str, new_default_profile_id: str | None = None) -> None:
        """Permanently remove a profile from the store.

        Another active profile is selected when the deleted one was the
        selected profile, and the default flag is moved to the replacement
        when the deleted one was the default.

        Args:
            profile_id: Identifier of the profile to remove.
            new_default_profile_id: Identifier of the active profile that
                takes over the default flag; required when deleting the
                default profile while other active profiles remain.

        Raises:
            ValueError: With the governance error code as its message when
                the deletion would leave no active profile, or when the
                replacement default is missing or not an active profile.

        """
        from ado2gh.api.profile_governance import ProfileGovernanceError, assert_can_delete

        settings = self.load()
        try:
            assert_can_delete(settings.migration_profiles, profile_id, new_default_profile_id)
        except ProfileGovernanceError as exc:
            raise ValueError(exc.code) from exc

        target = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if target and target.is_default and new_default_profile_id:
            replacement = next(
                (p for p in settings.migration_profiles if p.id == new_default_profile_id),
                None,
            )
            if replacement:
                replacement.is_default = True
                target.is_default = False

        settings.migration_profiles = [p for p in settings.migration_profiles if p.id != profile_id]
        if settings.active_profile_id == profile_id:
            active = [p for p in settings.migration_profiles if p.status == "active"]
            settings.active_profile_id = active[0].id if active else None
        self._normalize_defaults(settings)
        self.save(settings)

    def deactivate_profile(self, profile_id: str, new_default_profile_id: str | None = None) -> MigrationProfile:
        """Retire a profile without deleting it or its history.

        Args:
            profile_id: Identifier of the profile to retire.
            new_default_profile_id: Identifier of the active profile that
                takes over the default flag; required when retiring the
                default profile while other active profiles remain.

        Returns:
            MigrationProfile: The profile with its status set to inactive,
            returned unchanged when it was already not active.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: With the governance error code as its message when
                retiring the profile would leave no active profile, or when
                the replacement default is missing or not an active
                profile.

        """
        from ado2gh.api.profile_governance import ProfileGovernanceError, assert_can_delete

        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        if prof.status != "active":
            return prof
        try:
            assert_can_delete(settings.migration_profiles, profile_id, new_default_profile_id)
        except ProfileGovernanceError as exc:
            raise ValueError(exc.code) from exc

        if prof.is_default and new_default_profile_id:
            replacement = next(
                (p for p in settings.migration_profiles if p.id == new_default_profile_id),
                None,
            )
            if replacement:
                replacement.is_default = True
                prof.is_default = False

        prof.status = "inactive"
        prof.updated_at = datetime.now(timezone.utc).isoformat()
        self._normalize_defaults(settings)
        self.save(settings)
        return prof

    def set_default_profile(self, profile_id: str) -> MigrationProfile:
        """Make one profile the default and clear the flag on the others.

        Args:
            profile_id: Identifier of the profile to make default.

        Returns:
            MigrationProfile: The profile that is now the default; it is
            also made the selected profile.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: If the profile is not active.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        if prof.status != "active":
            raise ValueError("profile_not_active")
        for p in settings.migration_profiles:
            p.is_default = p.id == profile_id
        prof.updated_at = datetime.now(timezone.utc).isoformat()
        settings.active_profile_id = profile_id
        self.save(settings)
        return prof

    def approve_profile(self, profile_id: str) -> MigrationProfile:
        """Admit an operator-submitted profile into service.

        Args:
            profile_id: Identifier of the pending profile to admit.

        Returns:
            MigrationProfile: The profile with its status set to active and
            its approval record stamped with the approval time and any
            earlier denial cleared. It also becomes the default and the
            selected profile when it is the first active one.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: If the profile is not awaiting approval.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        if prof.status != "pending_approval":
            raise ValueError("not_pending")
        now = datetime.now(timezone.utc).isoformat()
        active_before = len([p for p in settings.migration_profiles if p.status == "active"])
        prof.status = "active"
        prof.updated_at = now
        prof.approval = {
            **prof.approval,
            "approved_at": now,
            "denied_at": None,
            "denial_reason": None,
        }
        if active_before == 0:
            prof.is_default = True
            settings.active_profile_id = prof.id
        self._normalize_defaults(settings)
        self.save(settings)
        return prof

    def deny_profile(self, profile_id: str, reason: str = "") -> MigrationProfile:
        """Reject an operator-submitted profile.

        Args:
            profile_id: Identifier of the pending profile to reject.
            reason: Explanation shown to the submitter, stored on the
                approval record.

        Returns:
            MigrationProfile: The profile with its status set to denied,
            its default flag cleared, and its approval record stamped with
            the denial time and reason.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: If the profile is not awaiting approval.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        if prof.status != "pending_approval":
            raise ValueError("not_pending")
        now = datetime.now(timezone.utc).isoformat()
        prof.status = "denied"
        prof.updated_at = now
        prof.approval = {
            **prof.approval,
            "denied_at": now,
            "denial_reason": reason,
            "approved_at": None,
        }
        if prof.is_default:
            prof.is_default = False
        self._normalize_defaults(settings)
        self.save(settings)
        return prof

    def appeal_profile(self, profile_id: str, actor: str) -> MigrationProfile:
        """Send a denied profile back to the approval queue for its submitter.

        Args:
            profile_id: Identifier of the denied profile to resubmit.
            actor: Identity making the appeal; it must match the recorded
                submitter when one was recorded.

        Returns:
            MigrationProfile: The profile with its status set back to
            pending approval and its approval record stamped with the
            appeal time and an incremented appeal count.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: If the profile has not been denied.
            PermissionError: If the actor is not the recorded submitter.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        if prof.status != "denied":
            raise ValueError("not_denied")
        if prof.submitted_by and prof.submitted_by != actor:
            raise PermissionError("not_submitter")
        now = datetime.now(timezone.utc).isoformat()
        prof.status = "pending_approval"
        prof.updated_at = now
        prof.approval = {
            **prof.approval,
            "appealed_at": now,
            "appeal_count": int(prof.approval.get("appeal_count", 0)) + 1,
        }
        self.save(settings)
        return prof
