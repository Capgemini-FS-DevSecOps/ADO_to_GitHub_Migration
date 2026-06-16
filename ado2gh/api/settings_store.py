"""Persistent migration profiles (source ADO + target GitHub) for the accelerator UI."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ado2gh.api.phase_definitions import (
    PhaseDefinition,
    default_phase_definitions,
    evaluate_coverage,
    parse_phase_definitions,
    span_phases_to_scan,
    validate_phases,
)


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
    migration_strategy: str = "mirror"
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

    def to_public(self) -> dict[str, Any]:
        adv = asdict(self.advanced)
        if not adv.get("phases"):
            adv["phases"] = [p.to_dict() for p in default_phase_definitions()]
        return {
            "active_profile_id": self.active_profile_id,
            "migration_profiles": [p.to_public() for p in self.migration_profiles],
            "advanced": adv,
        }


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or _settings_path()

    def load(self) -> UISettings:
        if not self.path.exists():
            settings = UISettings()
        else:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            secrets = data.get("_secrets", {})
            if data.get("migration_profiles") is not None:
                settings = self._load_v2(data, secrets)
            else:
                settings = self._migrate_v1(data, secrets)

        sqlite_env = os.environ.get("ADO2GH_SQLITE_PATH")
        if sqlite_env:
            settings.advanced.db_path = sqlite_env
        return settings

    def _load_v2(self, data: dict[str, Any], secrets: dict[str, Any]) -> UISettings:
        profile_secrets = secrets.get("profiles", secrets)
        token_bucket = secrets.get("_tokens", {})
        profiles: list[MigrationProfile] = []

        for raw in data.get("migration_profiles", []):
            pid = raw["id"]
            sec = profile_secrets.get(pid, {})
            tokens: list[GitHubToken] = []
            for t in raw.get("github_tokens", []):
                tok_val = sec.get("tokens", {}).get(t["id"], token_bucket.get(t["id"], ""))
                tokens.append(GitHubToken(
                    id=t["id"],
                    name=t["name"],
                    token=tok_val,
                    note=t.get("note", ""),
                    created_at=t.get("created_at", ""),
                    updated_at=t.get("updated_at", ""),
                    last_validated_at=t.get("last_validated_at", ""),
                    last_validation=t.get("last_validation", {}),
                ))
            profiles.append(MigrationProfile(
                id=pid,
                name=raw["name"],
                ado_org_url=raw.get("ado_org_url", ""),
                ado_pat=sec.get("ado_pat", ""),
                gh_org=raw.get("gh_org", ""),
                github_tokens=tokens,
                created_at=raw.get("created_at", ""),
                updated_at=raw.get("updated_at", ""),
                last_scan_at=raw.get("last_scan_at", ""),
                scan_summary=raw.get("scan_summary", {}),
            ))

        adv = data.get("advanced", {})
        return UISettings(
            active_profile_id=data.get("active_profile_id"),
            migration_profiles=profiles,
            advanced=AdvancedSettings(**{**asdict(AdvancedSettings()), **adv}),
        )

    def _migrate_v1(self, data: dict[str, Any], secrets: dict[str, Any]) -> UISettings:
        """Upgrade legacy flat profiles + global tokens."""
        token_secrets = secrets.get("_tokens", {})
        global_tokens: list[GitHubToken] = []
        for t in data.get("github_tokens", []):
            global_tokens.append(GitHubToken(
                id=t["id"],
                name=t["name"],
                token=token_secrets.get(t["id"], ""),
                note=t.get("note", ""),
                created_at=t.get("created_at", ""),
                updated_at=t.get("updated_at", ""),
                last_validated_at=t.get("last_validated_at", ""),
                last_validation=t.get("last_validation", {}),
            ))

        profiles: list[MigrationProfile] = []
        for p in data.get("profiles", []):
            sec = secrets.get(p["id"], {})
            prof_tokens = list(global_tokens)
            for key, label in (("gh_token", "Primary"), ("gh_token_2", "Secondary")):
                val = sec.get(key, "")
                if val:
                    prof_tokens.append(GitHubToken(
                        id=str(uuid.uuid4()),
                        name=f"{p['name']} — {label}",
                        token=val,
                        created_at=p.get("created_at", ""),
                        updated_at=p.get("updated_at", ""),
                    ))
            profiles.append(MigrationProfile(
                id=p["id"],
                name=p["name"],
                ado_org_url=p.get("ado_org_url", ""),
                ado_pat=sec.get("ado_pat", ""),
                gh_org=p.get("gh_org", ""),
                github_tokens=prof_tokens if prof_tokens else [],
                created_at=p.get("created_at", ""),
                updated_at=p.get("updated_at", ""),
            ))

        if global_tokens and profiles and not any(p.github_tokens for p in profiles):
            active = data.get("active_profile_id") or (profiles[0].id if profiles else None)
            for p in profiles:
                if p.id == active:
                    p.github_tokens = list(global_tokens)
                    break

        adv = data.get("advanced", {})
        return UISettings(
            active_profile_id=data.get("active_profile_id"),
            migration_profiles=profiles,
            advanced=AdvancedSettings(**{**asdict(AdvancedSettings()), **adv}),
        )

    def save(self, settings: UISettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        profile_secrets: dict[str, dict[str, Any]] = {}
        public_profiles = []

        for p in settings.migration_profiles:
            profile_secrets[p.id] = {
                "ado_pat": p.ado_pat,
                "tokens": {t.id: t.token for t in p.github_tokens},
            }
            public_profiles.append(p.to_public())

        payload = {
            "active_profile_id": settings.active_profile_id,
            "migration_profiles": public_profiles,
            "advanced": asdict(settings.advanced),
            "_secrets": {"profiles": profile_secrets},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_profile(self, profile_id: str) -> Optional[MigrationProfile]:
        return next((p for p in self.load().migration_profiles if p.id == profile_id), None)

    def get_active_profile(self) -> Optional[MigrationProfile]:
        s = self.load()
        if not s.active_profile_id:
            return s.migration_profiles[0] if s.migration_profiles else None
        return next((p for p in s.migration_profiles if p.id == s.active_profile_id), None)

    def apply_to_process_env(self, profile: MigrationProfile | None = None) -> None:
        p = profile or self.get_active_profile()
        if not p:
            return
        if p.ado_org_url:
            os.environ["ADO_ORG_URL"] = p.ado_org_url
        if p.ado_pat:
            os.environ["ADO_PAT"] = p.ado_pat
        if p.gh_org:
            os.environ["GH_ORG"] = p.gh_org

        for key in list(os.environ):
            if key == "GH_TOKEN" or key.startswith("GH_TOKEN_"):
                del os.environ[key]

        for i, tok in enumerate(p.github_tokens):
            if not tok.token:
                continue
            env_key = "GH_TOKEN" if i == 0 else f"GH_TOKEN_{i + 1}"
            os.environ[env_key] = tok.token

    def setup_profile(self, data: dict[str, Any]) -> MigrationProfile:
        """Atomically create profile with source, target, and initial GitHub token."""
        settings = self.load()
        now = datetime.now(timezone.utc).isoformat()
        tok = GitHubToken(
            id=str(uuid.uuid4()),
            name=data.get("github_token_name", "Primary"),
            token=data.get("github_token", ""),
            created_at=now,
            updated_at=now,
        )
        prof = MigrationProfile(
            id=str(uuid.uuid4()),
            name=data["name"],
            ado_org_url=data.get("ado_org_url", ""),
            ado_pat=data.get("ado_pat", ""),
            gh_org=data.get("gh_org", ""),
            github_tokens=[tok] if tok.token else [],
            created_at=now,
            updated_at=now,
        )
        settings.migration_profiles.append(prof)
        if not settings.active_profile_id:
            settings.active_profile_id = prof.id
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

    def delete_profile(self, profile_id: str) -> None:
        settings = self.load()
        settings.migration_profiles = [p for p in settings.migration_profiles if p.id != profile_id]
        if settings.active_profile_id == profile_id:
            settings.active_profile_id = (
                settings.migration_profiles[0].id if settings.migration_profiles else None
            )
        self.save(settings)

    def upsert_github_token(
        self, profile_id: str, data: dict[str, Any], token_id: str | None = None,
    ) -> GitHubToken:
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        now = datetime.now(timezone.utc).isoformat()
        if token_id:
            existing = next((t for t in prof.github_tokens if t.id == token_id), None)
            if not existing:
                raise KeyError(token_id)
            if data.get("token") and data["token"] != "***":
                existing.token = data["token"]
            existing.name = data.get("name", existing.name)
            existing.note = data.get("note", existing.note)
            existing.updated_at = now
            tok = existing
        else:
            tok = GitHubToken(
                id=str(uuid.uuid4()),
                name=data["name"],
                token=data.get("token", ""),
                note=data.get("note", ""),
                created_at=now,
                updated_at=now,
            )
            prof.github_tokens.append(tok)
        prof.updated_at = now
        self.save(settings)
        return tok

    def delete_github_token(self, profile_id: str, token_id: str) -> None:
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        prof.github_tokens = [t for t in prof.github_tokens if t.id != token_id]
        prof.updated_at = datetime.now(timezone.utc).isoformat()
        self.save(settings)

    def get_github_token(self, profile_id: str, token_id: str) -> Optional[GitHubToken]:
        prof = self.get_profile(profile_id)
        if not prof:
            return None
        return next((t for t in prof.github_tokens if t.id == token_id), None)

    def record_token_validation(
        self, profile_id: str, token_id: str, result: dict[str, Any],
    ) -> GitHubToken:
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        tok = next((t for t in prof.github_tokens if t.id == token_id), None)
        if not tok:
            raise KeyError(token_id)
        now = datetime.now(timezone.utc).isoformat()
        tok.last_validated_at = now
        tok.last_validation = result
        tok.updated_at = now
        prof.updated_at = now
        self.save(settings)
        return tok

    def get_phases(self) -> list[PhaseDefinition]:
        settings = self.load()
        return parse_phase_definitions(settings.advanced.phases or None)

    def phases_payload(
        self,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        from ado2gh.api.state_db import get_state_db

        phases = self.get_phases()
        db = get_state_db()
        scores = db.scan_repo_scores(profile_id) if hasattr(db, "scan_repo_scores") else []
        coverage = evaluate_coverage(phases, scores)
        repo_counts: dict[str, int] = {}
        for p in phases:
            if hasattr(db, "count_repos_by_phase"):
                counts = db.count_repos_by_phase(p.id, profile_id)
                repo_counts[p.id] = counts.get("profile_scan", 0) + counts.get("risk_scores", 0)
            else:
                repo_counts[p.id] = 0
        from ado2gh.api.phase_definitions import phase_risk_bands
        return {
            "phases": phase_risk_bands(phases),
            "coverage": coverage,
            "repo_counts_by_phase": repo_counts,
            "default_phase": self.load().advanced.default_phase,
        }

    def update_phases(
        self,
        phases_raw: list[dict[str, Any]],
        *,
        removals: list[dict[str, str]] | None = None,
        span_to_scan: bool = False,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        from ado2gh.api.state_db import get_state_db

        settings = self.load()
        old_phases = parse_phase_definitions(settings.advanced.phases or None)
        phases = parse_phase_definitions(phases_raw)
        if span_to_scan:
            db = get_state_db()
            scores = db.scan_repo_scores(profile_id) if hasattr(db, "scan_repo_scores") else []
            phases = span_phases_to_scan(phases, scores)

        errors = validate_phases(phases)
        if errors:
            raise ValueError("; ".join(errors))

        db = get_state_db()
        new_ids = {p.id for p in phases}
        removal_map = {r.get("phase_id", ""): r.get("move_repos_to", "") for r in (removals or [])}

        for old in old_phases:
            if old.id in new_ids:
                continue
            if hasattr(db, "count_repos_by_phase"):
                counts = db.count_repos_by_phase(old.id, profile_id)
                total = counts.get("profile_scan", 0) + counts.get("risk_scores", 0)
            else:
                total = 0
            if total > 0:
                target = removal_map.get(old.id)
                if not target or target not in new_ids:
                    raise ValueError(
                        f"Phase {old.name} ({old.id}) has {total} assigned repo(s) — "
                        "choose a target phase to move them to before removing."
                    )
                if hasattr(db, "reassign_phase_repos"):
                    db.reassign_phase_repos(old.id, target, profile_id)

        if len(new_ids) < 1:
            raise ValueError("At least one migration phase is required")

        settings.advanced.phases = [p.to_dict() for p in sorted(phases, key=lambda p: p.order)]
        if settings.advanced.default_phase not in new_ids:
            settings.advanced.default_phase = sorted(phases, key=lambda p: p.order)[0].id
        self.save(settings)
        payload = self.phases_payload(profile_id)
        target_profile = profile_id or settings.active_profile_id
        if target_profile:
            payload["rescan"] = self._rescan_profile_after_phase_change(
                target_profile, phases,
            )
        return payload

    def _rescan_profile_after_phase_change(
        self,
        profile_id: str,
        phases: list[PhaseDefinition],
    ) -> dict[str, Any]:
        """Re-scan ADO and re-bucket repos using updated phase bands (read-only on ADO)."""
        profile = self.get_profile(profile_id)
        if not profile:
            return {"skipped": True, "reason": "profile not found"}
        if not profile.ado_org_url or not profile.ado_pat:
            return {"skipped": True, "reason": "profile missing ADO credentials"}

        from ado2gh.api.migration_scan import persist_scan_results, scan_with_credentials
        from ado2gh.api.profile_discovery import resolve_gh_org, sync_profile_scan_to_risk_scores

        adv = self.load().advanced
        gh_org = resolve_gh_org(profile, config_path=adv.config_path)
        raw = scan_with_credentials(
            profile.ado_org_url,
            profile.ado_pat,
            gh_org=gh_org,
            phase_definitions=[p.to_dict() for p in phases],
        )
        if gh_org and not raw.get("gh_org"):
            raw["gh_org"] = gh_org
        persist_scan_results(profile_id, raw)
        self.record_scan_summary(profile_id, raw)
        synced = sync_profile_scan_to_risk_scores(
            profile_id, raw, config_path=adv.config_path,
        )
        return {
            "profile_id": profile_id,
            "repos_scanned": raw.get("repos_scanned", 0),
            "projects_scanned": raw.get("projects_scanned", 0),
            "scanned_at": raw.get("scanned_at", ""),
            "synced": synced,
        }

    def update_advanced(self, data: dict[str, Any]) -> AdvancedSettings:
        settings = self.load()
        for k, v in data.items():
            if hasattr(settings.advanced, k):
                setattr(settings.advanced, k, v)
        self.save(settings)
        return settings.advanced

    def set_active(self, profile_id: str) -> None:
        settings = self.load()
        if not any(p.id == profile_id for p in settings.migration_profiles):
            raise KeyError(profile_id)
        settings.active_profile_id = profile_id
        self.save(settings)
