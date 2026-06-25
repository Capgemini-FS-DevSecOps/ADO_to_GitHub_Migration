"""Data models for migration settings and profiles."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ado2gh.api.phase_definitions import default_phase_definitions
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY


def _settings_path() -> Path:
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "ui_settings.json"


@dataclass
class GitHubToken:
    id: str
    name: str
    token: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    last_validation: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
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
    active_profile_id: Optional[str] = None
    migration_profiles: list[MigrationProfile] = field(default_factory=list)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)
    operator_resolutions: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        adv = asdict(self.advanced)
        if not adv.get("phases"):
            adv["phases"] = [p.to_dict() for p in default_phase_definitions()]
        return {
            "active_profile_id": self.active_profile_id,
            "migration_profiles": [p.to_public() for p in self.migration_profiles],
            "advanced": adv,
        }
