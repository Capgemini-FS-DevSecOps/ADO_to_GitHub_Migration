"""Profile management mixin for SettingsStore."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ado2gh.api.settings_models import GitHubToken, MigrationProfile


class ProfileMixin:
    """Profile CRUD, approval workflow, and query methods."""

    def get_profile(self, profile_id: str) -> Optional[MigrationProfile]:
        return next((p for p in self.load().migration_profiles if p.id == profile_id), None)

    def get_active_profiles(self) -> list[MigrationProfile]:
        return [p for p in self.load().migration_profiles if p.status == "active"]

    def get_default_profile(self) -> Optional[MigrationProfile]:
        from ado2gh.api.profile_governance import get_default_profile
        return get_default_profile(self.load().migration_profiles)

    def get_active_profile(self) -> Optional[MigrationProfile]:
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
        from ado2gh.api.profile_governance import assert_can_delete, ProfileGovernanceError

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
        from ado2gh.api.profile_governance import assert_can_delete, ProfileGovernanceError

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
