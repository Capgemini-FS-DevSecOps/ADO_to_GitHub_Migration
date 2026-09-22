"""Persistent migration profiles (source ADO + target GitHub) for the accelerator UI.

Decomposed into:
  - settings_models: data models (GitHubToken, MigrationProfile, AdvancedSettings, UISettings)
  - settings_profiles: profile management mixin
  - settings_scan: scan management mixin
  - settings_store: core load/save/token/phase methods (this file)

Re-exports all public names for backward compatibility (FR-013).
"""
from __future__ import annotations

import dataclasses
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, cast, get_type_hints

from ado2gh.api.phase_definitions import (
    PhaseDefinition,
    evaluate_coverage,
    parse_phase_definitions,
    span_phases_to_scan,
    validate_phases,
)
from ado2gh.api.settings_models import (
    AdvancedSettings,
    GitHubToken,
    MigrationProfile,
    UISettings,
    _settings_path,
)
from ado2gh.api.settings_profiles import ProfileMixin
from ado2gh.api.settings_scan import ScanMixin

__all__ = [
    "AdvancedSettings",
    "GitHubToken",
    "MigrationProfile",
    "SettingsStore",
    "UISettings",
]


def _rehydrate_nested_dataclasses(cls: type, merged: dict[str, Any]) -> dict[str, Any]:
    """Rebuild any field of ``cls`` typed as a dataclass that arrived as a plain dict.

    Loading merges the saved JSON over ``asdict(cls())`` and constructs ``cls``
    from the result. ``asdict`` recurses into nested dataclasses too, so a
    field like ``AdvancedSettings.concurrency`` or ``.agent_runtime`` comes
    back flattened to a plain dict, not the dataclass instance the field is
    declared as. Passing that dict straight to ``cls(**merged)`` leaves the
    field holding a dict instead of the expected dataclass.

    Args:
        cls: The dataclass about to be constructed, e.g. ``AdvancedSettings``.
        merged: Field values already merged over the defaults, keyed by field
            name.

    Returns:
        dict[str, Any]: A copy of ``merged`` with every dataclass-typed field
        rebuilt into an instance of its declared type. A missing key inside
        the nested dict takes that dataclass's own default; a key it does not
        declare is dropped instead of raising. A field whose value is not a
        dict (already the right instance, or something else) is left alone.

    """
    result = dict(merged)
    hints = get_type_hints(cls)
    for f in dataclasses.fields(cls):
        field_type = hints.get(f.name, f.type)
        if not dataclasses.is_dataclass(field_type):
            continue
        # is_dataclass narrows to the DataclassInstance protocol, which has no
        # constructor; the field type itself is always a concrete class here.
        field_type = cast("type[Any]", field_type)
        value = result.get(f.name)
        if not isinstance(value, dict):
            continue
        defaults = asdict(field_type())
        known = {k: v for k, v in value.items() if k in defaults}
        result[f.name] = field_type(**{**defaults, **known})
    return result


