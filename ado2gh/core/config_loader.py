"""Safe configuration and source-to-target repository mapping ingestion.

Configuration is a trust boundary: a typo or a case-insensitive destination
collision can send source data to the wrong GitHub repository.  This module
therefore validates and normalizes every input format before returning domain
objects.  Existing text, CSV, and YAML formats remain supported; an optional
``global.mapping`` section adds deterministic project/repository mapping rules.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from yaml.constructor import ConstructorError

from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, PhaseType, RepoConfig, WaveConfig
from ado2gh.governance import normalize_governance_settings
from ado2gh.pipelines.approvals import ManualApprovalManifest
from ado2gh.pipelines.llm import normalize_pipeline_llm_settings
from ado2gh.pev.contracts import (
    access_disposition_digest,
    target_access_policy_digest,
)


_MAX_CONFIG_BYTES = 50 * 1024 * 1024
_GH_ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GH_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_VALID_SCOPES = {scope.value for scope in MigrationScope}
_VALID_PHASES = {phase.value for phase in PhaseType}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten duplicate keys."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class ConfigLoader:

    @staticmethod
    def load(path: str) -> tuple[dict, list[WaveConfig]]:
        """Load and validate a YAML configuration file.

        Returns ``(global_cfg, waves)``.  ``waves`` may be empty when the file
        contains only connection settings and repositories come from a separate
        input file.
        """
        cfg_path = _input_file(path, "Config file")
        if cfg_path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError(
                f"Config file exceeds {_MAX_CONFIG_BYTES // (1024 * 1024)} MiB: {path}"
            )
        with cfg_path.open("r", encoding="utf-8-sig") as fh:
            raw = yaml.load(fh, Loader=_UniqueKeyLoader)

        if not isinstance(raw, dict):
            raise ValueError(
                f"Config root must be a mapping, got {type(raw).__name__}"
            )
        _string_keys(raw, "config root")
        unknown_root = set(raw) - {"global", "phases", "waves"}
        if unknown_root:
            raise ValueError(
                "config root has unknown key(s): "
                + ", ".join(sorted(unknown_root))
            )
        global_cfg = _validate_global(raw.get("global", {}))
        conversion = global_cfg.get("pipeline_conversion")
        if isinstance(conversion, dict) and "manual_approval_manifest" in conversion:
            manifest = ManualApprovalManifest.from_config(
                conversion["manual_approval_manifest"],
                base_dir=cfg_path.parent,
            )
            conversion["manual_approval_manifest"] = manifest.to_dict()
        _validate_phase_settings(raw.get("phases", {}))

        waves_raw = raw.get("waves", [])
        if waves_raw is None:
            waves_raw = []
        if not isinstance(waves_raw, list):
            raise ValueError("waves must be a list")

        waves: list[WaveConfig] = []
        seen_wave_ids: set[int] = set()
        for index, wave_raw in enumerate(waves_raw):
            field = f"waves[{index}]"
            wave = _parse_wave(wave_raw, global_cfg, field)
            if wave.wave_id in seen_wave_ids:
                raise ValueError(f"duplicate wave_id {wave.wave_id}")
            seen_wave_ids.add(wave.wave_id)
            waves.append(wave)

        _validate_repository_mappings(
            [repo for wave in waves for repo in wave.repos], "waves"
        )
        total_repos = sum(len(wave.repos) for wave in waves)
        if waves:
            log.info(
                "Loaded config: %d wave(s), %d repos from %s",
                len(waves), total_repos, cfg_path.name,
            )
        else:
            log.info(
                "Loaded settings from %s (no waves — use --input for repo list)",
                cfg_path.name,
            )
        return global_cfg, waves

    @staticmethod
    def load_text_input(
        path: str,
        gh_org: str,
        scopes: list[str] = None,
        mapping: dict = None,
        strict: bool = False,
    ) -> list[RepoConfig]:
        """Parse ``project/repo[::gh_org/gh_repo]`` lines.

        Invalid individual lines retain the legacy warning-and-skip behavior.
        Set ``strict=True`` to fail the whole load instead.  Mapping collisions
        always fail because skipping them would make the resulting plan unsafe.
        """
        txt_path = _input_file(path, "Input file")
        default_scopes = _validate_scopes(scopes or ["repo"], "scopes")
        normalized_mapping = _validate_mapping_config(mapping or {})
        repos: list[RepoConfig] = []
        with txt_path.open("r", encoding="utf-8-sig") as fh:
            for lineno, raw_line in enumerate(fh, 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    repos.append(_parse_text_line(
                        line, gh_org, default_scopes, normalized_mapping
                    ))
                except ValueError as exc:
                    if strict:
                        raise ValueError(
                            f"{txt_path}:{lineno}: {exc}"
                        ) from exc
                    log.warning("line %d skipped: %s", lineno, exc)
        _validate_repository_mappings(repos, str(txt_path))
        log.info("Loaded %d repo(s) from %s", len(repos), txt_path.name)
        return repos

    @staticmethod
    def load_csv_input(
        path: str,
        gh_org: str,
        default_scopes: list[str] = None,
        mapping: dict = None,
        strict: bool = False,
    ) -> list[RepoConfig]:
        """Parse a CSV repository manifest with per-repository overrides."""
        csv_path = _input_file(path, "CSV input file")
        scopes_default = _validate_scopes(
            default_scopes or ["repo"], "default_scopes"
        )
        normalized_mapping = _validate_mapping_config(mapping or {})
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            # Comments are permitted anywhere outside quoted CSV records.  This
            # preserves the established input format; manifests requiring '#'
            # at the start of a quoted physical line should use YAML instead.
            lines = [line for line in fh if not line.lstrip().startswith("#")]
        if not lines:
            return []
        reader = csv.DictReader(io.StringIO("".join(lines)))
        if reader.fieldnames is None:
            raise ValueError(f"CSV input has no header: {path}")
        reader.fieldnames = [
            str(name).strip() if name is not None else name
            for name in reader.fieldnames
        ]
        headers = {name for name in reader.fieldnames if name is not None}
        missing = {"ado_project", "ado_repo"} - headers
        if missing:
            raise ValueError(
                "CSV input missing required column(s): " + ", ".join(sorted(missing))
            )

        repos: list[RepoConfig] = []
        for row in reader:
            try:
                if None in row:
                    raise ValueError("row has more fields than the CSV header")
                ado_project = str(row.get("ado_project") or "").strip()
                ado_repo = str(row.get("ado_repo") or "").strip()
                if not ado_project or not ado_repo:
                    raise ValueError("ado_project and ado_repo are required")
                scopes_text = str(row.get("scopes") or "").strip()
                repo_scopes = (
                    _validate_scopes(scopes_text.split("|"), "scopes")
                    if scopes_text else list(scopes_default)
                )
                explicit_org = str(row.get("gh_org") or "").strip() or None
                explicit_repo = str(row.get("gh_repo") or "").strip() or None
                target_org, target_repo = resolve_repository_mapping(
                    ado_project, ado_repo, gh_org, normalized_mapping,
                    explicit_org, explicit_repo,
                )
                repos.append(_build_repo(
                    ado_project=ado_project,
                    ado_repo=ado_repo,
                    gh_org=target_org,
                    gh_repo=target_repo,
                    scopes=repo_scopes,
                    pipeline_filter=str(row.get("pipeline_filter") or "").strip(),
                ))
            except ValueError as exc:
                if strict:
                    raise ValueError(
                        f"{csv_path}: CSV record {reader.line_num}: {exc}"
                    ) from exc
                log.warning("CSV record ending line %d skipped: %s", reader.line_num, exc)
        _validate_repository_mappings(repos, str(csv_path))
        log.info("Loaded %d repo(s) from CSV %s", len(repos), csv_path.name)
        return repos

    @staticmethod
    def load_input(
        path: str,
        gh_org: str,
        default_scopes: list[str] = None,
        mapping: dict = None,
        strict: bool = False,
    ) -> list[RepoConfig]:
        """Auto-detect text or CSV input by a case-insensitive suffix."""
        if Path(path).suffix.casefold() == ".csv":
            return ConfigLoader.load_csv_input(
                path, gh_org, default_scopes, mapping, strict
            )
        return ConfigLoader.load_text_input(
            path, gh_org, default_scopes, mapping, strict
        )


def resolve_repository_mapping(
    ado_project: str,
    ado_repo: str,
    default_gh_org: str,
    mapping: dict = None,
    explicit_gh_org: str = None,
    explicit_gh_repo: str = None,
) -> tuple[str, str]:
    """Resolve a repository target using deterministic precedence.

    Precedence (highest first) is a row/repository explicit override, an exact
    ``mapping.repositories`` rule, ``mapping.project_to_org``, and finally the
    global GitHub organization.  The source repository name is preserved unless
    a rule explicitly renames it.
    """
    ado_project = _source_name(ado_project, "ado_project")
    ado_repo = _source_name(ado_repo, "ado_repo")
    rules = _validate_mapping_config(mapping or {})
    source_key = _source_key(ado_project, ado_repo)
    exact = _casefold_get(rules["repositories"], source_key, {})
    project_org = _casefold_get(rules["project_to_org"], ado_project, "")
    project_policy = _casefold_get(rules["projects"], ado_project, {})
    policy_org = project_policy.get("gh_org", "") \
        if isinstance(project_policy, dict) else ""
    base_org = rules.get("target_org") or default_gh_org
    target_org = (
        explicit_gh_org if explicit_gh_org is not None
        else exact.get("gh_org") or project_org or policy_org or base_org
    )
    if explicit_gh_repo is not None:
        target_repo = explicit_gh_repo
    elif exact.get("gh_repo"):
        target_repo = exact["gh_repo"]
    elif rules.get("apply_to_explicit", False):
        strategy = rules.get("strategy", "project-prefix")
        if strategy == "project-prefix":
            prefix = ado_project
            if isinstance(project_policy, str):
                prefix = project_policy
            elif isinstance(project_policy, dict):
                prefix = project_policy.get("prefix", ado_project)
            target_repo = _github_repo_slug(
                f"{prefix}{rules.get('separator', '-')}{ado_repo}",
                lowercase=rules.get("lowercase", True),
            )
        else:
            target_repo = _github_repo_slug(
                ado_repo, lowercase=rules.get("lowercase", True)
            )
    else:
        target_repo = ado_repo
    return _gh_org(target_org, "gh_org"), _gh_repo(target_repo, "gh_repo")


def _input_file(path: str, label: str) -> Path:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    if not input_path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    return input_path


def _string_keys(value: dict, field: str) -> None:
    non_strings = [key for key in value if not isinstance(key, str)]
    if non_strings:
        raise ValueError(f"{field} keys must be strings: {non_strings[0]!r}")


def _mapping(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    _string_keys(value, field)
    return value


def _positive_int(value: Any, field: str, *, minimum: int = 1,
                  maximum: int = 10_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _source_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    name = value.strip()
    if len(name) > 256:
        raise ValueError(f"{field} is longer than 256 characters")
    if _CONTROL_RE.search(name) or "/" in name or "\\" in name or "::" in name:
        raise ValueError(f"{field} contains unsafe path or control characters")
    return name


def _gh_org(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty GitHub organization")
    name = value.strip()
    if len(name) > 39 or not _GH_ORG_RE.fullmatch(name) or "--" in name:
        raise ValueError(
            f"{field} must be a 1-39 character GitHub organization slug"
        )
    return name


def _gh_repo(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty GitHub repository name")
    name = value.strip()
    if len(name) > 100 or not _GH_REPO_RE.fullmatch(name):
        raise ValueError(
            f"{field} must use 1-100 letters, numbers, '.', '_', or '-'"
        )
    if name in {".", ".."} or name.endswith(".") or name.casefold().endswith(".git"):
        raise ValueError(f"{field} has an unsafe or ambiguous Git repository name")
    return name


def _validate_scopes(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} entries must be non-empty strings")
        scope = item.strip()
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"unknown migration scope {scope!r}; expected one of "
                + ", ".join(sorted(_VALID_SCOPES))
            )
        if scope in seen:
            raise ValueError(f"duplicate migration scope {scope!r}")
        seen.add(scope)
        result.append(scope)
    return result


def _validate_mapping_config(value: Any) -> dict:
    mapping = _mapping(value, "mapping")
    allowed = {
        "target_org", "strategy", "separator", "lowercase",
        "existing_target_policy", "allow_nonempty_target",
        "include_disabled", "apply_to_explicit", "preflight_targets",
        "allowed_target_visibilities",
        "project_to_org", "projects", "repositories",
    }
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(
            "mapping has unknown key(s): " + ", ".join(sorted(unknown))
        )
    normalized: dict[str, Any] = {}
    if mapping.get("target_org"):
        normalized["target_org"] = _gh_org(
            mapping["target_org"], "mapping.target_org"
        )
    strategy = mapping.get("strategy", "project-prefix")
    if not isinstance(strategy, str) or strategy not in {"project-prefix", "preserve"}:
        raise ValueError("mapping.strategy must be 'project-prefix' or 'preserve'")
    normalized["strategy"] = strategy
    separator = mapping.get("separator", "-")
    if not isinstance(separator, str) or not separator \
            or len(separator) > 5 or not re.fullmatch(r"[._-]+", separator):
        raise ValueError("mapping.separator must use 1-5 '.', '_', or '-' characters")
    normalized["separator"] = separator
    for key, default in (
        ("lowercase", True), ("allow_nonempty_target", False),
        ("include_disabled", False), ("apply_to_explicit", False),
        ("preflight_targets", True),
    ):
        normalized[key] = _bool(mapping.get(key, default), f"mapping.{key}")
    existing_policy = mapping.get("existing_target_policy", "fail")
    if existing_policy not in {"fail", "reuse"}:
        raise ValueError("mapping.existing_target_policy must be 'fail' or 'reuse'")
    normalized["existing_target_policy"] = existing_policy
    raw_visibilities = mapping.get(
        "allowed_target_visibilities", ["private", "internal"]
    )
    if not isinstance(raw_visibilities, list) or not raw_visibilities:
        raise ValueError(
            "mapping.allowed_target_visibilities must be a non-empty list"
        )
    visibilities: list[str] = []
    for item in raw_visibilities:
        if not isinstance(item, str) or item not in {
            "private", "internal", "public"
        }:
            raise ValueError(
                "mapping.allowed_target_visibilities entries must be private, "
                "internal, or public"
            )
        if item not in visibilities:
            visibilities.append(item)
    normalized["allowed_target_visibilities"] = visibilities

    projects_raw = _mapping(mapping.get("project_to_org", {}),
                            "mapping.project_to_org")
    planner_projects_raw = _mapping(mapping.get("projects", {}),
                                    "mapping.projects")
    repos_raw = _mapping(mapping.get("repositories", {}),
                         "mapping.repositories")
    projects: dict[str, str] = {}
    project_keys: dict[str, str] = {}
    for project, target in projects_raw.items():
        source_project = _source_name(project, "mapping project")
        key = source_project.casefold()
        if key in project_keys:
            raise ValueError(
                f"case-insensitive duplicate mapping project {source_project!r}"
            )
        if isinstance(target, dict):
            target_map = _mapping(target, f"mapping.project_to_org[{project!r}]")
            unknown_target = set(target_map) - {"gh_org"}
            if unknown_target:
                raise ValueError(
                    f"project mapping {project!r} has unknown fields: "
                    + ", ".join(sorted(unknown_target))
                )
            target = target_map.get("gh_org")
        project_keys[key] = source_project
        projects[source_project] = _gh_org(target, f"target org for {project!r}")

    planner_projects: dict[str, Any] = {}
    planner_project_keys: set[str] = set()
    for project, policy in planner_projects_raw.items():
        source_project = _source_name(project, "mapping project")
        folded = source_project.casefold()
        if folded in planner_project_keys:
            raise ValueError(
                f"case-insensitive duplicate mapping project {source_project!r}"
            )
        planner_project_keys.add(folded)
        if isinstance(policy, str):
            if not policy.strip():
                raise ValueError(f"mapping project prefix for {project!r} is empty")
            planner_projects[source_project] = policy.strip()
        else:
            policy_map = _mapping(policy, f"mapping.projects[{project!r}]")
            unknown_policy = set(policy_map) - {"prefix", "gh_org"}
            if unknown_policy:
                raise ValueError(
                    f"project mapping {project!r} has unknown fields: "
                    + ", ".join(sorted(unknown_policy))
                )
            normalized_policy: dict[str, str] = {}
            if "prefix" in policy_map:
                prefix = policy_map["prefix"]
                if not isinstance(prefix, str) or not prefix.strip():
                    raise ValueError(f"mapping prefix for {project!r} is empty")
                normalized_policy["prefix"] = prefix.strip()
            if "gh_org" in policy_map:
                normalized_policy["gh_org"] = _gh_org(
                    policy_map["gh_org"], f"target org for {project!r}"
                )
            planner_projects[source_project] = normalized_policy

    repositories: dict[str, dict[str, str]] = {}
    repository_keys: set[str] = set()
    targets: dict[str, str] = {}
    for source, target in repos_raw.items():
        project, repo = _split_pair(source, "mapping repository source")
        source_key = _source_key(project, repo)
        if source_key in repository_keys:
            raise ValueError(
                f"case-insensitive duplicate repository mapping {source!r}"
            )
        repository_keys.add(source_key)
        if isinstance(target, str):
            if "/" in target:
                target_org, target_repo = _split_pair(
                    target, f"mapping target for {source!r}", target=True
                )
                target_mapping = {"gh_org": target_org, "gh_repo": target_repo}
            else:
                target_repo = _gh_repo(target, f"mapping target for {source!r}")
                target_org = normalized.get("target_org", "")
                target_mapping = {"gh_repo": target_repo}
        else:
            target_map = _mapping(target, f"mapping target for {source!r}")
            unknown_target = set(target_map) - {"gh_org", "gh_repo"}
            if unknown_target:
                raise ValueError(
                    f"repository mapping {source!r} has unknown fields: "
                    + ", ".join(sorted(unknown_target))
                )
            target_repo = _gh_repo(
                target_map.get("gh_repo"), f"target repo for {source!r}"
            )
            target_mapping = {"gh_repo": target_repo}
            if target_map.get("gh_org"):
                target_org = _gh_org(
                    target_map["gh_org"], f"target org for {source!r}"
                )
                target_mapping["gh_org"] = target_org
            else:
                target_org = normalized.get("target_org", "")
        if not target_org:
            target_org = _casefold_get(projects, project, "")
        if not target_org:
            project_policy = _casefold_get(planner_projects, project, {})
            if isinstance(project_policy, dict):
                target_org = project_policy.get("gh_org", "")
        target_key = f"{target_org.casefold()}/{target_repo.casefold()}"
        if target_key in targets:
            raise ValueError(
                f"mapping target collision: {source!r} and {targets[target_key]!r} "
                f"both map to {target_org}/{target_repo}"
            )
        targets[target_key] = source
        repositories[f"{project}/{repo}"] = target_mapping
    normalized["project_to_org"] = projects
    normalized["projects"] = planner_projects
    normalized["repositories"] = repositories
    return normalized


def _validate_global(value: Any) -> dict:
    raw = dict(_mapping(value, "global"))
    allowed_global = {
        "ado_org_url", "gh_org", "gh_api_url", "gh_web_url",
        "gh_enterprise_slug", "gh_api_version",
        "migration_strategy", "gei", "parallel", "pipeline_parallel",
        "execution_lease_seconds", "default_scopes",
        "allow_inline_secrets", "enforce_scope_dependencies",
        "include_unlinked_work_items", "ado_pat", "gh_token",
        "gh_token_config", "pipeline_conversion", "pipeline_delivery",
        "validation", "cleanup", "mapping", "governance",
    }
    unknown_global = set(raw) - allowed_global
    if unknown_global:
        raise ValueError(
            "global has unknown key(s): " + ", ".join(sorted(unknown_global))
        )
    if "ado_org_url" in raw and raw["ado_org_url"]:
        raw["ado_org_url"] = _service_url(
            raw["ado_org_url"], "global.ado_org_url"
        )
    for key in ("gh_api_url", "gh_web_url"):
        if key in raw and raw[key]:
            raw[key] = _service_url(raw[key], f"global.{key}")
    if "gh_org" in raw and raw["gh_org"]:
        raw["gh_org"] = _gh_org(raw["gh_org"], "global.gh_org")
    if "gh_enterprise_slug" in raw and (
        not isinstance(raw["gh_enterprise_slug"], str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", raw["gh_enterprise_slug"]
        )
    ):
        raise ValueError("global.gh_enterprise_slug is invalid")
    if "gh_api_version" in raw and (
        not isinstance(raw["gh_api_version"], str)
        or not re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", raw["gh_api_version"]
        )
    ):
        raise ValueError("global.gh_api_version must be YYYY-MM-DD")
    if raw.get("gh_enterprise_slug") and (
        "gh_api_version" not in raw or raw["gh_api_version"] < "2026-03-10"
    ):
        raise ValueError(
            "global.gh_enterprise_slug requires an explicitly configured "
            "global.gh_api_version of 2026-03-10 or newer for enterprise "
            "installation-audit endpoints"
        )
    if "migration_strategy" in raw:
        if not isinstance(raw["migration_strategy"], str):
            raise ValueError("global.migration_strategy must be 'mirror' or 'gei'")
        strategy = raw["migration_strategy"].strip().casefold()
        if strategy not in {"mirror", "gei"}:
            raise ValueError("global.migration_strategy must be 'mirror' or 'gei'")
        raw["migration_strategy"] = strategy
    if "gei" in raw:
        gei = dict(_mapping(raw["gei"], "global.gei"))
        unknown_gei = set(gei) - {
            "ado2gh_executable_path", "ado2gh_executable_sha256",
        }
        if unknown_gei:
            raise ValueError(
                "global.gei has unknown key(s): "
                + ", ".join(sorted(unknown_gei))
            )
        executable_path = gei.get("ado2gh_executable_path")
        if not isinstance(executable_path, str) or not executable_path.strip():
            raise ValueError(
                "global.gei.ado2gh_executable_path must be a non-empty "
                "absolute path to the standalone gh-ado2gh binary"
            )
        if not Path(executable_path).is_absolute():
            raise ValueError(
                "global.gei.ado2gh_executable_path must be absolute"
            )
        executable_sha256 = gei.get("ado2gh_executable_sha256")
        if not isinstance(executable_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", executable_sha256
        ):
            raise ValueError(
                "global.gei.ado2gh_executable_sha256 must be 64 lowercase "
                "hexadecimal characters"
            )
        gei["ado2gh_executable_path"] = executable_path.strip()
        raw["gei"] = gei
    if raw.get("migration_strategy", "mirror") == "gei" and "gei" not in raw:
        raise ValueError(
            "global.migration_strategy=gei requires global.gei with an exact "
            "standalone gh-ado2gh executable path and SHA-256"
        )
    for key, maximum in (("parallel", 512), ("pipeline_parallel", 2048)):
        if key in raw:
            raw[key] = _positive_int(raw[key], f"global.{key}", maximum=maximum)
    if "execution_lease_seconds" in raw:
        raw["execution_lease_seconds"] = _positive_int(
            raw["execution_lease_seconds"],
            "global.execution_lease_seconds",
            minimum=30,
            maximum=86_400,
        )
    if "default_scopes" in raw:
        raw["default_scopes"] = _validate_scopes(
            raw["default_scopes"], "global.default_scopes"
        )
    for key in (
        "allow_inline_secrets", "enforce_scope_dependencies",
        "include_unlinked_work_items",
    ):
        if key in raw:
            raw[key] = _bool(raw[key], f"global.{key}")
    for key in ("ado_pat", "gh_token", "gh_token_config"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"global.{key} must be a string")
    if "pipeline_conversion" in raw:
        conversion = dict(_mapping(
            raw["pipeline_conversion"], "global.pipeline_conversion"
        ))
        allowed_conversion = {
            "min_llm_confidence", "max_llm_resolutions",
            "llm_provider", "llm_model", "llm_base_url",
            "llm_organization", "llm_project", "llm_api_key_env",
            "llm_egress_mode",
            "require_permissions", "require_pinned_action_sha",
            "forbid_remote_script_execution", "max_workflow_bytes",
            "max_jobs", "max_steps_per_job", "action_pins",
            "manual_approval_manifest",
            "manual_approval_governance_envelope_env",
            "credential_attestation",
        }
        unknown = set(conversion) - allowed_conversion
        if unknown:
            raise ValueError(
                "global.pipeline_conversion has unknown key(s): "
                + ", ".join(sorted(unknown))
            )
        if "min_llm_confidence" in conversion:
            conversion["min_llm_confidence"] = _percentage(
                conversion["min_llm_confidence"],
                "global.pipeline_conversion.min_llm_confidence",
                scale=1,
            )
        if "max_llm_resolutions" in conversion:
            conversion["max_llm_resolutions"] = _positive_int(
                conversion["max_llm_resolutions"],
                "global.pipeline_conversion.max_llm_resolutions",
                minimum=0,
                maximum=100,
            )
        for key in (
            "require_permissions", "require_pinned_action_sha",
            "forbid_remote_script_execution",
        ):
            if key in conversion:
                conversion[key] = _bool(
                    conversion[key], f"global.pipeline_conversion.{key}"
                )
        for key, maximum in (
            ("max_workflow_bytes", 10_000_000),
            ("max_jobs", 10_000),
            ("max_steps_per_job", 100_000),
        ):
            if key in conversion:
                conversion[key] = _positive_int(
                    conversion[key],
                    f"global.pipeline_conversion.{key}",
                    maximum=maximum,
                )
        if "action_pins" in conversion:
            pins = _mapping(
                conversion["action_pins"],
                "global.pipeline_conversion.action_pins",
            )
            normalized_pins: dict[str, str] = {}
            for action, sha in pins.items():
                if not re.fullmatch(
                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.-]+)?",
                    action,
                ):
                    raise ValueError(
                        "pipeline action pin keys must be owner/repo or owner/repo@ref"
                    )
                if not isinstance(sha, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{40}", sha
                ):
                    raise ValueError(
                        f"pipeline action pin for {action!r} must be a 40-character SHA"
                    )
                normalized_pins[action] = sha.lower()
            conversion["action_pins"] = normalized_pins
        if "manual_approval_manifest" in conversion and not isinstance(
            conversion["manual_approval_manifest"], (str, dict)
        ):
            raise ValueError(
                "global.pipeline_conversion.manual_approval_manifest must be "
                "a manifest mapping or a path relative to the configuration file"
            )
        envelope_env = conversion.get("manual_approval_governance_envelope_env")
        if envelope_env is not None and (
            not isinstance(envelope_env, str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", envelope_env)
        ):
            raise ValueError(
                "global.pipeline_conversion.manual_approval_governance_envelope_env "
                "must be an uppercase environment-variable reference"
            )
        if "credential_attestation" in conversion:
            attestation = dict(_mapping(
                conversion["credential_attestation"],
                "global.pipeline_conversion.credential_attestation",
            ))
            unknown_attestation = set(attestation) - {
                "key_env_by_id", "trusted_workflow_sha_by_key_id",
                "max_age_seconds", "max_ttl_seconds", "clock_skew_seconds",
            }
            if unknown_attestation:
                raise ValueError(
                    "global.pipeline_conversion.credential_attestation has "
                    "unknown key(s): "
                    + ", ".join(sorted(unknown_attestation))
                )
            key_refs = _mapping(
                attestation.get("key_env_by_id"),
                "global.pipeline_conversion.credential_attestation.key_env_by_id",
            )
            if not key_refs or len(key_refs) > 32:
                raise ValueError(
                    "credential attestation key_env_by_id must contain 1 to 32 keys"
                )
            normalized_refs: dict[str, str] = {}
            seen_env_names: set[str] = set()
            for key_id, env_name in key_refs.items():
                if not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key_id
                ):
                    raise ValueError(
                        "credential attestation key ids must be safe identifiers"
                    )
                if not isinstance(env_name, str) or not re.fullmatch(
                    r"[A-Z_][A-Z0-9_]{0,127}", env_name
                ):
                    raise ValueError(
                        "credential attestation keys must reference uppercase "
                        "environment variable names, never key material"
                    )
                if env_name in seen_env_names:
                    raise ValueError(
                        "credential attestation environment key references must be unique"
                    )
                seen_env_names.add(env_name)
                normalized_refs[str(key_id)] = env_name
            attestation["key_env_by_id"] = normalized_refs
            workflow_refs = _mapping(
                attestation.get("trusted_workflow_sha_by_key_id"),
                "global.pipeline_conversion.credential_attestation."
                "trusted_workflow_sha_by_key_id",
            )
            if set(workflow_refs) != set(normalized_refs):
                raise ValueError(
                    "credential attestation trusted workflow SHA keys must "
                    "exactly match key_env_by_id"
                )
            normalized_workflows: dict[str, str] = {}
            for key_id, sha in workflow_refs.items():
                if not isinstance(sha, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{40}", sha
                ):
                    raise ValueError(
                        "credential attestation trusted workflows must be "
                        "immutable 40-character commit SHAs"
                    )
                normalized_workflows[str(key_id)] = sha.lower()
            attestation["trusted_workflow_sha_by_key_id"] = normalized_workflows
            for key, minimum, maximum in (
                ("max_age_seconds", 1, 86_400),
                ("max_ttl_seconds", 1, 86_400),
                ("clock_skew_seconds", 0, 300),
            ):
                if key in attestation:
                    attestation[key] = _positive_int(
                        attestation[key],
                        f"global.pipeline_conversion.credential_attestation.{key}",
                        minimum=minimum,
                        maximum=maximum,
                    )
            max_age = int(attestation.get("max_age_seconds", 300))
            max_ttl = int(attestation.get("max_ttl_seconds", 900))
            if max_age > max_ttl:
                raise ValueError(
                    "credential attestation max_age_seconds cannot exceed "
                    "max_ttl_seconds"
                )
            conversion["credential_attestation"] = attestation
        # Persist every non-secret provider routing default in the normalized
        # configuration. The organization-level immutable plan therefore owns
        # the exact provider, endpoint, model, organization, project, and key
        # reference used later by the executor.
        conversion.update(normalize_pipeline_llm_settings(
            conversion,
            enforce_environment_match=False,
        ))
        raw["pipeline_conversion"] = conversion
    else:
        # Make disabled/default provider routing explicit so even plans with no
        # pipeline-specific overrides bind the same execution identity used by
        # direct library callers and the executor.
        raw["pipeline_conversion"] = normalize_pipeline_llm_settings(
            {},
            enforce_environment_match=False,
        )
    if "pipeline_delivery" in raw:
        delivery = dict(_mapping(
            raw["pipeline_delivery"], "global.pipeline_delivery"
        ))
        unknown = set(delivery) - {"mode", "branch", "title"}
        if unknown:
            raise ValueError(
                "global.pipeline_delivery has unknown key(s): "
                + ", ".join(sorted(unknown))
            )
        mode = delivery.get("mode", "pull_request")
        if mode not in {"pull_request", "local"}:
            raise ValueError(
                "global.pipeline_delivery.mode must be 'pull_request' or 'local'"
            )
        delivery["mode"] = mode
        branch = delivery.get("branch", "ado2gh/migrated-workflows")
        if not isinstance(branch, str) or not _safe_git_branch(branch):
            raise ValueError("global.pipeline_delivery.branch is not a safe Git ref")
        delivery["branch"] = branch
        title = delivery.get("title", "Review migrated GitHub Actions workflows")
        if (
            not isinstance(title, str) or not title.strip()
            or len(title) > 256 or _CONTROL_RE.search(title)
        ):
            raise ValueError(
                "global.pipeline_delivery.title must be a non-empty safe string"
            )
        delivery["title"] = title.strip()
        raw["pipeline_delivery"] = delivery
    if "validation" in raw:
        validation = dict(_mapping(raw["validation"], "global.validation"))
        unknown = set(validation) - {"max_repair_attempts"}
        if unknown:
            raise ValueError(
                "global.validation has unknown key(s): "
                + ", ".join(sorted(unknown))
            )
        if "max_repair_attempts" in validation:
            validation["max_repair_attempts"] = _positive_int(
                validation["max_repair_attempts"],
                "global.validation.max_repair_attempts",
                minimum=0,
                maximum=100,
            )
        raw["validation"] = validation
    if "cleanup" in raw:
        cleanup = dict(_mapping(raw["cleanup"], "global.cleanup"))
        allowed_cleanup = {
            "allow_disable_pipelines", "allow_redirect", "allow_archive"
        }
        unknown = set(cleanup) - allowed_cleanup
        if unknown:
            raise ValueError(
                "global.cleanup has unknown key(s): "
                + ", ".join(sorted(unknown))
            )
        for key in allowed_cleanup:
            if key in cleanup:
                cleanup[key] = _bool(cleanup[key], f"global.cleanup.{key}")
        raw["cleanup"] = cleanup
    if "governance" in raw:
        raw["governance"] = normalize_governance_settings(raw["governance"])
    raw["mapping"] = _validate_mapping_config(raw.get("mapping", {}))
    return raw


def _safe_git_branch(value: str) -> bool:
    """Conservative subset of git-check-ref-format for generated branches."""
    if not value or len(value) > 255 or value in {"@", ".", ".."}:
        return False
    if value.startswith(("/", ".")) or value.endswith(("/", ".", ".lock")):
        return False
    if ".." in value or "@{" in value or "//" in value:
        return False
    return not bool(re.search(r"[\x00-\x20\x7f~^:?*\[\]\\]", value))


def _service_url(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a URL string")
    url = value.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc \
            or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(
            f"{field} must be an absolute HTTPS URL without credentials, "
            "query, or fragment"
        )
    return url


def _validate_phase_settings(value: Any) -> None:
    phases = _mapping(value, "phases")
    allowed_settings = {
        "repo_cap", "risk_max", "batch_size", "repo_parallel",
        "pipeline_parallel", "gate_repo_success_pct",
        "gate_pipeline_success_pct", "gate_min_completed",
    }
    for phase, settings_value in phases.items():
        if phase not in _VALID_PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        settings = _mapping(settings_value, f"phases.{phase}")
        unknown = set(settings) - allowed_settings
        if unknown:
            raise ValueError(
                f"phases.{phase} has unknown key(s): "
                + ", ".join(sorted(unknown))
            )
        for key in ("repo_cap", "batch_size", "repo_parallel", "pipeline_parallel",
                    "gate_min_completed"):
            if key in settings:
                _positive_int(settings[key], f"phases.{phase}.{key}", maximum=999_999)
        if "risk_max" in settings:
            _percentage(settings["risk_max"], f"phases.{phase}.risk_max", scale=100)
        for key in ("gate_repo_success_pct", "gate_pipeline_success_pct"):
            if key in settings:
                _percentage(settings[key], f"phases.{phase}.{key}", scale=1)


def _percentage(value: Any, field: str, scale: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)) or not 0 <= float(value) <= scale:
        raise ValueError(f"{field} must be between 0 and {scale:g}")
    return float(value)


def _parse_wave(raw_value: Any, global_cfg: dict, field: str) -> WaveConfig:
    raw = _mapping(raw_value, field)
    if "wave_id" not in raw:
        raise ValueError(f"{field}.wave_id is required")
    wave_id = _positive_int(raw["wave_id"], f"{field}.wave_id", minimum=0,
                            maximum=2_147_483_647)
    repos_raw = raw.get("repos", [])
    if not isinstance(repos_raw, list):
        raise ValueError(f"{field}.repos must be a list")
    repos = _parse_repos(repos_raw, global_cfg, field)
    name = raw.get("name", f"wave-{wave_id}")
    description = raw.get("description", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{field}.name must be a non-empty string")
    if not isinstance(description, str):
        raise ValueError(f"{field}.description must be a string")
    phase = raw.get("phase", "")
    if not isinstance(phase, str) or (phase and phase not in _VALID_PHASES):
        raise ValueError(f"{field}.phase must be one of {sorted(_VALID_PHASES)}")
    return WaveConfig(
        wave_id=wave_id,
        name=name.strip(),
        description=description,
        repos=repos,
        parallel=_positive_int(
            raw.get("parallel", global_cfg.get("parallel", 4)),
            f"{field}.parallel", maximum=512,
        ),
        retry_max=_positive_int(
            raw.get("retry_max", 3), f"{field}.retry_max", minimum=0,
            maximum=100,
        ),
        timeout_sec=_positive_int(
            raw.get("timeout_sec", 1800), f"{field}.timeout_sec",
            maximum=604_800,
        ),
        pipeline_parallel=_positive_int(
            raw.get("pipeline_parallel", global_cfg.get("pipeline_parallel", 8)),
            f"{field}.pipeline_parallel", maximum=2048,
        ),
        phase=phase,
    )


def _parse_repos(repos_raw: list[dict], global_cfg: dict,
                 field: str = "repos") -> list[RepoConfig]:
    repos: list[RepoConfig] = []
    default_gh_org = global_cfg.get("gh_org", "")
    default_scopes = _validate_scopes(
        global_cfg.get("default_scopes", ["repo"]), "global.default_scopes"
    )
    mapping = global_cfg.get("mapping", {})
    for index, raw_value in enumerate(repos_raw):
        repo_field = f"{field}.repos[{index}]"
        raw = _mapping(raw_value, repo_field)
        allowed_repo = {
            "ado_project", "ado_repo", "gh_org", "gh_repo", "scopes",
            "team_mapping", "access_policy_approved",
            "access_policy_evidence", "skip_lfs", "archive_source", "tags",
            "pipeline_parallel", "pipeline_filter", "risk_score", "phase",
        }
        unknown_repo = set(raw) - allowed_repo
        if unknown_repo:
            raise ValueError(
                f"{repo_field} has unknown key(s): "
                + ", ".join(sorted(unknown_repo))
            )
        if "ado_project" not in raw or "ado_repo" not in raw:
            raise ValueError(f"{repo_field} requires ado_project and ado_repo")
        ado_project = _source_name(raw["ado_project"], f"{repo_field}.ado_project")
        ado_repo = _source_name(raw["ado_repo"], f"{repo_field}.ado_repo")
        gh_org, gh_repo = resolve_repository_mapping(
            ado_project, ado_repo, default_gh_org, mapping,
            raw.get("gh_org"), raw.get("gh_repo"),
        )
        team_mapping = _mapping(raw.get("team_mapping", {}),
                                f"{repo_field}.team_mapping")
        normalized_teams: dict[str, dict[str, str]] = {}
        for source_team, target_access in team_mapping.items():
            if not isinstance(source_team, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", source_team.strip()
            ):
                raise ValueError(
                    f"{repo_field}.team_mapping keys must be exact sha256 ACL "
                    "principal keys from the source access snapshot"
                )
            access = _mapping(
                target_access,
                f"{repo_field}.team_mapping.{source_team}",
            )
            unknown_access = set(access) - {"github_team", "permission"}
            if unknown_access:
                raise ValueError(
                    f"{repo_field}.team_mapping.{source_team} has unknown key(s): "
                    + ", ".join(sorted(unknown_access))
                )
            github_team = access.get("github_team")
            permission = access.get("permission")
            if not isinstance(github_team, str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99})", github_team.strip()
            ):
                raise ValueError(
                    f"{repo_field}.team_mapping.{source_team}.github_team "
                    "must be an exact GitHub team slug"
                )
            if permission not in {"pull", "triage", "push", "maintain", "admin"}:
                raise ValueError(
                    f"{repo_field}.team_mapping.{source_team}.permission must be "
                    "pull, triage, push, maintain, or admin"
                )
            normalized_teams[source_team.strip()] = {
                "github_team": github_team.strip(),
                "permission": permission,
            }
        tags = raw.get("tags", [])
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            raise ValueError(f"{repo_field}.tags must be a list of non-empty strings")
        risk_score = _percentage(
            raw.get("risk_score", 0.0), f"{repo_field}.risk_score", scale=100
        )
        phase = raw.get("phase", "")
        if not isinstance(phase, str) or (phase and phase not in _VALID_PHASES):
            raise ValueError(f"{repo_field}.phase must be one of {sorted(_VALID_PHASES)}")
        access_approved = _bool(
            raw.get("access_policy_approved", False),
            f"{repo_field}.access_policy_approved",
        )
        evidence = _mapping(
            raw.get("access_policy_evidence", {}),
            f"{repo_field}.access_policy_evidence",
        )
        evidence_fields = {
            "approver", "ticket", "approved_at", "source_acl_digest",
            "review_evidence_digest", "excluded_principals",
            "disposition_digest", "target_base_repository_permission",
            "target_access_digest", "target_team_members",
            "target_team_parents", "target_deploy_keys",
            "identity_mapping",
            "target_github_apps",
        }
        if evidence and set(evidence) != evidence_fields:
            raise ValueError(
                f"{repo_field}.access_policy_evidence must contain exactly: "
                + ", ".join(sorted(evidence_fields))
            )
        if access_approved and not evidence:
            raise ValueError(
                f"{repo_field}.access_policy_approved requires ACL-bound evidence"
            )
        if not access_approved and evidence:
            raise ValueError(
                f"{repo_field}.access_policy_evidence requires access_policy_approved"
            )
        normalized_evidence: dict[str, Any] = {}
        for key in (
            "approver", "ticket", "approved_at", "source_acl_digest",
            "review_evidence_digest", "disposition_digest",
            "target_base_repository_permission", "target_access_digest",
        ):
            if key not in evidence:
                continue
            value = evidence[key]
            if not isinstance(value, str) or not value.strip() \
                    or _CONTROL_RE.search(value):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.{key} must be safe text"
                )
            normalized_evidence[key] = value.strip()
        excluded = _mapping(
            evidence.get("excluded_principals", {}),
            f"{repo_field}.access_policy_evidence.excluded_principals",
        )
        normalized_excluded: dict[str, str] = {}
        for principal, rationale in excluded.items():
            if not isinstance(principal, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", principal.strip()
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.excluded_principals "
                    "keys must be exact sha256 ACL principal keys"
                )
            if (
                not isinstance(rationale, str) or not rationale.strip()
                or len(rationale) > 2_048 or _CONTROL_RE.search(rationale)
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.excluded_principals."
                    f"{principal} must be a safe non-empty rationale"
                )
            normalized_excluded[principal.strip()] = rationale.strip()
        if evidence:
            normalized_evidence["excluded_principals"] = normalized_excluded
        raw_target_members = _mapping(
            evidence.get("target_team_members", {}),
            f"{repo_field}.access_policy_evidence.target_team_members",
        )
        normalized_target_members: dict[str, list[str]] = {}
        for raw_slug, raw_members in raw_target_members.items():
            if not isinstance(raw_slug, str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99})", raw_slug.strip()
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_members "
                    "keys must be exact GitHub team slugs"
                )
            if not isinstance(raw_members, list) or any(
                not isinstance(member, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", member)
                for member in raw_members
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_members."
                    f"{raw_slug} must be a list of hashed immutable user IDs"
                )
            members = sorted(set(raw_members))
            if len(members) != len(raw_members):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_members."
                    f"{raw_slug} contains duplicate member IDs"
                )
            slug = raw_slug.strip().casefold()
            if slug in normalized_target_members:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_members "
                    "contains duplicate team slugs"
                )
            normalized_target_members[slug] = members
        if evidence:
            normalized_evidence["target_team_members"] = normalized_target_members
        raw_target_parents = _mapping(
            evidence.get("target_team_parents", {}),
            f"{repo_field}.access_policy_evidence.target_team_parents",
        )
        normalized_target_parents: dict[str, str] = {}
        for raw_slug, raw_parent in raw_target_parents.items():
            if not isinstance(raw_slug, str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99})", raw_slug.strip()
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_parents "
                    "keys must be exact GitHub team slugs"
                )
            if not isinstance(raw_parent, str) or (
                raw_parent and not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", raw_parent
                )
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_parents."
                    f"{raw_slug} must be empty or a hashed immutable team ID"
                )
            slug = raw_slug.strip().casefold()
            if slug in normalized_target_parents:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_team_parents "
                    "contains duplicate team slugs"
                )
            normalized_target_parents[slug] = raw_parent
        raw_deploy_keys = _mapping(
            evidence.get("target_deploy_keys", {}),
            f"{repo_field}.access_policy_evidence.target_deploy_keys",
        )
        normalized_deploy_keys: dict[str, bool] = {}
        for key, read_only in raw_deploy_keys.items():
            if not isinstance(key, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", key
            ) or not isinstance(read_only, bool):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_deploy_keys "
                    "must map hashed immutable key IDs to boolean read_only flags"
                )
            normalized_deploy_keys[key] = read_only
        if evidence:
            normalized_evidence["target_team_parents"] = normalized_target_parents
            normalized_evidence["target_deploy_keys"] = normalized_deploy_keys
        raw_identity_mapping = _mapping(
            evidence.get("identity_mapping", {}),
            f"{repo_field}.access_policy_evidence.identity_mapping",
        )
        normalized_identity_mapping: dict[str, str] = {}
        for source_identity, target_identity in raw_identity_mapping.items():
            if (
                not isinstance(source_identity, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_identity)
                or not isinstance(target_identity, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", target_identity)
            ):
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.identity_mapping "
                    "must map hashed ADO identity IDs to hashed GitHub user IDs"
                )
            normalized_identity_mapping[source_identity] = target_identity
        if len(set(normalized_identity_mapping.values())) \
                != len(normalized_identity_mapping):
            raise ValueError(
                f"{repo_field}.access_policy_evidence.identity_mapping must be "
                "one-to-one"
            )
        if evidence:
            normalized_evidence["identity_mapping"] = normalized_identity_mapping
        target_github_apps = _mapping(
            evidence.get("target_github_apps", {}),
            f"{repo_field}.access_policy_evidence.target_github_apps",
        )
        if target_github_apps:
            raise ValueError(
                f"{repo_field}.access_policy_evidence.target_github_apps must "
                "remain empty until exhaustive GitHub App access inventory is "
                "available"
            )
        if evidence:
            normalized_evidence["target_github_apps"] = {}
        if normalized_evidence and (
            not re.fullmatch(
                r"[0-9a-f]{64}", normalized_evidence["source_acl_digest"]
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                normalized_evidence["review_evidence_digest"],
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                normalized_evidence["disposition_digest"],
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                normalized_evidence["target_access_digest"],
            )
        ):
            raise ValueError(
                f"{repo_field}.access_policy_evidence digests are invalid"
            )
        if normalized_evidence:
            try:
                approved_at = datetime.fromisoformat(
                    normalized_evidence["approved_at"].replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.approved_at must be ISO-8601"
                ) from exc
            if approved_at.tzinfo is None:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.approved_at must include "
                    "a UTC offset"
                )
            overlap = set(normalized_teams) & set(normalized_excluded)
            if overlap:
                raise ValueError(
                    f"{repo_field} access principals cannot be both mapped and "
                    f"excluded: {sorted(overlap)}"
                )
            expected_disposition = access_disposition_digest(
                normalized_evidence["source_acl_digest"],
                (
                    (source, access["github_team"], access["permission"])
                    for source, access in normalized_teams.items()
                ),
                normalized_excluded,
                normalized_identity_mapping,
            )
            if normalized_evidence["disposition_digest"] != expected_disposition:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.disposition_digest does "
                    "not bind the exact snapshot, mappings, and exclusions"
                )
            expected_target_access = target_access_policy_digest(
                normalized_evidence["target_base_repository_permission"],
                (
                    (source, access["github_team"], access["permission"])
                    for source, access in normalized_teams.items()
                ),
                normalized_target_members,
                normalized_target_parents,
                normalized_deploy_keys,
                {},
            )
            if normalized_evidence["target_access_digest"] \
                    != expected_target_access:
                raise ValueError(
                    f"{repo_field}.access_policy_evidence.target_access_digest "
                    "does not bind the exact base permission, exhaustive teams, "
                    "and empty direct-collaborator policy"
                )
        if normalized_teams and not access_approved:
            raise ValueError(
                f"{repo_field}.team_mapping requires access_policy_approved"
            )
        repos.append(_build_repo(
            ado_project=ado_project,
            ado_repo=ado_repo,
            gh_org=gh_org,
            gh_repo=gh_repo,
            scopes=_validate_scopes(
                raw.get("scopes", default_scopes), f"{repo_field}.scopes"
            ),
            team_mapping=normalized_teams,
            access_policy_approved=access_approved,
            access_policy_evidence=normalized_evidence,
            source_access_snapshot={},
            skip_lfs=_bool(raw.get("skip_lfs", False), f"{repo_field}.skip_lfs"),
            archive_source=_bool(
                raw.get("archive_source", False), f"{repo_field}.archive_source"
            ),
            tags=[tag.strip() for tag in tags],
            pipeline_parallel=_positive_int(
                raw.get("pipeline_parallel", global_cfg.get("pipeline_parallel", 8)),
                f"{repo_field}.pipeline_parallel", maximum=2048,
            ),
            pipeline_filter=raw.get("pipeline_filter", ""),
            risk_score=risk_score,
            phase=phase,
        ))
    return repos


def _build_repo(**values) -> RepoConfig:
    values["ado_project"] = _source_name(values["ado_project"], "ado_project")
    values["ado_repo"] = _source_name(values["ado_repo"], "ado_repo")
    values["gh_org"] = _gh_org(values["gh_org"], "gh_org")
    values["gh_repo"] = _gh_repo(values["gh_repo"], "gh_repo")
    pipeline_filter = values.get("pipeline_filter", "")
    if not isinstance(pipeline_filter, str):
        raise ValueError("pipeline_filter must be a string")
    try:
        re.compile(pipeline_filter)
    except re.error as exc:
        raise ValueError(f"invalid pipeline_filter regex: {exc}") from exc
    values["pipeline_filter"] = pipeline_filter
    return RepoConfig(**values)


def _split_pair(value: Any, field: str, target: bool = False) -> tuple[str, str]:
    if not isinstance(value, str) or value.count("/") != 1:
        expected = "gh_org/gh_repo" if target else "project/repo"
        raise ValueError(f"{field} must use {expected!r} format")
    first, second = (part.strip() for part in value.split("/", 1))
    if target:
        return _gh_org(first, field), _gh_repo(second, field)
    return _source_name(first, field), _source_name(second, field)


def _source_key(project: str, repo: str) -> str:
    return f"{project.casefold()}/{repo.casefold()}"


def _casefold_get(mapping: dict, key: str, default=None):
    folded = key.casefold()
    for candidate, value in mapping.items():
        if candidate.casefold() == folded:
            return value
    return default


def _github_repo_slug(value: str, lowercase: bool = True) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip(".-_")
    slug = re.sub(r"-{2,}", "-", slug)
    if lowercase:
        slug = slug.lower()
    slug = slug[:100].rstrip(".-_")
    return _gh_repo(slug, "mapped gh_repo")


def _parse_text_line(line: str, default_gh_org: str,
                     scopes: list[str], mapping: dict = None) -> RepoConfig:
    if line.count("::") > 1:
        raise ValueError("repository line contains more than one '::' delimiter")
    source_part, separator, target_part = line.partition("::")
    ado_project, ado_repo = _split_pair(source_part, "source repository")
    explicit_org = explicit_repo = None
    if separator:
        explicit_org, explicit_repo = _split_pair(
            target_part, "target repository", target=True
        )
    gh_org, gh_repo = resolve_repository_mapping(
        ado_project, ado_repo, default_gh_org, mapping,
        explicit_org, explicit_repo,
    )
    return _build_repo(
        ado_project=ado_project,
        ado_repo=ado_repo,
        gh_org=gh_org,
        gh_repo=gh_repo,
        scopes=list(scopes),
    )


def _validate_repository_mappings(repos: list[RepoConfig], context: str) -> None:
    sources: dict[str, RepoConfig] = {}
    targets: dict[str, RepoConfig] = {}
    for repo in repos:
        source_key = _source_key(repo.ado_project, repo.ado_repo)
        target_key = f"{repo.gh_org.casefold()}/{repo.gh_repo.casefold()}"
        if source_key in sources:
            prior = sources[source_key]
            raise ValueError(
                f"duplicate source mapping in {context}: "
                f"{prior.ado_project}/{prior.ado_repo}"
            )
        if target_key in targets:
            prior = targets[target_key]
            raise ValueError(
                f"GitHub target collision in {context}: "
                f"{prior.ado_project}/{prior.ado_repo} and "
                f"{repo.ado_project}/{repo.ado_repo} both map to "
                f"{repo.gh_org}/{repo.gh_repo}"
            )
        sources[source_key] = repo
        targets[target_key] = repo
