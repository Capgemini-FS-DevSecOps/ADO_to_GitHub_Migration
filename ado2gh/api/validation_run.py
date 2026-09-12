"""Profile- and database-backed validation (no host file paths required)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
from ado2gh.api.contracts import ValidateRequest, ValidateResult
from ado2gh.api.profile_discovery import repo_configs_for_phase, resolve_gh_org
from ado2gh.api.repo_input import load_repos
from ado2gh.api.settings_store import AdvancedSettings, MigrationProfile, SettingsStore
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.models import RepoConfig, WaveConfig
from ado2gh.reporting.post_migration_validator import PostMigrationValidator
from ado2gh.state.factory import create_state_db


def build_global_cfg(
    profile: MigrationProfile | None,
    advanced: AdvancedSettings,
    *,
    config_path: str | None = None,
    config_yaml: str | None = None,
) -> tuple[dict[str, Any], list[WaveConfig]]:
    """Resolve migration settings from upload, on-disk config, or active profile."""
    if config_yaml:
        global_cfg, waves = ConfigLoader.load_yaml_text(config_yaml)
    else:
        path = config_path or os.environ.get("ADO2GH_CONFIG") or advanced.config_path
        if path and Path(path).exists():
            global_cfg, waves = ConfigLoader.load(path)
        elif profile:
            global_cfg, waves = _global_cfg_from_profile(profile, advanced), []
        else:
            raise FileNotFoundError(
                "No migration configuration available. Activate a migration profile "
                "or upload migration.yaml."
            )

    if profile:
        global_cfg = _merge_profile_credentials(global_cfg, profile, advanced)
    return global_cfg, waves


def _global_cfg_from_profile(
    profile: MigrationProfile,
    advanced: AdvancedSettings,
) -> dict[str, Any]:
    """Derive a migration config from a profile when no migration.yaml exists.

    Args:
        profile: Active migration profile supplying the ADO org, its
            credential, the GitHub org and the first GitHub credential.
        advanced: Advanced settings supplying the migration strategy and the
            repo and pipeline parallelism.

    Returns:
        A global config dict with the ADO and GitHub connection settings, the
        migration strategy, both parallelism knobs, and a default scope list of
        just the repo scope.
    """
    gh_token = profile.github_tokens[0].token if profile.github_tokens else ""
    return {
        "ado_org_url": profile.ado_org_url,
        "ado_pat": profile.ado_pat,
        "gh_org": profile.gh_org,
        "gh_token": gh_token,
        "migration_strategy": advanced.migration_strategy,
        "parallel": advanced.repo_parallel,
        "pipeline_parallel": advanced.pipeline_parallel,
        "default_scopes": ["repo"],
    }


def _merge_profile_credentials(
    global_cfg: dict[str, Any],
    profile: MigrationProfile,
    advanced: AdvancedSettings,
) -> dict[str, Any]:
    merged = dict(global_cfg)
    if profile.ado_org_url:
        merged["ado_org_url"] = profile.ado_org_url
    if profile.ado_pat:
        merged["ado_pat"] = profile.ado_pat
    if profile.gh_org:
        merged["gh_org"] = profile.gh_org
    if profile.github_tokens and profile.github_tokens[0].token:
        merged["gh_token"] = profile.github_tokens[0].token
    merged.setdefault("migration_strategy", advanced.migration_strategy)
    merged.setdefault("parallel", advanced.repo_parallel)
    merged.setdefault("pipeline_parallel", advanced.pipeline_parallel)
    merged.setdefault("default_scopes", ["repo"])
    if not merged.get("gh_org"):
        merged["gh_org"] = resolve_gh_org(profile, global_cfg=merged)
    return merged


def resolve_validation_repos(
    request: ValidateRequest,
    *,
    profile: MigrationProfile | None,
    advanced: AdvancedSettings,
    global_cfg: dict[str, Any],
    waves: list[WaveConfig],
) -> list[RepoConfig]:
    """Work out which repositories a validation request should cover.

    The first source that yields repos wins: inline text on the request, then
    an on-disk repo list it points at, then the phase of the active profile,
    and finally the wave configuration.

    Args:
        request: Validation request, optionally naming inline repo text, a repo
            list path or a phase.
        profile: Active migration profile, or ``None`` when none is selected.
        advanced: Advanced settings supplying the config path and the default
            phase.
        global_cfg: Resolved migration config, read for the GitHub org and the
            default scopes.
        waves: Wave configuration used as the last resort.

    Returns:
        The repository configurations to validate, empty when no source names
        any repository.
    """
    gh_org = str(global_cfg.get("gh_org", ""))
    default_scopes = global_cfg.get("default_scopes", ["repo"])

    if request.input_text:
        return ConfigLoader.load_text_content(
            request.input_text, gh_org, default_scopes,
        )

    if request.input_path and Path(request.input_path).exists():
        return load_repos(request.input_path, global_cfg, waves)

    if profile:
        phase = request.phase or advanced.default_phase or "poc"
        repos = repo_configs_for_phase(
            phase, profile, config_path=advanced.config_path,
        )
        if repos:
            return repos

    return load_repos("", global_cfg, waves)


def run_validation(
    request: ValidateRequest,
    settings_store: SettingsStore,
) -> ValidateResult:
    """Validate migrated repositories against their ADO sources.

    Resolves the migration config and credentials from the request and the
    stored profile, works out the repositories in scope, then compares each one
    to its ADO source at commit-SHA level.

    Args:
        request: Validation request naming the profile, config, repositories
            and optional report output path.
        settings_store: Store the profile and advanced settings are read from.

    Returns:
        A result with the number of repositories validated, how many passed and
        failed, the report output path when one was requested, and the
        per-repository check details. Counts are zero and details empty when no
        repository was in scope.

    Raises:
        FileNotFoundError: No migration configuration could be resolved from
            the request, the environment or an active profile.
    """
    settings = settings_store.load()
    advanced = settings.advanced
    profile = (
        settings_store.get_profile(request.profile_id)
        if request.profile_id
        else settings_store.get_active_profile()
    )

    global_cfg, waves = build_global_cfg(
        profile,
        advanced,
        config_path=request.config_path,
        config_yaml=request.config_yaml,
    )

    ado_url = profile.ado_org_url if profile else None
    ado_pat = profile.ado_pat if profile else None
    gh_token = global_cfg.get("gh_token") or (
        profile.github_tokens[0].token if profile and profile.github_tokens else None
    )

    repos = resolve_validation_repos(
        request,
        profile=profile,
        advanced=advanced,
        global_cfg=global_cfg,
        waves=waves,
    )
    if not repos:
        return ValidateResult(total=0, passed=0, failed=0, details=[])

    ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
    gh = _build_gh_client(global_cfg, gh_token=gh_token)
    db = create_state_db(request.db_path or advanced.db_path)

    results = PostMigrationValidator(ado, gh, db).validate(
        repos, output_path=request.output_path,
    )
    passed = sum(1 for r in results if r.get("overall") == "PASS")
    return ValidateResult(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        output_path=request.output_path,
        details=results,
    )