class SettingsStore(ProfileMixin, ScanMixin):
    """Read and write the JSON document holding profiles and settings.

    Every method loads the document from disk, mutates it and writes it
    back, so the file on disk is always the single source of truth and
    concurrent readers see committed state only.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Bind the store to a settings file.

        Args:
            path: Location of the settings JSON document. When omitted the
                location — ``ui_settings.json`` under the configured data
                directory — is resolved on every access, not here: the route
                layer keeps one store for the process lifetime
                (``services/accelerator_api/routes/_shared.py``), so resolving
                it in the constructor froze it at import time and every later
                change to ``ADO2GH_DATA_DIR`` was ignored.

        """
        self._path = path

    @property
    def path(self) -> Path:
        """Settings document this store reads and writes, resolved on every access.

        Returns:
            Path: The explicit path this store was constructed with, or the
            current location under the configured data directory.
        """
        return self._path or _settings_path()

    @path.setter
    def path(self, value: Path) -> None:
        """Pin the store to one settings document, whatever the environment says.

        Args:
            value: File to read and write from now on.
        """
        self._path = value

    def load(self) -> UISettings:
        """Read the settings document, upgrading older layouts on the way.

        Returns:
            UISettings: The persisted profiles, the selected profile, the
            advanced settings and the operator resolutions. Defaults are
            returned when no settings file exists yet, a legacy flat
            document is upgraded in memory to the profile layout, the
            database path is overridden from the environment when one is
            set, and the selected profile and default flag are repaired so
            they always point at an active profile when one exists.

        """
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
        self._normalize_defaults(settings)
        return settings

    def _normalize_defaults(self, settings: UISettings) -> None:
        """Repair the selected profile and default flag in place.

        Clears the selection when nothing is active; otherwise points it at
        an active profile and guarantees exactly one active profile carries
        the default flag.

        Args:
            settings: Settings document to fix up; mutated in place.

        """
        active_profiles = [
            p for p in settings.migration_profiles
            if p.status == "active"
        ]
        if not active_profiles:
            settings.active_profile_id = None
            return
        if settings.active_profile_id:
            current = next(
                (p for p in active_profiles if p.id == settings.active_profile_id),
                None,
            )
            if not current:
                settings.active_profile_id = active_profiles[0].id
        else:
            default = next((p for p in active_profiles if p.is_default), None)
            settings.active_profile_id = (default or active_profiles[0]).id
        if not any(p.is_default for p in active_profiles):
            target = next(
                (p for p in active_profiles if p.id == settings.active_profile_id),
                active_profiles[0],
            )
            target.is_default = True

    def _load_v2(self, data: dict[str, Any], secrets: dict[str, Any]) -> UISettings:
        """Rebuild settings from the current profile-based document layout.

        Args:
            data: Public half of the settings document.
            secrets: Secret half of the document, holding the per-profile
                ADO credential and GitHub credentials keyed by identifier.

        Returns:
            UISettings: The reassembled settings, with each profile's
            secrets merged back onto it and the selected profile and
            default flag normalised.

        """
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
                status=raw.get("status", "active"),
                is_default=bool(raw.get("is_default", False)),
                submitted_by=raw.get("submitted_by", ""),
                approval=raw.get("approval") or {},
                created_at=raw.get("created_at", ""),
                updated_at=raw.get("updated_at", ""),
                last_scan_at=raw.get("last_scan_at", ""),
                scan_summary=raw.get("scan_summary", {}),
            ))

        adv = data.get("advanced", {})
        merged_adv = _rehydrate_nested_dataclasses(
            AdvancedSettings, {**asdict(AdvancedSettings()), **adv},
        )
        settings = UISettings(
            active_profile_id=data.get("active_profile_id"),
            migration_profiles=profiles,
            advanced=AdvancedSettings(**merged_adv),
            operator_resolutions=data.get("operator_resolutions", {}) or {},
        )
        self._normalize_defaults(settings)
        return settings

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
                        name=f"{p['name']}  {label}",
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
        merged_adv = _rehydrate_nested_dataclasses(
            AdvancedSettings, {**asdict(AdvancedSettings()), **adv},
        )
        return UISettings(
            active_profile_id=data.get("active_profile_id"),
            migration_profiles=profiles,
            advanced=AdvancedSettings(**merged_adv),
        )

    def save(self, settings: UISettings) -> None:
        """Write the settings document to disk, creating its directory.

        Public profile data and secrets are written to separate sections of
        the same file so the public section can be served as-is.

        Args:
            settings: Settings document to persist.

        """
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
            "operator_resolutions": settings.operator_resolutions,
            "_secrets": {"profiles": profile_secrets},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_operator_resolutions(self, profile_id: str) -> dict[str, str]:
        """Read the stored operator answers for one profile.

        Args:
            profile_id: Identifier of the profile to read.

        Returns:
            dict[str, str]: A copy of the recorded decisions, keyed by the
            question they answer. Empty when the profile has none.

        """
        return dict(self.load().operator_resolutions.get(profile_id, {}))

    def set_operator_resolutions(
        self, profile_id: str, resolutions: dict[str, str],
    ) -> dict[str, str]:
        """Merge new operator answers into a profile and persist them.

        Args:
            profile_id: Identifier of the profile to update.
            resolutions: Decisions to record, keyed by the question they
                answer. Existing keys are overwritten; keys not mentioned
                are left alone.

        Returns:
            dict[str, str]: The profile's complete set of decisions after
            the merge.

        """
        settings = self.load()
        current = dict(settings.operator_resolutions.get(profile_id, {}))
        current.update(resolutions or {})
        settings.operator_resolutions[profile_id] = current
        self.save(settings)
        return current

    def apply_to_process_env(self, profile: MigrationProfile | None = None) -> None:
        """Export a profile's credentials into the current process environment.

        Any GitHub credential variables already present are removed first,
        so the environment reflects exactly the profile's credential list
        and never leaks the previous profile's values. Does nothing when no
        profile is available.

        Args:
            profile: Profile to export. Defaults to the active profile.

        """
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

    def upsert_github_token(
        self, profile_id: str, data: dict[str, Any], token_id: str | None = None,
    ) -> GitHubToken:
        """Add a GitHub credential to a profile or edit an existing one.

        Args:
            profile_id: Identifier of the owning profile.
            data: Credential fields: display name, secret value and note.
                On an edit, omitted fields keep their current value and the
                stored secret is left untouched when the submitted value is
                the mask marker returned by the public representation.
            token_id: Identifier of the credential to edit; when omitted a
                new credential is added with a generated identifier.

        Returns:
            GitHubToken: The created or updated credential after the
            profile has been persisted, with its timestamps refreshed.

        Raises:
            KeyError: If no profile has that identifier, or a credential
                identifier is given that the profile does not have.

        """
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
        """Remove a GitHub credential from a profile.

        Removing a credential that is not present is not an error.

        Args:
            profile_id: Identifier of the owning profile.
            token_id: Identifier of the credential to remove.

        Raises:
            KeyError: If no profile has that identifier.

        """
        settings = self.load()
        prof = next((p for p in settings.migration_profiles if p.id == profile_id), None)
        if not prof:
            raise KeyError(profile_id)
        prof.github_tokens = [t for t in prof.github_tokens if t.id != token_id]
        prof.updated_at = datetime.now(timezone.utc).isoformat()
        self.save(settings)

    def get_github_token(self, profile_id: str, token_id: str) -> Optional[GitHubToken]:
        """Look up one of a profile's GitHub credentials.

        Args:
            profile_id: Identifier of the owning profile.
            token_id: Identifier of the credential to fetch.

        Returns:
            Optional[GitHubToken]: The stored credential including its
            secret value, or ``None`` when either the profile or the
            credential does not exist.

        """
        prof = self.get_profile(profile_id)
        if not prof:
            return None
        return next((t for t in prof.github_tokens if t.id == token_id), None)

    def record_token_validation(
        self, profile_id: str, token_id: str, result: dict[str, Any],
    ) -> GitHubToken:
        """Record the outcome of a GitHub credential validation check.

        Args:
            profile_id: Identifier of the owning profile.
            token_id: Identifier of the credential that was checked.
            result: Validation outcome to store verbatim, such as whether
                the check passed, the authenticated account and the
                remaining rate limit.

        Returns:
            GitHubToken: The credential with its last validation outcome
            and validation timestamp updated and persisted.

        Raises:
            KeyError: If no profile has that identifier, or the profile has
                no credential with that identifier.

        """
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
        """Read the configured rollout phases.

        Returns:
            list[PhaseDefinition]: The phases saved in the advanced
            settings, or the built-in defaults when none are configured.

        """
        settings = self.load()
        return parse_phase_definitions(settings.advanced.phases or None)

    def phases_payload(
        self,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the phase overview shown in the settings UI.

        Args:
            profile_id: Profile whose scanned repositories the counts and
                coverage are computed from. Defaults to counting across
                every profile.

        Returns:
            dict[str, Any]: The configured phases with their risk bands, a
            coverage report saying whether the bands span the observed risk
            scores and leave no gaps or overlaps, the number of
            repositories assigned to each phase, and the phase new
            repositories default to.

        """
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
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the rollout phases with exactly the definitions supplied.

        Args:
            phases_raw: The complete new set of phase definitions; phases
                absent from it are removed.
            removals: For each phase being removed that still has
                repositories assigned, the phase to move them to.
            profile_id: Profile the repository counts and the follow-up
                rescan apply to. Defaults to the active profile.

        Returns:
            dict[str, Any]: The refreshed phase overview, plus a ``rescan``
            entry describing the background rescan started to reassign the
            profile's repositories to the new phases.

        Raises:
            ValueError: If the new phases are invalid, if none are given,
                or if a removed phase still has repositories and no valid
                target phase was named for them.

        """
        return self._apply_phase_update(
            parse_phase_definitions(phases_raw),
            removals=removals,
            profile_id=profile_id,
        )

    def update_phases_spanning_scan(
        self,
        phases_raw: list[dict[str, Any]],
        *,
        removals: list[dict[str, str]] | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the rollout phases, stretching their risk bands over the scan.

        Behaves like :meth:`update_phases` except that the supplied risk
        bands are first widened so that together they cover the full range
        of risk scores seen in the profile's latest scan, leaving no
        scanned repository outside every phase.

        Args:
            phases_raw: The complete new set of phase definitions; phases
                absent from it are removed.
            removals: For each phase being removed that still has
                repositories assigned, the phase to move them to.
            profile_id: Profile whose scanned risk scores the bands are
                stretched over, and which the repository counts and the
                follow-up rescan apply to. Defaults to the active profile.

        Returns:
            dict[str, Any]: The refreshed phase overview for the widened
            phases, plus a ``rescan`` entry describing the background
            rescan started to reassign the profile's repositories.

        Raises:
            ValueError: If the widened phases are invalid, if no phases are
                given, or if a removed phase still has repositories and no
                valid target phase was named for them.

        """
        from ado2gh.api.state_db import get_state_db

        db = get_state_db()
        scores = db.scan_repo_scores(profile_id) if hasattr(db, "scan_repo_scores") else []
        return self._apply_phase_update(
            span_phases_to_scan(parse_phase_definitions(phases_raw), scores),
            removals=removals,
            profile_id=profile_id,
        )

    def _apply_phase_update(
        self,
        phases: list[PhaseDefinition],
        *,
        removals: list[dict[str, str]] | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate, migrate away from and persist a new set of phases.

        Args:
            phases: The resolved phase definitions to save.
            removals: For each phase disappearing from the configuration
                that still has repositories assigned, the phase to move
                them to.
            profile_id: Profile the repository counts and the follow-up
                rescan apply to. Defaults to the active profile.

        Returns:
            dict[str, Any]: The refreshed phase overview, plus a ``rescan``
            entry when a profile was resolved to rescan.

        Raises:
            ValueError: If the phases are invalid, if none are given, or if
                a phase that still has repositories is being removed
                without a valid target phase to move them to.

        """
        from ado2gh.api.state_db import get_state_db

        settings = self.load()
        old_phases = parse_phase_definitions(settings.advanced.phases or None)

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
                        f"Phase {old.name} ({old.id}) has {total} assigned repo(s)  "
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
            payload["rescan"] = self._start_rescan_after_phase_change(
                target_profile, phases,
            )
        return payload

    def update_advanced(self, data: dict[str, Any]) -> AdvancedSettings:
        """Apply a partial update to the shared advanced settings.

        Args:
            data: Advanced settings fields to change, keyed by field name.
                Unknown keys are ignored and unmentioned fields keep their
                current value.

        Returns:
            AdvancedSettings: The advanced settings as persisted after the
            update.

        """
        settings = self.load()
        for k, v in data.items():
            if hasattr(settings.advanced, k):
                setattr(settings.advanced, k, v)
        self.save(settings)
        return settings.advanced

    def set_active(self, profile_id: str) -> None:
        """Select the profile that migrations and scans run against.

        Args:
            profile_id: Identifier of the profile to select.

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
        settings.active_profile_id = profile_id
        self.save(settings)
