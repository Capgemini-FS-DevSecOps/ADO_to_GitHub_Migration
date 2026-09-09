"""Data models for migration settings and profiles."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ado2gh.api.phase_definitions import default_phase_definitions
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY


def _settings_path() -> Path:
    """Locate the JSON file that backs the UI settings store.

    Returns:
        Path: ``ui_settings.json`` inside the directory named by the
        ``ADO2GH_DATA_DIR`` environment variable, or inside the current
        working directory when that variable is unset.

    """
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "ui_settings.json"


@dataclass
class GitHubToken:
    """One named GitHub credential belonging to a migration profile.

    The secret itself is held in ``token`` and is never included in the
    public representation returned by :meth:`to_public`.
    """

    id: str
    name: str
    token: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    last_validation: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the token for API responses with the secret withheld.

        Returns:
            dict[str, Any]: The token identifier, display name, note and
            validation timestamps, with the secret replaced by a fixed
            mask marker when one is stored and an empty string when it is
            not. The raw credential is never present.

        """
        return {
            "id": self.id,
            "name": self.name,
            "token": "***" if self.token else "",
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_validated_at": self.last_validated_at,
            "last_validation": self.last_validation,
        }


@dataclass
class MigrationProfile:
    """One source ADO org → target GitHub org migration configuration."""

    id: str
    name: str
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""
    github_tokens: list[GitHubToken] = field(default_factory=list)
    status: str = "active"
    is_default: bool = False
    submitted_by: str = ""
    approval: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_scan_at: str = ""
    scan_summary: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the profile for API responses with secrets withheld.

        Returns:
            dict[str, Any]: The profile identity, source ADO organization
            URL, target GitHub organization, lifecycle status, approval
            record, timestamps and last scan summary, plus each GitHub
            token in its own masked public form. The stored ADO personal
            access token is replaced by a fixed mask marker when present
            and an empty string when absent.

        """
        return {
            "id": self.id,
            "name": self.name,
            "ado_org_url": self.ado_org_url,
            "ado_pat": "***" if self.ado_pat else "",
            "gh_org": self.gh_org,
            "github_tokens": [t.to_public() for t in self.github_tokens],
            "status": self.status,
            "is_default": self.is_default,
            "submitted_by": self.submitted_by,
            "approval": self.approval,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_scan_at": self.last_scan_at,
            "scan_summary": self.scan_summary,
        }


@dataclass
class AdvancedSettings:
    """Deployment-wide migration defaults shared by every profile.

    Covers file locations, the default execution mode, the migration
    strategy, concurrency limits, the configured rollout phases and the
    policy rules applied to generated workflows.
    """

    config_path: str = "migration.yaml"
    db_path: str = "migration_state.db"
    dry_run_default: bool = True
    migration_strategy: str = DEFAULT_MIGRATION_STRATEGY
    default_phase: str = "poc"
    repo_parallel: int = 4
    pipeline_parallel: int = 8
    output_dir: str = "output"
    phases: list[dict[str, Any]] = field(default_factory=list)
    workflow_layout_policy: str = "modular"
    policy_rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class UISettings:
    """The complete persisted state of the accelerator settings store.

    Holds every migration profile, which one is currently active, the
    shared advanced settings and the per-profile operator resolutions.
    """

    active_profile_id: Optional[str] = None
    migration_profiles: list[MigrationProfile] = field(default_factory=list)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)
    operator_resolutions: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the whole settings document for API responses.

        Returns:
            dict[str, Any]: The active profile identifier, every migration
            profile in its masked public form, and the advanced settings.
            When no rollout phases have been configured the advanced block
            is filled in with the built-in default phase definitions so
            callers always receive a usable phase list. Operator
            resolutions are deliberately omitted.

        """
        adv = asdict(self.advanced)
        if not adv.get("phases"):
            adv["phases"] = [p.to_dict() for p in default_phase_definitions()]
        return {
            "active_profile_id": self.active_profile_id,
            "migration_profiles": [p.to_public() for p in self.migration_profiles],
            "advanced": adv,
        }
